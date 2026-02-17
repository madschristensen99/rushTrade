"""
btc_market_tasks.py
--------------------
Celery task that rotates 60-second BTC/USD prediction markets.

At every UTC minute boundary the task:
  1. Fetches the current BTC/USD price from Pyth.
  2. Resolves the previous active round (calls MarketFactory.resolveMarket
     for each of the 5 strike markets).
  3. Creates a new round with 5 fresh strike markets on-chain.

Beat schedule entry is registered in trading_tasks.py.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from decimal import Decimal

from eth_abi import encode as abi_encode
from eth_utils import keccak

from app.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Celery app (same instance as trading_tasks.py)
# ---------------------------------------------------------------------------

from celery import Celery

celery = Celery(
    "btc_market_tasks",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.redis_url,
)
celery.conf.update(task_serializer="json")


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _current_minute_boundary() -> datetime:
    """Return the current UTC minute (seconds and microseconds zeroed)."""
    now = datetime.now(timezone.utc)
    return now.replace(second=0, microsecond=0)


def _make_question_id(round_start_ts: int, strike_index: int) -> bytes:
    """
    Deterministic 32-byte question ID: keccak256(abi.encode(round_start_ts, strike_index)).
    Mirrors what Solidity would produce with keccak256(abi.encode(...)).
    """
    encoded = abi_encode(["uint256", "uint256"], [round_start_ts, strike_index])
    return keccak(encoded)


# ---------------------------------------------------------------------------
# Core async logic
# ---------------------------------------------------------------------------

async def _rotate_btc_round_async() -> None:
    from sqlalchemy import select, update
    from app.database.connection import async_session_factory
    from app.modules.terminal.clob.models import (
        BtcMarketRound,
        BtcRoundStatus,
        Market,
        MarketStatus,
    )
    from app.services.chain_service import chain
    from app.services.price_oracle import (
        STRIKE_LABELS,
        PriceOracleError,
        compute_strikes,
        get_btc_usd_price,
    )

    # Fetch current BTC price (used both for resolution and as new open price).
    try:
        btc_price = await get_btc_usd_price()
    except PriceOracleError as exc:
        logger.error("rotate_btc_round: price fetch failed — %s", exc)
        return

    round_start = _current_minute_boundary()
    round_start_iso = round_start.isoformat()
    round_end = round_start.replace(minute=(round_start.minute + 1) % 60)
    # Handle hour rollover properly.
    round_end_ts = int(round_start.timestamp()) + 60
    round_end_dt = datetime.fromtimestamp(round_end_ts, tz=timezone.utc)
    round_end_iso = round_end_dt.isoformat()

    async with async_session_factory() as db:
        # ------------------------------------------------------------------
        # 1. Resolve the previous active round (if any).
        # ------------------------------------------------------------------
        prev_round = (
            await db.execute(
                select(BtcMarketRound)
                .where(BtcMarketRound.status == BtcRoundStatus.ACTIVE)
                .order_by(BtcMarketRound.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

        if prev_round is not None:
            markets = (
                await db.execute(
                    select(Market)
                    .where(Market.btc_round_id == prev_round.id)
                    .order_by(Market.strike_index)
                )
            ).scalars().all()

            resolution_errors = 0
            for market in markets:
                try:
                    strike = Decimal(str(market.strike_price))
                    # YES wins if price > strike; tie goes to NO.
                    if btc_price > strike:
                        payouts = [1, 0]
                    else:
                        payouts = [0, 1]

                    await chain.resolve_market_onchain(market.condition_id, payouts)
                    market.status = MarketStatus.RESOLVED
                    market.yes_payout = payouts[0]
                    market.no_payout = payouts[1]
                    logger.info(
                        "Resolved market %s strike=%.2f price=%.2f payouts=%s",
                        market.condition_id, strike, btc_price, payouts,
                    )
                except Exception as exc:
                    resolution_errors += 1
                    logger.error(
                        "Failed to resolve market %s: %s", market.condition_id, exc
                    )

            prev_round.btc_price_at_close = float(btc_price)
            prev_round.status = (
                BtcRoundStatus.FAILED if resolution_errors else BtcRoundStatus.RESOLVED
            )
            await db.commit()

        # ------------------------------------------------------------------
        # 2. Idempotency: skip if round already created for this minute.
        # ------------------------------------------------------------------
        existing = (
            await db.execute(
                select(BtcMarketRound).where(BtcMarketRound.round_start == round_start_iso)
            )
        ).scalar_one_or_none()

        if existing is not None:
            logger.info("rotate_btc_round: round for %s already exists, skipping", round_start_iso)
            return

        # ------------------------------------------------------------------
        # 3. Create new round in DB.
        # ------------------------------------------------------------------
        new_round = BtcMarketRound(
            round_start=round_start_iso,
            round_end=round_end_iso,
            btc_price_at_open=float(btc_price),
            status=BtcRoundStatus.ACTIVE,
        )
        db.add(new_round)
        await db.flush()  # get new_round.id without committing yet

        strikes = compute_strikes(btc_price)
        collateral = settings.COLLATERAL_TOKEN_ADDRESS
        oracle = settings.OPERATOR_PRIVATE_KEY and __import__(
            "eth_account", fromlist=["Account"]
        ).Account.from_key(settings.OPERATOR_PRIVATE_KEY).address

        # ------------------------------------------------------------------
        # 4. Create 5 markets on-chain and in DB.
        # ------------------------------------------------------------------
        round_start_ts = int(round_start.timestamp())
        created_count = 0

        for i, strike in enumerate(strikes):
            label = STRIKE_LABELS[i]
            title = f"BTC/USD > ${strike:,.2f} @ {round_end_dt.strftime('%H:%M')} UTC"
            description = (
                f"Resolves YES if BTC/USD closes above ${strike:,.2f} "
                f"at {round_end_dt.isoformat()}. "
                f"Opening price: ${btc_price:,.2f}. Strike offset: {label}."
            )

            question_id_bytes = _make_question_id(round_start_ts, i)

            try:
                condition_id = await chain.create_market_onchain(
                    question_id=question_id_bytes,
                    oracle=oracle,
                    collateral_token=collateral,
                    resolution_time=round_end_ts,
                    title=title,
                    description=description,
                    category="BTC",
                )

                # Fetch YES/NO token IDs from chain.
                yes_id, no_id = await chain.get_position_ids(condition_id)

                market = Market(
                    condition_id=condition_id,
                    question_id="0x" + question_id_bytes.hex(),
                    oracle_address=oracle,
                    collateral_token=collateral,
                    yes_token_id=str(yes_id),
                    no_token_id=str(no_id),
                    title=title,
                    description=description,
                    category="BTC",
                    resolution_time=round_end_ts,
                    status=MarketStatus.ACTIVE,
                    strike_price=float(strike),
                    btc_round_id=new_round.id,
                    strike_index=i,
                )
                db.add(market)
                created_count += 1
                logger.info(
                    "Created BTC market strike=%s index=%d conditionId=%s",
                    strike, i, condition_id,
                )

            except Exception as exc:
                logger.error("Failed to create market for strike %s: %s", strike, exc)

        await db.commit()
        logger.info(
            "rotate_btc_round complete: round_id=%d price=%.2f created=%d/5",
            new_round.id, btc_price, created_count,
        )


# ---------------------------------------------------------------------------
# Celery task
# ---------------------------------------------------------------------------

@celery.task(name="btc_market_tasks.rotate_btc_round")
def rotate_btc_round():
    """
    Rotate the 60-second BTC/USD market round.
    Resolves the previous round and creates 5 new strike markets.
    Scheduled by Celery beat every minute at :00.
    """
    _run(_rotate_btc_round_async())
