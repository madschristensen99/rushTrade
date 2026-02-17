from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import bcrypt
from jose import jwt
from datetime import datetime, timedelta
from typing import Optional

from app.modules.user.models import User
from app.modules.user.schema import UserCreate
from app.config import get_settings
from app.core.exceptions import UnauthorizedError, BadRequestError

settings = get_settings()


class UserService:
    """User authentication service"""
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    def hash_password(self, password: str) -> str:
        """Hash password"""
        return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    
    def verify_password(self, plain_password: str, hashed_password: str) -> bool:
        """Verify password"""
        return bcrypt.checkpw(plain_password.encode(), hashed_password.encode())
    
    def create_access_token(self, user_id: int, username: str) -> str:
        """Create JWT token"""
        expires = datetime.utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
        to_encode = {
            "user_id": user_id,
            "username": username,
            "exp": expires
        }
        return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    
    async def create_user(self, data: UserCreate) -> User:
        """Create new user"""
        # Check if username exists
        result = await self.db.execute(
            select(User).where(User.username == data.username)
        )
        if result.scalar_one_or_none():
            raise BadRequestError("Username already exists")
        
        # Check if email exists
        result = await self.db.execute(
            select(User).where(User.email == data.email)
        )
        if result.scalar_one_or_none():
            raise BadRequestError("Email already exists")
        
        # Create user
        user = User(
            username=data.username,
            email=data.email,
            hashed_password=self.hash_password(data.password),
        )
        
        self.db.add(user)
        await self.db.commit()
        await self.db.refresh(user)
        
        return user
    
    async def authenticate(self, username: str, password: str) -> User:
        """Authenticate user"""
        result = await self.db.execute(
            select(User).where(User.username == username)
        )
        user = result.scalar_one_or_none()
        
        if not user or not self.verify_password(password, user.hashed_password):
            raise UnauthorizedError("Invalid credentials")
        
        if not user.is_active:
            raise UnauthorizedError("User account is inactive")
        
        return user
    
    async def get_user_by_id(self, user_id: int) -> Optional[User]:
        """Get user by ID"""
        result = await self.db.execute(
            select(User).where(User.id == user_id)
        )
        return result.scalar_one_or_none()