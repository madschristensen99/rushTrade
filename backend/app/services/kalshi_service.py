"""Simplified Kalshi API wrapper for trading tasks - uses per-user credentials"""
import httpx
from typing import Dict, Any, Optional
from datetime import datetime
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.backends import default_backend
import base64


class KalshiTradingService:
    """
    Per-user Kalshi API wrapper for trading tasks.
    Unlike KalshiService (which uses env vars), this accepts credentials per instance.
    """
    
    def __init__(self, api_key: str, private_key_content: str):
        """
        Initialize with user-specific credentials.
        
        Args:
            api_key: Kalshi API key
            private_key_content: Raw PEM private key content (string)
        """
        self.api_key = api_key
        self.base_url = "https://api.elections.kalshi.com/trade-api/v2"
        self.private_key = self._load_private_key(private_key_content)
        self.client = httpx.AsyncClient(timeout=10.0)
    
    def _load_private_key(self, key_content: str):
        """Load private key from string content"""
        return serialization.load_pem_private_key(
            key_content.encode(),
            password=None,
            backend=default_backend()
        )
    
    def _sign_request(self, method: str, path: str, body: str = "") -> str:
        """Sign API request with private key"""
        timestamp = str(int(datetime.now().timestamp() * 1000))
        message = f"{timestamp}{method}{path}{body}"
        
        signature = self.private_key.sign(
            message.encode(),
            padding.PKCS1v15(),
            hashes.SHA256()
        )
        
        return base64.b64encode(signature).decode()
    
    async def _request(self, method: str, endpoint: str, **kwargs) -> dict:
        """Make authenticated API request"""
        path = f"/trade-api/v2{endpoint}"
        url = f"{self.base_url}{endpoint}"
        
        body = ""
        if kwargs.get('json'):
            import json
            body = json.dumps(kwargs['json'])
        
        signature = self._sign_request(method, path, body)
        timestamp = str(int(datetime.now().timestamp() * 1000))
        
        headers = {
            "KALSHI-ACCESS-KEY": self.api_key,
            "KALSHI-ACCESS-SIGNATURE": signature,
            "KALSHI-ACCESS-TIMESTAMP": timestamp,
            "Content-Type": "application/json",
        }
        
        try:
            response = await self.client.request(method, url, headers=headers, **kwargs)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as e:
            print(f"Kalshi API error: {str(e)}")
            return {}
    
    async def get_orderbook(self, market_ticker: str) -> Dict[str, Any]:
        """
        Get orderbook formatted for frontend.
        
        Returns:
            {
                "volume": int,
                "last_traded": int (cents),
                "bids": [{"price": int, "size": int}, ...],
                "asks": [{"price": int, "size": int}, ...]
            }
        """
        data = await self._request("GET", f"/markets/{market_ticker}/orderbook", params={"depth": 10})
        
        orderbook = data.get("orderbook", {})
        
        # Convert from Kalshi format [[price_cents, size], ...] to [{price, size}, ...]
        yes_offers = orderbook.get("yes", [])
        no_offers = orderbook.get("no", [])
        
        # YES offers = bids, NO offers = asks (from market perspective)
        bids = [{"price": p, "size": s} for p, s in sorted(yes_offers, key=lambda x: x[0], reverse=True)]
        asks = [{"price": p, "size": s} for p, s in sorted(no_offers, key=lambda x: x[0], reverse=True)]
        
        # Get last traded from market data
        market_data = await self._request("GET", f"/markets/{market_ticker}")
        last_traded = market_data.get("market", {}).get("last_price", 0)
        volume = market_data.get("market", {}).get("volume", 0)
        
        return {
            "volume": volume,
            "last_traded": last_traded,
            "bids": bids,
            "asks": asks
        }
    
    async def get_positions(self, market_ticker: str) -> Dict[str, int]:
        """
        Get positions for a market.
        
        Returns:
            {"yes": position_count, "no": position_count}
        """
        data = await self._request("GET", "/portfolio/positions", params={"ticker": market_ticker, "count_filter": "position"})
        
        positions = {"yes": 0, "no": 0}
        
        for pos in data.get("market_positions", []):
            if pos.get("ticker") == market_ticker:
                position_val = pos.get("position", 0)
                if position_val > 0:
                    positions["yes"] = position_val
                elif position_val < 0:
                    positions["no"] = abs(position_val)
        
        return positions
    
    async def get_resting_orders(self, market_ticker: str) -> Dict[str, Optional[Dict]]:
        """
        Get resting orders for a market.
        
        Returns:
            {
                "yes": {"price_level": int, "quantity": int} or None,
                "no": {"price_level": int, "quantity": int} or None
            }
        """
        data = await self._request("GET", "/portfolio/orders", params={"ticker": market_ticker, "status": "resting"})
        
        orders = {"yes": None, "no": None}
        
        for order in data.get("orders", []):
            if order.get("ticker") == market_ticker:
                side = order.get("side")
                remaining = order.get("remaining_count", order.get("count", 0))
                
                if side == "yes":
                    price = order.get("yes_price", 0)
                    orders["yes"] = {"price_level": price, "quantity": remaining}
                elif side == "no":
                    price = order.get("no_price", 0)
                    orders["no"] = {"price_level": price, "quantity": remaining}
        
        return orders
    
    async def close(self):
        """Close HTTP client"""
        await self.client.aclose()