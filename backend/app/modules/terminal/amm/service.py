from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List, Dict, Any
from datetime import datetime
import json

from app.modules.terminal.auto.models import TradingInstance, InstanceStatus, ScriptType
from app.modules.terminal.auto.schema import DeployConfig, InstanceResponse
from app.modules.terminal.auto.crypto import CryptoService
from app.core.exceptions import BadRequestError, NotFoundError
from app.tasks.trading_tasks import start_trading_instance
from app.config import get_settings


class TerminalService:
    """Terminal trading service"""
    
    def __init__(self, db: AsyncSession, user_id: int, redis):
        self.db = db
        self.user_id = user_id
        self.redis = redis
        settings = get_settings()
        self.crypto = CryptoService(master_key=settings.SECRET_KEY)
    
    async def store_session_credentials(self, api_key: str, rsa_key: str) -> None:
        """Store encrypted credentials in Redis session (4 hours)"""
        # Encrypt both keys
        encrypted_api_key = self.crypto.encrypt(api_key)
        encrypted_rsa_key = self.crypto.encrypt(rsa_key)
        
        session_data = json.dumps({
            "api_key": encrypted_api_key,
            "rsa_key": encrypted_rsa_key,
            "timestamp": datetime.utcnow().isoformat()
        })
        
        # Store for 4 hours
        await self.redis.set(
            f"user:{self.user_id}:credentials",
            session_data,
            ex=14400  # 4 hours in seconds
        )
    
    async def get_session_credentials(self) -> Dict[str, str]:
        """Get decrypted credentials from Redis session"""
        session_key = f"user:{self.user_id}:credentials"
        session_data = await self.redis.get(session_key)
        
        if not session_data:
            raise BadRequestError("Session expired. Please re-enter your Kalshi credentials.")
        
        data = json.loads(session_data)
        
        # Decrypt both keys
        api_key = self.crypto.decrypt(data["api_key"])
        rsa_key = self.crypto.decrypt(data["rsa_key"])
        
        return {
            "api_key": api_key,
            "rsa_key": rsa_key
        }
    
    async def clear_session_credentials(self) -> None:
        """Delete credentials from Redis (on logout)"""
        await self.redis.delete(f"user:{self.user_id}:credentials")
    
    async def deploy_instance(self, config: DeployConfig) -> TradingInstance:
        """Deploy a new trading instance"""
        # Validate RSA key format
        rsa_private_key = config.rsa_key_path  # This is actually the content
        if "BEGIN PRIVATE KEY" not in rsa_private_key and "BEGIN RSA PRIVATE KEY" not in rsa_private_key:
            raise BadRequestError("Invalid RSA private key format")
        
        # Store credentials in encrypted Redis session (4 hours)
        await self.store_session_credentials(config.kalshi_api_key, rsa_private_key)
        
        # Determine script type
        if config.mode == "hotkeys":
            script = ScriptType.KEYS1 if config.num_markets == 1 else ScriptType.KEYS2
        else:
            script = ScriptType.AUTO1 if config.num_markets == 1 else ScriptType.AUTO2
        
        # Build configuration
        instance_config = {
            "both_side": config.both_side,
            "market_priority": config.market_priority,
            "side_priority": config.side_priority,
            "min_spread": config.min_spread,
            "max_spread": config.max_spread,
            "m1_bounds": config.m1_bounds,
            "m2_bounds": config.m2_bounds,
            "position_increment": config.position_increment,
            "max_position": config.max_position,
            "join_only": config.join_only,
            "grid_mode": config.grid_mode,
            "jump_mode": config.jump_mode,
            "grid_levels": config.grid_levels,
            "contract_increment": config.contract_increment
        }
        
        # Create instance
        instance = TradingInstance(
            user_id=self.user_id,
            script=script,
            markets={"markets": config.markets},
            config=instance_config,
            status=InstanceStatus.PENDING,
            start_time=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        )
        
        self.db.add(instance)
        await self.db.commit()
        await self.db.refresh(instance)
        
        # Start Celery task
        task = start_trading_instance.delay(
            instance_id=instance.id,
            user_id=self.user_id,
            script_type=script.value,
            markets=config.markets,
            config=instance_config
        )
        
        instance.celery_task_id = task.id
        instance.status = InstanceStatus.RUNNING
        await self.db.commit()
        
        return instance

    async def get_user_instances(self) -> List[TradingInstance]:
        """Get all instances for user (exclude DEAD)"""
        result = await self.db.execute(
            select(TradingInstance)
            .where(
                TradingInstance.user_id == self.user_id,
                TradingInstance.status != InstanceStatus.DEAD
            )
            .order_by(TradingInstance.created_at.desc())
        )
        return list(result.scalars().all())
    
    async def get_instance(self, instance_id: int) -> TradingInstance:
        """Get specific instance"""
        result = await self.db.execute(
            select(TradingInstance).where(
                TradingInstance.id == instance_id,
                TradingInstance.user_id == self.user_id
            )
        )
        instance = result.scalar_one_or_none()
        if not instance:
            raise NotFoundError("Trading instance not found")
        return instance
    
    async def toggle_pause_instance(self, instance_id: int) -> dict:
        """Toggle pause/resume for instance"""
        instance = await self.get_instance(instance_id)
        
        command_key = f"trading:instance:{instance_id}:command"
        await self.redis.rpush(command_key, json.dumps({"action": "toggle_pause"}))
        
        return {"instance_id": instance_id, "action": "toggle_pause"}
    
    async def single_fire_instance(self, instance_id: int) -> dict:
        """Execute single fire on instance"""
        instance = await self.get_instance(instance_id)
        
        command_key = f"trading:instance:{instance_id}:command"
        await self.redis.rpush(command_key, json.dumps({"action": "single_fire"}))
        
        return {"instance_id": instance_id, "action": "single_fire"}
    
    async def set_trading_mode_instance(self, instance_id: int, mode: str) -> dict:
        """Set trading mode (join/jump/pennyif)"""
        instance = await self.get_instance(instance_id)
        
        if mode not in ["join", "jump", "pennyif"]:
            raise BadRequestError("Invalid mode. Must be join, jump, or pennyif")
        
        command_key = f"trading:instance:{instance_id}:command"
        await self.redis.rpush(command_key, json.dumps({"action": "set_mode", "mode": mode}))
        
        return {"instance_id": instance_id, "mode": mode}
    
    async def toggle_fair_value_instance(self, instance_id: int) -> dict:
        """Toggle fair value tracking"""
        instance = await self.get_instance(instance_id)
        
        command_key = f"trading:instance:{instance_id}:command"
        await self.redis.rpush(command_key, json.dumps({"action": "toggle_fair_value"}))
        
        return {"instance_id": instance_id, "action": "toggle_fair_value"}
    
    async def stop_instance(self, instance_id: int) -> TradingInstance:
        """Stop instance after current cycle completes"""
        instance = await self.get_instance(instance_id)
        
        if instance.status in [InstanceStatus.STOPPED, InstanceStatus.ERROR]:
            raise BadRequestError("Instance is already stopped")
        
        # Send stop signal via Redis - trading script will handle completion
        await self.redis.rpush(
            f"trading:instance:{instance_id}:command",
            json.dumps({"action": "stop"})
        )
        
        # Don't change status yet - let trading script handle it after cycle completes
        return instance
    
    async def end_instance(self, instance_id: int) -> TradingInstance:
        """End a paused instance (set to DEAD status)"""
        instance = await self.get_instance(instance_id)
        
        if instance.status != InstanceStatus.PAUSED:
            raise BadRequestError("Can only end paused instances")
        
        instance.status = InstanceStatus.DEAD
        await self.db.commit()
        return instance
    
    async def get_instance_status(self, instance_id: int) -> Dict[str, Any]:
        """Get real-time instance status from Redis"""
        instance = await self.get_instance(instance_id)
        
        # Get cached data from Redis
        status_data = await self.redis.get(f"trading:instance:{instance_id}:status")
        
        if status_data:
            status_data = json.loads(status_data)
        else:
            status_data = {
                "position": instance.position,
                "pnl": instance.pnl,
                "orderbook": instance.orderbook_data or {},
                "current_increment": instance.current_increment or {}
            }
        
        return {
            "id": instance.id,
            "status": instance.status.value,
            **status_data
        }
    
    def format_instance_response(self, instance: TradingInstance) -> InstanceResponse:
        """Format instance for API response"""
        # Determine trade mode
        if instance.config.get("jump_mode"):
            trade_mode = "Jump"
        elif instance.config.get("grid_mode"):
            trade_mode = "Grid"
        else:
            trade_mode = "Join"
        
        # Format P&L
        pnl_value = instance.pnl
        pnl_str = f"+${pnl_value:.2f}" if pnl_value >= 0 else f"-${abs(pnl_value):.2f}"
        
        return InstanceResponse(
            id=instance.id,
            script=instance.script.value,
            markets=instance.markets.get("markets", []),
            status=instance.status.value,
            start_time=instance.start_time,
            position=instance.position,
            pnl=pnl_str,
            config=instance.config,
            trade_mode=trade_mode,
            orderbook=instance.orderbook_data,
            celery_task_id=instance.celery_task_id,
            current_increment=instance.current_increment
        )
    
    async def toggle_bump(self, instance_id: int, market_index: int) -> dict:
        """Toggle bump mode for a market"""
        instance = await self.get_instance(instance_id)
        
        # Send command via Redis
        command_key = f"trading:instance:{instance_id}:command"
        await self.redis.rpush(command_key, json.dumps({
            "action": "toggle_bump",
            "market_index": market_index
        }))
        
        # Get current state
        state_key = f"trading:instance:{instance_id}:state"
        state = await self.redis.get(state_key)
        
        if state:
            state_data = json.loads(state)
            bump_active = state_data.get("bump_active", {})
            market_key = f"market_{market_index}"
            is_active = bump_active.get(market_key, False)
            return {
                "market_index": market_index,
                "bump_active": not is_active
            }
        
        return {"market_index": market_index, "bump_active": True}

    async def force_stop_instance(self, instance_id: int) -> TradingInstance:
        """Force stop instance (cancel all orders immediately)"""
        instance = await self.get_instance(instance_id)
        
        if instance.status in [InstanceStatus.STOPPED, InstanceStatus.ERROR]:
            raise BadRequestError("Instance is already stopped")
        
        # Send force stop command
        await self.redis.rpush(
            f"trading:instance:{instance_id}:command",
            json.dumps({"action": "force_stop"})
        )
        
        # Don't change status yet - let trading script handle it
        return instance