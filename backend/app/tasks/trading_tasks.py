from celery import Celery
import time
import json
import asyncio
import redis.asyncio as redis

from app.config import get_settings

settings = get_settings()

celery = Celery(
    'trading_tasks',
    broker=settings.CELERY_BROKER_URL,
    backend=settings.redis_url
)


async def get_user_credentials(user_id: int):
    """Get credentials from Redis session"""
    redis_client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    session_key = f"user:{user_id}:credentials"
    session_data = await redis_client.get(session_key)
    await redis_client.close()
    
    if not session_data:
        raise ValueError("Session expired. Credentials not found in Redis.")
    
    data = json.loads(session_data)
    from app.modules.terminal.auto.crypto import CryptoService
    crypto = CryptoService(master_key=settings.SECRET_KEY)
    
    return {
        "api_key": crypto.decrypt(data["api_key"]), 
        "private_key": crypto.decrypt(data["rsa_key"])
    }


@celery.task(bind=True, name='trading_tasks.start_trading_instance')
def start_trading_instance(self, instance_id: int, user_id: int, script_type: str, markets: list, config: dict):
    import redis as sync_redis
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        # Conditional import
        if script_type == "single_market":
            from app.modules.terminal.auto.auto1 import SMTrader as AutomatedTrader
        else:
            from app.modules.terminal.auto.auto2 import AutomatedTrader
        
        from app.modules.terminal.auto.kalshi_api import Config
        
        credentials = loop.run_until_complete(get_user_credentials(user_id))
        redis_client_async = redis.Redis.from_url(settings.redis_url, decode_responses=True)
        
        # Initialize trader based on script type
        if script_type == "single_market":
            trader = AutomatedTrader(
                api_key=credentials["api_key"],
                api_secret=credentials["private_key"],
                market_id=markets[0],
                config=Config()
            )
        else:
            trader = AutomatedTrader(
                api_key=credentials["api_key"],
                api_secret=credentials["private_key"],
                market_1=markets[0],
                market_2=markets[1] if len(markets) > 1 else markets[0],
                config=Config()
            )
        
        trader.redis_client = sync_redis.Redis.from_url(settings.redis_url, decode_responses=True)
        trader.instance_id = instance_id
        trader.contract_increment = config["contract_increment"]
        trader.penny_mode = config.get("jump_mode", False)
        
        if script_type == "single_market":
            trader.one_side_first_mode = (config.get("market_priority") == "expensive")
        else:
            trader.higher_first_mode = (config.get("market_priority") == "expensive")
        
        # Set flags
        trader.running = True
        trader.active = True
        
        loop.run_until_complete(trader.refresh_market_data_async())
        loop.run_until_complete(trader.initialize_orders_async())
        
        async def trading_loop_with_status():
            while trader.running and trader.active:
                await trader.process_redis_commands_async()
                
                if not trader.active:
                    break
                
                await trader.refresh_market_data_async()
                trader.check_fills()
                
                if not trader.active:
                    break
                
                if trader.stopping:
                    if await trader.both_filled_async():
                        await trader.cancel_all_orders_async()
                        trader.active = False
                        break
                elif trader.paused:
                    await trader.update_orders_async()
                elif trader.waiting_for_manual_resume:
                    if await trader.initialize_orders_async():
                        pass
                    trader.waiting_for_manual_resume = False
                elif await trader.both_filled_async():
                    if not trader.active:
                        break
                    await trader.start_new_cycle_async()
                else:
                    await trader.update_orders_async()
                
                status_data = await format_status(trader, instance_id, markets, script_type)
                await redis_client_async.publish(
                    f"trading:instance:{instance_id}:updates",
                    json.dumps(status_data)
                )
                
                await asyncio.sleep(1)
            
            await trader.close_session()
        
        loop.run_until_complete(trading_loop_with_status())
        
        trader.redis_client.close()
        loop.run_until_complete(redis_client_async.close())
        return {'status': 'completed'}
    except Exception as e:
        import traceback
        error_detail = f"{type(e).__name__}: {str(e)}\n{traceback.format_exc()}"
        print(f"CELERY ERROR: {error_detail}")
        return {'status': 'error', 'error': error_detail}
    finally:
        loop.close()


async def format_status(trader, instance_id, markets, script_type):
    """Format status with full 5-level orderbook"""
    if script_type == "single_market":
        m = markets[0]
        ob = await trader._request("GET", f"/markets/{m}/orderbook")
        
        def format_side(side):
            if not ob or 'orderbook' not in ob:
                return {"side": side.upper(), "last_traded": 0, "volume": 0, "resting_order": None, "queue_position": None, "bids": [], "asks": []}
            
            orderbook = ob['orderbook']
            
            if side == "yes":
                yes_levels = [lvl for lvl in orderbook.get('yes', []) if lvl[1] > 0]
                yes_sorted = sorted(yes_levels, key=lambda x: x[0], reverse=True)[:5]
                bids = [{"price": lvl[0], "size": lvl[1]} for lvl in yes_sorted]
                
                no_levels = [lvl for lvl in orderbook.get('no', []) if lvl[1] > 0]
                no_sorted = sorted(no_levels, key=lambda x: x[0], reverse=True)[:5]
                asks = [{"price": 100 - lvl[0], "size": lvl[1]} for lvl in no_sorted]
            else:  # no
                no_levels = [lvl for lvl in orderbook.get('no', []) if lvl[1] > 0]
                no_sorted = sorted(no_levels, key=lambda x: x[0], reverse=True)[:5]
                bids = [{"price": lvl[0], "size": lvl[1]} for lvl in no_sorted]
                
                yes_levels = [lvl for lvl in orderbook.get('yes', []) if lvl[1] > 0]
                yes_sorted = sorted(yes_levels, key=lambda x: x[0], reverse=True)[:5]
                asks = [{"price": 100 - lvl[0], "size": lvl[1]} for lvl in yes_sorted]
            
            last_traded = bids[0]['price'] if bids else 0
            
            resting_price = trader.last_prices.get(side)
            resting_qty = trader.cached_resting.get(side, 0) or 0
            resting_order = None
            
            if resting_price and resting_qty > 0:
                resting_price_cents = int(resting_price * 100) if resting_price < 1 else int(resting_price)
                if any(b['price'] == resting_price_cents for b in bids):
                    resting_order = {"price_level": resting_price_cents, "quantity": resting_qty}
            
            queue_pos = trader.cached_queue_position.get(side)
            queue_position = None
            if resting_order and queue_pos is not None:
                queue_position = {"price_level": resting_order["price_level"], "position": queue_pos}
            
            return {
                "side": side.upper(),
                "last_traded": last_traded,
                "volume": 0,
                "resting_order": resting_order,
                "queue_position": queue_position,
                "bids": bids,
                "asks": asks
            }
        
        return {
            "id": instance_id,
            "status": "running",
            "position": (trader.cached_position.get("yes", 0) or 0) + (trader.cached_position.get("no", 0) or 0),
            "pnl": "+$0.00",
            "orderbook": {
                "yes": format_side("yes"),
                "no": format_side("no")
            },
            "current_increment": {
                "yes": {"filled": trader.current_increment.get("yes", 0), "total": trader.contract_increment},
                "no": {"filled": trader.current_increment.get("no", 0), "total": trader.contract_increment}
            }
        }
    else:
        m1, m2 = markets[0], markets[1] if len(markets) > 1 else markets[0]
        m1_ob, m2_ob = await asyncio.gather(
            trader._request("GET", f"/markets/{m1}/orderbook"),
            trader._request("GET", f"/markets/{m2}/orderbook")
        )
        
        def format_orderbook(market_id, ob_data):
            if not ob_data or 'orderbook' not in ob_data:
                return {"side": "NO", "last_traded": 0, "volume": 0, "resting_order": None, "queue_position": None, "bids": [], "asks": []}
            
            orderbook = ob_data['orderbook']
            no_levels = [lvl for lvl in orderbook.get('no', []) if lvl[1] > 0]
            no_sorted = sorted(no_levels, key=lambda x: x[0], reverse=True)[:5]
            bids = [{"price": lvl[0], "size": lvl[1]} for lvl in no_sorted]
            
            yes_levels = [lvl for lvl in orderbook.get('yes', []) if lvl[1] > 0]
            yes_sorted = sorted(yes_levels, key=lambda x: x[0], reverse=True)[:5]
            asks = [{"price": 100 - lvl[0], "size": lvl[1]} for lvl in yes_sorted]
            
            last_traded = bids[0]['price'] if bids else 0
            
            resting_price = trader.last_prices.get(market_id)
            resting_qty = trader.cached_resting.get(market_id, 0) or 0
            resting_order = None
            
            if resting_price and resting_qty > 0:
                resting_price_cents = int(resting_price * 100) if resting_price < 1 else int(resting_price)
                if any(b['price'] == resting_price_cents for b in bids):
                    resting_order = {"price_level": resting_price_cents, "quantity": resting_qty}
            
            queue_pos = trader.cached_queue_position.get(market_id)
            queue_position = None
            if resting_order and queue_pos is not None:
                queue_position = {"price_level": resting_order["price_level"], "position": queue_pos}
            
            return {
                "side": "NO",
                "last_traded": last_traded,
                "volume": 0,
                "resting_order": resting_order,
                "queue_position": queue_position,
                "bids": bids,
                "asks": asks
            }
        
        return {
            "id": instance_id,
            "status": "running",
            "position": (trader.cached_position.get(m1, 0) or 0) + (trader.cached_position.get(m2, 0) or 0),
            "pnl": "+$0.00",
            "orderbook": {
                m1: format_orderbook(m1, m1_ob),
                m2: format_orderbook(m2, m2_ob)
            },
            "current_increment": {
                "m1": {"filled": trader.current_increment.get(m1, 0), "total": trader.contract_increment},
                "m2": {"filled": trader.current_increment.get(m2, 0), "total": trader.contract_increment}
            }
        }