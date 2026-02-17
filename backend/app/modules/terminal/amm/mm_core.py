#!/usr/bin/env python3
"""
Core market making functions - ASYNC support added
"""

import sys
import time
import json
import asyncio
from typing import Optional, Dict
from collections import deque
from app.modules.terminal.auto.kalshi_api import KalshiAPITrader


class BaseMarketMaker(KalshiAPITrader):
    """Base market maker with shared logic"""
    
    def __init__(self, api_key: str, api_secret: str, config):
        super().__init__(api_key, api_secret, config)
        
        self.running = False
        self.stopping = False
        self.active = False
        self.paused = False
        self.waiting_for_manual_resume = False
        self.penny_mode = False
        self.pennyif_mode = False
        self.is_rebalancing = False
        self.contract_increment = 3
        
        # Single fire
        self.single_fire_mode = False
        self.single_fire_cycles_completed = 0
        
        # Fair value tracking
        self.fair_value_enabled = False
        self.fair_value_history = deque(maxlen=10)
        self.current_fair_value = None
        
        # Bump mode support
        self.bump_active: Dict[str, bool] = {}
        self.bump_target: Dict[str, Optional[int]] = {}
        self.instance_id: Optional[int] = None
        self.redis_client = None
    
    def toggle_bump(self, market_id: str):
        """Toggle bump mode for a market"""
        if self.penny_mode:
            print(f"\n❌ Bump not available in Penny mode")
            return
        
        self.bump_active[market_id] = not self.bump_active.get(market_id, False)
        if not self.bump_active[market_id]:
            self.bump_target[market_id] = None
        
        status = "ON" if self.bump_active[market_id] else "OFF"
        market_label = market_id[-7:] if len(market_id) > 7 else market_id
        print(f"\n{'🔼' if self.bump_active[market_id] else '🔽'} {market_label}: Bump {status}")
    
    def update_fair_value(self, yes_bid: Optional[float], no_bid: Optional[float]):
        """Update fair value with rolling average"""
        if yes_bid is not None and no_bid is not None:
            midpoint = (yes_bid + no_bid) / 2
            self.fair_value_history.append(midpoint)
            
            if len(self.fair_value_history) > 0:
                self.current_fair_value = sum(self.fair_value_history) / len(self.fair_value_history)
    
    async def process_redis_commands_async(self):
        """Process commands from Redis - ASYNC"""
        if not self.redis_client or not self.instance_id:
            return
        
        command_key = f"trading:instance:{self.instance_id}:command"
        while True:
            cmd_data = self.redis_client.lpop(command_key)
            if not cmd_data:
                break
            
            try:
                cmd = json.loads(cmd_data)
                action = cmd.get("action")
                
                if action == "toggle_bump":
                    market_idx = cmd.get("market_index", 0)
                    markets = getattr(self, '_get_markets', lambda: [])()
                    if markets and market_idx < len(markets):
                        self.toggle_bump(markets[market_idx])
                
                elif action == "toggle_pause":
                    if self.paused or self.waiting_for_manual_resume:
                        print("\n▶️  Resuming trading")
                        self.paused = False
                        self.waiting_for_manual_resume = False
                    else:
                        print("\n⏸️  Pausing trading")
                        self.paused = True
                
                elif action == "single_fire":
                    if not (self.paused or self.waiting_for_manual_resume):
                        print("\n⚠️  Already in continuous mode")
                    else:
                        print("\n🎯 Single fire mode")
                        self.single_fire_mode = True
                        self.single_fire_cycles_completed = 0
                        self.paused = False
                
                elif action == "set_mode":
                    mode = cmd.get("mode", "join")
                    if mode == "join":
                        self.penny_mode = False
                        self.pennyif_mode = False
                        print("\n📍 Mode: JOIN")
                    elif mode == "jump":
                        self.penny_mode = True
                        self.pennyif_mode = False
                        print("\n📍 Mode: JUMP")
                    elif mode == "pennyif":
                        self.penny_mode = False
                        self.pennyif_mode = True
                        print("\n📍 Mode: PENNYIF")
                
                elif action == "toggle_fair_value":
                    self.fair_value_enabled = not self.fair_value_enabled
                    if not self.fair_value_enabled:
                        self.fair_value_history.clear()
                        self.current_fair_value = None
                    status = "ON" if self.fair_value_enabled else "OFF"
                    print(f"\n📊 Fair Value: {status}")
                
                elif action == "stop":
                    print("\n⏸️  Stop requested - will complete current cycle")
                    self.stopping = True
                    
                elif action == "force_stop":
                    print("\n⛔ Force stop received")
                    self.active = False
                    self.stopping = False
                    
            except Exception as e:
                print(f"Error processing command: {e}")
    
    def process_redis_commands(self):
        """Process commands from Redis - SYNC (for standalone scripts)"""
        if not self.redis_client or not self.instance_id:
            return
        
        command_key = f"trading:instance:{self.instance_id}:command"
        while True:
            cmd_data = self.redis_client.lpop(command_key)
            if not cmd_data:
                break
            
            try:
                cmd = json.loads(cmd_data)
                action = cmd.get("action")
                
                if action == "toggle_bump":
                    market_idx = cmd.get("market_index", 0)
                    markets = getattr(self, '_get_markets', lambda: [])()
                    if markets and market_idx < len(markets):
                        self.toggle_bump(markets[market_idx])
                
                elif action == "toggle_pause":
                    if self.paused or self.waiting_for_manual_resume:
                        print("\n▶️  Resuming trading")
                        self.paused = False
                        self.waiting_for_manual_resume = False
                    else:
                        print("\n⏸️  Pausing trading")
                        self.paused = True
                
                elif action == "single_fire":
                    if not (self.paused or self.waiting_for_manual_resume):
                        print("\n⚠️  Already in continuous mode")
                    else:
                        print("\n🎯 Single fire mode")
                        self.single_fire_mode = True
                        self.single_fire_cycles_completed = 0
                        self.paused = False
                
                elif action == "set_mode":
                    mode = cmd.get("mode", "join")
                    if mode == "join":
                        self.penny_mode = False
                        self.pennyif_mode = False
                        print("\n📍 Mode: JOIN")
                    elif mode == "jump":
                        self.penny_mode = True
                        self.pennyif_mode = False
                        print("\n📍 Mode: JUMP")
                    elif mode == "pennyif":
                        self.penny_mode = False
                        self.pennyif_mode = True
                        print("\n📍 Mode: PENNYIF")
                
                elif action == "toggle_fair_value":
                    self.fair_value_enabled = not self.fair_value_enabled
                    if not self.fair_value_enabled:
                        self.fair_value_history.clear()
                        self.current_fair_value = None
                    status = "ON" if self.fair_value_enabled else "OFF"
                    print(f"\n📊 Fair Value: {status}")
                
                elif action == "stop":
                    print("\n⏸️  Stop requested - will complete current cycle")
                    self.stopping = True
                    
                elif action == "force_stop":
                    print("\n⛔ Force stop received - cancelling orders immediately")
                    self.cancel_all_orders()
                    self.active = False
                    self.stopping = False
                    
            except Exception as e:
                print(f"Error processing command: {e}")
    
    def check_target_price(self, side: str, bid: float, bid_size: int, 
                          second_bid: Optional[float], current_price: Optional[float],
                          our_resting: int, other_bid: Optional[float] = None,
                          market_id: Optional[str] = None) -> Optional[float]:
        """Determine target price with bump mode support"""
        if bid is None:
            return None
        
        bid_cents = round(bid * 100)
        current_cents = round(current_price * 100) if current_price else None
        
        if bid_size > our_resting:
            others_best_cents = bid_cents
        elif second_bid:
            others_best_cents = round(second_bid * 100)
        else:
            others_best_cents = None
        
        # BUMP MODE (only in join mode, requires market_id)
        if market_id and self.bump_active.get(market_id, False):
            if self.bump_target[market_id] is None and others_best_cents:
                self.bump_target[market_id] = others_best_cents + 1
            
            target = self.bump_target[market_id]
            if target and others_best_cents and others_best_cents >= target:
                market_label = market_id[-7:] if len(market_id) > 7 else market_id
                print(f"\n⚠️ {market_label}: Bump disabled - others at/above ${target/100:.2f}")
                self.bump_active[market_id] = False
                self.bump_target[market_id] = None
            elif target and target <= 99:
                return target / 100
        
        # PENNYIF MODE
        if self.pennyif_mode and others_best_cents and other_bid:
            jump_target_cents = others_best_cents + 1
            other_bid_cents = round(other_bid * 100)
            
            if jump_target_cents <= 99:
                potential_spread = (100 - jump_target_cents - other_bid_cents if side == "yes" 
                                  else 100 - other_bid_cents - jump_target_cents)
                if potential_spread >= 3:
                    if current_cents is None or others_best_cents > current_cents:
                        return jump_target_cents / 100
            
            if bid_size <= our_resting and current_cents and others_best_cents < current_cents:
                return current_price
            return current_price if current_price else bid
        
        # PENNY MODE
        if self.penny_mode and others_best_cents:
            if current_cents is None or others_best_cents > current_cents:
                jump_target_cents = others_best_cents + 1
                if other_bid:
                    other_bid_cents = round(other_bid * 100)
                    if jump_target_cents + other_bid_cents <= 99 and jump_target_cents <= 99:
                        return jump_target_cents / 100
                return bid
            else:
                if bid_size <= our_resting and current_cents and others_best_cents < current_cents:
                    jump_target_cents = others_best_cents + 1
                    if other_bid:
                        other_bid_cents = round(other_bid * 100)
                        if jump_target_cents + other_bid_cents <= 99 and jump_target_cents <= 99:
                            return jump_target_cents / 100
                    return bid
                return current_price
        
        if self.penny_mode:
            return current_price if current_price else bid
        
        # JOIN MODE
        if current_cents and bid_cents > current_cents:
            return bid
        if bid_size > our_resting:
            return bid
        if second_bid:
            return second_bid
        return bid
    
    def trading_loop(self):
        """Main trading loop"""
        last_status = time.time()
        
        while self.running and self.active:
            self.process_redis_commands()
            
            if not self.active:
                break
            
            self.refresh_market_data()
            self.check_fills()

            if not self.active:
                break
            
            if self.stopping:
                if self.both_filled():
                    print("\n✓ Current cycle completed - stopping")
                    self.cancel_all_orders()
                    self.active = False
                    break
                print("\r⏳ Waiting for current cycle to complete before stopping...", end="")
                sys.stdout.flush()
            elif self.paused:
                self.update_orders()
            elif self.waiting_for_manual_resume:
                if self.initialize_orders():
                    print("✓ Orders initialized")
                self.waiting_for_manual_resume = False
            elif self.both_filled():
                if not self.active:
                    break
                self.start_new_cycle()
            else:
                self.update_orders()
            
            if time.time() - last_status >= 1:
                self.print_status()
                last_status = time.time()
            
            time.sleep(1)
        
        print("\n✓ Trading stopped")
    
    def stop_trading(self):
        """Stop trading"""
        if not self.active:
            print("⚠️  Not currently trading")
            return
        print("\n⏸️  Stopping... waiting for current cycle to complete")
        self.stopping = True