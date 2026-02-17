from celery import Celery
import os
import sys
import time
import json
import asyncio
from typing import Dict, Any
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.modules.terminal.auto.crypto import CryptoService
from app.services.redis_service import RedisService
from app.services.kalshi_service import KalshiTradingService

settings = get_settings()

# Initialize Celery
celery = Celery(
    'trading_tasks',
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND
)


async def get_user_credentials(user_id: int):
    """Get decrypted user credentials"""
    engine = create_async_engine(settings.DATABASE_URL)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    async with async_session() as session:
        from sqlalchemy import select
        from app.modules.terminal.auto.models import SigningProxy
        
        result = await session.execute(
            select(SigningProxy).where(
                SigningProxy.user_id == user_id,
                SigningProxy.is_active == True
            )
        )
        proxy = result.scalar_one_or_none()
        
        if not proxy:
            raise ValueError("No active signing proxy found")
        
        crypto = CryptoService()
        private_key = crypto.decrypt_private_key(proxy.encrypted_private_key)
        
        return {
            "api_key": proxy.kalshi_api_key,
            "private_key": private_key
        }


@celery.task(bind=True, name='trading_tasks.start_trading_instance')
def start_trading_instance(
    self,
    instance_id: int,
    user_id: int,
    script_type: str,
    markets: list,
    config: dict
):
    """Start a trading instance"""
    
    # Run async code in sync Celery task
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        # Get user credentials
        credentials = loop.run_until_complete(get_user_credentials(user_id))
        
        # Initialize services
        redis = RedisService()
        trading_service = KalshiTradingService(
            api_key=credentials["api_key"],
            private_key_content=credentials["private_key"]
        )
        
        # Main trading loop
        running = True
        loop_count = 0
        
        while running:
            # Check for control signals
            control_signal = loop.run_until_complete(
                redis.get_value(f"trading:instance:{instance_id}:control")
            )
            
            if control_signal == "stop":
                print(f"Instance {instance_id}: Received stop signal")
                break
            elif control_signal == "pause":
                print(f"Instance {instance_id}: Paused")
                time.sleep(5)
                continue
            
            # Execute trading logic based on script type
            if script_type == "auto1":
                status = loop.run_until_complete(
                    run_auto1_script(instance_id, markets[0], config, trading_service, redis)
                )
            elif script_type == "auto2":
                status = loop.run_until_complete(
                    run_auto2_script(instance_id, markets, config, trading_service, redis)
                )
            else:
                raise ValueError(f"Unsupported script type: {script_type}")
            
            # Update instance status in Redis
            loop.run_until_complete(
                redis.set_value(
                    f"trading:instance:{instance_id}:status",
                    json.dumps(status),
                    expire=30
                )
            )
            
            # Update progress
            loop_count += 1
            self.update_state(
                state='PROGRESS',
                meta={'instance_id': instance_id, 'loops': loop_count}
            )
            
            time.sleep(1)
        
        return {'status': 'completed', 'instance_id': instance_id}
        
    except Exception as e:
        print(f"Instance {instance_id} error: {str(e)}")
        
        # Mark as error in database
        loop.run_until_complete(update_instance_error(instance_id, str(e)))
        
        return {'status': 'error', 'error': str(e)}
    finally:
        loop.close()


async def run_auto1_script(
    instance_id: int,
    market_id: str,
    config: dict,
    trading_service: KalshiTradingService,
    redis: RedisService
) -> Dict[str, Any]:
    """Run single market automated script"""
    
    # Fetch market data
    orderbook = await trading_service.get_orderbook(market_id)
    positions = await trading_service.get_positions(market_id)
    resting_orders = await trading_service.get_resting_orders(market_id)
    
    # Trading logic
    yes_pos = positions.get("yes", 0)
    no_pos = positions.get("no", 0)
    
    # Format orderbook for frontend
    orderbook_data = {
        market_id: {
            "volume": orderbook.get("volume", 0),
            "last_traded": orderbook.get("last_traded", 0),
            "resting_yes": resting_orders.get("yes"),
            "resting_no": resting_orders.get("no"),
            "bids": orderbook.get("bids", []),
            "asks": orderbook.get("asks", [])
        }
    }
    
    return {
        "position": yes_pos + no_pos,
        "pnl": 0.0,  # Calculate actual P&L
        "orderbook": orderbook_data,
        "current_increment": {
            "yes": {"filled": 0, "total": config["contract_increment"]},
            "no": {"filled": 0, "total": config["contract_increment"]}
        }
    }


async def run_auto2_script(
    instance_id: int,
    markets: list,
    config: dict,
    trading_service: KalshiTradingService,
    redis: RedisService
) -> Dict[str, Any]:
    """Run two market automated script"""
    
    market1, market2 = markets
    
    # Fetch data for both markets
    ob1 = await trading_service.get_orderbook(market1)
    ob2 = await trading_service.get_orderbook(market2)
    
    pos1 = await trading_service.get_positions(market1)
    pos2 = await trading_service.get_positions(market2)
    
    orders1 = await trading_service.get_resting_orders(market1)
    orders2 = await trading_service.get_resting_orders(market2)
    
    # Format for frontend
    side = config.get("both_side", "no").upper()
    
    orderbook_data = {
        market1: {
            "side": side,
            "volume": ob1.get("volume", 0),
            "last_traded": ob1.get("last_traded", 0),
            "resting_order": orders1.get(side.lower()),
            "bids": ob1.get("bids", []),
            "asks": ob1.get("asks", [])
        },
        market2: {
            "side": side,
            "volume": ob2.get("volume", 0),
            "last_traded": ob2.get("last_traded", 0),
            "resting_order": orders2.get(side.lower()),
            "bids": ob2.get("bids", []),
            "asks": ob2.get("asks", [])
        }
    }
    
    total_pos = abs(pos1.get(side.lower(), 0)) + abs(pos2.get(side.lower(), 0))
    
    return {
        "position": total_pos,
        "pnl": 0.0,
        "orderbook": orderbook_data,
        "current_increment": {
            "m1": {"filled": 0, "total": config["contract_increment"]},
            "m2": {"filled": 0, "total": config["contract_increment"]}
        }
    }


async def update_instance_error(instance_id: int, error_message: str):
    """Update instance status to error"""
    from sqlalchemy import update
    from app.modules.terminal.auto.models import TradingInstance, InstanceStatus
    from app.config import get_settings
    
    settings = get_settings()
    engine = create_async_engine(settings.DATABASE_URL)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    async with async_session() as session:
        await session.execute(
            update(TradingInstance)
            .where(TradingInstance.id == instance_id)
            .values(status=InstanceStatus.ERROR)
        )
        await session.commit()