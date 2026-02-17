"""
clob/models.py
--------------
SQLAlchemy models for the $rush CLOB.

Market  – mirrors on-chain MarketFactory data (cached for fast reads)
Order   – off-chain CLOB order (mirrors CTFExchange Order struct)
Fill    – record of each order fill (pending on-chain settlement → settled)
"""

from __future__ import annotations

from enum import Enum

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum as SQLEnum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderStatus(str, Enum):
    OPEN = "open"
    PARTIAL = "partial"        # partially filled, still resting
    FILLED = "filled"          # fully filled
    CANCELLED = "cancelled"    # cancelled by maker or operator
    EXPIRED = "expired"        # past expiration timestamp


class FillStatus(str, Enum):
    PENDING = "pending"        # matched off-chain, awaiting on-chain tx
    SETTLED = "settled"        # on-chain tx confirmed
    FAILED = "failed"          # on-chain tx reverted


class MarketStatus(str, Enum):
    ACTIVE = "active"
    RESOLVED = "resolved"
    CANCELLED = "cancelled"


# ---------------------------------------------------------------------------
# Market
# ---------------------------------------------------------------------------

class Market(Base, TimestampMixin):
    """
    Cached copy of an on-chain MarketFactory market.
    Synced from chain events; the canonical source of truth is the contract.
    """
    __tablename__ = "markets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # On-chain identifiers (bytes32 stored as 0x-prefixed hex strings)
    condition_id: Mapped[str] = mapped_column(String(66), unique=True, nullable=False, index=True)
    question_id: Mapped[str] = mapped_column(String(66), nullable=False)

    oracle_address: Mapped[str] = mapped_column(String(42), nullable=False)
    collateral_token: Mapped[str] = mapped_column(String(42), nullable=False)

    # CTF ERC-1155 position token IDs (uint256 as decimal string to avoid overflow)
    yes_token_id: Mapped[str] = mapped_column(String(80), nullable=True)
    no_token_id: Mapped[str] = mapped_column(String(80), nullable=True)

    # Market metadata
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    category: Mapped[str] = mapped_column(String(64), nullable=True)

    # Resolution
    resolution_time: Mapped[int] = mapped_column(BigInteger, nullable=False)  # unix timestamp
    status: Mapped[str] = mapped_column(SQLEnum(MarketStatus), default=MarketStatus.ACTIVE, nullable=False)
    yes_payout: Mapped[int] = mapped_column(Integer, nullable=True)  # 0 or 1 after resolution
    no_payout: Mapped[int] = mapped_column(Integer, nullable=True)

    # Derived / cached stats (updated by the matching engine)
    yes_price: Mapped[float] = mapped_column(Numeric(20, 6), default=0.5, nullable=True)
    no_price: Mapped[float] = mapped_column(Numeric(20, 6), default=0.5, nullable=True)
    volume_24h: Mapped[float] = mapped_column(Numeric(30, 6), default=0.0, nullable=True)
    total_volume: Mapped[float] = mapped_column(Numeric(30, 6), default=0.0, nullable=True)


# ---------------------------------------------------------------------------
# Order
# ---------------------------------------------------------------------------

class Order(Base, TimestampMixin):
    """
    An off-chain CLOB order, matching the CTFExchange Order struct exactly.

    The EIP-712 signature from the maker (produced by their wallet) is stored
    so the operator can call CTFExchange.fillOrder() when the order matches.

    Price conventions
    -----------------
      BUY  side: price = maker_amount / taker_amount  (collateral per token)
      SELL side: price = taker_amount / maker_amount  (collateral per token)
    """
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    # Market linkage
    condition_id: Mapped[str] = mapped_column(String(66), nullable=False, index=True)
    token_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)

    # CTFExchange Order struct fields
    maker_address: Mapped[str] = mapped_column(String(42), nullable=False, index=True)
    maker_amount: Mapped[str] = mapped_column(String(40), nullable=False)   # uint256 as string
    taker_amount: Mapped[str] = mapped_column(String(40), nullable=False)   # uint256 as string
    expiration: Mapped[int] = mapped_column(BigInteger, nullable=False)      # 0 = never
    nonce: Mapped[int] = mapped_column(BigInteger, nullable=False)
    fee_rate_bps: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    side: Mapped[str] = mapped_column(SQLEnum(OrderSide), nullable=False)
    signer: Mapped[str] = mapped_column(String(42), default="0x0000000000000000000000000000000000000000")

    # EIP-712 signature from the maker's wallet (hex string)
    signature: Mapped[str] = mapped_column(Text, nullable=False)

    # Derived (computed on receipt, used for matching)
    order_hash: Mapped[str] = mapped_column(String(66), unique=True, nullable=False, index=True)

    # Fill tracking
    status: Mapped[str] = mapped_column(SQLEnum(OrderStatus), default=OrderStatus.OPEN, nullable=False, index=True)
    filled_amount: Mapped[str] = mapped_column(String(40), default="0")  # token units filled so far


# ---------------------------------------------------------------------------
# Fill
# ---------------------------------------------------------------------------

class Fill(Base, TimestampMixin):
    """
    Records each time two orders are matched and a fill is submitted on-chain.
    One fill can partially satisfy both a maker and a taker order.
    """
    __tablename__ = "fills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # The maker order being filled
    maker_order_id: Mapped[int] = mapped_column(Integer, ForeignKey("orders.id"), nullable=False, index=True)
    # The taker order providing liquidity against the maker
    taker_order_id: Mapped[int] = mapped_column(Integer, ForeignKey("orders.id"), nullable=True, index=True)

    # Addresses
    maker_address: Mapped[str] = mapped_column(String(42), nullable=False)
    taker_address: Mapped[str] = mapped_column(String(42), nullable=False)

    # Amounts (token units and collateral units as decimal strings)
    token_amount: Mapped[str] = mapped_column(String(40), nullable=False)      # tokens transferred
    collateral_amount: Mapped[str] = mapped_column(String(40), nullable=False)  # collateral transferred
    fee: Mapped[str] = mapped_column(String(40), default="0")                   # protocol fee

    # Settlement
    status: Mapped[str] = mapped_column(SQLEnum(FillStatus), default=FillStatus.PENDING, nullable=False)
    tx_hash: Mapped[str] = mapped_column(String(66), nullable=True)             # set after on-chain confirm
    settled_at: Mapped[str] = mapped_column(String(32), nullable=True)          # ISO timestamp
