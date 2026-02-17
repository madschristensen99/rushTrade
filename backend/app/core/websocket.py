from fastapi import WebSocket, WebSocketDisconnect
from jose import jwt, JWTError
from app.config import get_settings
from app.dependencies import get_redis
from app.services.redis_service import get_redis_client
import json

settings = get_settings()


async def websocket_endpoint(websocket: WebSocket, instance_id: int):
    """WebSocket endpoint for real-time trading updates"""
    await websocket.accept()
    pubsub = None
    
    try:
        # Authenticate
        token = websocket.query_params.get("token")
        if not token:
            await websocket.close(code=1008)
            return
        
        try:
            payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
            user_id = payload.get("user_id")
            if not user_id:
                await websocket.close(code=1008)
                return
        except JWTError:
            await websocket.close(code=1008)
            return
        
        redis_client = await get_redis_client()
        pubsub = redis_client.pubsub()

        await pubsub.subscribe(f"trading:instance:{instance_id}:updates")
        
        # Listen and broadcast
        async for message in pubsub.listen():
            if message["type"] == "message":
                await websocket.send_text(message["data"])
                
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"WebSocket error: {e}")
    finally:
        if pubsub:
            await pubsub.unsubscribe(f"trading:instance:{instance_id}:updates")
            await pubsub.close()