#!/usr/bin/env python3
"""
Single Market Automated YES/NO Market Maker - ASYNC
"""

import os
import sys
import time
import asyncio
from typing import Optional, Dict
from dotenv import load_dotenv
from app.modules.terminal.amm.kalshi_api import Config, MarketState
from app.modules.terminal.amm.mm_core import BaseMarketMaker

load_dotenv()


class SMTrader(BaseMarketMaker):
    """Single market YES/NO trader - ASYNC"""
    
    SIDES = ["yes", "no"]
    
    def __init__(self, api_key: str, api_secret: str, market_id: str, config: Config):
        super().__init__(api_key, api_secret, config)
        self.market_id = market_id
        self.market_state = MarketState(market_id=market_id)
        
        self.order_ids: Dict[str, Optional[str]] = {"yes": None, "no": None}
        self.last_prices: Dict[str, Optional[float]] = {"yes": None, "no": None}
        self.current_increment: Dict[str, int] = {"yes": 0, "no": 0}
        self.cycle_start_resting: Dict[str, int] = {"yes": 0, "no": 0}
        self.cached_resting: Dict[str, Optional[int]] = {"yes": None, "no": None}
        self.cached_position: Dict[str, Optional[int]] = {"yes": None, "no": None}
        self.cached_queue_position: Dict[str, Optional[int]] = {"yes": None, "no": None}
        self.fill_prices: Dict[str, Optional[float]] = {"yes": None, "no": None}
        
        self.one_side_first_mode = False
        self.active_side: Optional[str] = None
    
    def _get_markets(self):
        """Helper for Redis command processing"""
        return [self.market_id]
    
    async def refresh_market_data_async(self):
        """Fetch market data concurrently - 5 endpoints in parallel"""
        tasks = [
            self._request("GET", f"/markets/{self.market_id}/orderbook"),
            self._request("GET", f"/portfolio/orders?ticker={self.market_id}&status=resting"),
            self._request("GET", f"/portfolio/positions?ticker={self.market_id}&count_filter=position"),
            self._request("GET", "/portfolio/orders?status=resting"),  # Queue positions
            self._request("GET", f"/portfolio/fills?ticker={self.market_id}")
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Parse orderbook (index 0)
        if isinstance(results[0], dict) and results[0]:
            self.market_state.update_orderbook(results[0])
        
        # Parse resting orders (index 1)
        if isinstance(results[1], dict) and results[1]:
            orders = results[1].get("orders", [])
            for side in self.SIDES:
                self.cached_resting[side] = sum(
                    o.get("remaining_count", o.get("count", 0))
                    for o in orders if o.get("side") == side
                )
        
        # Parse positions (index 2)
        if isinstance(results[2], dict) and results[2]:
            self.cached_position["yes"] = self.cached_position["no"] = 0
            for pos in results[2].get("market_positions", []):
                if pos.get("ticker") == self.market_id:
                    position_val = pos.get("position", 0)
                    if position_val > 0:
                        self.cached_position["yes"] = position_val
                    elif position_val < 0:
                        self.cached_position["no"] = abs(position_val)
                    break
        
        # Parse queue positions (index 3)
        if isinstance(results[3], dict) and results[3]:
            orders = results[3].get("orders", [])
            
            # Reset queue positions
            self.cached_queue_position = {"yes": None, "no": None}
            
            # Match order_ids to sides and extract queue position
            for order in orders:
                order_id = order.get("order_id")
                ticker = order.get("ticker")
                side = order.get("side")
                queue_position = order.get("queue_position")
                
                if ticker == self.market_id and side in self.SIDES:
                    if self.order_ids.get(side) == order_id and queue_position is not None:
                        self.cached_queue_position[side] = queue_position
        
        # Update fair value if enabled
        if self.fair_value_enabled:
            if self.market_state.yes_bid and self.market_state.no_bid:
                self.update_fair_value(self.market_state.yes_bid, self.market_state.no_bid)
    
    async def cancel_all_orders_async(self):
        """Cancel all orders - ASYNC"""
        tasks = []
        for side in self.SIDES:
            if self.order_ids[side]:
                tasks.append(self.cancel_order(self.order_ids[side]))
        
        await asyncio.gather(*tasks, return_exceptions=True)
        
        for side in self.SIDES:
            self.order_ids[side] = self.last_prices[side] = None
    
    def get_bid_info(self, side: str):
        """Get bid info (synchronous)"""
        if side == "yes":
            return self.market_state.yes_bid, self.market_state.yes_bid_size, self.market_state.yes_second_bid
        return self.market_state.no_bid, self.market_state.no_bid_size, self.market_state.no_second_bid
    
    def get_market_spread(self) -> Optional[int]:
        """Get market spread (synchronous)"""
        if self.market_state.yes_bid and self.market_state.no_bid:
            return 100 - round(self.market_state.yes_bid * 100) - round(self.market_state.no_bid * 100)
        return None
    
    async def initialize_orders_async(self) -> bool:
        """Initialize orders - ASYNC"""
        success = True
        sides = [self.active_side] if self.one_side_first_mode else self.SIDES
        
        for side in sides:
            existing = self.cached_resting[side]
            if existing is None:
                print(f"⚠️  {side.upper()}: API error checking resting orders")
                success = False
                continue
            if existing > 0:
                print(f"⚠️  {side.upper()}: Already has {existing} resting orders")
                continue
            
            bid, bid_size, _ = self.get_bid_info(side)
            if bid is None:
                print(f"❌ {side.upper()}: No bid available")
                success = False
                continue
            
            target_price = bid
            
            if self.pennyif_mode and bid_size > 0:
                other_side = "no" if side == "yes" else "yes"
                other_bid, _, _ = self.get_bid_info(other_side)
                jump_target_cents = round(bid * 100) + 1
                
                if other_bid and jump_target_cents <= 99:
                    other_bid_cents = round(other_bid * 100)
                    potential_spread = (100 - jump_target_cents - other_bid_cents if side == "yes" 
                                      else 100 - other_bid_cents - jump_target_cents)
                    if potential_spread >= 3:
                        target_price = jump_target_cents / 100
            
            elif self.penny_mode and bid_size > 0:
                target_price = (round(bid * 100) + 1) / 100
            
            order_id = await self.place_order(self.market_id, side, target_price, self.contract_increment)
            if order_id:
                self.order_ids[side] = order_id
                self.last_prices[side] = target_price
                self.cycle_start_resting[side] = self.contract_increment
                print(f"✓ {side.upper()}: Placed {self.contract_increment} @ ${target_price:.2f}")
                await asyncio.sleep(0.2)
            else:
                print(f"❌ {side.upper()}: Failed to place order")
                success = False
        
        return success
    
    def check_fills(self):
        """Check fills (synchronous)"""
        sides = [self.active_side] if self.one_side_first_mode else self.SIDES
        
        for side in sides:
            resting = self.cached_resting[side]
            if resting is None:
                continue
            
            filled = self.cycle_start_resting[side] - resting
            if filled > self.current_increment[side]:
                if self.current_increment[side] < self.contract_increment and filled >= self.contract_increment:
                    self.fill_prices[side] = self.last_prices[side]
                self.current_increment[side] = filled
    
    async def rebalance_async(self, yes_pos: int, no_pos: int):
        """Rebalance positions - ASYNC"""
        print(f"\n⚠️ Position mismatch: YES={yes_pos}, NO={no_pos}")
        await self.cancel_all_orders_async()
        
        lagging_side = "yes" if yes_pos < no_pos else "no"
        diff = abs(yes_pos - no_pos)
        
        self.current_increment = {"yes": 0, "no": 0}
        self.order_ids = {"yes": None, "no": None}
        self.last_prices = {"yes": None, "no": None}
        self.cycle_start_resting = {"yes": 0, "no": 0}
        self.fill_prices = {"yes": None, "no": None}
        
        bid, _, _ = self.get_bid_info(lagging_side)
        if bid:
            order_id = await self.place_order(self.market_id, lagging_side, bid, diff)
            if order_id:
                self.order_ids[lagging_side] = order_id
                self.last_prices[lagging_side] = bid
                self.cycle_start_resting[lagging_side] = diff
                self.is_rebalancing = True
                print(f"✓ Rebalancing: Placed {diff} {lagging_side.upper()} @ ${bid:.2f}")
    
    async def both_filled_async(self) -> bool:
        """Check if both sides filled - ASYNC"""
        if self.one_side_first_mode:
            return self.current_increment[self.active_side] >= self.contract_increment
        
        if self.is_rebalancing:
            if any(self.order_ids[s] and (self.cached_resting[s] or 0) > 0 for s in self.SIDES):
                return False
            
            yes_pos, no_pos = self.cached_position["yes"], self.cached_position["no"]
            if yes_pos is None or no_pos is None:
                return False
            
            if yes_pos == no_pos:
                self.is_rebalancing = False
                return True
            
            await self.rebalance_async(yes_pos, no_pos)
            return False
        
        if not all(self.current_increment[s] >= self.contract_increment for s in self.SIDES):
            return False
        
        yes_pos, no_pos = self.cached_position["yes"], self.cached_position["no"]
        if yes_pos is None or no_pos is None:
            return False
        
        if yes_pos == no_pos:
            return True
        
        await self.rebalance_async(yes_pos, no_pos)
        return False
    
    async def start_new_cycle_async(self):
        """Start new cycle - ASYNC"""
        if not self.active:
            return
        
        yes_pos, no_pos = self.cached_position["yes"], self.cached_position["no"]
        
        if self.one_side_first_mode:
            print(f"\n✓ {self.active_side.upper()} filled {self.contract_increment} - Position: YES={yes_pos}, NO={no_pos}")
            self.active_side = "no" if self.active_side == "yes" else "yes"
        else:
            print(f"\n✓ Both sides filled {self.contract_increment} - Position: YES={yes_pos}, NO={no_pos}")
        
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
        
        self.current_increment = {"yes": 0, "no": 0}
        self.order_ids = {"yes": None, "no": None}
        self.last_prices = {"yes": None, "no": None}
        self.fill_prices = {"yes": None, "no": None}
        
        if await self.initialize_orders_async():
            status = f"on {self.active_side.upper()}" if self.one_side_first_mode else ""
            print(f"✓ New cycle initialized {status}")
        else:
            print("❌ Failed to start new cycle")
            if not self.one_side_first_mode and yes_pos != no_pos:
                await self.rebalance_async(yes_pos, no_pos)
    
    async def update_orders_async(self):
        """Update orders - ASYNC"""
        sides = [self.active_side] if self.one_side_first_mode else self.SIDES
        
        for side in sides:
            if not self.cached_resting[side]:
                continue
            
            bid, bid_size, second_bid = self.get_bid_info(side)
            other_side = "no" if side == "yes" else "yes"
            other_bid, _, _ = self.get_bid_info(other_side)
            
            target_price = self.check_target_price(
                side, bid, bid_size, second_bid, 
                self.last_prices[side], self.cached_resting[side] or 0, 
                other_bid
            )
            
            if target_price and self.last_prices[side]:
                if round(target_price * 100) != round(self.last_prices[side] * 100):
                    new_order_id = await self.modify_order(
                        self.market_id, side, target_price,
                        self.order_ids[side], self.cached_resting[side],
                        self.cached_position[side]
                    )
                    if new_order_id:
                        self.order_ids[side] = new_order_id
                        direction = "↑" if target_price > self.last_prices[side] else "↓"
                        spread_str = f" [Spread: {self.get_market_spread()}c]" if self.get_market_spread() else ""
                        print(f"\n{direction} {side.upper()}: Updated ${self.last_prices[side]:.2f} → ${target_price:.2f}{spread_str}")
                        self.last_prices[side] = target_price
                        self.cycle_start_resting[side] = self.current_increment[side] + self.cached_resting[side]
                    else:
                        self.order_ids[side] = self.last_prices[side] = None
    
    def print_status(self):
        """Print status (synchronous)"""
        spread_str = f" | Spread: {self.get_market_spread()}c" if self.get_market_spread() else ""
        mode_str = "PENNYIF" if self.pennyif_mode else ("PENNY" if self.penny_mode else "JOIN")
        side_str = f"[{self.active_side.upper()}]" if self.one_side_first_mode else ""
        rebal_str = " [REBAL]" if self.is_rebalancing else ""
        pause_str = " [PAUSED]" if self.paused or self.waiting_for_manual_resume else ""
        
        fair_str = ""
        if self.fair_value_enabled and self.current_fair_value:
            fair_str = f" | FV: ${self.current_fair_value:.2f}"
        
        print(f"\r[{mode_str}]{side_str}{rebal_str}{pause_str} YES: Bid ${self.market_state.yes_bid:.2f if self.market_state.yes_bid else 0} "
              f"({self.market_state.yes_bid_size}) | Rest: {self.cached_resting['yes']} | "
              f"Cycle: {self.current_increment['yes']}/{self.contract_increment} | Pos: {self.cached_position['yes']} || "
              f"NO: Bid ${self.market_state.no_bid:.2f if self.market_state.no_bid else 0} "
              f"({self.market_state.no_bid_size}) | Rest: {self.cached_resting['no']} | "
              f"Cycle: {self.current_increment['no']}/{self.contract_increment} | Pos: {self.cached_position['no']}{spread_str}{fair_str}", end="")
        sys.stdout.flush()
    
    def format_status_data(self, instance_id: int) -> dict:
        """Format status data for WebSocket with queue positions"""
        return {
            "id": instance_id,
            "status": "running",
            "position": (self.cached_position.get("yes", 0) or 0) + (self.cached_position.get("no", 0) or 0),
            "pnl": "+$0.00",
            "current_increment": {
                "yes": {"filled": self.current_increment.get("yes", 0), "total": self.contract_increment},
                "no": {"filled": self.current_increment.get("no", 0), "total": self.contract_increment}
            },
            "queue_positions": {
                "yes": self.cached_queue_position.get("yes"),
                "no": self.cached_queue_position.get("no")
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