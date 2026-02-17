# app/middleware/rate_limiter.py

from fastapi import Request, HTTPException, status
from fastapi.responses import JSONResponse
from typing import Callable
import time
from collections import defaultdict, deque
import asyncio


class RateLimiter:
    """Sliding window rate limiter"""
    
    def __init__(self, requests_per_second: int = 30, window_size: int = 1):
        self.requests_per_second = requests_per_second
        self.window_size = window_size
        self.request_history = defaultdict(lambda: deque(maxlen=int(requests_per_second * 2)))
        self.lock = asyncio.Lock()
    
    async def is_allowed(self, user_id: str) -> tuple[bool, dict]:
        """Check if request allowed"""
        async with self.lock:
            current_time = time.time()
            window_start = current_time - self.window_size
            
            user_requests = self.request_history[user_id]
            
            # Remove old requests
            while user_requests and user_requests[0] < window_start:
                user_requests.popleft()
            
            request_count = len(user_requests)
            allowed = request_count < self.requests_per_second
            
            if allowed:
                user_requests.append(current_time)
            
            remaining = max(0, self.requests_per_second - request_count - (1 if allowed else 0))
            reset_time = int(current_time) + self.window_size if user_requests else int(current_time)
            
            info = {
                "limit": self.requests_per_second,
                "remaining": remaining,
                "reset": reset_time,
                "retry_after": 1 if not allowed else None
            }
            
            return allowed, info
    
    async def cleanup_old_entries(self):
        """Memory cleanup"""
        while True:
            await asyncio.sleep(60)
            async with self.lock:
                current_time = time.time()
                inactive_threshold = current_time - 300
                users_to_remove = [
                    user_id for user_id, requests in self.request_history.items()
                    if not requests or requests[-1] < inactive_threshold
                ]
                for user_id in users_to_remove:
                    del self.request_history[user_id]


# Global rate limiter
rate_limiter = RateLimiter(requests_per_second=30, window_size=1)


def get_user_identifier(request: Request) -> str:
    """Get user ID from request (authenticated or IP)"""
    if hasattr(request.state, "user") and request.state.user:
        return f"user_{request.state.user.id}"
    
    # Fallback to IP
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return f"ip_{forwarded.split(',')[0].strip()}"
    return f"ip_{request.client.host}"


async def rate_limit_middleware(request: Request, call_next: Callable):
    """Global rate limiting: 30 req/sec per user"""
    
    # Skip for health/docs
    if request.url.path in ["/health", "/metrics", "/docs", "/openapi.json", "/redoc"]:
        return await call_next(request)
    
    if request.url.path.startswith("/static"):
        return await call_next(request)
    
    user_id = get_user_identifier(request)
    allowed, info = await rate_limiter.is_allowed(user_id)
    
    if not allowed:
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={
                "detail": "Rate limit exceeded. Maximum 30 requests per second.",
                "limit": info["limit"],
                "remaining": info["remaining"],
                "reset": info["reset"],
                "retry_after": info["retry_after"]
            },
            headers={
                "X-RateLimit-Limit": str(info["limit"]),
                "X-RateLimit-Remaining": str(info["remaining"]),
                "X-RateLimit-Reset": str(info["reset"]),
                "Retry-After": str(info["retry_after"])
            }
        )
    
    response = await call_next(request)
    
    # Add headers
    response.headers["X-RateLimit-Limit"] = str(info["limit"])
    response.headers["X-RateLimit-Remaining"] = str(info["remaining"])
    response.headers["X-RateLimit-Reset"] = str(info["reset"])
    
    return response


# FIXED: Per-endpoint rate limiting as FastAPI dependency
class EndpointRateLimiter:
    """Per-endpoint rate limiter using Depends() pattern"""
    
    def __init__(self, requests: int, window: int):
        self.limiter = RateLimiter(requests_per_second=requests/window, window_size=window)
        self.requests = requests
        self.window = window
    
    async def __call__(self, request: Request):
        """Called as FastAPI dependency"""
        endpoint = request.url.path
        user_id = f"{get_user_identifier(request)}:{endpoint}"
        
        allowed, info = await self.limiter.is_allowed(user_id)
        
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit: max {self.requests} requests per {self.window}s",
                headers={
                    "X-RateLimit-Limit": str(self.requests),
                    "X-RateLimit-Remaining": str(info["remaining"]),
                    "X-RateLimit-Reset": str(info["reset"]),
                    "Retry-After": str(info["retry_after"])
                }
            )


def endpoint_rate_limit(requests: int, window: int):
    """
    Create rate limit dependency
    
    Usage:
        @router.post("/deploy")
        async def deploy(
            _rate_limit: bool = Depends(endpoint_rate_limit(5, 60))
        ):
    """
    return EndpointRateLimiter(requests, window)