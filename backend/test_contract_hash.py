#!/usr/bin/env python3
"""
Call the contract's getOrderHash function to see what hash it computes
"""
import asyncio
from app.services.chain_service import chain
from app.database.connection import init_db, get_db_session
from app.modules.terminal.clob.models import Order

async def main():
    await init_db()
    
    async for db in get_db_session():
        order = await db.get(Order, 12)  # Bot order
        
        # Build the order tuple exactly as we send it to the contract
        from web3 import Web3
        w3 = Web3()
        order_tuple = (
            w3.to_checksum_address(order.maker_address),
            int(order.token_id),
            int(order.maker_amount),
            int(order.taker_amount),
            order.expiration,
            order.nonce,
            order.fee_rate_bps,
            1,  # SELL
            w3.to_checksum_address(order.signer)
        )
        
        print(f"Order tuple: {order_tuple}")
        print()
        
        # Call the contract's getOrderHash function
        order_hash = await chain.exchange.functions.getOrderHash(order_tuple).call()
        
        print(f"Contract computed hash: {order_hash.hex()}")
        print(f"Stored signature:       {order.signature[:66]}")
        print()
        
        # Now try to recover the signer from the signature using the contract's hash
        from eth_account.messages import encode_defunct
        
        # The contract uses ECDSA.recover which expects the hash directly
        from eth_account import Account
        recovered = Account._recover_hash(order_hash, signature=order.signature)
        
        print(f"Recovered from contract hash: {recovered}")
        print(f"Expected (maker):             {order.maker_address}")
        print(f"Match: {recovered.lower() == order.maker_address.lower()}")
        
        break

asyncio.run(main())
