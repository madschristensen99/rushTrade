#!/usr/bin/env python3
"""
Automated NO bid market maker for two markets - ASYNC
"""

import os
import sys
import time
import asyncio
from typing import Optional, Dict
from dotenv import load_dotenv
from app.modules.terminal.auto.kalshi_api import Config, MarketState
from app.modules.terminal.auto.mm_core import BaseMarketMaker

load_dotenv()


class AutomatedTrader(BaseMarketMaker):
    """Two market NO trader - ASYNC"""
    
    def __init__(self, api_key: str, api_secret: str, market_1: str, market_2: str, config: Config):
        super().__init__(api_key, api_secret, config)
        self.market_1, self.market_2 = market_1, market_2
        
        self.market_states: Dict[str, MarketState] = {
            market_1: MarketState(market_id=market_1),
            market_2: MarketState(market_id=market_2)
        }
        
        self.order_ids: Dict[str, Optional[str]] = {market_1: None, market_2: None}
        self.last_prices: Dict[str, Optional[float]] = {market_1: None, market_2: None}
        self.current_increment: Dict[str, int] = {market_1: 0, market_2: 0}
        self.cycle_start_resting: Dict[str, int] = {market_1: 0, market_2: 0}
        self.cached_resting: Dict[str, Optional[int]] = {market_1: None, market_2: None}
        self.cached_position: Dict[str, Optional[int]] = {market_1: None, market_2: None}
        self.cached_queue_position: Dict[str, Optional[int]] = {market_1: None, market_2: None}
        self.fill_prices: Dict[str, Optional[float]] = {market_1: None, market_2: None}
        
        self.higher_first_mode = False
        self.active_market: Optional[str] = None
        
        # Initialize bump dicts for both markets
        self.bump_active = {market_1: False, market_2: False}
        self.bump_target = {market_1: None, market_2: None}
    
    def _get_markets(self):
        """Helper for Redis command processing"""
        return [self.market_1, self.market_2]
    
    async def refresh_market_data_async(self):
        """Fetch market data concurrently - 5 endpoints in parallel"""
        tasks = []
        
        # Orderbook for both markets
        for market_id in [self.market_1, self.market_2]:
            tasks.append(self._request("GET", f"/markets/{market_id}/orderbook"))
        
        # Resting orders for both markets
        for market_id in [self.market_1, self.market_2]:
            tasks.append(self._request("GET", f"/portfolio/orders?ticker={market_id}&status=resting"))
        
        # Positions for both markets (combined endpoint)
        tasks.append(self._request("GET", "/portfolio/positions?count_filter=position"))
        
        # Queue positions (all orders)
        tasks.append(self._request("GET", "/portfolio/orders?status=resting"))
        
        # Execute all requests concurrently
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Parse orderbooks (indices 0, 1)
        for idx, market_id in enumerate([self.market_1, self.market_2]):
            if isinstance(results[idx], dict) and results[idx]:
                self.market_states[market_id].update_orderbook(results[idx])
        
        # Parse resting orders (indices 2, 3)
        for idx, market_id in enumerate([self.market_1, self.market_2]):
            result_idx = idx + 2
            if isinstance(results[result_idx], dict) and results[result_idx]:
                orders = results[result_idx].get("orders", [])
                self.cached_resting[market_id] = sum(
                    o.get("remaining_count", o.get("count", 0))
                    for o in orders if o.get("side") == "no"
                )
        
        # Parse positions (index 4)
        if isinstance(results[4], dict) and results[4]:
            for market_id in [self.market_1, self.market_2]:
                self.cached_position[market_id] = 0
            
            for pos in results[4].get("market_positions", []):
                ticker = pos.get("ticker")
                if ticker in [self.market_1, self.market_2]:
                    self.cached_position[ticker] = abs(pos.get("position", 0))
        
        # Parse queue positions (index 5)
        if isinstance(results[5], dict) and results[5]:
            orders = results[5].get("orders", [])
            
            # Reset queue positions
            self.cached_queue_position = {self.market_1: None, self.market_2: None}
            
            # Match order_ids to markets and extract queue position
            for order in orders:
                order_id = order.get("order_id")
                ticker = order.get("ticker")
                queue_position = order.get("queue_position")
                
                # Match to our current orders
                if ticker in [self.market_1, self.market_2]:
                    if self.order_ids.get(ticker) == order_id and queue_position is not None:
                        self.cached_queue_position[ticker] = queue_position
        
        # Update fair value if enabled
        if self.fair_value_enabled:
            m1_state = self.market_states[self.market_1]
            m2_state = self.market_states[self.market_2]
            
            if m1_state.no_bid and m2_state.no_bid:
                avg_bid = (m1_state.no_bid + m2_state.no_bid) / 2
                self.update_fair_value(1 - avg_bid, avg_bid)
    
    async def cancel_all_orders_async(self):
        """Cancel all orders - ASYNC"""
        tasks = []
        for market_id in [self.market_1, self.market_2]:
            if self.order_ids[market_id]:
                tasks.append(self.cancel_order(self.order_ids[market_id]))
        
        await asyncio.gather(*tasks, return_exceptions=True)
        
        for market_id in [self.market_1, self.market_2]:
            self.order_ids[market_id] = self.last_prices[market_id] = None
    
    async def initialize_orders_async(self) -> bool:
        """Initialize orders - ASYNC"""
        success = True
        markets = [self.active_market] if self.higher_first_mode else [self.market_1, self.market_2]
        
        for market_id in markets:
            existing = self.cached_resting[market_id]
            if existing is None:
                print(f"⚠️  {market_id}: API error")
                success = False
                continue
            if existing > 0:
                print(f"⚠️  {market_id}: Already has {existing} resting orders")
                continue
            
            state = self.market_states[market_id]
            if state.no_bid:
                order_id = await self.place_order(market_id, "no", state.no_bid, self.contract_increment)
                if order_id:
                    self.order_ids[market_id] = order_id
                    self.last_prices[market_id] = state.no_bid
                    self.cycle_start_resting[market_id] = self.contract_increment
                    print(f"✓ {market_id}: Placed {self.contract_increment} NO @ ${state.no_bid:.2f}")
                    await asyncio.sleep(0.2)
                else:
                    print(f"❌ {market_id}: Failed to place order")
                    success = False
            else:
                print(f"❌ {market_id}: No NO bid")
                success = False
        
        return success
    
    def check_fills(self):
        """Check fills (synchronous logic)"""
        markets = [self.active_market] if self.higher_first_mode else [self.market_1, self.market_2]
        
        for market_id in markets:
            resting = self.cached_resting[market_id]
            if resting is None:
                continue
            
            filled = self.cycle_start_resting[market_id] - resting
            if filled > self.current_increment[market_id]:
                if self.current_increment[market_id] < self.contract_increment and filled >= self.contract_increment:
                    self.fill_prices[market_id] = self.last_prices[market_id]
                self.current_increment[market_id] = filled
    
    async def rebalance_async(self, m1_pos: int, m2_pos: int):
        """Rebalance positions - ASYNC"""
        print(f"\n⚠️ Position mismatch: {self.market_1}={m1_pos}, {self.market_2}={m2_pos}")
        await self.cancel_all_orders_async()
        
        lagging_market = self.market_1 if m1_pos < m2_pos else self.market_2
        diff = abs(m1_pos - m2_pos)
        
        self.current_increment = {self.market_1: 0, self.market_2: 0}
        self.order_ids = {self.market_1: None, self.market_2: None}
        self.last_prices = {self.market_1: None, self.market_2: None}
        self.cycle_start_resting = {self.market_1: 0, self.market_2: 0}
        self.fill_prices = {self.market_1: None, self.market_2: None}
        
        state = self.market_states[lagging_market]
        if state.no_bid:
            order_id = await self.place_order(lagging_market, "no", state.no_bid, diff)
            if order_id:
                self.order_ids[lagging_market] = order_id
                self.last_prices[lagging_market] = state.no_bid
                self.cycle_start_resting[lagging_market] = diff
                self.is_rebalancing = True
                print(f"✓ Rebalancing: Placed {diff} NO @ ${state.no_bid:.2f} on {lagging_market}")
    
    async def both_filled_async(self) -> bool:
        """Check if both sides filled - ASYNC"""
        if self.higher_first_mode:
            return self.current_increment[self.active_market] >= self.contract_increment
        
        if self.is_rebalancing:
            if any(self.order_ids[m] and (self.cached_resting[m] or 0) > 0 for m in [self.market_1, self.market_2]):
                return False
            
            m1_pos, m2_pos = self.cached_position[self.market_1], self.cached_position[self.market_2]
            if m1_pos is None or m2_pos is None:
                return False
            
            if m1_pos == m2_pos:
                self.is_rebalancing = False
                return True
            
            await self.rebalance_async(m1_pos, m2_pos)
            return False
        
        if not all(self.current_increment[m] >= self.contract_increment for m in [self.market_1, self.market_2]):
            return False
        
        m1_pos, m2_pos = self.cached_position[self.market_1], self.cached_position[self.market_2]
        if m1_pos is None or m2_pos is None:
            return False
        
        if m1_pos == m2_pos:
            return True
        
        await self.rebalance_async(m1_pos, m2_pos)
        return False
    
    async def start_new_cycle_async(self):
        """Start new cycle - ASYNC"""
        if not self.active:
            return
        
        m1_pos, m2_pos = self.cached_position[self.market_1], self.cached_position[self.market_2]
        
        if self.higher_first_mode:
            print(f"\n✓ {self.active_market} filled {self.contract_increment} - Position: {self.market_1}={m1_pos}, {self.market_2}={m2_pos}")
            self.active_market = self.market_2 if self.active_market == self.market_1 else self.market_1
        else:
            print(f"\n✓ Both filled {self.contract_increment} - Position: {self.market_1}={m1_pos}, {self.market_2}={m2_pos}")
        
        # Handle single fire
        if self.single_fire_mode:
            self.single_fire_cycles_completed += 1
            if self.single_fire_cycles_completed >= 1:
                print("✓ Single fire complete - pausing")
                self.paused = True
                self.single_fire_mode = False
                self.single_fire_cycles_completed = 0
                self.waiting_for_manual_resume = True
                return
        
        self.current_increment = {self.market_1: 0, self.market_2: 0}
        self.order_ids = {self.market_1: None, self.market_2: None}
        self.last_prices = {self.market_1: None, self.market_2: None}
        self.fill_prices = {self.market_1: None, self.market_2: None}
        
        if await self.initialize_orders_async():
            print("✓ New cycle initialized")
        else:
            print("❌ Failed to start new cycle")
            if not self.higher_first_mode and m1_pos != m2_pos:
                await self.rebalance_async(m1_pos, m2_pos)
    
    async def update_orders_async(self):
        """Update orders - ASYNC"""
        markets = [self.active_market] if self.higher_first_mode else [self.market_1, self.market_2]
        
        for market_id in markets:
            if not self.cached_resting[market_id]:
                continue
            
            state = self.market_states[market_id]
            target_price = self.check_target_price(
                "no", state.no_bid, state.no_bid_size, state.no_second_bid,
                self.last_prices[market_id], self.cached_resting[market_id] or 0,
                market_id=market_id
            )
            
            if target_price and self.last_prices[market_id]:
                if round(target_price * 100) != round(self.last_prices[market_id] * 100):
                    new_order_id = await self.modify_order(
                        market_id, "no", target_price,
                        self.order_ids[market_id], self.cached_resting[market_id],
                        self.cached_position[market_id]
                    )
                    if new_order_id:
                        self.order_ids[market_id] = new_order_id
                        direction = "↑" if target_price > self.last_prices[market_id] else "↓"
                        print(f"\n{direction} {market_id}: Updated ${self.last_prices[market_id]:.2f} → ${target_price:.2f}")
                        self.last_prices[market_id] = target_price
                        self.cycle_start_resting[market_id] = self.current_increment[market_id] + self.cached_resting[market_id]
                    else:
                        self.order_ids[market_id] = self.last_prices[market_id] = None
    
    def print_status(self):
        """Print status (synchronous)"""
        mode_str = "PENNYIF" if self.pennyif_mode else ("PENNY" if self.penny_mode else "JOIN")
        type_str = f"[{self.active_market[-7:]}]" if self.higher_first_mode else ""
        rebal_str = "[REBAL]" if self.is_rebalancing else ""
        pause_str = "[PAUSED]" if self.paused or self.waiting_for_manual_resume else ""
        
        m1_bump = "↑" if self.bump_active.get(self.market_1, False) else ""
        m2_bump = "↑" if self.bump_active.get(self.market_2, False) else ""
        
        m1_state, m2_state = self.market_states[self.market_1], self.market_states[self.market_2]
        
        fair_str = ""
        if self.fair_value_enabled and self.current_fair_value:
            fair_str = f" | FV: ${self.current_fair_value:.2f}"
        
        print(f"\r{mode_str} {type_str}{rebal_str}{pause_str} {self.market_1[-7:]}{m1_bump}: ${m1_state.no_bid:.2f if m1_state.no_bid else 0} | "
              f"Rest: {self.cached_resting[self.market_1]} | Cycle: {self.current_increment[self.market_1]}/{self.contract_increment} | "
              f"Pos: {self.cached_position[self.market_1]} || {self.market_2[-7:]}{m2_bump}: ${m2_state.no_bid:.2f if m2_state.no_bid else 0} | "
              f"Rest: {self.cached_resting[self.market_2]} | Cycle: {self.current_increment[self.market_2]}/{self.contract_increment} | "
              f"Pos: {self.cached_position[self.market_2]}{fair_str}", end="")
        sys.stdout.flush()
    
    def format_status_data(self, instance_id: int) -> dict:
        """Format status data for WebSocket with queue positions"""
        return {
            "id": instance_id,
            "status": "running",
            "position": (self.cached_position.get(self.market_1, 0) or 0) + (self.cached_position.get(self.market_2, 0) or 0),
            "pnl": "+$0.00",
            "current_increment": {
                "m1": {"filled": self.current_increment.get(self.market_1, 0), "total": self.contract_increment},
                "m2": {"filled": self.current_increment.get(self.market_2, 0), "total": self.contract_increment}
            },
            "queue_positions": {
                self.market_1: self.cached_queue_position.get(self.market_1),
                self.market_2: self.cached_queue_position.get(self.market_2)
            }
        }
    
    async def run_trading_instance(self, instance_id: int, markets: list):
        """Main async trading loop for webapp"""
        last_status = time.time()
        
        while self.running and self.active:
            await self.process_redis_commands_async()
            
            if not self.active:
                break
            
            await self.refresh_market_data_async()
            self.check_fills()
            
            if not self.active:
                break
            
            if self.stopping:
                if await self.both_filled_async():
                    print("\n✓ Current cycle completed - stopping")
                    await self.cancel_all_orders_async()
                    self.active = False
                    break
                print("\r⏳ Waiting for current cycle to complete before stopping...", end="")
                sys.stdout.flush()
            elif self.paused:
                await self.update_orders_async()
            elif self.waiting_for_manual_resume:
                if await self.initialize_orders_async():
                    print("✓ Orders initialized")
                self.waiting_for_manual_resume = False
            elif await self.both_filled_async():
                if not self.active:
                    break
                await self.start_new_cycle_async()
            else:
                await self.update_orders_async()
            
            if time.time() - last_status >= 1:
                self.print_status()
                last_status = time.time()
            
            await asyncio.sleep(1)
        
        await self.close_session()
        print("\n✓ Trading stopped")