#!/usr/bin/env python3
"""
Simple Market Maker for RushTrade CLOB
Creates counter-orders to provide liquidity for user bets
"""
import asyncio
import httpx
from eth_account import Account
from eth_account.messages import encode_typed_data

# Configuration
API_BASE = "http://localhost:8000/api/v1/terminal/clob"
CHAIN_ID = 10143
EXCHANGE_ADDRESS = "0x5121fe4e7ba3130c56ea3e9e0c67c1b8eacccaa1"

# Market maker wallet (create a new one for testing)
MM_PRIVATE_KEY = "0x0000000000000000000000000000000000000000000000000000000000000001"  # CHANGE THIS
mm_account = Account.from_key(MM_PRIVATE_KEY)
MM_ADDRESS = mm_account.address

print(f"🤖 Market Maker Address: {MM_ADDRESS}")
print(f"⚠️  Fund this address with USDC and approve CTFExchange")


def sign_order(order_dict):
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
    signed = mm_account.sign_message(signable_message)
    return signed.signature.hex()


async def get_open_orders():
    """Fetch open orders from the orderbook"""
    async with httpx.AsyncClient() as client:
        # Get markets
        resp = await client.get(f"{API_BASE}/markets")
        markets = resp.json().get("markets", [])
        
        if not markets:
            print("No markets found")
            return []
        
        # Get orderbook for first market
        market = markets[0]
        condition_id = market["condition_id"]
        
        resp = await client.get(f"{API_BASE}/markets/{condition_id}/orderbook")
        orderbook = resp.json()
        
        print(f"\n📊 Market: {market['title']}")
        print(f"   YES bids: {len(orderbook['yes']['bids'])}, asks: {len(orderbook['yes']['asks'])}")
        print(f"   NO bids: {len(orderbook['no']['bids'])}, asks: {len(orderbook['no']['asks'])}")
        
        return orderbook, condition_id


async def create_counter_order(condition_id, token_id, side, amount):
    """Create a counter-order to match user orders"""
    import time
    import random
    
    # Create order structure
    order = {
        "maker": MM_ADDRESS,
        "tokenId": int(token_id),
        "makerAmount": int(amount * 1e6),  # USDC or tokens
        "takerAmount": int(amount * 1e6),  # USDC or tokens
        "expiration": int(time.time()) + 3600,  # 1 hour
        "nonce": random.randint(1, 1000000),
        "feeRateBps": 50,  # 0.5%
        "side": 1 if side == "sell" else 0,  # SELL=1, BUY=0
        "signer": "0x0000000000000000000000000000000000000000"
    }
    
    # Sign it
    signature = sign_order(order)
    
    # Submit to backend
    payload = {
        "condition_id": condition_id,
        "token_id": str(token_id),
        "side": side,
        "maker_address": MM_ADDRESS,
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
            print(f"✅ Created {side.upper()} order #{result['id']}")
            return result
        else:
            print(f"❌ Failed to create order: {resp.text}")
            return None


async def main():
    """Main market maker loop"""
    print("\n🤖 RushTrade Simple Market Maker")
    print("=" * 50)
    
    while True:
        try:
            orderbook, condition_id = await get_open_orders()
            
            # Simple strategy: if there are YES bids but no YES asks, create a SELL order
            if orderbook["yes"]["bids"] and not orderbook["yes"]["asks"]:
                print("\n💡 Creating SELL order to match YES buyers...")
                await create_counter_order(condition_id, 1, "sell", 1.0)
            
            # If there are NO bids but no NO asks, create a SELL order
            if orderbook["no"]["bids"] and not orderbook["no"]["asks"]:
                print("\n💡 Creating SELL order to match NO buyers...")
                await create_counter_order(condition_id, 2, "sell", 1.0)
            
        except Exception as e:
            print(f"❌ Error: {e}")
        
        await asyncio.sleep(5)  # Check every 5 seconds


if __name__ == "__main__":
    print("\n⚠️  WARNING: Update MM_PRIVATE_KEY before running!")
    print("⚠️  Fund the MM address with USDC and approve CTFExchange\n")
    # asyncio.run(main())  # Uncomment to run
