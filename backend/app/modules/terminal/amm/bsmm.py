#!/usr/bin/env python3
"""
Black-Scholes Market Maker with Higher-First Mode
"""
import os
import sys
import time
import base64
import csv
import math
import asyncio
import aiohttp
from collections import deque
from typing import Optional, Dict
from datetime import datetime, timedelta
import numpy as np
from scipy.stats import norm
from scipy.optimize import brentq
import ccxt
from dotenv import load_dotenv
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.backends import default_backend
from bisect import bisect_right
from dataclasses import dataclass

load_dotenv()

@dataclass
class MarketState:
    """Current market state for both sides"""
    market_id: str
    yes_bid: Optional[float] = None
    yes_bid_size: int = 0
    yes_second_bid: Optional[float] = None
    no_bid: Optional[float] = None
    no_bid_size: int = 0
    no_second_bid: Optional[float] = None
    
    def update_orderbook(self, orderbook_data: dict):
        """Update all bid info from orderbook data"""
        orderbook = orderbook_data.get("orderbook", {})
        
        # YES side
        yes_offers = orderbook.get("yes", [])
        if yes_offers and len(yes_offers) > 0:
            yes_sorted = sorted(yes_offers, key=lambda x: x[0], reverse=True)
            self.yes_bid = yes_sorted[0][0] / 100
            self.yes_bid_size = yes_sorted[0][1]
            if len(yes_sorted) > 1:
                self.yes_second_bid = yes_sorted[1][0] / 100
            else:
                self.yes_second_bid = None
        else:
            self.yes_bid = None
            self.yes_bid_size = 0
            self.yes_second_bid = None
        
        # NO side
        no_offers = orderbook.get("no", [])
        if no_offers and len(no_offers) > 0:
            no_sorted = sorted(no_offers, key=lambda x: x[0], reverse=True)
            self.no_bid = no_sorted[0][0] / 100
            self.no_bid_size = no_sorted[0][1]
            if len(no_sorted) > 1:
                self.no_second_bid = no_sorted[1][0] / 100
            else:
                self.no_second_bid = None
        else:
            self.no_bid = None
            self.no_bid_size = 0
            self.no_second_bid = None

def bs_probability_above_strike(S_current: float, K: float, time_remaining_seconds: float, 
                                  sigma_annual: float) -> float:
    """Calculate Black-Scholes probability of ending above strike"""
    if time_remaining_seconds <= 0:
        return 1.0 if S_current > K else 0.0
    
    tau = time_remaining_seconds / 31536000
    d = (np.log(K/S_current) - (-0.5*sigma_annual**2)*tau) / (sigma_annual*np.sqrt(tau))
    
    return 1 - norm.cdf(d)

def bs_theta(S_current: float, K: float, time_remaining_seconds: float, 
             sigma_annual: float) -> float:
    """Calculate theta (time decay) for binary option in cents per second"""
    if time_remaining_seconds <= 0:
        return 0.0
    
    tau = time_remaining_seconds / 31536000
    d = (np.log(K/S_current) - (-0.5*sigma_annual**2)*tau) / (sigma_annual*np.sqrt(tau))
    
    # Binary option theta (per year)
    theta_annual = norm.pdf(d) * S_current * sigma_annual / (2 * np.sqrt(tau))
    
    # Convert to cents per second
    theta_per_second = (theta_annual / 31536000) * 100
    
    return theta_per_second

def extract_implied_vol(market_price: float, S: float, K: float, 
                       time_remaining: float, initial_guess: float = 0.2) -> Optional[float]:
    """Extract IV from market price using Brent's method"""
    if market_price <= 0.01 or market_price >= 0.99:
        return None
    
    def objective(sigma):
        return bs_probability_above_strike(S, K, time_remaining, sigma) - market_price
    
    try:
        f_low = objective(0.0001)
        f_high = objective(200.0)
        
        if f_low * f_high > 0:
            print(f"\n⚠️ IV bracket fail: price={market_price:.4f}, f_low={f_low:.4f}, f_high={f_high:.4f}, S={S:.2f}, K={K:.2f}, t={time_remaining}s")
            return None
            
        iv = brentq(objective, 0.0001, 200.0, xtol=1e-6)
        return iv
    except:
        return None

def calculate_quoted_spread(bs_prob: float, spread_width_cents: int = 3) -> tuple:
    """Calculate bid/ask with exact spread"""
    half_spread = spread_width_cents / 200
    
    # Bid: floor
    bid_raw = max(0.01, bs_prob - half_spread)
    bid = math.floor(bid_raw * 100) / 100
    
    # Ask: bid + exact spread
    ask = min(0.99, bid + (spread_width_cents / 100))
    
    return bid, ask

def get_dynamic_spread_cents(iv: float) -> Optional[int]:
    """Calculate spread based on IV - None if IV > 0.5"""
    if iv > 100:
        print("no spread because IV went too high")
        return None
    
    thresholds = [1, 2, 5]
    spreads = [1, 3, 5, 10]
    return spreads[bisect_right(thresholds, iv)]

def get_seconds_to_next_15min() -> int:
    """Calculate seconds remaining until next 15-minute boundary"""
    now = datetime.now()
    current_minute = now.minute
    next_boundary = ((current_minute // 15) + 1) * 15
    
    if next_boundary == 60:
        next_time = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    else:
        next_time = now.replace(minute=next_boundary, second=0, microsecond=0)
    
    return int((next_time - now).total_seconds())

class EWMAVolEstimator:
    """
    10-second EWMA of realized variance from spot log-returns.

    Each call to update() consumes one price observation (assumed ~1 obs/second).
    The EWMA variance is kept in annualized units so that forecast() directly
    returns the next-second implied vol estimate without extra conversion.

    halflife=10s  →  alpha = 1 - exp(-ln2 / 10) ≈ 0.0670
    """

    HALFLIFE_SECONDS: int = 10
    SECONDS_PER_YEAR: int = 31_536_000

    def __init__(self) -> None:
        self._alpha: float = 1.0 - math.exp(-math.log(2) / self.HALFLIFE_SECONDS)
        self._ewma_var: Optional[float] = None   # annualized variance, EWMA-smoothed
        self._prev_price: Optional[float] = None
        self._n_obs: int = 0

    def update(self, price: float) -> None:
        """Ingest a new spot price and advance the EWMA one step."""
        if self._prev_price is None:
            self._prev_price = price
            return

        log_ret = math.log(price / self._prev_price)
        self._prev_price = price

        # Annualise the squared return (1 obs ≈ 1 second → × 31 536 000)
        sq_ret_annual = log_ret ** 2 * self.SECONDS_PER_YEAR

        if self._ewma_var is None:
            self._ewma_var = sq_ret_annual
        else:
            self._ewma_var = (
                self._alpha * sq_ret_annual
                + (1.0 - self._alpha) * self._ewma_var
            )

        self._n_obs += 1

    @property
    def forecast(self) -> Optional[float]:
        """
        Annualised vol forecast for the next second.
        Returns None until the first return is observed.
        Under EWMA, the current estimate IS the one-step-ahead forecast.
        """
        if self._ewma_var is None:
            return None
        return math.sqrt(max(self._ewma_var, 1e-12))

    @property
    def is_ready(self) -> bool:
        """True once at least one full halflife of observations has been ingested."""
        return self._n_obs >= self.HALFLIFE_SECONDS


class BTCPriceFeed:
    """Manages BTC price from multiple exchanges with 60s moving average"""

    def __init__(self):
        self.exchanges = {
            'cryptocom': ccxt.cryptocom(),
            'coinbase': ccxt.coinbase(),
            'bitstamp': ccxt.bitstamp(),
            'kraken': ccxt.kraken(),
            'gemini': ccxt.gemini(),
            'bullish': ccxt.bullish()
        }

        self.price_history = deque(maxlen=60)
        self.ewma_vol = EWMAVolEstimator()
        print("✓ CCXT exchanges initialized")
    
    def fetch_prices(self) -> Dict[str, Optional[float]]:
        """Fetch current BTC-USD price from all exchanges"""
        prices = {}
        for name, exchange in self.exchanges.items():
            try:
                ticker = exchange.fetch_ticker('BTC/USD')
                prices[name] = ticker['last']
            except:
                prices[name] = None
        return prices
    
    def get_average_price(self) -> Optional[float]:
        """Get weighted average of exchange prices"""
        prices = self.fetch_prices()
        
        weighted_sum = 0
        total_weight = 0
        
        for name, price in prices.items():
            if price is not None:
                weight = 2 if name in ['coinbase', 'cryptocom'] else 1
                weighted_sum += price * weight
                total_weight += weight
        
        if total_weight == 0:
            return None

        avg_price = weighted_sum / total_weight
        self.price_history.append(avg_price)
        self.ewma_vol.update(avg_price)
        return avg_price

    def get_iv_forecast(self) -> Optional[float]:
        """
        Return the 10-second EWMA realized-vol forecast (annualised) for the
        next second.  This is used directly as the implied-vol input to BS.
        Returns None until the first log-return is available.
        """
        return self.ewma_vol.forecast

    def get_60s_ma(self) -> Optional[float]:
        """Get 60-second moving average"""
        if len(self.price_history) == 0:
            return None
        return sum(self.price_history) / len(self.price_history)

class KalshiAPI:
    """Kalshi API client with async requests"""
    
    def __init__(self, api_key: str, api_secret: str, market_id: str):
        self.api_key = api_key
        self.api_base = "https://api.elections.kalshi.com/trade-api/v2"
        self.market_state = MarketState(market_id=market_id)
        
        try:
            if os.path.isfile(api_secret):
                with open(api_secret, 'r') as f:
                    key_data = f.read()
            else:
                key_data = api_secret
            
            self.private_key = serialization.load_pem_private_key(
                key_data.encode() if isinstance(key_data, str) else key_data,
                password=None,
                backend=default_backend()
            )
        except Exception as e:
            print(f"❌ Failed to load private key: {e}")
            sys.exit(1)
    
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
    
    async def _async_request(self, method: str, endpoint: str, data=None) -> dict:
        """Make async authenticated API request"""
        url = f"{self.api_base}{endpoint}"
        timestamp = str(int(time.time() * 1000))
        sig = self._sign_request(timestamp, method, endpoint)
        headers = {
            'KALSHI-ACCESS-KEY': self.api_key,
            'KALSHI-ACCESS-SIGNATURE': sig,
            'KALSHI-ACCESS-TIMESTAMP': timestamp,
            'Content-Type': 'application/json'
        }
        
        try:
            timeout = aiohttp.ClientTimeout(total=1)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                if method == "GET":
                    async with session.get(url, headers=headers) as resp:
                        return await resp.json()
                elif method == "POST":
                    async with session.post(url, json=data, headers=headers) as resp:
                        return await resp.json()
                elif method == "DELETE":
                    async with session.delete(url, headers=headers) as resp:
                        return await resp.json()
        except Exception:
            return {}
    
    async def get_strike_price(self, market_id: str) -> Optional[float]:
        """Get floor strike price from market"""
        result = await self._async_request("GET", f"/markets/{market_id}")
        if not result:
            return None
        
        market = result.get("market", {})
        floor_strike = market.get("floor_strike")
        return float(floor_strike) if floor_strike else None
    
    async def refresh_market_data_async(self, market_id: str) -> tuple:
        """Fetch all market data concurrently and return position/resting data"""
        tasks = [
            self._async_request("GET", f"/portfolio/orders?ticker={market_id}&status=resting"),
            self._async_request("GET", f"/portfolio/positions?ticker={market_id}&count_filter=position"),
            self._async_request("GET", f"/markets/{market_id}/orderbook")
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Update orderbook in market_state
        if results[2] and not isinstance(results[2], Exception):
            self.market_state.update_orderbook(results[2])
        
        # Process resting orders
        cached_resting = {"yes": 0, "no": 0}
        if results[0] is not None and not isinstance(results[0], Exception):
            orders = results[0].get("orders", [])
            cached_resting["yes"] = sum(
                o.get("remaining_count", o.get("count", 0))
                for o in orders if o.get("side") == "yes"
            )
            cached_resting["no"] = sum(
                o.get("remaining_count", o.get("count", 0))
                for o in orders if o.get("side") == "no"
            )
        
        # Process positions
        cached_position = {"yes": 0, "no": 0}
        if results[1] is not None and not isinstance(results[1], Exception):
            positions = results[1].get("market_positions", [])
            for pos in positions:
                if pos.get("ticker") == market_id:
                    position_val = pos.get("position", 0)
                    if position_val > 0:
                        cached_position["yes"] = position_val
                    elif position_val < 0:
                        cached_position["no"] = abs(position_val)
                    break
        
        return cached_resting, cached_position


class BSMarketMaker:
    """Black-Scholes market maker with order management"""
    
    SIDES = ["yes", "no"]
    
    def __init__(self, base_market_id: str, api_key: str, api_secret: str, contract_increment: int = 1):
        self.base_market_id = base_market_id
        self.current_market_id = base_market_id
        self.btc_feed = BTCPriceFeed()
        self.kalshi = KalshiAPI(api_key, api_secret, self.current_market_id)
        self.contract_increment = contract_increment
        self.last_valid_iv = 0.2
        self.last_iv_timestamp = None
        
        self.strike_price = None
        
        # Trading state
        self.running = False
        self.active = False
        self.stopping = False
        self.paused = False
        self.current_market_prices = []
        self.in_transition = False
        self.transition_start_time = None
        self.paused_for_high_iv = False
        
        # Higher-first mode
        self.higher_first_mode = False
        self.cycle_expensive_side: Optional[str] = None
        self.expensive_filled = False

        # Loss tracking for higher-first mode
        self.expensive_fill_price: Optional[float] = None
        self.cheaper_fill_price: Optional[float] = None
        self.consecutive_losses = 0
        self.bid_penalty_cents = 0  # Current penalty from losses
        self.penalty_start_time: Optional[float] = None
        self.waiting_for_manual_resume = True
        
        # Order tracking
        self.order_ids: Dict[str, Optional[str]] = {"yes": None, "no": None}
        self.last_prices: Dict[str, Optional[float]] = {"yes": None, "no": None}
        self.current_increment: Dict[str, int] = {"yes": 0, "no": 0}
        self.cycle_start_resting: Dict[str, int] = {"yes": 0, "no": 0}
        self.cycle_start_position: Dict[str, int] = {"yes": 0, "no": 0}
        self.cached_resting: Dict[str, Optional[int]] = {"yes": None, "no": None}
        self.cached_position: Dict[str, Optional[int]] = {"yes": None, "no": None}
        self.is_rebalancing = False
        
        # CSV logging
        self.csv_file = None
        self.csv_writer = None
        self.current_hour = None
        
        # Chart data
        self.chart_data = {
            'kalshi_bid': [], 'kalshi_ask': [],
            'bs_true': [], 'bs_true_bid': [], 'bs_true_ask': [],
            'bs_used_bid': [], 'bs_used_ask': []
        }
        
        self.trading_task: Optional[asyncio.Task] = None
        
        print(f"✓ Contract increment: {self.contract_increment}")
        print(f"✓ Dynamic spreads: IV-based (3c-20c)\n")

    def pause_trading(self) -> None:
        """Pause trading - completes current cycle"""
        if not self.active:
            print("⚠️  Not trading")
            return
        if self.paused:
            print("⚠️  Already paused")
            return
        
        print("\n⏸️  Pausing... will complete current cycle if active")
        self.paused = True

    def resume_trading(self) -> None:
        """Resume trading"""
        if not self.active:
            print("⚠️  Not trading")
            return
        if not self.paused and not self.waiting_for_manual_resume:
            print("⚠️  Not paused")
            return
        
        print("\n▶️  Resuming trading")
        self.paused = False
        self.waiting_for_manual_resume = False

    def track_fill_price(self, side: str) -> None:
        """Track fill price when order fills"""
        if not self.higher_first_mode:
            return
        
        if side == self.cycle_expensive_side and self.expensive_fill_price is None:
            self.expensive_fill_price = self.last_prices[side]
            if self.expensive_fill_price:
                print(f"\n📝 Tracked expensive fill: {side.upper()} @ ${self.expensive_fill_price:.2f}")
            else:
                print(f"\n⚠️ WARNING: Expensive side filled but last_prices[{side}] is None")
        elif side != self.cycle_expensive_side and self.cheaper_fill_price is None:
            self.cheaper_fill_price = self.last_prices[side]
            if self.cheaper_fill_price:
                print(f"\n📝 Tracked cheaper fill: {side.upper()} @ ${self.cheaper_fill_price:.2f}")
            else:
                print(f"\n⚠️ WARNING: Cheaper side filled but last_prices[{side}] is None")


    
    async def init_strike(self):
        """Initialize strike price"""
        print(f"Fetching strike for {self.base_market_id}...")
        self.strike_price = await self.kalshi.get_strike_price(self.base_market_id)
        
        if not self.strike_price:
            print("❌ Failed to get strike price")
            sys.exit(1)
        
        print(f"✓ Strike: ${self.strike_price:,.2f}")
    
    def _get_csv_filename(self) -> str:
        """Generate CSV filename"""
        now = datetime.now()
        return f"bs_log_{now.strftime('%Y%m%d_%H')}00.csv"
    
    def _init_csv(self):
        """Initialize or switch CSV file"""
        now = datetime.now()
        hour = now.hour
        
        if hour != self.current_hour:
            if self.csv_file:
                self.csv_file.close()
            
            self.current_hour = hour
            filename = self._get_csv_filename()
            
            file_exists = os.path.exists(filename)
            self.csv_file = open(filename, 'a', newline='')
            self.csv_writer = csv.writer(self.csv_file)
            
            if not file_exists:
                self.csv_writer.writerow([
                    'timestamp', 'market_id', 'btc_price', 'strike_price', 'iv', 'spread_cents',
                    'kalshi_bid', 'kalshi_ask', 'bs_true', 'bs_true_bid', 'bs_true_ask',
                    'bs_used_bid', 'bs_used_ask'
                ])
                print(f"✓ Created CSV: {filename}")
    
    def _log_to_csv(self, btc_price: float, strike_price: float, iv: float, spread_cents: int,
                kalshi_bid: float, kalshi_ask: float, bs_true: float,
                bs_true_bid: float, bs_true_ask: float, bs_used_bid: float, bs_used_ask: float):
        """Log data to CSV"""
        self._init_csv()
        
        self.csv_writer.writerow([
            datetime.now().isoformat(), self.current_market_id,
            round(btc_price, 2), round(strike_price, 2), round(iv, 4), spread_cents,
            round(kalshi_bid, 4), round(kalshi_ask, 4), round(bs_true, 4),
            round(bs_true_bid, 4), round(bs_true_ask, 4),
            round(bs_used_bid, 4), round(bs_used_ask, 4)
        ])
        self.csv_file.flush()
        
        # Store for charting
        self.chart_data['kalshi_bid'].append(kalshi_bid)
        self.chart_data['kalshi_ask'].append(kalshi_ask)
        self.chart_data['bs_true'].append(bs_true)
        self.chart_data['bs_true_bid'].append(bs_true_bid)
        self.chart_data['bs_true_ask'].append(bs_true_ask)
        self.chart_data['bs_used_bid'].append(bs_used_bid)
        self.chart_data['bs_used_ask'].append(bs_used_ask)
    
    async def place_order(self, side: str, price: float, count: int = None) -> Optional[str]:
        """Place YES or NO order"""
        # Don't place if we already have a position
        if self.cached_position[side] and self.cached_position[side] >= 1:
            print(f"⚠️ Skipping {side.upper()} - already have position")
            return None
        
        # Don't place if we already have a resting order
        if self.cached_resting[side] and self.cached_resting[side] >= 1:
            print(f"⚠️ Skipping {side.upper()} - already have resting order")
            return None
        print(f"placing {side} order")

        if self.cached_position[side] == 1:
            return print("skipping order placement due to fill")

        order_data = {
            "ticker": self.current_market_id,
            "side": side,
            "action": "buy",
            "count": count,
            "type": "limit",
            "client_order_id": f"{self.current_market_id}-{side}-{int(time.time() * 1000)}",
            f"{side}_price": int(round(price * 100))
        }
        result = await self.kalshi._async_request("POST", "/portfolio/orders", order_data)
        return result.get("order", {}).get("order_id") if result else None
    
    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an order"""
        if order_id:
            result = await self.kalshi._async_request("DELETE", f"/portfolio/orders/{order_id}")
            return bool(result)
        return False
    
    async def cancel_all_orders(self) -> None:
        """Cancel all orders"""
        tasks = []
        for side in self.SIDES:
            if self.order_ids[side]:
                tasks.append(self.cancel_order(self.order_ids[side]))
                self.order_ids[side] = None
                self.last_prices[side] = None
        
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
    
    async def modify_order(self, side: str, new_price: float) -> Optional[str]:
        """Cancel and replace order"""
        old_order_id = self.order_ids[side]
        resting_before = self.contract_increment
        
        if old_order_id:
            resting_before = self.cached_resting[side] or 0
            
            if resting_before == 0:
                self.order_ids[side] = None
                self.last_prices[side] = None
                return None
            
            # Check if this side already filled its increment
            if self.current_increment[side] >= self.contract_increment:
                print(f"\n✓ {side.upper()} already filled {self.current_increment[side]}/{self.contract_increment} - not replacing")
                await self.cancel_order(old_order_id)
                self.order_ids[side] = None
                self.last_prices[side] = None
                return None
            
            await self.cancel_order(old_order_id)
            
            # Refresh to detect any fills during cancel
            self.cached_resting, self.cached_position = await self.kalshi.refresh_market_data_async(self.current_market_id)
            print(f"current side: {side}")
            print(f"cache resting: {self.cached_resting}")
            print(f"cached position: {self.cached_position}")
            
            # Re-check increment after refresh
            pos_current = self.cached_position[side] or 0
            filled_this_cycle = pos_current - self.cycle_start_position[side]
            if filled_this_cycle > self.current_increment[side]:
                self.current_increment[side] = filled_this_cycle

            if self.current_increment[side] >= self.contract_increment:
                print(f"\n⚡ {side.upper()} filled during cancel")
                self.order_ids[side] = None
                self.last_prices[side] = None
                return None
            
            new_order_id = await self.place_order(side, new_price, resting_before)
        else:
            new_order_id = await self.place_order(side, new_price, self.contract_increment)
        
        if new_order_id:
            self.order_ids[side] = new_order_id
            self.last_prices[side] = new_price
            self.cycle_start_resting[side] = resting_before if old_order_id else self.contract_increment
            return new_order_id
        
        if old_order_id:
            self.order_ids[side] = None
            self.last_prices[side] = None
        
        return None
    
    def check_fills(self) -> None:
        """Update fill tracking based on POSITION changes"""
        for side in self.SIDES:
            pos_current = self.cached_position[side] or 0
            pos_start = self.cycle_start_position[side]
            
            # Only count fills if position actually increased
            filled_this_cycle = pos_current - pos_start
            
            if filled_this_cycle > self.current_increment[side]:
                self.current_increment[side] = filled_this_cycle

                self.track_fill_price(side)
                
                # Track expensive fill in higher-first mode
                if self.higher_first_mode and side == self.cycle_expensive_side and not self.expensive_filled:
                    if self.current_increment[side] >= self.contract_increment:
                        self.expensive_filled = True
                        print(f"\n✓ Expensive side ({side.upper()}) filled - switching to cheaper side")

    def check_cycle_pnl(self) -> None:
        """Check P&L and adjust strategy after cycle completion"""
        if not self.higher_first_mode:
            return
        
        if self.expensive_fill_price is None or self.cheaper_fill_price is None:
            print(f"\n⚠️ Cycle complete but missing fill prices (Expensive: {self.expensive_fill_price}, Cheaper: {self.cheaper_fill_price})")
            return
        
        # Calculate spread (expensive buy + cheaper buy = cost)
        spread_cents = int(round((self.expensive_fill_price + self.cheaper_fill_price) * 100))
        pnl_cents = 100 - spread_cents
        
        print(f"\n💰 Cycle P&L: {pnl_cents:+d}¢ (Expensive: ${self.expensive_fill_price:.2f}, Cheaper: ${self.cheaper_fill_price:.2f})")
        
        if pnl_cents < 0:
            self.consecutive_losses += 1
            print(f"⚠️  Loss detected! Consecutive losses: {self.consecutive_losses}")
            
            if self.consecutive_losses >= 2:
                print(f"🛑 AUTO-PAUSE: 2 consecutive losses - please review and press R to resume")
                self.paused = True
                self.waiting_for_manual_resume = True
                self.consecutive_losses = 0
            else:
                self.bid_penalty_cents = 10
                self.penalty_start_time = time.time()
                print(f"📉 Applying 10¢ bid penalty, recovering at 1¢/s")
        else:
            if self.consecutive_losses > 0:
                print(f"✅ Profitable cycle - consecutive loss count reset")
            self.consecutive_losses = 0

    def get_bid_info(self, side: str):
        """Get bid, bid_size, second_bid for a side"""
        if side == "yes":
            return self.kalshi.market_state.yes_bid, self.kalshi.market_state.yes_bid_size, self.kalshi.market_state.yes_second_bid
        else:
            return self.kalshi.market_state.no_bid, self.kalshi.market_state.no_bid_size, self.kalshi.market_state.no_second_bid
        
    def get_adjusted_bid(self, base_bid: float) -> float:
        """Apply penalty and recovery to bid price"""
        if self.bid_penalty_cents == 0:
            return base_bid
        
        # Recover 1c per second
        elapsed = time.time() - self.penalty_start_time
        recovered_cents = int(elapsed)
        current_penalty = max(0, self.bid_penalty_cents - recovered_cents)
        
        if current_penalty == 0:
            self.bid_penalty_cents = 0
            self.penalty_start_time = None
            print(f"\n✅ Bid penalty fully recovered")
            return base_bid
        
        adjusted = base_bid - (current_penalty / 100)
        return max(0.01, adjusted)
    
    async def rebalance(self, yes_pos: int, no_pos: int) -> None:
        """Fix position mismatch by placing order only on lagging side"""
        print(f"\n⚠️ Position mismatch: YES={yes_pos}, NO={no_pos}")
        
        await self.cancel_all_orders()
        
        if yes_pos < no_pos:
            lagging_side = "yes"
            diff = no_pos - yes_pos
        else:
            lagging_side = "no"
            diff = yes_pos - no_pos
        
        print(f"DEBUG: Lagging side = {lagging_side}, diff = {diff}")
        
        self.current_increment = {"yes": 0, "no": 0}
        self.order_ids = {"yes": None, "no": None}
        self.last_prices = {"yes": None, "no": None}
        self.cycle_start_resting = {"yes": 0, "no": 0}
        
        bid, _, _ = self.get_bid_info(lagging_side)
        if bid is not None:
            order_id = await self.place_order(lagging_side, bid, diff)
            if order_id:
                self.order_ids[lagging_side] = order_id
                self.last_prices[lagging_side] = bid
                self.cycle_start_resting[lagging_side] = diff
                self.is_rebalancing = True
                print(f"✓ Rebalancing: Placed {diff} {lagging_side.upper()} @ ${bid:.2f}")
    
    def both_filled(self) -> bool:
        """Check if cycle complete"""
        if self.is_rebalancing:
            lagging_has_resting = any(
                self.order_ids[side] is not None and (self.cached_resting[side] or 0) > 0
                for side in self.SIDES
            )
            if lagging_has_resting:
                return False
            
            yes_pos = self.cached_position["yes"]
            no_pos = self.cached_position["no"]
            
            if yes_pos is None or no_pos is None:
                return False
            
            if yes_pos == no_pos:
                self.is_rebalancing = False
                return True
            
            return False
        
        if self.higher_first_mode:
            if not self.expensive_filled:
                return False
            
            yes_pos = self.cached_position["yes"] or 0
            no_pos = self.cached_position["no"] or 0
            
            return yes_pos == no_pos 
               
        # Normal mode: both sides must have filled their increments
        cycle_complete = all(self.current_increment[side] >= self.contract_increment for side in self.SIDES)
        
        if not cycle_complete:
            return False
        
        yes_pos = self.cached_position["yes"]
        no_pos = self.cached_position["no"]
        
        if yes_pos is None or no_pos is None:
            return False
        
        if yes_pos == no_pos:
            return True
        
        return False
    
    def can_start_new_cycle(self, bs_prob: float, bs_yes_bid: float = None, bs_no_bid: float = None) -> bool:
        """Check if BS probability and adjusted bid are in valid range to start new cycle"""
        if not self.higher_first_mode:
            return True
        
        # Check BS probability range
        if bs_prob > 0.80:
            expensive_bid = bs_yes_bid if bs_yes_bid else None
        elif bs_prob < 0.20:
            expensive_bid = bs_no_bid if bs_no_bid else None
        else:
            return False
        
        # Check adjusted bid if provided
        if expensive_bid and self.get_adjusted_bid(expensive_bid) < 0.80:
            # Set flag if adjusted bid drops below threshold
            if not self.waiting_for_manual_resume:
                print(f"\n⚠️  Adjusted bid below 80¢ - press R when ready to resume")
                self.waiting_for_manual_resume = True
            return False
        
        return True
    
    async def initialize_orders(self, bs_yes_bid: float, bs_no_bid: float, bs_prob: float) -> bool:
        """Place initial orders"""
        success = True

        # APPLY PENALTY HERE
        adjusted_yes_bid = self.get_adjusted_bid(bs_yes_bid)
        adjusted_no_bid = self.get_adjusted_bid(bs_no_bid)
        
        if self.higher_first_mode:
            if bs_prob > 0.80:
                expensive_side = "yes"
                expensive_price = adjusted_yes_bid  # Use adjusted price
            elif bs_prob < 0.20:
                expensive_side = "no"
                expensive_price = adjusted_no_bid

            
            self.cycle_expensive_side = expensive_side
            self.expensive_filled = False
            
            existing = self.cached_resting[expensive_side]
            
            if existing is None:
                print(f"⚠️  {expensive_side.upper()}: API error")
                return False
            
            # Check if position already exists (fast fill)
            existing_pos = self.cached_position[expensive_side] or 0
            if existing_pos >= self.contract_increment:
                print(f"✓ {expensive_side.upper()} (expensive): Already filled - switching to cheaper")
                self.expensive_filled = True
                
                # Place cheaper side immediately
                cheaper_side = "no" if expensive_side == "yes" else "yes"
                cheaper_price = adjusted_yes_bid if cheaper_side == "yes" else adjusted_no_bid
                
                order_id = await self.place_order(cheaper_side, cheaper_price, self.contract_increment)
                if order_id:
                    self.order_ids[cheaper_side] = order_id
                    self.last_prices[cheaper_side] = cheaper_price
                    self.cycle_start_resting[cheaper_side] = self.contract_increment
                    print(f"✓ {cheaper_side.upper()} (cheaper): Placed {self.contract_increment} @ ${cheaper_price:.2f}")
                return True

            
            if existing > 0:
                # Track existing order
                result = await self.kalshi._async_request("GET", f"/portfolio/orders?ticker={self.current_market_id}&status=resting")
                if result:
                    orders = result.get("orders", [])
                    for o in orders:
                        if o.get("side") == expensive_side:
                            self.order_ids[expensive_side] = o.get("order_id")
                            price_key = f"{expensive_side}_price"
                            self.last_prices[expensive_side] = o.get(price_key, 0) / 100 if o.get(price_key) else None
                            self.cycle_start_resting[expensive_side] = o.get("remaining_count", o.get("count", 0))
                            print(f"✓ {expensive_side.upper()} (expensive): Tracking existing {existing} @ ${self.last_prices[expensive_side]:.2f}")
                            break
                return True
            
            order_id = await self.place_order(expensive_side, expensive_price, self.contract_increment)
            if order_id:
                self.order_ids[expensive_side] = order_id
                self.last_prices[expensive_side] = expensive_price
                self.cycle_start_resting[expensive_side] = self.contract_increment
                print(f"✓ {expensive_side.upper()} (expensive): Placed {self.contract_increment} @ ${expensive_price:.2f}")
            else:
                print(f"❌ {expensive_side.upper()}: Failed to place")
                return False
            
            return True
        
        # Normal mode: place both sides
        for side in self.SIDES:
            existing = self.cached_resting[side]
            
            if existing is None:
                print(f"⚠️  {side.upper()}: API error checking resting")
                success = False
                continue
            if existing > 0:
                # Get existing order details to track it
                result = await self.kalshi._async_request("GET", f"/portfolio/orders?ticker={self.current_market_id}&status=resting")
                if result:
                    orders = result.get("orders", [])
                    for o in orders:
                        if o.get("side") == side:
                            self.order_ids[side] = o.get("order_id")
                            price_key = f"{side}_price"
                            self.last_prices[side] = o.get(price_key, 0) / 100 if o.get(price_key) else None
                            self.cycle_start_resting[side] = o.get("remaining_count", o.get("count", 0))
                            print(f"✓ {side.upper()}: Tracking existing {existing} @ ${self.last_prices[side]:.2f}")
                            break
                continue
            
            price = bs_yes_bid if side == "yes" else bs_no_bid
            order_id = await self.place_order(side, price, self.contract_increment)
            if order_id:
                self.order_ids[side] = order_id
                self.last_prices[side] = price
                self.cycle_start_resting[side] = self.contract_increment
                print(f"✓ {side.upper()}: Placed {self.contract_increment} @ ${price:.2f}")
            else:
                success = False
                print(f"❌ {side.upper()}: Failed to place")
        
        return success
    
    async def start_new_cycle(self, bs_yes_bid: float, bs_no_bid: float, bs_prob: float) -> None:
        """Start new cycle"""
        print("starting new cycle")
        self.check_cycle_pnl()
        self.expensive_fill_price = None
        self.cheaper_fill_price = None
        yes_pos = self.cached_position["yes"]
        no_pos = self.cached_position["no"]
        yes_pos_str = str(yes_pos) if yes_pos is not None else "?"
        no_pos_str = str(no_pos) if no_pos is not None else "?"
        
        # Only check bounds for FRESH cycles (both positions at 0)
        both_zero = (yes_pos == 0 or yes_pos is None) and (no_pos == 0 or no_pos is None)
        if both_zero and not self.can_start_new_cycle(bs_prob):
            print(f"\n⏸️  BS prob {bs_prob:.2%} not in range (>80% or <20%) - waiting")
            await self.cancel_all_orders()
            return

        print(f"\n✓ Cycle complete - Position: YES={yes_pos_str}, NO={no_pos_str}")
        
        self.current_increment = {"yes": 0, "no": 0}
        self.cycle_start_position = {"yes": yes_pos or 0, "no": no_pos or 0}
        self.order_ids = {"yes": None, "no": None}
        self.last_prices = {"yes": None, "no": None}
        self.expensive_filled = False
        self.cycle_expensive_side = None
        
        if await self.initialize_orders(bs_yes_bid, bs_no_bid, bs_prob):
            mode_str = f" [Expensive: {self.cycle_expensive_side.upper()}]" if self.higher_first_mode else ""
            print(f"✓ New cycle initialized{mode_str}")
        else:
            print("❌ Failed to start new cycle")
    
    async def update_orders(self, bs_yes_bid: float, bs_no_bid: float, bs_prob: float) -> None:
        """Update orders if BS prices changed"""

        adjusted_yes_bid = self.get_adjusted_bid(bs_yes_bid)
        adjusted_no_bid = self.get_adjusted_bid(bs_no_bid)
        # Higher-first mode logic
        if self.higher_first_mode:
            # If expensive side not filled yet, only update expensive side
            if not self.expensive_filled:
                expensive_side = self.cycle_expensive_side
                target_price = adjusted_yes_bid if expensive_side == "yes" else adjusted_no_bid
                last_price = self.last_prices[expensive_side]
                
                if last_price is not None:
                    last_cents = round(last_price * 100)
                    target_cents = round(target_price * 100)
                    
                    if target_cents != last_cents:
                        new_order_id = await self.modify_order(expensive_side, target_price)
                        if new_order_id:
                            direction = "↑" if target_price > last_price else "↓"
                            print(f"\n{direction} {expensive_side.upper()} (expensive): ${last_price:.2f} → ${target_price:.2f}")
                return
            
             # Expensive filled, check if cheaper also filled
            cheaper_side = "no" if self.cycle_expensive_side == "yes" else "yes"
            
            # DON'T place/update if cheaper side already filled
            if self.current_increment[cheaper_side] >= self.contract_increment:
                return
            target_price = adjusted_yes_bid if cheaper_side == "yes" else adjusted_no_bid
            last_price = self.last_prices[cheaper_side]
            
            # Place initial cheaper order if it doesn't exist
            if last_price is None:
                order_id = await self.place_order(cheaper_side, target_price, self.contract_increment)
                if order_id:
                    self.order_ids[cheaper_side] = order_id
                    self.last_prices[cheaper_side] = target_price
                    self.cycle_start_resting[cheaper_side] = self.contract_increment
                    print(f"\n✓ {cheaper_side.upper()} (cheaper): Placed {self.contract_increment} @ ${target_price:.2f}")
                return
            
            # Update cheaper side price
            last_cents = round(last_price * 100)
            target_cents = round(target_price * 100)
            
            if target_cents != last_cents:
                new_order_id = await self.modify_order(cheaper_side, target_price)
                if new_order_id:
                    direction = "↑" if target_price > last_price else "↓"
                    print(f"\n{direction} {cheaper_side.upper()} (cheaper): ${last_price:.2f} → ${target_price:.2f}")
            return
        
        # Normal mode: update both sides
        prices = {"yes": bs_yes_bid, "no": bs_no_bid}
        
        for side in self.SIDES:
            target_price = prices[side]
            last_price = self.last_prices[side]
            
            if last_price is not None:
                last_cents = round(last_price * 100)
                target_cents = round(target_price * 100)
                
                if target_cents != last_cents:
                    new_order_id = await self.modify_order(side, target_price)
                    if new_order_id:
                        direction = "↑" if target_price > last_price else "↓"
                        print(f"\n{direction} {side.upper()}: ${last_price:.2f} → ${target_price:.2f}")
    
    def print_status(self, btc_price: float, iv: float, spread_cents: int, 
                    bs_true_bid: float, bs_true_ask: float, bs_prob: float, time_remaining: int, theta: float) -> None:
        """Print status"""
        yes_rest = self.cached_resting["yes"]
        no_rest = self.cached_resting["no"]
        yes_pos = self.cached_position["yes"]
        no_pos = self.cached_position["no"]
        
        rest_str = f"R:{yes_rest if yes_rest is not None else '?'}/{no_rest if no_rest is not None else '?'}"
        cycle_str = f"C:{self.current_increment['yes']}/{self.contract_increment},{self.current_increment['no']}/{self.contract_increment}"
        pos_str = f"P:{yes_pos if yes_pos is not None else '?'}/{no_pos if no_pos is not None else '?'}"
        
        mode_parts = []
        if self.is_rebalancing:
            mode_parts.append("REBAL")
        if self.higher_first_mode:
            if self.cycle_expensive_side:
                exp_status = "✓" if self.expensive_filled else "⏳"
                mode_parts.append(f"HF:{self.cycle_expensive_side.upper()}{exp_status}")
            if self.bid_penalty_cents > 0:
                current_penalty = max(0, self.bid_penalty_cents - int(time.time() - self.penalty_start_time))
                mode_parts.append(f"PENALTY:-{current_penalty}¢")
        if self.waiting_for_manual_resume:  
            mode_parts.append("WAITING-R")
        mode_str = f"[{','.join(mode_parts)}]" if mode_parts else ""
        
        print(f"\r[{int(time_remaining):3d}s] BTC:${btc_price:,.2f} | IV:{iv:.4f} | Spread:{spread_cents}c | Θ:{theta:.4f}¢/s | "
          f"BS:{bs_prob:.4f} | Quote: {bs_true_bid:.4f}/{bs_true_ask:.4f} | {rest_str} | {cycle_str} | {pos_str} {mode_str}", 
          end="", flush=True)

    
    async def switch_to_next_market(self) -> bool:
        """Switch to next 15-min market"""
        await self.cancel_all_orders()
        
        parts = self.current_market_id.split('-')
        if len(parts) < 2:
            return False
        
        time_part = parts[-2]
        if len(time_part) < 4:
            return False
        
        time_str = time_part[-4:]
        hour = int(time_str[:2])
        minute = int(time_str[2:])
        
        next_minute = (minute + 15) % 60
        next_hour = (hour + 1) % 24 if next_minute < minute else hour
        
        day_transition = (hour == 23 and next_hour == 0)
        
        if day_transition:
            year = 2000 + int(time_part[:2])
            month_str = time_part[2:5]
            day = int(time_part[5:7])
            
            months = {
                'JAN': 1, 'FEB': 2, 'MAR': 3, 'APR': 4, 'MAY': 5, 'JUN': 6,
                'JUL': 7, 'AUG': 8, 'SEP': 9, 'OCT': 10, 'NOV': 11, 'DEC': 12
            }
            month = months[month_str]
            
            current_date = datetime(year, month, day)
            next_date = current_date + timedelta(days=1)
            
            year = next_date.year % 100
            month_names = ['', 'JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN',
                          'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC']
            month_str = month_names[next_date.month]
            day = next_date.day
            
            date_prefix = f"{year:02d}{month_str}{day:02d}"
        else:
            date_prefix = time_part[:-4]
        
        base_prefix = '-'.join(parts[:-2]) + '-' + date_prefix
        time_str = f"{next_hour:02d}{next_minute:02d}"
        next_market = f"{base_prefix}{time_str}-{next_minute:02d}"
        
        print(f"\n🔄 Switching: {self.current_market_id} → {next_market}")
        
        # Fetch new strike
        max_retries = 3
        new_strike = None
        
        for attempt in range(max_retries + 1):
            new_strike = await self.kalshi.get_strike_price(next_market)
            if new_strike:
                break
            
            if attempt < max_retries:
                wait_time = 10 + (attempt * 10)
                print(f"⚠️  Retrying... ({attempt + 1}/{max_retries + 1}) in {wait_time}s")
                await asyncio.sleep(wait_time)
            else:
                print(f"❌ Failed after {max_retries + 1} attempts")
                return False
        
        # Switch
        self.current_market_id = next_market
        self.kalshi.market_state.market_id = next_market
        self.strike_price = new_strike
        self.current_market_prices = []
        
        # Reset order tracking
        self.order_ids = {"yes": None, "no": None}
        self.last_prices = {"yes": None, "no": None}
        self.current_increment = {"yes": 0, "no": 0}
        self.cycle_start_resting = {"yes": 0, "no": 0}
        self.cycle_start_position = {"yes": 0, "no": 0}
        self.is_rebalancing = False
        self.expensive_filled = False
        self.cycle_expensive_side = None
        
        print(f"✓ New strike: ${self.strike_price:,.2f}")
        
        # Transition mode
        self.in_transition = True
        self.transition_start_time = time.time()
        self.paused_for_high_iv = False
        print("⏸️  Transition: 60s pause...")
        
        return True
    
    async def trading_loop(self) -> None:
        """Main trading loop"""
        warmup_complete = False
        prev_time_remaining = None
        last_status = time.time()
        
        while self.running and self.active:
            time_remaining = get_seconds_to_next_15min()
            
            # Transition mode
            if self.in_transition:
                elapsed = time.time() - self.transition_start_time
                if elapsed >= 60:
                    self.in_transition = False
                    prev_time_remaining = None
                    print("\n✓ Transition complete")
                else:
                    self.btc_feed.get_average_price()
                    print(f"\r⏸️  Transition: {60 - int(elapsed)}s", end="", flush=True)
                    await asyncio.sleep(0.5)
                    continue
            
            # Market expiration
            market_expired = (time_remaining <= 0) or (prev_time_remaining is not None and prev_time_remaining < 30 and time_remaining > 800)
            
            if market_expired:
                print("\n⏭️  Market expired, switching...")
                
                if not await self.switch_to_next_market():
                    print("⚠️  Failed to switch, waiting 30s...")
                    await asyncio.sleep(30)
                    continue
                
                warmup_complete = False
                continue
            
            # Warmup
            self.btc_feed.get_average_price()
            
            if not warmup_complete:
                if len(self.btc_feed.price_history) < 60:
                    print(f"\r⏳ Warmup: {len(self.btc_feed.price_history)}/60", end="", flush=True)
                    await asyncio.sleep(0.5)
                    continue
                else:
                    warmup_complete = True
                    print("\n✓ Warmup complete\n")
            
            btc_price_ma = self.btc_feed.get_60s_ma()
            
            if btc_price_ma:
                self.current_market_prices.append(btc_price_ma)

                self.cached_resting, self.cached_position = await self.kalshi.refresh_market_data_async(self.current_market_id)
                
                # Get orderbook (for logging; IV now comes from realized vol)
                kalshi_bid = self.kalshi.market_state.yes_bid
                kalshi_ask = (1 - self.kalshi.market_state.no_bid) if self.kalshi.market_state.no_bid else None

                # --- IV from 10-second EWMA realized vol ----------------------------
                # Each price tick updates the EWMA of squared log-returns (annualised).
                # The current EWMA value is the one-step-ahead vol forecast used as IV.
                ewma_iv = self.btc_feed.get_iv_forecast()

                if ewma_iv is not None:
                    self.last_valid_iv = ewma_iv
                    self.last_iv_timestamp = time.time()
                    current_iv = ewma_iv
                else:
                    current_iv = self.last_valid_iv
                    if self.last_iv_timestamp is None or (time.time() - self.last_iv_timestamp) > 120:
                        print(f"\n⚠️ Realized vol unavailable for >120s - stopping")
                        await self.cancel_all_orders()
                        self.active = False
                        break
                # --------------------------------------------------------------------

                # Get dynamic spread
                spread_cents = get_dynamic_spread_cents(current_iv)

                # Pause if IV too high
                if spread_cents is None:
                    if not self.paused_for_high_iv:
                        print(f"\n⚠️  IV > 0.5 ({current_iv:.4f}) - cancelling orders")
                        await self.cancel_all_orders()
                        self.paused_for_high_iv = True
                    print(f"\r[{int(time_remaining):3d}s] BTC:${btc_price_ma:,.2f} | IV:{current_iv:.4f} | ⏸️  PAUSED",
                          end="", flush=True)
                    prev_time_remaining = time_remaining
                    await asyncio.sleep(0.5)
                    continue

                # Resume if was paused
                if self.paused_for_high_iv:
                    print(f"\n✓ IV back below 0.5 ({current_iv:.4f}) - resuming")
                    self.paused_for_high_iv = False

                # Calculate BS probability and quotes
                bs_prob = bs_probability_above_strike(
                    S_current=btc_price_ma,
                    K=self.strike_price,
                    time_remaining_seconds=time_remaining,
                    sigma_annual=current_iv
                )
                theta_cents_per_sec = bs_theta(
                    S_current=btc_price_ma,
                    K=self.strike_price,
                    time_remaining_seconds=time_remaining,
                    sigma_annual=current_iv
                )

                bs_true_bid, bs_true_ask = calculate_quoted_spread(bs_prob, spread_cents)
                bs_yes_bid = bs_true_bid
                bs_no_bid = math.floor((1 - (bs_true_ask)) * 100) / 100

                # Log to CSV (use 0.0 for orderbook fields when unavailable)
                self._log_to_csv(
                    btc_price_ma, self.strike_price, current_iv, spread_cents,
                    kalshi_bid or 0.0, kalshi_ask or 0.0, bs_prob,
                    bs_true_bid, bs_true_ask,
                    bs_yes_bid, bs_no_bid
                )

                # Trading
                if self.paused:
                    self.check_fills()

                    if self.both_filled():
                        print("\n Cycle completed during pause, waiting to resume)")
                        await self.cancel_all_orders()
                    else:
                        await self.update_orders(bs_yes_bid, bs_no_bid, bs_prob)

                    print(f"\r[{int(time_remaining):3d}s] BTC:${btc_price_ma:,.2f} | IV:{current_iv:.4f} | Spread:{spread_cents}c | Θ:{theta_cents_per_sec:.4f}¢/s | ⏸️  PAUSED",
                          end="", flush=True)

                elif not self.stopping:
                    self.check_fills()

                    if self.both_filled():
                        yes_pos = self.cached_position["yes"]
                        no_pos = self.cached_position["no"]
                        if not self.higher_first_mode and yes_pos is not None and no_pos is not None and yes_pos != no_pos:
                            print("rebalancing from trading loop")
                            await self.rebalance(yes_pos, no_pos)
                        else:
                            await self.start_new_cycle(bs_yes_bid, bs_no_bid, bs_prob)
                    else:
                        has_orders = any(self.cached_resting[side] and self.cached_resting[side] > 0
                                         for side in self.SIDES)

                        # In higher-first mode, if expensive filled but cheaper hasn't, always update
                        if self.higher_first_mode and self.expensive_filled and not self.both_filled():
                            await self.update_orders(bs_yes_bid, bs_no_bid, bs_prob)
                        elif not has_orders and not self.is_rebalancing:
                            if not self.waiting_for_manual_resume and self.can_start_new_cycle(bs_prob, bs_yes_bid, bs_no_bid):
                                await self.initialize_orders(bs_yes_bid, bs_no_bid, bs_prob)
                        else:
                            await self.update_orders(bs_yes_bid, bs_no_bid, bs_prob)

                    if time.time() - last_status >= 0.5:
                        self.print_status(btc_price_ma, current_iv, spread_cents,
                                          bs_true_bid, bs_true_ask, bs_prob, time_remaining, theta_cents_per_sec)
                        last_status = time.time()
                else:
                    self.cached_resting, self.cached_position = await self.kalshi.refresh_market_data_async(self.current_market_id)
                    self.check_fills()
                    if self.both_filled():
                        print("\n✓ Cycle complete - stopping")
                        await self.cancel_all_orders()
                        self.active = False
                    else:
                        await self.update_orders(bs_yes_bid, bs_no_bid, bs_prob)
                    print(f"\r⏳ Waiting for cycle to complete...", end="", flush=True)
            
            prev_time_remaining = time_remaining
            await asyncio.sleep(0.5)
        
        print("\n✓ Trading stopped")
    
    async def start_trading_async(self) -> None:
        """Start trading"""
        if self.active:
            print("⚠️  Already running")
            return
        
        # Prompt for higher-first mode
        higher_input = input("Use higher-first mode? (y/n): ").strip().lower()
        self.higher_first_mode = (higher_input == 'y')
        
        mode_desc = "HIGHER-FIRST (80%/20% bounds)" if self.higher_first_mode else "NORMAL"
        confirm = input(f"Start {mode_desc} trading with {self.contract_increment} contracts? (y/n): ").strip().lower()
        if confirm != 'y':
            print("❌ Cancelled")
            return
        
        print(f"\n🚀 Starting BS market maker ({mode_desc}, {self.contract_increment} contracts)...")
        
        self.cached_resting, self.cached_position = await self.kalshi.refresh_market_data_async(self.current_market_id)
        
        # Wait for warmup
        print("⏳ Warming up price feed...")
        for i in range(60):
            self.btc_feed.get_average_price()
            print(f"\r⏳ Warmup: {i+1}/60", end="", flush=True)
            await asyncio.sleep(0.5)
        print("\n✓ Warmup complete")

        self.last_iv_timestamp = time.time()
        
        self.active = True
        self.stopping = False
        self.waiting_for_manual_resume = True
        
        # Create background task for trading loop
        self.trading_task = asyncio.create_task(self.trading_loop())
        print("✓ Trading active")
    
    def stop_trading(self) -> None:
        """Stop trading"""
        if not self.active:
            print("⚠️  Not trading")
            return
        
        print("\n⏸️  Stopping... waiting for cycle to complete")
        self.stopping = True
    
    def cleanup(self):
        """Cleanup"""
        if self.csv_file:
            self.csv_file.close()


async def main_async():
    api_key = os.getenv("KALSHI_API_KEY")
    api_secret = os.getenv("KALSHI_API_SECRET")
    market_id = os.getenv("KALSHI_INTERVAL_MARKET")
    
    if not all([api_key, api_secret, market_id]):
        print("❌ Missing env vars")
        sys.exit(1)
    
    print("\n" + "="*80)
    print("BLACK-SCHOLES MARKET MAKER - HIGHER-FIRST MODE + DYNAMIC IV SPREADS")
    print("="*80 + "\n")
    
    # Prompt for contract increment
    while True:
        try:
            increment_input = input("Enter contract increment (1, 3, or 5): ").strip()
            increment = int(increment_input)
            if increment in [1, 3, 5]:
                break
            else:
                print("❌ Please enter 1, 3, or 5")
        except ValueError:
            print("❌ Please enter a valid number")
    
    trader = BSMarketMaker(market_id, api_key, api_secret, contract_increment=increment)
    await trader.init_strike()
    trader.running = True
    
    print("\nControls:")
    print("  [G] Start trading")
    print("  [P] Pause trading")
    print("  [R] Resume trading")
    print("  [S] Stop trading (completes cycle)")
    print("  [X] Exit")
    print("="*80 + "\n")
    
    loop = asyncio.get_event_loop()
    
    while trader.running:
        choice = await loop.run_in_executor(None, lambda: input("Command: ").strip().upper())
        
        if choice == "G":
            await trader.start_trading_async()
        elif choice == "P":        
            trader.pause_trading()     
        elif choice == "R":            
            trader.resume_trading()     
        elif choice == "S":
            trader.stop_trading()
        elif choice == "X":
            print("\n👋 Exiting...")
            trader.running = False
            trader.active = False
            await trader.cancel_all_orders()
            if trader.trading_task:
                trader.trading_task.cancel()
                try:
                    await trader.trading_task
                except asyncio.CancelledError:
                    pass
            trader.cleanup()
            sys.exit(0)
        else:
            print("❌ Invalid (G=start, P=pause, R=resume, S=stop, X=exit)")


if __name__ == "__main__":
    asyncio.run(main_async())