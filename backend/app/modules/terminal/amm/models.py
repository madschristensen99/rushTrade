from sqlalchemy import String, Integer, Float, JSON, Enum as SQLEnum, ForeignKey, Text, Boolean
from sqlalchemy.orm import Mapped, mapped_column
from enum import Enum

from app.database.base import Base, TimestampMixin


class InstanceStatus(str, Enum):
    """Trading instance status"""
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    ERROR = "error"
    DEAD = "dead"


class ScriptType(str, Enum):
    """Trading script types"""
    KEYS1 = "keys1"
    KEYS2 = "keys2"
    AUTO1 = "auto1"
    AUTO2 = "auto2"


class TradingInstance(Base, TimestampMixin):
    """Trading instance model"""
    __tablename__ = "trading_instances"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    
    script: Mapped[str] = mapped_column(SQLEnum(ScriptType), nullable=False)
    markets: Mapped[dict] = mapped_column(JSON, nullable=False)
    config: Mapped[dict] = mapped_column(JSON, nullable=False)
    
    status: Mapped[str] = mapped_column(SQLEnum(InstanceStatus), default=InstanceStatus.PENDING, nullable=False)
    
    position: Mapped[int] = mapped_column(Integer, default=0)
    pnl: Mapped[float] = mapped_column(Float, default=0.0)
    
    celery_task_id: Mapped[str] = mapped_column(String, nullable=True)
    start_time: Mapped[str] = mapped_column(String, nullable=True)
    
    orderbook_data: Mapped[dict] = mapped_column(JSON, nullable=True)
    current_increment: Mapped[dict] = mapped_column(JSON, nullable=True)
