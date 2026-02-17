#!/usr/bin/env python3
"""Create a test market for development"""
import asyncio
from app.database.connection import init_db
from app.dependencies import get_db
from app.modules.terminal.clob.models import Market, MarketStatus
from datetime import datetime, timezone, timedelta

async def main():
    await init_db()
    
    # Import after init
    from app.database.connection import AsyncSessionLocal
    
    async with AsyncSessionLocal() as db:
        # Create a simple test market
        resolution_time = int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp())
        
        market = Market(
            condition_id="0x" + "2" * 64,  # Test condition ID
            question_id="0x" + "3" * 64,
            oracle_address="0xda932ff69169319cfc285c3bd42dc63b018994df",  # Your address as oracle
            collateral_token="0x534b2f3a21130d7a60830c2df862319e593943a3",  # USDC
            yes_token_id="1",  # YES token
            no_token_id="2",   # NO token
            title="Test Market: Will BTC be above $100k?",
            description="A test prediction market for development",
            category="Crypto",
            resolution_time=resolution_time,
            status=MarketStatus.ACTIVE,
            strike_price=100000.0
        )
        
        db.add(market)
        await db.commit()
        await db.refresh(market)
        
        print(f"✅ Created test market!")
        print(f"   ID: {market.id}")
        print(f"   Condition ID: {market.condition_id}")
        print(f"   Title: {market.title}")
        print(f"   YES Token ID: {market.yes_token_id}")
        print(f"   NO Token ID: {market.no_token_id}")
        print(f"\n📍 Use this condition ID in your frontend orders!")

if __name__ == "__main__":
    asyncio.run(main())
