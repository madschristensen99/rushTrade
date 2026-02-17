"""
clob/matching.py
----------------
Price-time priority CLOB matching engine for $rush.

How it works
------------
  - BUY orders rest on the book at a maximum price (maker pays collateral, wants tokens).
  - SELL orders rest on the book at a minimum price (maker pays tokens, wants collateral).
  - Implied price for BUY  = maker_amount / taker_amount  (collateral per token)
  - Implied price for SELL = taker_amount / maker_amount  (collateral per token)

  Orders match when:  best BUY price >= best SELL price
  Settlement uses the resting (maker) order's price.

Data flow
---------
  1. New order arrives via POST /orders.
  2. CLOBService.submit_order() calls match_new_order().
  3. Matched pairs are returned as MatchResult objects.
  4. CLOBService persists Fill records (status=PENDING).
  5. The Celery task `settle_fills` picks up PENDING fills and submits
     fillOrders() transactions to CTFExchange via ChainService.

All quantities are in integer token units (uint256, stored as strings in DB).
Use Python `int()` for arithmetic.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import and_, select

from app.modules.terminal.clob.models import Order, OrderSide, OrderStatus

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class MatchResult:
    """A single crossing between a resting (maker) order and an incoming (taker) order."""
    maker_order_id: int
    taker_order_id: int
    maker_address: str
    taker_address: str
    token_amount: int        # YES or NO tokens transferred (integer units)
    collateral_amount: int   # Collateral transferred (integer units)
    fee: int                 # Protocol fee in collateral (integer units, computed later)
    maker_order_exhausted: bool   # True → remove maker from book
    taker_order_exhausted: bool   # True → taker is fully filled


# ---------------------------------------------------------------------------
# Price helpers
# ---------------------------------------------------------------------------

def _buy_price(order: Order) -> Decimal:
    """Collateral per token a BUY maker is willing to pay."""
    return Decimal(order.maker_amount) / Decimal(order.taker_amount)


def _sell_price(order: Order) -> Decimal:
    """Collateral per token a SELL maker requires."""
    return Decimal(order.taker_amount) / Decimal(order.maker_amount)


def _remaining_tokens(order: Order) -> int:
    """How many tokens are still available on this order."""
    if order.side == OrderSide.BUY:
        return int(order.taker_amount) - int(order.filled_amount)
    else:
        return int(order.maker_amount) - int(order.filled_amount)


# ---------------------------------------------------------------------------
# Matching engine
# ---------------------------------------------------------------------------

async def match_new_order(
    db: AsyncSession,
    new_order: Order,
    protocol_fee_bps: int = 50,
) -> list[MatchResult]:
    """
    Try to match `new_order` against resting orders on the opposite side.

    Returns a list of MatchResult objects.  The caller is responsible for:
      - Updating filled_amount on both orders.
      - Updating order status (OPEN → PARTIAL / FILLED).
      - Creating Fill records.

    Does NOT commit; the caller manages the transaction.
    """
    results: list[MatchResult] = []

    is_buy = new_order.side == OrderSide.BUY

    # Remaining tokens in the incoming order.
    taker_remaining = _remaining_tokens(new_order)
    if taker_remaining <= 0:
        return results

    # Fetch resting orders on the opposite side, sorted by best price first,
    # then by creation time (oldest first) for time priority.
    if is_buy:
        # Incoming BUY: match against resting SELL orders (lowest ask first).
        resting_query = (
            select(Order)
            .where(
                and_(
                    Order.condition_id == new_order.condition_id,
                    Order.token_id == new_order.token_id,
                    Order.side == OrderSide.SELL,
                    Order.status.in_([OrderStatus.OPEN, OrderStatus.PARTIAL]),
                )
            )
            .order_by(Order.id)  # we'll sort in Python by price
        )
    else:
        # Incoming SELL: match against resting BUY orders (highest bid first).
        resting_query = (
            select(Order)
            .where(
                and_(
                    Order.condition_id == new_order.condition_id,
                    Order.token_id == new_order.token_id,
                    Order.side == OrderSide.BUY,
                    Order.status.in_([OrderStatus.OPEN, OrderStatus.PARTIAL]),
                )
            )
            .order_by(Order.id)
        )

    resting_orders_raw: list[Order] = (await db.execute(resting_query)).scalars().all()

    # Sort by price (best first), then by id (oldest first within same price).
    if is_buy:
        resting_orders = sorted(resting_orders_raw, key=lambda o: (_sell_price(o), o.id))
    else:
        resting_orders = sorted(resting_orders_raw, key=lambda o: (-_buy_price(o), o.id))

    incoming_price = _buy_price(new_order) if is_buy else _sell_price(new_order)

    for maker_order in resting_orders:
        if taker_remaining <= 0:
            break

        maker_price = _sell_price(maker_order) if is_buy else _buy_price(maker_order)

        # Check crossing condition.
        if is_buy:
            if incoming_price < maker_price:
                break  # no more crossings (orders are sorted cheapest first)
        else:
            if incoming_price > maker_price:
                break  # no more crossings (orders are sorted most expensive first)

        maker_remaining = _remaining_tokens(maker_order)
        if maker_remaining <= 0:
            continue

        # Fill quantity = min of what each side has available.
        fill_tokens = min(taker_remaining, maker_remaining)

        # Collateral at the maker's price (favour maker so they get their price exactly).
        if is_buy:
            # Maker is the SELL order: collateral = fill_tokens * sell_price
            collateral = int(
                (Decimal(fill_tokens) * _sell_price(maker_order)).to_integral_value(ROUND_DOWN)
            )
        else:
            # Maker is the BUY order: collateral = fill_tokens * buy_price
            collateral = int(
                (Decimal(fill_tokens) * _buy_price(maker_order)).to_integral_value(ROUND_DOWN)
            )

        # Protocol fee (on collateral leg, taken from taker).
        fee = (collateral * protocol_fee_bps) // 10_000

        results.append(
            MatchResult(
                maker_order_id=maker_order.id,
                taker_order_id=new_order.id,
                maker_address=maker_order.maker_address,
                taker_address=new_order.maker_address,
                token_amount=fill_tokens,
                collateral_amount=collateral,
                fee=fee,
                maker_order_exhausted=(fill_tokens >= maker_remaining),
                taker_order_exhausted=(fill_tokens >= taker_remaining),
            )
        )

        taker_remaining -= fill_tokens

    return results


# ---------------------------------------------------------------------------
# Orderbook snapshot (for GET /markets/{condition_id}/orderbook)
# ---------------------------------------------------------------------------

@dataclass
class AggregatedLevel:
    price: Decimal
    size: int
    order_count: int


async def get_orderbook_snapshot(
    db: AsyncSession,
    condition_id: str,
    token_id: str,
    depth: int = 10,
) -> dict:
    """
    Aggregate resting BUY / SELL orders into price levels.

    Returns a dict with keys 'bids' and 'asks', each a list of
    { price, size, order_count } dicts, formatted as decimal strings.
    """
    query = (
        select(Order)
        .where(
            and_(
                Order.condition_id == condition_id,
                Order.token_id == token_id,
                Order.status.in_([OrderStatus.OPEN, OrderStatus.PARTIAL]),
            )
        )
    )
    orders: list[Order] = (await db.execute(query)).scalars().all()

    buy_levels: dict[Decimal, AggregatedLevel] = {}
    sell_levels: dict[Decimal, AggregatedLevel] = {}

    PRICE_PRECISION = Decimal("0.000001")

    for o in orders:
        remaining = _remaining_tokens(o)
        if remaining <= 0:
            continue
        if o.side == OrderSide.BUY:
            price = _buy_price(o).quantize(PRICE_PRECISION)
            if price not in buy_levels:
                buy_levels[price] = AggregatedLevel(price=price, size=0, order_count=0)
            buy_levels[price].size += remaining
            buy_levels[price].order_count += 1
        else:
            price = _sell_price(o).quantize(PRICE_PRECISION)
            if price not in sell_levels:
                sell_levels[price] = AggregatedLevel(price=price, size=0, order_count=0)
            sell_levels[price].size += remaining
            sell_levels[price].order_count += 1

    bids = sorted(buy_levels.values(), key=lambda x: -x.price)[:depth]
    asks = sorted(sell_levels.values(), key=lambda x: x.price)[:depth]

    def fmt(level: AggregatedLevel) -> dict:
        return {
            "price": str(level.price),
            "size": str(level.size),
            "order_count": level.order_count,
        }

    mid_price: str | None = None
    if bids and asks:
        mid = (bids[0].price + asks[0].price) / 2
        mid_price = str(mid.quantize(PRICE_PRECISION))

    return {
        "bids": [fmt(b) for b in bids],
        "asks": [fmt(a) for a in asks],
        "mid_price": mid_price,
    }
