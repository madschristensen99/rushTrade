#!/usr/bin/env python3
"""Create superuser script"""
import asyncio
from app.database.connection import init_db, get_db_session
from app.modules.user.models import User
from app.modules.user.service import UserService


async def create_superuser():
    """Create superuser account"""
    await init_db()
    
    async for db in get_db_session():
        service = UserService(db)
        
        username = input("Username: ")
        email = input("Email: ")
        password = input("Password: ")
        
        # Create superuser
        user = User(
            username=username,
            email=email,
            hashed_password=service.hash_password(password),
            is_superuser=True,
            is_active=True,
        )
        
        db.add(user)
        await db.commit()
        
        print(f"✅ Superuser '{username}' created successfully!")
        break


if __name__ == "__main__":
    asyncio.run(create_superuser())
