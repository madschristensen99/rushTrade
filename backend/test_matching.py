#!/usr/bin/env python3
"""
Test order matching and settlement.
Creates a BUY and SELL order that should match.
"""
import asyncio
import httpx
import time
import random
from eth_account import Account
from eth_account.messages import encode_typed_data

API_BASE = "http://localhost:8000/api/v1/terminal/clob"
CHAIN_ID = 10143
EXCHANGE_ADDRESS = "0x5121fe4e7ba3130c56ea3e9e0c67c1b8eacccaa1"
CONDITION_ID = "0x2222222222222222222222222222222222222222222222222222222222222222"

# Create two test accounts
buyer_account = Account.create()
seller_account = Account.create()

print(f"🔵 Buyer: {buyer_account.address}")
print(f"🔴 Seller: {seller_account.address}")
print("\n⚠️  In production, these addresses would need USDC and approval")


def sign_order(account, order_dict):
    """Sign an order with EIP-712"""
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
    
    signable_message = encode_typed_data(domain, types, message)
    signed = account.sign_message(signable_message)
    return signed.signature.hex()


async def create_order(account, side, amount=1.0):
    """Create and submit an order"""
    order = {
        "maker": account.address,
        "tokenId": 1,  # YES token
        "makerAmount": int(amount * 1e6),
        "takerAmount": int(amount * 1e6),
        "expiration": int(time.time()) + 3600,
        "nonce": random.randint(1, 1000000),
        "feeRateBps": 50,
        "side": 0 if side == "buy" else 1,  # BUY=0, SELL=1
        "signer": "0x0000000000000000000000000000000000000000"
    }
    
    signature = sign_order(account, order)
    
    payload = {
        "condition_id": CONDITION_ID,
        "token_id": "1",
        "side": side,
        "maker_address": account.address,
        "maker_amount": str(order["makerAmount"]),
        "taker_amount": str(order["takerAmount"]),
        "expiration": order["expiration"],
        "nonce": order["nonce"],
        "fee_rate_bps": order["feeRateBps"],
        "signer": order["signer"],
        "signature": signature
    }
    
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{API_BASE}/orders", json=payload)
        if resp.status_code == 201:
            result = resp.json()
            print(f"✅ Created {side.upper()} order #{result['id']} - Status: {result['status']}")
            return result
        else:
            print(f"❌ Failed: {resp.status_code} - {resp.text}")
            return None


async def check_fills():
    """Check if any fills were created"""
    async with httpx.AsyncClient() as client:
        # This endpoint might not exist yet, but we can check the database
        print("\n📊 Checking database for fills...")
        print("   Run: sqlite3 rushtrade.db 'SELECT * FROM fills;'")


async def main():
    print("\n🧪 Testing Order Matching\n" + "=" * 50)
    
    # Step 1: Create a SELL order first
    print("\n1️⃣  Creating SELL order...")
    sell_order = await create_order(seller_account, "sell", 1.0)
    
    await asyncio.sleep(1)
    
    # Step 2: Create a BUY order (should match!)
    print("\n2️⃣  Creating BUY order (should match!)...")
    buy_order = await create_order(buyer_account, "buy", 1.0)
    
    await asyncio.sleep(1)
    
    # Step 3: Check for fills
    await check_fills()
    
    print("\n✅ Test complete!")
    print("\nTo verify:")
    print("  1. Check backend logs for 'Created X fills'")
    print("  2. Run: sqlite3 rushtrade.db 'SELECT * FROM fills;'")
    print("  3. Run: python3 trigger_settlement.py")


if __name__ == "__main__":
    asyncio.run(main())
