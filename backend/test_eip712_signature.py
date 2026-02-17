#!/usr/bin/env python3
"""
Test EIP-712 signature generation to match what the contract expects
"""
from eth_account import Account
from eth_account.messages import encode_typed_data
from web3 import Web3

# Test data
CHAIN_ID = 10143
EXCHANGE_ADDRESS = "0x5121fe4e7ba3130c56ea3e9e0c67c1b8eacccaa1"
bot_key = "0x" + "42" * 32
bot_account = Account.from_key(bot_key)

print(f"Bot Address: {bot_account.address}")
print()

# Create a test order
order = {
    "maker": bot_account.address,
    "tokenId": 1,
    "makerAmount": 1000000,
    "takerAmount": 1000000,
    "expiration": 1771370025,
    "nonce": 123456,
    "feeRateBps": 50,
    "side": 1,  # SELL
    "signer": "0x0000000000000000000000000000000000000000"
}

# EIP-712 domain and types (matching frontend and contract)
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

# Create full message
full_message = {
    "types": types,
    "primaryType": "Order",
    "domain": domain,
    "message": order
}

# Sign it
signable_message = encode_typed_data(full_message=full_message)
signed = bot_account.sign_message(signable_message)
signature = "0x" + signed.signature.hex()

print(f"Signature: {signature}")
print()

# Now verify we can recover the signer
from eth_account.messages import encode_defunct, _hash_eip191_message
from eth_utils import keccak

# The message hash that was signed
message_hash = signable_message.body
print(f"Message Hash (what was signed): {message_hash.hex()}")

# Recover the signer
w3 = Web3()
recovered = w3.eth.account.recover_message(signable_message, signature=signature)
print(f"Recovered Address: {recovered}")
print(f"Expected Address:  {bot_account.address}")
print(f"Match: {recovered.lower() == bot_account.address.lower()}")
