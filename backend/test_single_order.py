#!/usr/bin/env python3
"""
Test calling fillOrders with just the user's order to isolate the signature issue
"""
import asyncio
from app.database.connection import init_db, get_db_session
from app.modules.terminal.clob.models import Order
from app.services.chain_service import chain
from web3 import Web3

async def main():
    await init_db()
    
    async for db in get_db_session():
        # Get user order #13
        order = await db.get(Order, 13)
        
        w3 = Web3()
        order_tuple = (
            w3.to_checksum_address(order.maker_address),
            int(order.token_id),
            int(order.maker_amount),
            int(order.taker_amount),
            order.expiration,
            order.nonce,
            order.fee_rate_bps,
            0,  # BUY
            w3.to_checksum_address(order.signer)
        )
        
        print(f"Testing single order (user order #13):")
        print(f"  Maker: {order.maker_address}")
        print(f"  Side: BUY")
        print(f"  Signature: {order.signature[:20]}...")
        print()
        
        # Try to call fillOrders with just this one order
        try:
            # Build the transaction
            # For BUY orders, fill amount is in tokens (takerAmount), not collateral
            fill_amount = int(order.taker_amount) if order.side.value == "buy" else int(order.maker_amount)
            print(f"  Fill amount: {fill_amount}")
            print()
            
            tx = await chain.exchange.functions.fillOrders(
                [order_tuple],
                [fill_amount],
                [bytes.fromhex(order.signature.lstrip("0x"))]
            ).build_transaction({
                "from": chain._operator.address,
                "nonce": await chain.w3.eth.get_transaction_count(chain._operator.address),
                "gasPrice": await chain.w3.eth.gas_price,
                "chainId": 10143,
            })
            
            print("✅ Transaction built successfully!")
            print("   This means the signature is valid!")
            
            # Try to estimate gas
            gas = await chain.w3.eth.estimate_gas(tx)
            print(f"✅ Gas estimated: {gas}")
            print("   The transaction would succeed!")
            
        except Exception as e:
            print(f"❌ Error: {e}")
            if "invalid signature" in str(e).lower():
                print("\n   The contract is rejecting the signature.")
                print("   Even though we verified it's valid off-chain!")
        
        break

asyncio.run(main())
