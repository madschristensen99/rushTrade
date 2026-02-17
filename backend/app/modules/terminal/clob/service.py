"""
clob/service.py
---------------
Business logic layer for the $rush CLOB.

Responsibilities
----------------
  submit_order   – validate signature, persist order, run matching, enqueue fills for settlement
  cancel_order   – mark order cancelled, optionally cancel on-chain if not yet settled
  get_market     – fetch from DB (sync with chain if missing)
  get_orderbook  – delegate to matching.get_orderbook_snapshot()
  get_positions  – read ERC-1155 balances from chain
  get_user_orders / get_user_fills – DB reads
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import and_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.terminal.clob.matching import (
    MatchResult,
    get_orderbook_snapshot,
    match_new_order,
)
from app.modules.terminal.clob.models import (
    Fill,
    FillStatus,
    Market,
    MarketStatus,
    Order,
    OrderSide,
    OrderStatus,
)
from app.modules.terminal.clob.schema import (
    FillResponse,
    MarketListResponse,
    MarketResponse,
    OrderCreate,
    OrderResponse,
    OrderbookResponse,
    OrderbookSide,
    PositionItem,
    PositionsResponse,
)
from app.services.chain_service import ChainOrder, OrderSide as ChainOrderSide, chain
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


# ---------------------------------------------------------------------------
# Order submission
# ---------------------------------------------------------------------------

async def submit_order(
    db: AsyncSession,
    payload: OrderCreate,
    current_user: User,
) -> OrderResponse:
    """
    Validate and persist a new signed order, then run matching.

    Steps:
      1. Verify the EIP-712 signature against the maker address.
      2. Compute the on-chain order hash via ChainService.
      3. Persist the Order record.
      4. Run the matching engine.
      5. Persist Fill records for each match (status=PENDING).
      6. Update filled_amount and status on matched orders.
      7. The Celery task `settle_fills` picks up PENDING fills asynchronously.
    """
    # Build ChainOrder for hash computation and signature verification.
    chain_order = ChainOrder(
        maker=payload.maker_address,
        token_id=int(payload.token_id),
        maker_amount=int(payload.maker_amount),
        taker_amount=int(payload.taker_amount),
        expiration=payload.expiration,
        nonce=payload.nonce,
        fee_rate_bps=payload.fee_rate_bps,
        side=ChainOrderSide.BUY if payload.side == "buy" else ChainOrderSide.SELL,
        signer=payload.signer,
    )

    # Verify signature (will raise if invalid).
    expected_signer = payload.signer if payload.signer != "0x" + "0" * 40 else payload.maker_address
    try:
        recovered = chain.recover_signer(chain_order, payload.signature)
        if recovered.lower() != expected_signer.lower():
            raise ValueError(f"Signature mismatch: recovered {recovered}, expected {expected_signer}")
    except Exception as exc:
        logger.warning("Order signature invalid: %s", exc)
        raise ValueError(f"Invalid order signature: {exc}") from exc

    # Compute order hash (from chain contract for canonical value).
    order_hash: str
    try:
        order_hash = await chain.get_order_hash(chain_order)
    except Exception:
        # Fallback: compute locally if node is unreachable during development.
        raw = json.dumps(chain_order.to_tuple(), sort_keys=True, default=str).encode()
        order_hash = "0x" + hashlib.sha256(raw).hexdigest()

    # Reject if this order hash already exists.
    existing = (
        await db.execute(select(Order).where(Order.order_hash == order_hash))
    ).scalar_one_or_none()
    if existing:
        raise ValueError("Order already submitted (duplicate hash)")

    # Persist the order.
    order = Order(
        user_id=current_user.id,
        condition_id=payload.condition_id,
        token_id=payload.token_id,
        maker_address=payload.maker_address,
        maker_amount=payload.maker_amount,
        taker_amount=payload.taker_amount,
        expiration=payload.expiration,
        nonce=payload.nonce,
        fee_rate_bps=payload.fee_rate_bps,
        side=OrderSide.BUY if payload.side == "buy" else OrderSide.SELL,
        signer=payload.signer,
        signature=payload.signature,
        order_hash=order_hash,
        status=OrderStatus.OPEN,
        filled_amount="0",
    )
    db.add(order)
    await db.flush()  # get order.id without committing

    # Run matching engine.
    matches: list[MatchResult] = await match_new_order(db, order)

    # Apply matches.
    fill_ids = []
    for match in matches:
        fill = await _apply_match(db, order, match, payload.signature)
        if fill:
            fill_ids.append(fill.id)

    await db.commit()
    await db.refresh(order)

    # Trigger settlement for matched fills
    if fill_ids:
        logger.info(f"✅ Created {len(fill_ids)} fills, triggering settlement...")
        # Import here to avoid circular dependency
        try:
            from app.tasks.settlement_tasks import settle_fills
            # Trigger async settlement (non-blocking)
            settle_fills.delay()
        except Exception as e:
            logger.warning(f"Could not trigger settlement task: {e}")
            logger.info("Run settlement manually with: python3 trigger_settlement.py")

    return _order_to_response(order)


async def _apply_match(
    db: AsyncSession,
    taker_order: Order,
    match: MatchResult,
    taker_signature: str,
) -> Fill:
    """Persist a Fill record and update order statuses. Returns the created Fill."""
    fill = Fill(
        maker_order_id=match.maker_order_id,
        taker_order_id=taker_order.id,
        maker_address=match.maker_address,
        taker_address=match.taker_address,
        token_amount=str(match.token_amount),
        collateral_amount=str(match.collateral_amount),
        fee=str(match.fee),
        status=FillStatus.PENDING,
    )
    db.add(fill)
    await db.flush()  # Get fill.id

    # Update maker order.
    maker_order = (
        await db.execute(select(Order).where(Order.id == match.maker_order_id))
    ).scalar_one()
    new_maker_filled = int(maker_order.filled_amount) + match.token_amount
    maker_order.filled_amount = str(new_maker_filled)
    maker_order.status = (
        OrderStatus.FILLED if match.maker_order_exhausted else OrderStatus.PARTIAL
    )

    # Update taker order.
    new_taker_filled = int(taker_order.filled_amount) + match.token_amount
    taker_order.filled_amount = str(new_taker_filled)
    taker_order.status = (
        OrderStatus.FILLED if match.taker_order_exhausted else OrderStatus.PARTIAL
    )

    logger.info(
        "Matched: maker_order=%d taker_order=%d tokens=%d collateral=%d",
        match.maker_order_id,
        taker_order.id,
        match.token_amount,
        match.collateral_amount,
    )
    
    return fill


# ---------------------------------------------------------------------------
# Order cancellation
# ---------------------------------------------------------------------------

async def cancel_order(
    db: AsyncSession,
    order_id: int,
    maker_address: str,
) -> OrderResponse:
    """Cancel an open or partially filled order."""
    order = (
        await db.execute(
            select(Order).where(and_(Order.id == order_id, Order.maker_address == maker_address))
        )
    ).scalar_one_or_none()

    if not order:
        raise ValueError("Order not found")
    if order.status in (OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.EXPIRED):
        raise ValueError(f"Cannot cancel order in status: {order.status}")

    order.status = OrderStatus.CANCELLED
    await db.commit()
    await db.refresh(order)

    # Fire-and-forget on-chain cancel (the contract also prevents the order from
    # being filled once cancelled on-chain).
    try:
        chain_order = _order_to_chain(order)
        await chain.cancel_order_onchain(chain_order)
    except Exception as exc:
        logger.warning("On-chain cancel failed (order already flagged in DB): %s", exc)

    return _order_to_response(order)


# ---------------------------------------------------------------------------
# Markets
# ---------------------------------------------------------------------------

async def get_markets(
    db: AsyncSession,
    category: Optional[str] = None,
    offset: int = 0,
    limit: int = 20,
) -> MarketListResponse:
    query = select(Market).where(Market.status == MarketStatus.ACTIVE)
    if category:
        query = query.where(Market.category == category)
    total_q = query
    query = query.offset(offset).limit(limit).order_by(Market.id.desc())

    markets = (await db.execute(query)).scalars().all()
    total = len((await db.execute(total_q)).scalars().all())

    return MarketListResponse(
        markets=[_market_to_response(m) for m in markets],
        total=total,
        offset=offset,
        limit=limit,
    )


async def get_market(db: AsyncSession, condition_id: str) -> MarketResponse:
    market = (
        await db.execute(select(Market).where(Market.condition_id == condition_id))
    ).scalar_one_or_none()
    if not market:
        raise ValueError(f"Market {condition_id} not found")
    return _market_to_response(market)


async def sync_market_from_chain(db: AsyncSession, condition_id: str) -> MarketResponse:
    """
    Pull market data from MarketFactory on Monad and upsert into the DB.
    Called by a background task when a MarketCreated event is detected.
    """
    chain_data = await chain.get_market(condition_id)
    yes_id, no_id = await chain.get_position_ids(condition_id)

    existing = (
        await db.execute(select(Market).where(Market.condition_id == condition_id))
    ).scalar_one_or_none()

    if existing:
        existing.title = chain_data["title"]
        existing.description = chain_data["description"]
        existing.category = chain_data["category"]
        existing.resolution_time = chain_data["resolution_time"]
        existing.resolved = chain_data["resolved"]
        existing.yes_token_id = str(yes_id)
        existing.no_token_id = str(no_id)
        market = existing
    else:
        market = Market(
            condition_id=condition_id,
            question_id=chain_data["question_id"],
            oracle_address=chain_data["oracle"],
            collateral_token=chain_data["collateral_token"],
            yes_token_id=str(yes_id),
            no_token_id=str(no_id),
            title=chain_data["title"],
            description=chain_data["description"],
            category=chain_data["category"],
            resolution_time=chain_data["resolution_time"],
            status=MarketStatus.RESOLVED if chain_data["resolved"] else MarketStatus.ACTIVE,
        )
        db.add(market)

    await db.commit()
    await db.refresh(market)
    return _market_to_response(market)


# ---------------------------------------------------------------------------
# Orderbook
# ---------------------------------------------------------------------------

async def get_orderbook(
    db: AsyncSession,
    condition_id: str,
    depth: int = 10,
) -> OrderbookResponse:
    market = (
        await db.execute(select(Market).where(Market.condition_id == condition_id))
    ).scalar_one_or_none()
    if not market:
        raise ValueError("Market not found")

    yes_snap = await get_orderbook_snapshot(db, condition_id, market.yes_token_id or "", depth)
    no_snap = await get_orderbook_snapshot(db, condition_id, market.no_token_id or "", depth)

    return OrderbookResponse(
        condition_id=condition_id,
        yes=OrderbookSide(bids=yes_snap["bids"], asks=yes_snap["asks"]),
        no=OrderbookSide(bids=no_snap["bids"], asks=no_snap["asks"]),
        mid_price_yes=yes_snap.get("mid_price"),
        mid_price_no=no_snap.get("mid_price"),
    )


# ---------------------------------------------------------------------------
# Positions
# ---------------------------------------------------------------------------

async def get_positions(db: AsyncSession, wallet: str) -> PositionsResponse:
    """Read on-chain ERC-1155 balances for all active markets."""
    markets = (
        await db.execute(select(Market).where(Market.status == MarketStatus.ACTIVE))
    ).scalars().all()

    positions: list[PositionItem] = []
    for m in markets:
        if not m.yes_token_id or not m.no_token_id:
            continue
        try:
            yes_balance = await chain.get_position_balance(wallet, int(m.yes_token_id))
            no_balance = await chain.get_position_balance(wallet, int(m.no_token_id))
        except Exception as exc:
            logger.warning("Failed to fetch balance for market %s: %s", m.condition_id, exc)
            continue

        if yes_balance > 0 or no_balance > 0:
            positions.append(
                PositionItem(
                    condition_id=m.condition_id,
                    market_title=m.title,
                    yes_token_id=m.yes_token_id,
                    no_token_id=m.no_token_id,
                    yes_balance=str(yes_balance),
                    no_balance=str(no_balance),
                    yes_price=str(m.yes_price) if m.yes_price else None,
                    no_price=str(m.no_price) if m.no_price else None,
                )
            )

    return PositionsResponse(wallet=wallet, positions=positions)


# ---------------------------------------------------------------------------
# Order / Fill history
# ---------------------------------------------------------------------------

async def get_user_orders(
    db: AsyncSession,
    maker_address: Optional[str] = None,
    status: Optional[str] = None,
    condition_id: Optional[str] = None,
    offset: int = 0,
    limit: int = 50,
) -> list[OrderResponse]:
    query = select(Order)
    if maker_address:
        query = query.where(Order.maker_address == maker_address)
    if status:
        query = query.where(Order.status == status)
    if condition_id:
        query = query.where(Order.condition_id == condition_id)
    query = query.order_by(Order.id.desc()).offset(offset).limit(limit)
    orders = (await db.execute(query)).scalars().all()
    return [_order_to_response(o) for o in orders]


async def get_user_fills(
    db: AsyncSession,
    wallet: Optional[str] = None,
    offset: int = 0,
    limit: int = 50,
) -> list[FillResponse]:
    """Return fills where the wallet is maker or taker."""
    query = select(Fill)
    if wallet:
        from sqlalchemy import or_
        query = query.where(
            or_(Fill.maker_address == wallet, Fill.taker_address == wallet)
        )
    query = query.order_by(Fill.id.desc()).offset(offset).limit(limit)
    fills = (await db.execute(query)).scalars().all()
    return [_fill_to_response(f) for f in fills]


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _order_to_response(order: Order) -> OrderResponse:
    return OrderResponse(
        id=order.id,
        condition_id=order.condition_id,
        token_id=order.token_id,
        maker_address=order.maker_address,
        maker_amount=order.maker_amount,
        taker_amount=order.taker_amount,
        expiration=order.expiration,
        nonce=order.nonce,
        fee_rate_bps=order.fee_rate_bps,
        side=order.side,
        status=order.status,
        filled_amount=order.filled_amount,
        order_hash=order.order_hash,
        created_at=order.created_at.isoformat() if order.created_at else "",
    )


def _market_to_response(m: Market) -> MarketResponse:
    return MarketResponse(
        condition_id=m.condition_id,
        question_id=m.question_id,
        oracle_address=m.oracle_address,
        collateral_token=m.collateral_token,
        yes_token_id=m.yes_token_id,
        no_token_id=m.no_token_id,
        title=m.title,
        description=m.description,
        category=m.category,
        resolution_time=m.resolution_time,
        status=m.status,
        yes_payout=m.yes_payout,
        no_payout=m.no_payout,
        yes_price=str(m.yes_price) if m.yes_price else None,
        no_price=str(m.no_price) if m.no_price else None,
        volume_24h=str(m.volume_24h) if m.volume_24h else None,
        total_volume=str(m.total_volume) if m.total_volume else None,
    )


def _fill_to_response(f: Fill) -> FillResponse:
    return FillResponse(
        id=f.id,
        maker_order_id=f.maker_order_id,
        taker_order_id=f.taker_order_id,
        maker_address=f.maker_address,
        taker_address=f.taker_address,
        token_amount=f.token_amount,
        collateral_amount=f.collateral_amount,
        fee=f.fee,
        status=f.status,
        tx_hash=f.tx_hash,
        settled_at=f.settled_at,
        created_at=f.created_at.isoformat() if f.created_at else "",
    )


def _order_to_chain(order: Order) -> ChainOrder:
    return ChainOrder(
        maker=order.maker_address,
        token_id=int(order.token_id),
        maker_amount=int(order.maker_amount),
        taker_amount=int(order.taker_amount),
        expiration=order.expiration,
        nonce=order.nonce,
        fee_rate_bps=order.fee_rate_bps,
        side=ChainOrderSide.BUY if order.side == OrderSide.BUY else ChainOrderSide.SELL,
        signer=order.signer,
    )
