from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.modules.user.service import UserService
from app.modules.user.schema import (
    UserCreate,
    UserLogin,
    UserResponse,
    Token,
)
from app.middleware.rate_limiter import endpoint_rate_limit

router = APIRouter()


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(
    data: UserCreate,
    db: AsyncSession = Depends(get_db),
    _rate_limit: bool = Depends(endpoint_rate_limit(3, 300))
):
    """Register new user - Rate limit: 3/5min"""
    service = UserService(db)
    user = await service.create_user(data)
    return user


@router.post("/login", response_model=Token)
async def login(
    data: UserLogin,
    db: AsyncSession = Depends(get_db),
    _rate_limit: bool = Depends(endpoint_rate_limit(5, 60))
):
    """Login user - Rate limit: 5/min (prevents brute force)"""
    service = UserService(db)
    user = await service.authenticate(data.username, data.password)
    
    access_token = service.create_access_token(user.id, user.username)
    
    return Token(access_token=access_token)