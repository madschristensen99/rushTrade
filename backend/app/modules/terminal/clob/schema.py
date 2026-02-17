"""
clob/schema.py
--------------
Pydantic schemas for the $rush CLOB API.
"""

from __future__ import annotations

from decimal import Decimal
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Enums (match models.py and Solidity contract)
# ---------------------------------------------------------------------------

class OrderSideEnum(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderStatusEnum(str, Enum):
    OPEN = "open"
    PARTIAL = "partial"
    FILLED = "filled"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class FillStatusEnum(str, Enum):
    PENDING = "pending"
    SETTLED = "settled"
    FAILED = "failed"


# ---------------------------------------------------------------------------
# Order
# ---------------------------------------------------------------------------

class OrderCreate(BaseModel):
    """
    Submitted by the frontend after the user's wallet signs the EIP-712 Order.

    The frontend must:
      1. Construct the Order struct (all uint256 fields as integers).
      2. Ask the connected wallet to sign via eth_signTypedData_v4.
      3. POST this payload to POST /orders.
    """

    # Market / position
    condition_id: str = Field(..., description="0x-prefixed bytes32 condition ID")
    token_id: str = Field(..., description="CTF ERC-1155 position token ID (uint256 as decimal string)")

    # CTFExchange Order struct fields (uint256 stored as strings to avoid JS precision loss)
    maker_address: str = Field(..., description="Maker's wallet address (checksummed)")
    maker_amount: str = Field(..., description="uint256: collateral (BUY) or tokens (SELL)")
    taker_amount: str = Field(..., description="uint256: tokens (BUY) or collateral (SELL)")
    expiration: int = Field(..., description="Unix timestamp; 0 means no expiry", ge=0)
    nonce: int = Field(..., description="Maker-controlled nonce for replay protection", ge=0)
    fee_rate_bps: int = Field(0, description="Maker fee rate in basis points (0–200)", ge=0, le=200)
    side: OrderSideEnum
    signer: str = Field(
        "0x0000000000000000000000000000000000000000",
        description="Optional alternate signer (zero address = maker is signer)",
    )

    # EIP-712 signature produced by the maker's wallet
    signature: str = Field(..., description="0x-prefixed EIP-712 signature hex")

    @field_validator("condition_id")
    @classmethod
    def validate_condition_id(cls, v: str) -> str:
        v = v.strip()
        if not v.startswith("0x") or len(v) != 66:
            raise ValueError("condition_id must be a 0x-prefixed 32-byte hex string (66 chars)")
        return v.lower()

    @field_validator("maker_address", "signer")
    @classmethod
    def validate_address(cls, v: str) -> str:
        v = v.strip()
        if not v.startswith("0x") or len(v) != 42:
            raise ValueError("Address must be 0x-prefixed 20-byte hex (42 chars)")
        return v.lower()

    @field_validator("maker_amount", "taker_amount", "token_id")
    @classmethod
    def validate_uint256_string(cls, v: str) -> str:
        try:
            n = int(v)
            if n < 0:
                raise ValueError
        except (ValueError, TypeError):
            raise ValueError("Must be a non-negative integer string")
        return v

    @field_validator("signature")
    @classmethod
    def validate_signature(cls, v: str) -> str:
        v = v.strip()
        if not v.startswith("0x"):
            raise ValueError("Signature must be 0x-prefixed hex")
        if len(v) != 132:  # 0x + 65 bytes * 2 = 132 chars
            raise ValueError("Signature must be 65 bytes (130 hex chars + 0x prefix)")
        return v.lower()


class OrderResponse(BaseModel):
    id: int
    condition_id: str
    token_id: str
    maker_address: str
    maker_amount: str
    taker_amount: str
    expiration: int
    nonce: int
    fee_rate_bps: int
    side: OrderSideEnum
    status: OrderStatusEnum
    filled_amount: str
    order_hash: str
    created_at: str

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# Orderbook
# ---------------------------------------------------------------------------

class PriceLevel(BaseModel):
    """A single price level in the orderbook."""
    price: str          # collateral per token, as decimal string (e.g. "0.650000")
    size: str           # total token units available at this price
    order_count: int    # number of orders aggregated at this level


class OrderbookSide(BaseModel):
    bids: list[PriceLevel]  # buyers: highest price first
    asks: list[PriceLevel]  # sellers: lowest price first


class OrderbookResponse(BaseModel):
    condition_id: str
    yes: OrderbookSide
    no: OrderbookSide
    mid_price_yes: Optional[str] = None  # (best_bid + best_ask) / 2 for YES
    mid_price_no: Optional[str] = None


# ---------------------------------------------------------------------------
# Market
# ---------------------------------------------------------------------------

class MarketResponse(BaseModel):
    condition_id: str
    question_id: str
    oracle_address: str
    collateral_token: str
    yes_token_id: Optional[str]
    no_token_id: Optional[str]
    title: str
    description: Optional[str]
    category: Optional[str]
    resolution_time: int
    status: str
    yes_payout: Optional[int]
    no_payout: Optional[int]
    yes_price: Optional[str]
    no_price: Optional[str]
    volume_24h: Optional[str]
    total_volume: Optional[str]

    class Config:
        from_attributes = True


class MarketListResponse(BaseModel):
    markets: list[MarketResponse]
    total: int
    offset: int
    limit: int


# ---------------------------------------------------------------------------
# Positions
# ---------------------------------------------------------------------------

class PositionItem(BaseModel):
    condition_id: str
    market_title: str
    yes_token_id: str
    no_token_id: str
    yes_balance: str   # uint256 as decimal string
    no_balance: str
    yes_price: Optional[str]
    no_price: Optional[str]


class PositionsResponse(BaseModel):
    wallet: str
    positions: list[PositionItem]


# ---------------------------------------------------------------------------
# Fills
# ---------------------------------------------------------------------------

class FillResponse(BaseModel):
    id: int
    maker_order_id: int
    taker_order_id: Optional[int]
    maker_address: str
    taker_address: str
    token_amount: str
    collateral_amount: str
    fee: str
    status: FillStatusEnum
    tx_hash: Optional[str]
    settled_at: Optional[str]
    created_at: str

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# BTC Strike Markets
# ---------------------------------------------------------------------------

class StrikeMarketResponse(BaseModel):
    """One strike market within a BTC round, including its live 5-level orderbook."""
    condition_id: str
    strike_price: str           # e.g. "97331.76"
    strike_label: str           # "+0.1%", "+0.05%", "ATM", "-0.05%", "-0.1%"
    strike_index: int           # 0 (highest) … 4 (lowest)
    yes_token_id: Optional[str] = None
    no_token_id: Optional[str] = None
    orderbook: OrderbookResponse


class BtcRoundResponse(BaseModel):
    """The current active 60-second BTC/USD round with all 5 strike markets."""
    round_id: int
    round_start: str            # ISO UTC string
    round_end: str              # ISO UTC string
    btc_price_at_open: str      # e.g. "97234.50"
    status: str                 # "active" | "resolved" | "failed"
    seconds_remaining: int      # 0 when expired
    markets: list[StrikeMarketResponse]  # ordered by strike_index (0 = highest strike)
