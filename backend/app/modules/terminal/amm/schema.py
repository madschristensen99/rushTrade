# app/modules/terminal/auto/schema.py

from pydantic import BaseModel, Field, validator
from typing import List, Optional, Dict, Any, Literal
from datetime import datetime
from enum import Enum
import re
import uuid


def sanitize_string(value: str) -> str:
    """Sanitize input to prevent XSS and injection attacks"""
    if not value:
        return value
    
    dangerous = ['<script', 'javascript:', 'onerror=', 'onclick=',
                '--', ';', 'drop ', 'delete ', 'insert ', 'update ', 'union ', 'select ']
    value_lower = value.lower()
    
    for pattern in dangerous:
        if pattern in value_lower:
            raise ValueError('Invalid characters or patterns detected')
    
    return value.strip()


class TradeModeEnum(str, Enum):
    JOIN = "Join"
    JUMP = "Jump"
    GRID = "Grid"


class DeployConfig(BaseModel):
    """Configuration for deploying a trading instance"""
    num_markets: int = Field(..., ge=1, le=2)
    mode: str = Field(..., pattern="^(hotkeys|automated)$")
    markets: List[str] = Field(..., min_items=1, max_items=2)
    
    # Kalshi credentials
    kalshi_api_key: str = Field(..., min_length=36, max_length=36, description="Kalshi API key (UUID format)")
    rsa_key_path: str = Field(..., min_length=100, max_length=10000, description="RSA private key content")
    
    both_side: Optional[str] = None
    market_priority: Optional[str] = None
    side_priority: Optional[str] = None
    
    min_spread: int = Field(..., ge=1, le=99)
    max_spread: int = Field(..., ge=1, le=99)
    m1_bounds: List[int] = Field(..., min_items=4, max_items=4)
    m2_bounds: Optional[List[int]] = Field(None, min_items=4, max_items=4)
    
    position_increment: int = Field(..., ge=1, le=1000)
    max_position: int = Field(..., ge=1, le=100000)
    
    join_only: bool = False
    grid_mode: bool = False
    jump_mode: bool = False
    grid_levels: Optional[List[List[int]]] = None
    
    contract_increment: int = Field(3, ge=1, le=30, description="Contract increment (1-30)")
    
    # ========================================================================
    # VALIDATORS
    # ========================================================================
    
    @validator('markets', each_item=True)
    def validate_market_ticker(cls, v):
        """Validate Kalshi market ticker format"""
        if not v:
            raise ValueError('Market ticker cannot be empty')
        
        v = sanitize_string(v)
        
        # Pattern: KXNBAGAME-25JUN01LALMIA-LAL (segments separated by hyphens)
        pattern = r'^[A-Z0-9]+-[A-Z0-9]+-[A-Z0-9]+$'
        if not re.match(pattern, v):
            raise ValueError(f'Invalid market ticker format: {v}. Expected: XXX-XXX-XXX')
        
        if len(v) < 5 or len(v) > 100:
            raise ValueError('Market ticker must be 5-100 characters')
        
        segments = v.split('-')
        if len(segments) < 3:
            raise ValueError('Market ticker must have at least 3 segments')
        
        return v.upper()
    
    @validator('markets')
    def validate_markets_count(cls, v, values):
        """Ensure market count matches num_markets"""
        num_markets = values.get('num_markets', 1)
        if len(v) != num_markets:
            raise ValueError(f'Must provide exactly {num_markets} market(s), got {len(v)}')
        
        # No duplicates
        if len(v) != len(set(v)):
            raise ValueError('Duplicate markets not allowed')
        
        return v
    
    @validator('kalshi_api_key')
    def validate_api_key(cls, v):
        """Validate API key is valid UUID v4 format"""
        try:
            uuid.UUID(v, version=4)
        except ValueError:
            raise ValueError('Invalid API key format (must be UUID v4)')
        
        # Strict UUID v4 format check
        if not re.match(r'^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$', v.lower()):
            raise ValueError('API key must be valid UUID v4')
        
        return v
    
    @validator('rsa_key_path')
    def validate_rsa_key(cls, v):
        """Validate RSA private key format (CRITICAL SECURITY)"""
        if not v or len(v) < 100:
            raise ValueError('RSA key too short or empty')
        
        if len(v) > 10000:
            raise ValueError('RSA key exceeds maximum length')
        
        # Check PEM format markers
        if not ('BEGIN PRIVATE KEY' in v or 'BEGIN RSA PRIVATE KEY' in v):
            raise ValueError('Invalid RSA key: must be PEM format')
        
        if not ('END PRIVATE KEY' in v or 'END RSA PRIVATE KEY' in v):
            raise ValueError('Invalid RSA key: missing END marker')
        
        # Check for malicious content
        dangerous = ['<script', 'javascript:', 'onerror=', 'drop table', 
                    'delete from', 'insert into', 'union select']
        v_lower = v.lower()
        for pattern in dangerous:
            if pattern in v_lower:
                raise ValueError('Invalid or malicious content detected in RSA key')
        
        # Validate key is loadable and meets minimum security requirements
        try:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.backends import default_backend
            
            key = serialization.load_pem_private_key(
                v.encode('utf-8'),
                password=None,
                backend=default_backend()
            )
            
            # Minimum 2048-bit key for security
            if key.key_size < 2048:
                raise ValueError(f'RSA key too weak: {key.key_size} bits (minimum 2048 required)')
            
        except Exception as e:
            raise ValueError(f'Invalid or corrupted RSA private key: {str(e)}')
        
        return v
    
    @validator('max_spread')
    def validate_spread_range(cls, v, values):
        """Ensure max_spread > min_spread"""
        min_spread = values.get('min_spread')
        if min_spread is not None:
            if v <= min_spread:
                raise ValueError(f'max_spread ({v}) must be greater than min_spread ({min_spread})')
            
            if v - min_spread > 50:
                raise ValueError('Spread range too wide (max difference: 50)')
        
        return v
    
    @validator('m1_bounds')
    def validate_m1_bounds(cls, v):
        """Validate price bounds logical ordering"""
        if len(v) != 4:
            raise ValueError('m1_bounds must have exactly 4 values')
        
        if not all(isinstance(x, int) and 1 <= x <= 99 for x in v):
            raise ValueError('All bounds must be integers between 1 and 99')
        
        # [lower1, upper1, lower2, upper2]
        if v[0] >= v[1]:
            raise ValueError(f'First range invalid: lower ({v[0]}) must be < upper ({v[1]})')
        
        if v[2] >= v[3]:
            raise ValueError(f'Second range invalid: lower ({v[2]}) must be < upper ({v[3]})')
        
        # Ranges should not overlap
        if v[1] > v[2]:
            raise ValueError(f'Ranges overlap: first upper ({v[1]}) must be <= second lower ({v[2]})')
        
        return v
    
    @validator('m2_bounds')
    def validate_m2_bounds(cls, v):
        """Validate second market bounds"""
        if v is None:
            return v
        
        if len(v) != 4:
            raise ValueError('m2_bounds must have exactly 4 values')
        
        if not all(isinstance(x, int) and 1 <= x <= 99 for x in v):
            raise ValueError('All m2_bounds must be integers between 1 and 99')
        
        if v[0] >= v[1]:
            raise ValueError(f'First range invalid: lower ({v[0]}) must be < upper ({v[1]})')
        
        if v[2] >= v[3]:
            raise ValueError(f'Second range invalid: lower ({v[2]}) must be < upper ({v[3]})')
        
        if v[1] > v[2]:
            raise ValueError(f'Ranges overlap: first upper ({v[1]}) must be <= second lower ({v[2]})')
        
        return v
    
    @validator('both_side')
    def validate_both_side(cls, v, values):
        """Validate both_side for 2-market mode"""
        num_markets = values.get('num_markets')
        
        if num_markets == 2:
            if v is None:
                raise ValueError('both_side required when num_markets=2')
            if v.lower() not in ['yes', 'no']:
                raise ValueError('both_side must be "yes" or "no"')
        
        return sanitize_string(v).lower() if v else v
    
    @validator('market_priority')
    def validate_market_priority(cls, v):
        """Validate market_priority options"""
        if v is None:
            return v
        
        v = sanitize_string(v).lower()
        valid = ['none', 'market1', 'market2', 'expensive']
        
        if v not in valid:
            raise ValueError(f'market_priority must be one of: {", ".join(valid)}')
        
        return v
    
    @validator('side_priority')
    def validate_side_priority(cls, v):
        """Validate side_priority options"""
        if v is None:
            return v
        
        v = sanitize_string(v).lower()
        valid = ['yes', 'no', 'expensive', 'cheap']
        
        if v not in valid:
            raise ValueError(f'side_priority must be one of: {", ".join(valid)}')
        
        return v
    
    @validator('position_increment')
    def validate_position_increment(cls, v):
        """Validate position increment is reasonable"""
        if v < 1:
            raise ValueError('position_increment must be at least 1')
        if v > 1000:
            raise ValueError('position_increment cannot exceed 1,000 contracts')
        return v
    
    @validator('max_position')
    def validate_max_position(cls, v):
        """Validate max position is reasonable"""
        if v < 1:
            raise ValueError('max_position must be at least 1')
        if v > 100000:
            raise ValueError('max_position cannot exceed 100,000 contracts')
        return v
    
    @validator('grid_levels')
    def validate_grid_levels(cls, v, values):
        """Validate grid levels structure"""
        if v is None:
            return v
        
        grid_mode = values.get('grid_mode', False)
        if not grid_mode and v:
            raise ValueError('grid_levels should only be set when grid_mode=True')
        
        if not isinstance(v, list):
            raise ValueError('grid_levels must be a list')
        
        for i, level in enumerate(v):
            if not isinstance(level, list) or len(level) != 2:
                raise ValueError(f'Grid level {i} must be [price, size]')
            
            price, size = level
            if not (isinstance(price, int) and 1 <= price <= 99):
                raise ValueError(f'Grid level {i} price must be integer 1-99')
            
            if not (isinstance(size, int) and size > 0):
                raise ValueError(f'Grid level {i} size must be positive')
        
        return v
    
    @validator('contract_increment')
    def validate_contract_increment(cls, v):
        """Validate contract increment range"""
        if not 1 <= v <= 30:
            raise ValueError('contract_increment must be 1-30')
        return v


class InstanceResponse(BaseModel):
    """Trading instance response"""
    id: int
    script: str
    markets: List[str]
    status: str
    start_time: Optional[str]
    position: int
    pnl: str
    config: Dict[str, Any]
    trade_mode: str
    orderbook: Optional[Dict[str, Any]]
    celery_task_id: Optional[str]
    current_increment: Optional[Dict[str, Any]]
    
    class Config:
        from_attributes = True


class InstanceListResponse(BaseModel):
    """List of trading instances"""
    instances: List[InstanceResponse]
    total: int


class InstanceControlRequest(BaseModel):
    action: Literal["pause", "resume", "stop", "force_stop"]
    
    @validator('action')
    def validate_action(cls, v):
        valid = ["pause", "resume", "stop", "force_stop"]
        if v not in valid:
            raise ValueError(f'action must be one of: {", ".join(valid)}')
        return v


class SigningProxyCreate(BaseModel):
    """Create signing proxy (deprecated - use Redis session storage)"""
    kalshi_api_key: str = Field(..., min_length=36, max_length=36)
    private_key_content: str = Field(..., min_length=100, max_length=10000)
    
    @validator('kalshi_api_key')
    def validate_api_key(cls, v):
        try:
            uuid.UUID(v, version=4)
        except ValueError:
            raise ValueError('Invalid API key format (must be UUID v4)')
        return v
    
    @validator('private_key_content')
    def validate_private_key(cls, v):
        if not ('BEGIN PRIVATE KEY' in v or 'BEGIN RSA PRIVATE KEY' in v):
            raise ValueError('Invalid private key format (must be PEM)')
        return v


class SigningProxyResponse(BaseModel):
    """Signing proxy response"""
    id: int
    user_id: int
    kalshi_api_key: str
    is_active: bool
    created_at: datetime
    
    class Config:
        from_attributes = True


class OrderbookLevel(BaseModel):
    """Orderbook price level"""
    price: int = Field(..., ge=1, le=99)
    size: int = Field(..., ge=0)
    
    @validator('size')
    def validate_size(cls, v):
        if v < 0:
            raise ValueError('Size cannot be negative')
        if v > 1000000:
            raise ValueError('Size exceeds maximum (1,000,000)')
        return v


class MarketOrderbook(BaseModel):
    """Market orderbook data"""
    side: Optional[str] = None
    last_traded: int = Field(..., ge=1, le=99)
    volume: int = Field(..., ge=0)
    resting_order: Optional[Dict[str, Any]] = None
    resting_yes: Optional[Dict[str, Any]] = None
    resting_no: Optional[Dict[str, Any]] = None
    bids: List[OrderbookLevel]
    asks: List[OrderbookLevel]
    
    @validator('side')
    def validate_side(cls, v):
        if v is not None and v.lower() not in ['yes', 'no']:
            raise ValueError('side must be "yes" or "no"')
        return v.lower() if v else v


class InstanceStatusUpdate(BaseModel):
    """Real-time instance status update"""
    instance_id: int
    status: str
    position: int = Field(..., ge=-100000, le=100000)
    pnl: str
    orderbook: Dict[str, MarketOrderbook]
    current_increment: Dict[str, Any]
    
    @validator('status')
    def validate_status(cls, v):
        valid = ['running', 'paused', 'stopped', 'error', 'starting']
        v_lower = v.lower()
        if v_lower not in valid:
            raise ValueError(f'status must be one of: {", ".join(valid)}')
        return v_lower
    
    @validator('position')
    def validate_position(cls, v):
        if abs(v) > 100000:
            raise ValueError('Position exceeds maximum (±100,000)')
        return v