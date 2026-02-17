# app/modules/user/schema.py

from pydantic import BaseModel, EmailStr, Field, validator
from datetime import datetime
import re


def sanitize_string(value: str) -> str:
    """Sanitize user input to prevent XSS and injection attacks"""
    if not value:
        return value
    
    # Check for dangerous patterns
    dangerous = ['<script', 'javascript:', 'onerror=', 'onclick=', 'onload=',
                '--', ';', 'drop ', 'delete ', 'insert ', 'update ', 'union ', 'select ']
    value_lower = value.lower()
    
    for pattern in dangerous:
        if pattern in value_lower:
            raise ValueError('Invalid characters or patterns detected')
    
    return value.strip()


class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    email: EmailStr
    password: str = Field(..., min_length=8)
    
    @validator('username')
    def validate_username(cls, v):
        """Validate username format"""
        # Only alphanumeric and underscore allowed
        if not re.match(r'^[a-zA-Z0-9_]+$', v):
            raise ValueError('Username must contain only letters, numbers, and underscores')
        
        # Sanitize
        return sanitize_string(v)
    
    @validator('password')
    def validate_password(cls, v):
        """Enforce password complexity requirements"""
        if len(v) < 8:
            raise ValueError('Password must be at least 8 characters')
        
        if not any(c.isupper() for c in v):
            raise ValueError('Password must contain at least one uppercase letter')
        
        if not any(c.islower() for c in v):
            raise ValueError('Password must contain at least one lowercase letter')
        
        if not any(c.isdigit() for c in v):
            raise ValueError('Password must contain at least one digit')
        
        # Optional: special character requirement
        if not any(c in '!@#$%^&*()_+-=[]{}|;:,.<>?' for c in v):
            raise ValueError('Password must contain at least one special character')
        
        return v


class UserLogin(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    password: str
    
    @validator('username')
    def sanitize_username(cls, v):
        return sanitize_string(v)


class UserResponse(BaseModel):
    id: int
    username: str
    email: str
    is_active: bool
    is_superuser: bool
    created_at: datetime
    
    class Config:
        from_attributes = True


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class TokenData(BaseModel):
    user_id: int
    username: str