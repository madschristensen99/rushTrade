from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    AsyncEngine,
    create_async_engine,
    async_sessionmaker
)

from app.config import get_settings


settings = get_settings()

# Global engine and session maker
engine: AsyncEngine | None = None
AsyncSessionLocal: async_sessionmaker[AsyncSession] | None = None


async def init_db() -> None:
    """Initialize database engine and session maker."""
    global engine, AsyncSessionLocal
    
    db_url = settings.database_url
    if "postgresql" in db_url and "postgresql+asyncpg" not in db_url:
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://")
    
    # SQLite doesn't support pool settings
    engine_kwargs = {"echo": settings.DEBUG}
    if "sqlite" not in db_url:
        engine_kwargs.update({
            "pool_pre_ping": True,
            "pool_size": 10,
            "max_overflow": 20,
        })
    
    engine = create_async_engine(db_url, **engine_kwargs)
    
    AsyncSessionLocal = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )


async def close_db() -> None:
    """Close database engine."""
    global engine
    if engine:
        await engine.dispose()


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Get database session."""
    if AsyncSessionLocal is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise