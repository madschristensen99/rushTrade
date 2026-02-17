#!/usr/bin/env python3
"""
Kalshi API communication layer - ASYNC
"""

import os
import time
import base64
import asyncio
from dataclasses import dataclass
from typing import Optional
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.backends import default_backend
import aiohttp


@dataclass
class Config:
    """Trading configuration"""
    api_base: str = "https://api.elections.kalshi.com/trade-api/v2"


@dataclass
class MarketState:
    """Current market state"""
    market_id: str
    yes_bid: Optional[float] = None
    yes_bid_size: int = 0
    yes_second_bid: Optional[float] = None
    no_bid: Optional[float] = None
    no_bid_size: int = 0
    no_second_bid: Optional[float] = None
    
    def update_orderbook(self, orderbook_data: dict):
        """Update bid info from orderbook data"""
        orderbook = orderbook_data.get("orderbook", {})
        
        yes_offers = orderbook.get("yes", [])
        if yes_offers:
            yes_sorted = sorted(yes_offers, key=lambda x: x[0], reverse=True)
            self.yes_bid = yes_sorted[0][0] / 100
            self.yes_bid_size = yes_sorted[0][1]
            self.yes_second_bid = yes_sorted[1][0] / 100 if len(yes_sorted) > 1 else None
        else:
            self.yes_bid = self.yes_bid_size = None
            self.yes_second_bid = None
        
        no_offers = orderbook.get("no", [])
        if no_offers:
            no_sorted = sorted(no_offers, key=lambda x: x[0], reverse=True)
            self.no_bid = no_sorted[0][0] / 100
            self.no_bid_size = no_sorted[0][1]
            self.no_second_bid = no_sorted[1][0] / 100 if len(no_sorted) > 1 else None
        else:
            self.no_bid = self.no_bid_size = None
            self.no_second_bid = None


class KalshiAPITrader:
    """Kalshi API interface - ASYNC"""
    
    def __init__(self, api_key: str, api_secret: str, config: Config):
        self.api_key = api_key
        self.config = config
        self.private_key = self._load_private_key(api_secret)
        self.session: Optional[aiohttp.ClientSession] = None
    
    def _load_private_key(self, api_secret: str):
        """Load RSA private key"""
        try:
            if os.path.isfile(api_secret):
                with open(api_secret, 'r') as f:
                    key_data = f.read()
            else:
                key_data = api_secret
            
            key = serialization.load_pem_private_key(
                key_data.encode() if isinstance(key_data, str) else key_data,
                password=None,
                backend=default_backend()
            )
            print("✓ Private key loaded successfully")
            return key
        except Exception as e:
            print(f"❌ Failed to load private key: {e}")
            raise
    
    def _sign_request(self, timestamp: str, method: str, path: str) -> str:
        """Sign request with RSA-PSS"""
        path_clean = path.split('?')[0]
        msg = timestamp + method + "/trade-api/v2" + path_clean
        sig = self.private_key.sign(
            msg.encode(),
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH
            ),
            hashes.SHA256()
        )
        return base64.b64encode(sig).decode()
    
    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create session"""
        if self.session is None or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=1)
            self.session = aiohttp.ClientSession(timeout=timeout)
        return self.session
    
    async def close_session(self):
        """Close aiohttp session"""
        if self.session and not self.session.closed:
            await self.session.close()
    
    async def _request(self, method: str, endpoint: str, data=None) -> dict:
        """Make authenticated API request - ASYNC"""
        url = f"{self.config.api_base}{endpoint}"
        timestamp = str(int(time.time() * 1000))
        headers = {
            'KALSHI-ACCESS-KEY': self.api_key,
            'KALSHI-ACCESS-SIGNATURE': self._sign_request(timestamp, method, endpoint),
            'KALSHI-ACCESS-TIMESTAMP': timestamp,
            'Content-Type': 'application/json'
        }
        
        try:
            session = await self._get_session()
            if method == "GET":
                async with session.get(url, headers=headers) as resp:
                    resp.raise_for_status()
                    return await resp.json()
            elif method == "POST":
                async with session.post(url, json=data, headers=headers) as resp:
                    resp.raise_for_status()
                    return await resp.json()
            elif method == "DELETE":
                async with session.delete(url, headers=headers) as resp:
                    resp.raise_for_status()
                    return await resp.json()
            else:
                return {}
        except Exception:
            return {}
    
    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an order - ASYNC"""
        if order_id:
            result = await self._request("DELETE", f"/portfolio/orders/{order_id}")
            return bool(result)
        return False
    
    async def place_order(self, market_id: str, side: str, price: float, count: int) -> Optional[str]:
        """Place an order - ASYNC"""
        order_data = {
            "ticker": market_id,
            "side": side,
            "action": "buy",
            "count": count,
            "type": "limit",
            "client_order_id": f"{market_id}-{side}-{int(time.time() * 1000)}",
            f"{side}_price": int(round(price * 100))
        }
        result = await self._request("POST", "/portfolio/orders", order_data)
        return result.get("order", {}).get("order_id") if result else None
    
    async def modify_order(self, market_id: str, side: str, new_price: float,
                          order_id: str, resting: int, position: int) -> Optional[str]:
        """Cancel and replace order - ASYNC"""
        if not order_id or resting == 0:
            return None
        
        pos_before = position
        await self.cancel_order(order_id)
        await asyncio.sleep(0.1)
        
        data = await self._request("GET", f"/portfolio/positions?ticker={market_id}&count_filter=position")
        pos_after = 0
        if data:
            for pos in data.get("market_positions", []):
                if pos.get("ticker") == market_id:
                    position_val = pos.get("position", 0)
                    if side == "yes" and position_val > 0:
                        pos_after = position_val
                    elif side == "no" and position_val < 0:
                        pos_after = abs(position_val)
                    break
        
        if pos_before is not None and pos_after > pos_before:
            return None
        
        return await self.place_order(market_id, side, new_price, resting)