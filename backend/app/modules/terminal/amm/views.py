from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession
from redis.asyncio import Redis
from app.dependencies import get_db, get_current_user, get_redis
from app.modules.user.models import User
from app.modules.terminal.auto.service import TerminalService
from app.modules.terminal.auto.schema import (
    DeployConfig,
    InstanceResponse,
    InstanceListResponse,
    InstanceControlRequest,
    InstanceStatusUpdate
)
from app.middleware.rate_limiter import endpoint_rate_limit

router = APIRouter()


@router.post("/logout")
async def logout_terminal(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    redis: Redis = Depends(get_redis)
):
    """Clear terminal session credentials"""
    service = TerminalService(db, current_user.id, redis)
    await service.clear_session_credentials()
    return {"message": "Credentials cleared successfully"}


@router.post("/deploy", response_model=InstanceResponse, status_code=status.HTTP_201_CREATED)
async def deploy_instance(
    config: DeployConfig,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    redis: Redis = Depends(get_redis),
    _rate_limit: bool = Depends(endpoint_rate_limit(5, 60))
):
    """Deploy new trading instance - Rate limit: 5/min"""
    service = TerminalService(db, current_user.id, redis)
    instance = await service.deploy_instance(config)
    return service.format_instance_response(instance)


@router.get("/instances", response_model=InstanceListResponse)
async def list_instances(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    redis: Redis = Depends(get_redis)
):
    """List all trading instances"""
    service = TerminalService(db, current_user.id, redis)
    instances = await service.get_user_instances()
    
    formatted = [service.format_instance_response(inst) for inst in instances]
    return InstanceListResponse(instances=formatted, total=len(formatted))


@router.get("/instances/{instance_id}", response_model=InstanceResponse)
async def get_instance(
    instance_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    redis: Redis = Depends(get_redis)
):
    """Get specific instance"""
    service = TerminalService(db, current_user.id, redis)
    instance = await service.get_instance(instance_id)
    return service.format_instance_response(instance)


@router.get("/instances/{instance_id}/status", response_model=InstanceStatusUpdate)
async def get_instance_status(
    instance_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    redis: Redis = Depends(get_redis)
):
    """Get real-time status"""
    service = TerminalService(db, current_user.id, redis)
    status_data = await service.get_instance_status(instance_id)
    return status_data


@router.delete("/instances/{instance_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_instance(
    instance_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    redis: Redis = Depends(get_redis),
    _rate_limit: bool = Depends(endpoint_rate_limit(10, 60))
):
    """Delete stopped instance - Rate limit: 10/min"""
    service = TerminalService(db, current_user.id, redis)
    instance = await service.get_instance(instance_id)
    
    from app.modules.terminal.auto.models import InstanceStatus
    if instance.status not in [InstanceStatus.STOPPED, InstanceStatus.ERROR]:
        from app.core.exceptions import BadRequestError
        raise BadRequestError("Can only delete stopped instances")
    
    await db.delete(instance)
    await db.commit()


@router.post("/instances/{instance_id}/control", response_model=InstanceResponse)
async def control_instance(
    instance_id: int,
    control_request: InstanceControlRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    redis: Redis = Depends(get_redis),
    _rate_limit: bool = Depends(endpoint_rate_limit(20, 60))
):
    """Control instance - Rate limit: 20/min"""
    service = TerminalService(db, current_user.id, redis)
    
    if control_request.action == "pause":
        instance = await service.pause_instance(instance_id)
    elif control_request.action == "resume":
        instance = await service.resume_instance(instance_id)
    elif control_request.action == "toggle_pause":
        await service.toggle_pause_instance(instance_id)
        instance = await service.get_instance(instance_id)
    elif control_request.action == "single_fire":
        await service.single_fire_instance(instance_id)
        instance = await service.get_instance(instance_id)
    elif control_request.action == "stop":
        instance = await service.stop_instance(instance_id)
    elif control_request.action == "force_stop":
        instance = await service.force_stop_instance(instance_id)
    elif control_request.action == "end":
        instance = await service.end_instance(instance_id)
    else:
        from app.core.exceptions import BadRequestError
        raise BadRequestError(f"Invalid action: {control_request.action}")
    
    return service.format_instance_response(instance)

@router.post("/instances/{instance_id}/mode")
async def set_trading_mode(
    instance_id: int,
    mode_request: dict,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    redis: Redis = Depends(get_redis),
    _rate_limit: bool = Depends(endpoint_rate_limit(30, 60))
):
    """Set trading mode - Rate limit: 30/min"""
    service = TerminalService(db, current_user.id, redis)
    mode = mode_request.get("mode", "join")
    result = await service.set_trading_mode_instance(instance_id, mode)
    return result

@router.post("/instances/{instance_id}/fair_value")
async def toggle_fair_value(
    instance_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    redis: Redis = Depends(get_redis),
    _rate_limit: bool = Depends(endpoint_rate_limit(30, 60))
):
    """Toggle fair value - Rate limit: 30/min"""
    service = TerminalService(db, current_user.id, redis)
    result = await service.toggle_fair_value_instance(instance_id)
    return result


@router.post("/instances/{instance_id}/bump", response_model=dict)
async def toggle_bump(
    instance_id: int,
    bump_request: dict,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    redis: Redis = Depends(get_redis),
    _rate_limit: bool = Depends(endpoint_rate_limit(30, 60))
):
    """Toggle bump mode - Rate limit: 30/min"""
    service = TerminalService(db, current_user.id, redis)
    market_index = bump_request.get("market_index", 0)
    result = await service.toggle_bump(instance_id, market_index)
    return result