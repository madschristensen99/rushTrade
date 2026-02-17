#!/usr/bin/env python3
"""
Verify that the bot's signature matches what the contract expects
"""
import asyncio
from sqlalchemy import select
from app.database.connection import init_db, get_db_session
from app.modules.terminal.clob.models import Order
from app.services.chain_service import chain
from eth_account import Account
from eth_account.messages import encode_typed_data

async def main():
    await init_db()
    
    async for db in get_db_session():
        # Get bot order #12
        order = await db.get(Order, 12)
        
        if not order:
            print("Order 12 not found")
            return
        
        print(f"\n🤖 Verifying Bot Order #{order.id}")
        print(f"   Maker: {order.maker_address}")
        print(f"   Signature: {order.signature[:20]}...")
        print()
        
        # Recreate the EIP-712 message exactly as the bot signed it
        domain = {
            "name": "CTFExchange",
            "version": "1",
            "chainId": 10143,
            "verifyingContract": "0x5121fe4e7ba3130c56ea3e9e0c67c1b8eacccaa1"
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
            "maker": order.maker_address,
            "tokenId": int(order.token_id),
            "makerAmount": int(order.maker_amount),
            "takerAmount": int(order.taker_amount),
            "expiration": order.expiration,
            "nonce": order.nonce,
            "feeRateBps": order.fee_rate_bps,
            "side": 1,  # SELL
            "signer": order.signer
        }
        
        full_message = {
            "types": types,
            "primaryType": "Order",
            "domain": domain,
            "message": message
        }
        
        # Encode and recover
        signable_message = encode_typed_data(full_message=full_message)
        
        from web3 import Web3
        w3 = Web3()
        recovered = w3.eth.account.recover_message(signable_message, signature=order.signature)
        
        print(f"Recovered Address: {recovered}")
        print(f"Expected Address:  {order.maker_address}")
        print(f"Match: {recovered.lower() == order.maker_address.lower()}")
        print()
        
        if recovered.lower() != order.maker_address.lower():
            print("❌ SIGNATURE DOES NOT MATCH!")
            print("   The bot's signature cannot be verified.")
            print("   This is why the contract rejects it.")
        else:
            print("✅ Signature is valid!")
            print("   The issue must be something else...")
        
        break

if __name__ == "__main__":
    asyncio.run(main())
