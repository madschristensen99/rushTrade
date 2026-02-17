#!/usr/bin/env python3
import os
import requests
import threading
import time
from datetime import datetime
from typing import List, Dict

try:
    from dotenv import load_dotenv
    load_dotenv()
except:
    pass

try:
    from nba_api.live.nba.endpoints import scoreboard
except ImportError:
    import subprocess
    subprocess.run(["pip", "install", "nba-api", "--break-system-packages"], check=True)
    from nba_api.live.nba.endpoints import scoreboard


class Telegram:
    """Telegram notification service for trading updates"""

    def __init__(self, bot_token=None, chat_id=None):
        self.trader_instances_ref = None
        self.kalshi_client_ref = None
        self.bot_token = bot_token or os.getenv('TELEGRAM_BOT_TOKEN')
        self.chat_id = chat_id or os.getenv('TELEGRAM_CHAT_ID')

        if not self.bot_token or not self.chat_id:
            print("⚠️  Telegram credentials not configured")
            self.enabled = False
        else:
            self.enabled = True
            print("✅ Telegram notifications enabled")

        self.base_url = f"https://api.telegram.org/bot{self.bot_token}"
        self.active_positions = {}
        self.positions_lock = threading.Lock()
        self.last_update_id = 0
        self.status_update_interval = 300

        if self.enabled:
            self._send_connection_message()
            self.status_thread = threading.Thread(target=self._status_update_loop, daemon=True)
            self.status_thread.start()
            self.command_thread = threading.Thread(target=self._command_listener, daemon=True)
            self.command_thread.start()

    def _send_connection_message(self):
        message = """
🔌 *TRADING SYSTEM CONNECTED*

Strategies:
- System1: Underdog scale-out
- System2: Favorite drop (hold to settlement)

Commands:
- `/stop <id>` - Emergency stop trader
- `/status <mins>` - Set update interval
- `/show_positions` - Show open positions
- `/show_traders` - Show active traders

Time: {time}
""".format(time=datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        self._send_message(message.strip())

    def _command_listener(self):
        while True:
            try:
                response = requests.get(
                    f"{self.base_url}/getUpdates",
                    params={'offset': self.last_update_id + 1, 'timeout': 30},
                    timeout=35
                )
                if response.status_code == 200:
                    data = response.json()
                    if data.get('ok') and data.get('result'):
                        for update in data['result']:
                            self.last_update_id = update['update_id']
                            if 'message' in update and 'text' in update['message']:
                                text = update['message']['text'].strip()
                                chat_id = update['message']['chat']['id']
                                if str(chat_id) == str(self.chat_id):
                                    self._handle_command(text)
                time.sleep(1)
            except Exception as e:
                print(f"Command listener error: {e}")
                time.sleep(5)

    def _handle_command(self, text: str):
        text_lower = text.lower().strip()

        if text_lower.startswith('/stop'):
            parts = text_lower.split()
            if len(parts) == 2:
                try:
                    trader_id = int(parts[1])
                    self.emergency_stop_trader(trader_id)
                except ValueError:
                    self._send_message(f"❌ Invalid trader ID: {parts[1]}")
            else:
                self._send_message("❌ Usage: `/stop <trader_id>`")

        elif text_lower.startswith('/status'):
            parts = text_lower.split()
            if len(parts) == 2:
                try:
                    minutes = int(parts[1])
                    if 1 <= minutes <= 60:
                        self.status_update_interval = minutes * 60
                        self._send_message(f"✅ Status updates set to every {minutes} minute(s)")
                    else:
                        self._send_message("❌ Interval must be 1-60 minutes")
                except ValueError:
                    self._send_message(f"❌ Invalid minutes: {parts[1]}")
            else:
                self._send_message(f"Current: {self.status_update_interval // 60} min\nUsage: `/status <minutes>`")

        elif text_lower == '/show_positions':
            self.show_positions()

        elif text_lower == '/show_traders':
            self.show_traders()

    def emergency_stop_trader(self, trader_id: int):
        if self.trader_instances_ref is None:
            self._send_message("❌ Trader instances not initialized")
            return

        if trader_id in self.trader_instances_ref:
            trader = self.trader_instances_ref[trader_id]
            trader.emergency_stop()
            self._send_message(f"🚨 *EMERGENCY STOP*\n\nTrader #{trader_id} closing...")
        else:
            active = list(self.trader_instances_ref.keys())
            self._send_message(f"❌ Trader #{trader_id} not found\nActive: {active or 'None'}")

    def show_positions(self):
        try:
            import positions_ledger as ledger
            positions = ledger.get_all_open_positions()

            if not positions:
                self._send_message("ℹ️ *NO OPEN POSITIONS*")
                return

            lines = ["📊 *OPEN POSITIONS*\n"]
            total = 0

            for pos in positions:
                ticker = pos['ticker']
                open_odds = float(pos['open_odds'])
                entry_amount = float(pos['entry_amount'])
                market_name = ticker.split('-')[-1] if '-' in ticker else ticker
                lines.append(f"*{market_name}*\n  Entry: {open_odds:.0f}¢ | ${entry_amount:.2f}\n")
                total += entry_amount

            lines.append(f"\n*Total:* ${total:.2f}")
            lines.append(f"\n_{datetime.now().strftime('%H:%M:%S')}_")
            self._send_message("\n".join(lines))

        except Exception as e:
            self._send_message(f"❌ Error: {str(e)}")

    def show_traders(self):
        if self.trader_instances_ref is None:
            self._send_message("❌ Trader instances not initialized")
            return

        try:
            if not self.trader_instances_ref:
                self._send_message("ℹ️ *NO ACTIVE TRADERS*")
                return

            lines = ["🤖 *ACTIVE TRADERS*\n"]

            for trader_id, trader in self.trader_instances_ref.items():
                ticker = trader.ticker
                market_name = ticker.split('-')[-1] if '-' in ticker else ticker

                # Detect trader type
                is_fav_trader = hasattr(trader, 'favorite_side')
               
                if is_fav_trader:
                    mode = "SYSTEM2"
                    side = trader.favorite_side.upper()
                else:
                    mode = "SYSTEM1"
                    side = "NO"

                # Status
                if trader.hedged or trader.fully_closed:
                    status = "✅ Closed"
                elif trader.entry_filled:
                    if hasattr(trader, 'scale_out_count') and trader.scale_out_count > 0:
                        status = f"📊 Scaling ({trader.scale_out_count})"
                    else:
                        status = "🔵 Active"
                else:
                    status = "⏳ Pending"

                lines.append(
                    f"*#{trader_id}* | {mode} | {side}\n"
                    f"  `{market_name}`\n"
                    f"  {status}\n"
                )

            lines.append(f"\n_{datetime.now().strftime('%H:%M:%S')}_")
            self._send_message("\n".join(lines))

        except Exception as e:
            self._send_message(f"❌ Error: {str(e)}")

    def _get_game_status(self, ticker: str):
        try:
            parts = ticker.split('-')
            if len(parts) < 2:
                return "Unknown"

            teams_str = parts[1][7:]
            if len(teams_str) != 6:
                return "Unknown"

            team1, team2 = teams_str[:3], teams_str[3:]
            games = scoreboard.ScoreBoard()
            games_dict = games.get_dict()

            for game in games_dict.get('scoreboard', {}).get('games', []):
                home = game.get('homeTeam', {}).get('teamTricode', '')
                away = game.get('awayTeam', {}).get('teamTricode', '')

                if {home, away} == {team1, team2}:
                    status = game.get('gameStatus')
                    if status == 1:
                        return "Pregame"
                    elif status == 3:
                        return "Final"
                    elif status == 2:
                        period = game.get('period', 0)
                        clock = game.get('gameClock', '')
                        qtr = f"{period}Q" if period <= 4 else f"OT{period-4}" if period > 5 else "OT"
                        if clock and clock.startswith('PT'):
                            clock = clock.replace('PT', '').replace('M', ':').replace('S', '')
                            return f"{qtr} {clock}"
                        return qtr
            return "Not Found"
        except:
            return "Error"

    def _send_message(self, text: str, parse_mode: str = "Markdown"):
        if not self.enabled:
            return False
        try:
            response = requests.post(
                f"{self.base_url}/sendMessage",
                json={'chat_id': self.chat_id, 'text': text, 'parse_mode': parse_mode},
                timeout=5
            )
            return response.status_code == 200
        except Exception as e:
            print(f"Telegram error: {e}")
            return False

    # ============================================================
    # SYSTEM1 NOTIFICATIONS (Underdog strategy)
    # ============================================================

    def notify_position_open(self, ticker: str, entry_odds: float, stake: float,
                            trader_id=None, enable_scale_out=True):
        with self.positions_lock:
            self.active_positions[ticker] = {
                'entry_odds': entry_odds, 'stake': stake, 'entry_time': datetime.now(),
                'current_odds': entry_odds, 'trader_id': trader_id,
                'enable_scale_out': enable_scale_out, 'scale_outs': 0, 'strategy': 'system1'
            }

        team = ticker.split('-')[-1] if '-' in ticker else ''
        trader_label = f"Trader #{trader_id}\n" if trader_id else ""
        mode = "SCALE-OUT" if enable_scale_out else "HEDGE"

        message = f"""
🟢 *NEW POSITION* - SYSTEM1 {mode}

{trader_label}Market: `{ticker}`
Team: {team}
Entry: NO @ {entry_odds}¢
Stake: ${stake:.2f}

Time: {datetime.now().strftime('%H:%M:%S')}
"""
        self._send_message(message.strip())

    def notify_duplicate_position(self, ticker: str, contracts: int, entry_price: float, trader_id=None):
        with self.positions_lock:
            self.active_positions[ticker] = {
                'entry_odds': entry_price, 'stake': (contracts * entry_price) / 100,
                'entry_time': datetime.now(), 'current_odds': entry_price,
                'trader_id': trader_id, 'enable_scale_out': True, 'scale_outs': 0,
                'strategy': 'system1'
            }

        team = ticker.split('-')[-1] if '-' in ticker else ''
        trader_label = f"Trader #{trader_id}\n" if trader_id else ""

        message = f"""
🔄 *DUPLICATE DETECTED* - SYSTEM1

{trader_label}Market: `{ticker}`
Team: {team}
Position: {contracts} @ {entry_price}¢
Continuing monitoring...

Time: {datetime.now().strftime('%H:%M:%S')}
"""
        self._send_message(message.strip())

    def notify_scale_out(self, ticker: str, exit_odds: float, contracts_sold: int,
                        remaining: int, pnl: float, reason: str, trader_id=None):
        with self.positions_lock:
            if ticker in self.active_positions:
                self.active_positions[ticker]['scale_outs'] += 1
                self.active_positions[ticker]['current_odds'] = exit_odds

        team = ticker.split('-')[-1] if '-' in ticker else ''
        trader_label = f"Trader #{trader_id}\n" if trader_id else ""
        emoji = "📊" if reason == "SCALE_OUT" else "⚠️"

        message = f"""
{emoji} *{reason.replace('_', ' ')}* - SYSTEM1

{trader_label}Market: `{ticker}`
Team: {team}
Sold: {contracts_sold} @ {exit_odds}¢
Remaining: {remaining}
PnL: ${pnl:+.2f}

Time: {datetime.now().strftime('%H:%M:%S')}
"""
        self._send_message(message.strip())

    def notify_position_close(self, ticker: str, exit_odds: float, pnl: float,
                             reason: str, trader_id=None):
        with self.positions_lock:
            if ticker in self.active_positions:
                del self.active_positions[ticker]

        team = ticker.split('-')[-1] if '-' in ticker else ''
        trader_label = f"Trader #{trader_id}\n" if trader_id else ""
        emoji = "✅" if pnl >= 0 else "❌"
        if reason == "EMERGENCY_STOP":
            emoji = "🚨"

        message = f"""
{emoji} *CLOSED* - {reason.replace('_', ' ')}

{trader_label}Market: `{ticker}`
Team: {team}
Exit: {exit_odds}¢
PnL: ${pnl:+.2f}

Time: {datetime.now().strftime('%H:%M:%S')}
"""
        self._send_message(message.strip())

    def notify_error(self, ticker: str, error: str, trader_id=None):
        trader_label = f"Trader #{trader_id}\n" if trader_id else ""
        message = f"""
⚠️ *ERROR*

{trader_label}Market: `{ticker}`
Error: {error}

Time: {datetime.now().strftime('%H:%M:%S')}
"""
        self._send_message(message.strip())

    def update_position_odds(self, ticker: str, current_odds: float, trader_id=None):
        with self.positions_lock:
            if ticker in self.active_positions:
                self.active_positions[ticker]['current_odds'] = current_odds

    # ============================================================
    # SYSTEM2 NOTIFICATIONS (Favorite drop strategy)
    # ============================================================

    def notify_fav_order_placed(self, ticker: str, entry_trigger: float, contracts: int,
                                opening_odds: float, favorite_side: str, trader_id=None):
        """Notify when resting limit order is placed"""
        with self.positions_lock:
            self.active_positions[ticker] = {
                'entry_odds': entry_trigger, 'stake': contracts * (entry_trigger / 100),
                'entry_time': datetime.now(), 'current_odds': opening_odds,
                'trader_id': trader_id, 'enable_scale_out': False, 'scale_outs': 0,
                'strategy': 'system2', 'status': 'pending'
            }

        team = ticker.split('-')[-1] if '-' in ticker else ''
        trader_label = f"Trader #{trader_id}\n" if trader_id else ""

        message = f"""
📋 *RESTING ORDER PLACED* - SYSTEM2

{trader_label}Market: `{ticker}`
Team: {team}
Side: {favorite_side.upper()} (Favorite)
Opening: {opening_odds}¢
Trigger: {entry_trigger}¢ (-5¢ drop)
Contracts: {contracts}

Waiting for fill through Q2...

Time: {datetime.now().strftime('%H:%M:%S')}
"""
        self._send_message(message.strip())

    def notify_fav_order_filled(self, ticker: str, entry_odds: float, contracts: int,
                                fill_time: datetime, elapsed_mins: float, trader_id=None):
        """Notify when order is filled (Q1/Q2)"""
        with self.positions_lock:
            if ticker in self.active_positions:
                self.active_positions[ticker]['status'] = 'filled'
                self.active_positions[ticker]['entry_time'] = fill_time

        team = ticker.split('-')[-1] if '-' in ticker else ''
        trader_label = f"Trader #{trader_id}\n" if trader_id else ""
       
        # Determine quarter
        if elapsed_mins < 20:
            quarter = "Q1"
        elif elapsed_mins < 45:
            quarter = "Q2"
        else:
            quarter = "Late Q2"

        message = f"""
✅ *ORDER FILLED* - SYSTEM2

{trader_label}Market: `{ticker}`
Team: {team}
Entry: {entry_odds}¢
Contracts: {contracts}
Quarter: {quarter}

Fill Time: {fill_time.strftime('%H:%M:%S')}
Elapsed: {elapsed_mins:.0f} mins

Holding to settlement...
"""
        self._send_message(message.strip())

    def notify_fav_order_canceled(self, ticker: str, entry_trigger: float,
                                  elapsed_mins: float, trader_id=None):
        """Notify when order is canceled at halftime"""
        with self.positions_lock:
            if ticker in self.active_positions:
                del self.active_positions[ticker]

        team = ticker.split('-')[-1] if '-' in ticker else ''
        trader_label = f"Trader #{trader_id}\n" if trader_id else ""

        message = f"""
⏰ *ORDER CANCELED* - SYSTEM2 HALFTIME

{trader_label}Market: `{ticker}`
Team: {team}
Trigger: {entry_trigger}¢ (not reached)
Elapsed: {elapsed_mins:.0f} mins

Order canceled - favorite never dropped to trigger.

Time: {datetime.now().strftime('%H:%M:%S')}
"""
        self._send_message(message.strip())

    def notify_fav_settlement(self, ticker: str, entry_odds: float, settlement_price: int,
                             won: bool, pnl: float, trader_id=None):
        """Notify settlement result (win/loss)"""
        with self.positions_lock:
            if ticker in self.active_positions:
                del self.active_positions[ticker]

        team = ticker.split('-')[-1] if '-' in ticker else ''
        trader_label = f"Trader #{trader_id}\n" if trader_id else ""
       
        if won:
            emoji = "🏆"
            result = "WIN"
        else:
            emoji = "❌"
            result = "LOSS"

        message = f"""
{emoji} *SETTLED* - SYSTEM2 {result}

{trader_label}Market: `{ticker}`
Team: {team}
Entry: {entry_odds}¢ → {settlement_price}¢
PnL: ${pnl:+.2f}

Time: {datetime.now().strftime('%H:%M:%S')}
"""
        self._send_message(message.strip())

    def notify_fav_emergency_exit(self, ticker: str, entry_odds: float, exit_odds: float,
                                  pnl: float, trader_id=None):
        """Notify emergency exit (sold position early)"""
        with self.positions_lock:
            if ticker in self.active_positions:
                del self.active_positions[ticker]

        team = ticker.split('-')[-1] if '-' in ticker else ''
        trader_label = f"Trader #{trader_id}\n" if trader_id else ""

        message = f"""
🚨 *EMERGENCY EXIT* - SYSTEM2

{trader_label}Market: `{ticker}`
Team: {team}
Entry: {entry_odds}¢ → Exit: {exit_odds}¢
PnL: ${pnl:+.2f}

Position sold early via emergency stop.

Time: {datetime.now().strftime('%H:%M:%S')}
"""
        self._send_message(message.strip())

    def notify_fav_duplicate(self, ticker: str, contracts: int, entry_price: float, trader_id=None):
        """Notify duplicate favorite position detected"""
        with self.positions_lock:
            self.active_positions[ticker] = {
                'entry_odds': entry_price, 'stake': contracts * (entry_price / 100),
                'entry_time': datetime.now(), 'current_odds': entry_price,
                'trader_id': trader_id, 'enable_scale_out': False, 'scale_outs': 0,
                'strategy': 'system2', 'status': 'filled'
            }

        team = ticker.split('-')[-1] if '-' in ticker else ''
        trader_label = f"Trader #{trader_id}\n" if trader_id else ""

        message = f"""
🔄 *DUPLICATE DETECTED* - SYSTEM2

{trader_label}Market: `{ticker}`
Team: {team}
Position: {contracts} @ {entry_price}¢
Waiting for settlement...

Time: {datetime.now().strftime('%H:%M:%S')}
"""
        self._send_message(message.strip())

    def notify_fav_error(self, ticker: str, error: str, trader_id=None):
        """Notify error in favorite strategy"""
        trader_label = f"Trader #{trader_id}\n" if trader_id else ""
        message = f"""
⚠️ *ERROR* - SYSTEM2

{trader_label}Market: `{ticker}`
Error: {error}

Time: {datetime.now().strftime('%H:%M:%S')}
"""
        self._send_message(message.strip())

    # ============================================================
    # STATUS UPDATES
    # ============================================================

    def send_status_update(self):
        if not self.enabled:
            return

        with self.positions_lock:
            if not self.active_positions:
                return
            positions = list(self.active_positions.items())

        lines = ["📊 *POSITION STATUS*\n"]
        total_pnl = 0
        to_remove = []

        for ticker, info in positions:
            entry = info['entry_odds']
            current = info['current_odds']
            stake = info['stake']
            duration = (datetime.now() - info['entry_time']).total_seconds() / 60
            trader_id = info.get('trader_id')
            strategy = info.get('strategy', 'system1')

            game_status = self._get_game_status(ticker)
            team = ticker.split('-')[-1] if '-' in ticker else ''

            if game_status == "Final":
                to_remove.append(ticker)
                continue

            move = current - entry
            unrealized = (move / entry) * stake if entry > 0 else 0
            total_pnl += unrealized

            status_emoji = "🟢" if move >= 0 else "🔴"
            trader_label = f"#{trader_id} " if trader_id else ""
            strat_badge = "S2" if strategy == 'system2' else "S1"

            lines.append(
                f"{status_emoji} {trader_label}*{strat_badge}* *{team}* | {game_status}\n"
                f"  Entry: {entry}¢ → {current}¢ ({move:+.0f}¢)\n"
                f"  PnL: ${unrealized:+.2f} | {duration:.0f}m\n"
            )

        if to_remove:
            with self.positions_lock:
                for t in to_remove:
                    if t in self.active_positions:
                        del self.active_positions[t]

        if len(lines) > 1:
            lines.append(f"\n*Unrealized:* ${total_pnl:+.2f}")
            lines.append(f"\n_{datetime.now().strftime('%H:%M:%S')}_")
            self._send_message("\n".join(lines))

    def _status_update_loop(self):
        while True:
            time.sleep(self.status_update_interval)
            self.send_status_update()

    def notify_system_start(self, markets_count: int, trader_type: str = "paper"):
        mode = "LIVE" if trader_type == "live" else "PAPER"
        message = f"""
🚀 *SYSTEM STARTED* - {mode}

Markets: {markets_count}
Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
        self._send_message(message.strip())

    def notify_system_stop(self):
        with self.positions_lock:
            count = len(self.active_positions)
        message = f"""
🛑 *SYSTEM STOPPED*

Open Positions: {count}
Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
        self._send_message(message.strip())
