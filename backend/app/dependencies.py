from typing import AsyncGenerator
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import select
import json

from app.config import get_settings
from app.database.connection import get_db_session
from app.services.redis_service import get_redis_client
from app.modules.user.models import User
from app.modules.user.auth import decode_access_token

settings = get_settings()
security = HTTPBearer()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async for session in get_db_session():
        yield session


async def get_redis() -> AsyncGenerator[Redis, None]:
    redis = await get_redis_client()
    try:
        yield redis
    finally:
        pass


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis)
) -> User:
    token = credentials.credentials
    
    # Check if token is blacklisted (for logout)
    is_blacklisted = await redis.get(f"blacklist:{token}")
    if is_blacklisted:
        raise HTTPException(status_code=401, detail="Token has been revoked")
    print(f"Token: {token[:20]}...")  
    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid token")
    print(f"Payload: {payload}")
    user_id: int = payload.get("user_id")
    if user_id is None:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    # Cache user in Redis for 5 minutes
    cached_user = await redis.get(f"user:{user_id}")
    if cached_user:
        user_data = json.loads(cached_user)
        user = User(**user_data)
    else:
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        
        if user is None:
            raise HTTPException(status_code=401, detail="User not found")
        
        # Cache for 5 minutes
        await redis.setex(
            f"user:{user_id}",
            300,
            json.dumps({
                "id": user.id,
                "username": user.username,
                "email": user.email,
                "is_active": user.is_active,
                "is_superuser": user.is_superuser
            })
        )
    
    return user


__all__ = ['get_db', 'get_redis', 'get_current_user']