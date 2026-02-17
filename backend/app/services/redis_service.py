from typing import Any, Optional
import json
from redis.asyncio import Redis, ConnectionPool
from app.config import get_settings

settings = get_settings()

_redis_client: Redis | None = None
_connection_pool: ConnectionPool | None = None


async def init_redis() -> None:
    global _redis_client, _connection_pool
    _connection_pool = ConnectionPool.from_url(
        settings.redis_url,
        decode_responses=True,
        max_connections=50,
    )
    _redis_client = Redis(connection_pool=_connection_pool)


async def close_redis() -> None:
    global _redis_client, _connection_pool
    if _redis_client:
        await _redis_client.close()
    if _connection_pool:
        await _connection_pool.disconnect()


async def get_redis_client() -> Redis:
    if _redis_client is None:
        raise RuntimeError("Redis not initialized. Call init_redis() first.")
    return _redis_client


class RedisService:
    def __init__(self, redis: Redis):
        self.redis = redis
    
    async def set_value(self, key: str, value: str, expire: int = None):
        await self.redis.set(key, value, ex=expire)
    
    async def get_value(self, key: str) -> Optional[str]:
        return await self.redis.get(key)
    
    async def cache_get(self, key: str) -> Any | None:
        value = await self.redis.get(key)
        if value:
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return value
        return None
    
    async def cache_set(self, key: str, value: Any, ttl: int = 3600) -> bool:
        serialized = json.dumps(value) if not isinstance(value, str) else value
        return await self.redis.setex(key, ttl, serialized)
    
    async def cache_delete(self, key: str) -> bool:
        return await self.redis.delete(key) > 0
    
    async def publish(self, channel: str, message: dict) -> int:
        return await self.redis.publish(channel, json.dumps(message))
    
    async def subscribe(self, channel: str):
        pubsub = self.redis.pubsub()
        await pubsub.subscribe(channel)
        return pubsub
    
    async def cache_market_data(self, market_ticker: str, data: dict, ttl: int = 60) -> bool:
        return await self.cache_set(f"market:{market_ticker}", data, ttl)
    
    async def cache_positions(self, user_id: str, positions: list, ttl: int = 300) -> bool:
        return await self.cache_set(f"positions:{user_id}", positions, ttl)
    
    async def invalidate_positions(self, user_id: str) -> bool:
        return await self.cache_delete(f"positions:{user_id}")
    
    async def set_control_signal(self, instance_id: int, action: str):
        await self.set_value(f"trading:instance:{instance_id}:control", action, expire=60)
    
    async def get_control_signal(self, instance_id: int) -> Optional[str]:
        return await self.get_value(f"trading:instance:{instance_id}:control")
    
    async def publish_instance_update(self, instance_id: int, data: dict):
        await self.redis.publish(f"trading:instance:{instance_id}:updates", json.dumps(data))