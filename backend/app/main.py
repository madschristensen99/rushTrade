import time
import uuid
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from app.config import get_settings
from app.core.api import api_router
from app.core.exceptions import setup_exception_handlers, get_user_id
from app.core.websocket import websocket_endpoint
from app.core.logging_config import setup_logging
from app.database.connection import init_db, close_db
from app.services.redis_service import init_redis, close_redis
from app.modules.user.views import router as auth_router
from app.core.auth import auth_middleware
from app.middleware.rate_limiter import rate_limit_middleware, rate_limiter

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Setup logging first
    setup_logging()
    
    logger.info("🚀 Starting application...")
    await init_db()
    logger.info("✅ Database initialized")
    await init_redis()
    logger.info("✅ Redis initialized")
    
    # Start rate limiter cleanup task
    import asyncio
    cleanup_task = asyncio.create_task(rate_limiter.cleanup_old_entries())
    
    yield
    
    logger.info("🛑 Shutting down application...")
    cleanup_task.cancel()
    await close_redis()
    logger.info("✅ Redis closed")
    await close_db()
    logger.info("✅ Database closed")


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.APP_NAME,
        version="1.0.0",
        docs_url=f"{settings.API_V1_PREFIX}/docs",
        redoc_url=f"{settings.API_V1_PREFIX}/redoc",
        openapi_url=f"{settings.API_V1_PREFIX}/openapi.json",
        lifespan=lifespan,
    )
    
    # ========================================================================
    # MIDDLEWARE (Applied in reverse order - last added runs first)
    # ========================================================================
    
    # 1. CORS (runs first)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    # 2. Request Logging (runs second - logs all requests with timing and user info)
    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        """Log all HTTP requests with user info, timing, and status codes"""
        # Skip health check logging
        if request.url.path == "/health":
            return await call_next(request)
        
        # Generate or extract request ID
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        
        # Start timer
        start_time = time.time()
        
        # Process request
        response = await call_next(request)
        
        # Calculate duration
        duration_ms = round((time.time() - start_time) * 1000, 2)
        
        # Get user ID (will be populated by auth middleware)
        user_id = get_user_id(request)
        
        # Log request
        logger.bind(
            request_id=request_id,
            user_id=user_id,
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=duration_ms,
            ip=request.client.host if request.client else "unknown",
            user_agent=request.headers.get("user-agent", "unknown")
        ).info(f"{request.method} {request.url.path} - {response.status_code} - {duration_ms}ms")
        
        # Add request ID to response headers
        response.headers["X-Request-ID"] = request_id
        
        return response
    
    # 3. Rate Limiting (runs third - uses request.state.user from auth)
    @app.middleware("http")
    async def rate_limiting(request: Request, call_next):
        """Global rate limiting: 30 requests/second per user"""
        return await rate_limit_middleware(request, call_next)
    
    # 4. Auth State Middleware (runs fourth - populates request.state.user)
    @app.middleware("http")
    async def auth_state(request: Request, call_next):
        """Populate request.state.user for authenticated requests"""
        return await auth_middleware(request, call_next)
    
    # 5. Security Headers (runs fifth)
    @app.middleware("http")
    async def add_security_headers(request: Request, call_next):
        """Add comprehensive security headers to all responses"""
        response = await call_next(request)
        
        # Content Security Policy - Strict policy to prevent XSS
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data: https:; "
            "connect-src 'self' wss: https:; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self'"
        )
        
        # Prevent MIME type sniffing
        response.headers["X-Content-Type-Options"] = "nosniff"
        
        # Prevent clickjacking
        response.headers["X-Frame-Options"] = "DENY"
        
        # XSS Protection (legacy browsers)
        response.headers["X-XSS-Protection"] = "1; mode=block"
        
        # Force HTTPS
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        
        # Referrer policy
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        
        # Permissions policy (restrict browser features)
        response.headers["Permissions-Policy"] = (
            "geolocation=(), "
            "microphone=(), "
            "camera=(), "
            "payment=(), "
            "usb=(), "
            "magnetometer=(), "
            "gyroscope=(), "
            "accelerometer=()"
        )
        
        return response
    
    # ========================================================================
    # EXCEPTION HANDLERS & ROUTERS
    # ========================================================================
    setup_exception_handlers(app)
    app.include_router(api_router, prefix=settings.API_V1_PREFIX)
    app.include_router(auth_router, prefix="/api/auth", tags=["auth"])
    
    # WebSocket
    app.websocket("/ws/terminal/{instance_id}")(websocket_endpoint)
    
    # Health check (bypass rate limiting)
    @app.get("/health")
    async def health_check():
        return {"status": "healthy", "app": settings.APP_NAME}
    
    return app


app = create_app()