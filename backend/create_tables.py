#!/usr/bin/env python3
"""Create database tables for development."""
import asyncio
from app.database.connection import init_db, engine
from app.database.base import Base
from app.modules.user.models import User
try:
    from app.modules.terminal.clob.models import Market, Order, Fill, BtcMarketRound
except ImportError:
    print("⚠️  CLOB models not found, creating user tables only")

async def main():
    await init_db()
    # Import engine after init_db sets it
    from app.database.connection import engine as db_engine
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("✅ Database tables created successfully!")
    await db_engine.dispose()

if __name__ == "__main__":
    asyncio.run(main())
