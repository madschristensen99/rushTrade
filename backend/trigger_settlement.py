#!/usr/bin/env python3
"""
Manually trigger settlement of pending fills.
Useful for testing without running Celery.
"""
import asyncio
from app.tasks.settlement_tasks import _settle_fills_async

async def main():
    print("🔄 Triggering settlement of pending fills...")
    await _settle_fills_async()
    print("✅ Settlement complete!")

if __name__ == "__main__":
    asyncio.run(main())
