# app/middleware/auth_middleware.py

"""
Optional authentication middleware for rate limiting.
Does NOT raise exceptions - just populates request.state.user if token exists.

Works alongside app/core/security.py which handles protected endpoints.
"""

from fastapi import Request
from typing import Callable
from jose import JWTError, jwt
from app.config import get_settings

settings = get_settings()


async def auth_middleware(request: Request, call_next: Callable):
    """
    Populate request.state.user for rate limiting (optional auth).
    Never raises exceptions - silently continues if no/invalid token.
    """
    request.state.user = None
    
    auth_header = request.headers.get("Authorization")
    
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header[7:]
        
        try:
            payload = jwt.decode(
                token,
                settings.JWT_SECRET_KEY,
                algorithms=[settings.JWT_ALGORITHM]
            )
            
            # Minimal user object for rate limiting
            class UserState:
                def __init__(self, user_id: int, username: str):
                    self.id = user_id
                    self.username = username
            
            request.state.user = UserState(
                user_id=payload.get("user_id"),
                username=payload.get("username")
            )
        
        except JWTError:
            # Invalid token - continue without user (rate limit by IP)
            pass
    
    response = await call_next(request)
    return response