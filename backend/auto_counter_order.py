#!/usr/bin/env python3
"""
Auto Counter-Order Bot
Automatically creates matching counter-orders when users place orders.
This makes the system work immediately for demo purposes.
"""
import asyncio
import time
import random
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.connection import init_db, get_db_session
from app.modules.terminal.clob.models import Order, OrderStatus, OrderSide
from app.modules.terminal.clob.service import submit_order
from app.modules.terminal.clob.schema import OrderCreate
from app.modules.user.models import User
from eth_account import Account
from eth_account.messages import encode_typed_data
import os
from dotenv import load_dotenv

load_dotenv()

# Bot configuration
CHAIN_ID = 10143
EXCHANGE_ADDRESS = "0x5121fe4e7ba3130c56ea3e9e0c67c1b8eacccaa1"

# Bot uses its own deterministic wallet
BOT_PRIVATE_KEY = "0x" + "42" * 32
bot_account = Account.from_key(BOT_PRIVATE_KEY)
BOT_ADDRESS = bot_account.address

print(f"🤖 Auto Counter-Order Bot")
print(f"   Address: {BOT_ADDRESS}")
print(f"   Watching for new orders...")


def sign_order(order_dict):
    """Sign an order with EIP-712 matching CTFExchange domain exactly"""
    # Use the exact same structure as the frontend and smart contract
    domain = {
        "name": "CTFExchange",
        "version": "1",
        "chainId": CHAIN_ID,
        "verifyingContract": EXCHANGE_ADDRESS
    }
    
    types = {
        "Order": [
            {"name": "maker", "type": "address"},
            {"name": "tokenId", "type": "uint256"},
            {"name": "makerAmount", "type": "uint256"},
            {"name": "takerAmount", "type": "uint256"},
            {"name": "expiration", "type": "uint256"},
            {"name": "nonce", "type": "uint256"},
            {"name": "feeRateBps", "type": "uint256"},
            {"name": "side", "type": "uint8"},
            {"name": "signer", "type": "address"}
        ]
    }
    
    message = {
        "maker": order_dict["maker"],
        "tokenId": order_dict["tokenId"],
        "makerAmount": order_dict["makerAmount"],
        "takerAmount": order_dict["takerAmount"],
        "expiration": order_dict["expiration"],
        "nonce": order_dict["nonce"],
        "feeRateBps": order_dict["feeRateBps"],
        "side": order_dict["side"],
        "signer": order_dict["signer"]
    }
    
    # Create full EIP-712 message
    full_message = {
        "types": types,
        "primaryType": "Order",
        "domain": domain,
        "message": message
    }
    
    signable_message = encode_typed_data(full_message=full_message)
    signed = bot_account.sign_message(signable_message)
    return "0x" + signed.signature.hex()


async def create_counter_order(db: AsyncSession, user_order: Order):
    """Create a counter-order to match the user's order"""
    
    # Determine counter side
    counter_side = "sell" if user_order.side == OrderSide.BUY else "buy"
    
    print(f"\n💡 Creating {counter_side.upper()} order to match Order #{user_order.id}")
    
    # Build counter order
    order_dict = {
        "maker": BOT_ADDRESS,
        "tokenId": int(user_order.token_id),
        "makerAmount": int(user_order.maker_amount),
        "takerAmount": int(user_order.taker_amount),
        "expiration": int(time.time()) + 3600,
        "nonce": random.randint(1, 1000000),
        "feeRateBps": 50,
        "side": 1 if counter_side == "sell" else 0,  # SELL=1, BUY=0
        "signer": "0x0000000000000000000000000000000000000000"
    }
    
    # Sign it
    signature = sign_order(order_dict)
    
    # Create OrderCreate payload
    payload = OrderCreate(
        condition_id=user_order.condition_id,
        token_id=str(user_order.token_id),
        side=counter_side,
        maker_address=BOT_ADDRESS,
        maker_amount=str(order_dict["makerAmount"]),
        taker_amount=str(order_dict["takerAmount"]),
        expiration=order_dict["expiration"],
        nonce=order_dict["nonce"],
        fee_rate_bps=order_dict["feeRateBps"],
        signer=order_dict["signer"],
        signature=signature
    )
    
    # Get or create bot user
    result = await db.execute(select(User).where(User.username == "bot_user"))
    bot_user = result.scalar_one_or_none()
    
    if not bot_user:
        bot_user = User(
            username="bot_user",
            email="bot@rushtrade.com",
            hashed_password="bot",
            is_active=True,
            is_superuser=False
        )
        db.add(bot_user)
        await db.flush()
    
    # Submit the counter order (this will trigger matching!)
    try:
        result = await submit_order(db, payload, bot_user)
        print(f"✅ Counter order created: Order #{result.id}")
        print(f"   Status: {result.status}")
        if result.status == "filled":
            print(f"   🎉 MATCHED AND FILLED!")
        return result
    except Exception as e:
        print(f"❌ Failed to create counter order: {e}")
        return None


async def watch_for_orders():
    """Watch for new user orders and create counter-orders"""
    await init_db()
    
    last_order_id = 0
    
    while True:
        try:
            async for db in get_db_session():
                # Find new OPEN orders
                result = await db.execute(
                    select(Order)
                    .where(Order.id > last_order_id)
                    .where(Order.status == OrderStatus.OPEN)
                    .where(Order.maker_address != BOT_ADDRESS)  # Don't match our own orders
                    .order_by(Order.id)
                )
                new_orders = result.scalars().all()
                
                for order in new_orders:
                    print(f"\n🔔 New order detected: #{order.id} - {order.side.value.upper()} {order.maker_amount} @ {order.condition_id[:10]}...")
                    
                    # Create counter order
                    await create_counter_order(db, order)
                    
                    last_order_id = order.id
                
                await db.commit()
        
        except Exception as e:
            print(f"❌ Error: {e}")
        
        await asyncio.sleep(2)  # Check every 2 seconds


if __name__ == "__main__":
    print("\n" + "="*60)
    print("Starting Auto Counter-Order Bot...")
    print("This will automatically match any user orders!")
    print("="*60 + "\n")
    
    asyncio.run(watch_for_orders())
