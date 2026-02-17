"""
trading_tasks.py
----------------
Celery tasks for the $rush CLOB settlement layer.

Tasks
-----
  settle_fills        – Pick up PENDING Fill records and submit fillOrders()
                        transactions to CTFExchange on Monad.  Runs every ~2 s.

  expire_orders       – Mark orders whose expiration timestamp has passed as
                        EXPIRED.  Runs every 60 s.

  sync_markets        – Pull new on-chain market data from MarketFactory and
                        upsert into the DB.  Runs every 30 s.

  update_market_stats – Recompute yes_price / no_price / volume_24h for each
                        market from the CLOB fill history.  Runs every 10 s.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from celery import Celery
from celery.schedules import crontab

from app.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)

celery = Celery(
    "trading_tasks",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.redis_url,
)
celery.conf.update(
    task_serializer="json",
    result_expires=3600,
    worker_prefetch_multiplier=1,
    beat_schedule={
        "settle-fills-every-2s": {
            "task": "trading_tasks.settle_fills",
            "schedule": 2.0,
        },
        "expire-orders-every-60s": {
            "task": "trading_tasks.expire_orders",
            "schedule": 60.0,
        },
        "sync-markets-every-30s": {
            "task": "trading_tasks.sync_markets",
            "schedule": 30.0,
        },
        "update-market-stats-every-10s": {
            "task": "trading_tasks.update_market_stats",
            "schedule": 10.0,
        },
        "rotate-btc-round-every-minute": {
            "task": "btc_market_tasks.rotate_btc_round",
            "schedule": crontab(minute="*"),
        },
    },
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(coro):
    """Run an async coroutine synchronously inside a Celery task."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# settle_fills
# ---------------------------------------------------------------------------

@celery.task(name="trading_tasks.settle_fills")
def settle_fills():
    """
    Find all PENDING fill records and submit fillOrders() to CTFExchange.

    Batches up to 20 fills per Celery tick into a single on-chain tx to
    minimise gas costs and latency on Monad.
    """
    _run(_settle_fills_async())


async def _settle_fills_async():
    from sqlalchemy import select, update
    from app.modules.terminal.clob.models import Fill, FillStatus, Order, OrderSide
    from app.services.chain_service import ChainOrder, OrderSide as ChainOrderSide, chain
    from app.database.connection import async_session_factory

    async with async_session_factory() as db:
        pending = (
            await db.execute(
                select(Fill)
                .where(Fill.status == FillStatus.PENDING)
                .order_by(Fill.id)
                .limit(20)
            )
        ).scalars().all()

        if not pending:
            return

        chain_orders: list[ChainOrder] = []
        fill_amounts: list[int] = []
        signatures: list[bytes] = []
        fill_ids: list[int] = []

        for fill in pending:
            maker_order = (
                await db.execute(
                    select(Order).where(Order.id == fill.maker_order_id)
                )
            ).scalar_one_or_none()
            if not maker_order:
                continue

            co = ChainOrder(
                maker=maker_order.maker_address,
                token_id=int(maker_order.token_id),
                maker_amount=int(maker_order.maker_amount),
                taker_amount=int(maker_order.taker_amount),
                expiration=maker_order.expiration,
                nonce=maker_order.nonce,
                fee_rate_bps=maker_order.fee_rate_bps,
                side=ChainOrderSide.BUY if maker_order.side == OrderSide.BUY else ChainOrderSide.SELL,
                signer=maker_order.signer,
            )
            chain_orders.append(co)
            fill_amounts.append(int(fill.token_amount))
            signatures.append(bytes.fromhex(maker_order.signature.lstrip("0x")))
            fill_ids.append(fill.id)

        if not chain_orders:
            return

        try:
            tx_hash = await chain.fill_orders_batch(chain_orders, fill_amounts, signatures)

            now_str = datetime.now(timezone.utc).isoformat()
            await db.execute(
                update(Fill)
                .where(Fill.id.in_(fill_ids))
                .values(
                    status=FillStatus.SETTLED,
                    tx_hash=tx_hash,
                    settled_at=now_str,
                )
            )
            await db.commit()
            logger.info("Settled %d fills in tx %s", len(fill_ids), tx_hash)

        except Exception as exc:
            logger.error("settle_fills failed: %s", exc)
            await db.execute(
                update(Fill)
                .where(Fill.id.in_(fill_ids))
                .values(status=FillStatus.FAILED)
            )
            await db.commit()


# ---------------------------------------------------------------------------
# expire_orders
# ---------------------------------------------------------------------------

@celery.task(name="trading_tasks.expire_orders")
def expire_orders():
    """Mark orders whose expiration has passed as EXPIRED."""
    _run(_expire_orders_async())


async def _expire_orders_async():
    from sqlalchemy import update
    from app.modules.terminal.clob.models import Order, OrderStatus
    from app.database.connection import async_session_factory

    now_ts = int(time.time())

    async with async_session_factory() as db:
        result = await db.execute(
            update(Order)
            .where(
                Order.expiration > 0,
                Order.expiration <= now_ts,
                Order.status.in_([OrderStatus.OPEN, OrderStatus.PARTIAL]),
            )
            .values(status=OrderStatus.EXPIRED)
            .returning(Order.id)
        )
        expired_ids = [row[0] for row in result.fetchall()]
        if expired_ids:
            await db.commit()
            logger.info("Expired %d orders", len(expired_ids))


# ---------------------------------------------------------------------------
# sync_markets
# ---------------------------------------------------------------------------

@celery.task(name="trading_tasks.sync_markets")
def sync_markets():
    """
    Pull the list of on-chain markets from MarketFactory and upsert any
    that are not yet in the database.
    """
    _run(_sync_markets_async())


async def _sync_markets_async():
    from sqlalchemy import select
    from app.modules.terminal.clob.models import Market
    from app.modules.terminal.clob.service import sync_market_from_chain
    from app.services.chain_service import chain
    from app.database.connection import async_session_factory

    try:
        total = await chain.get_market_count()
        if total == 0:
            return
        condition_ids = await chain.get_market_condition_ids(0, int(total))
    except Exception as exc:
        logger.warning("sync_markets chain read failed: %s", exc)
        return

    async with async_session_factory() as db:
        for cid in condition_ids:
            existing = (
                await db.execute(
                    select(Market).where(Market.condition_id == cid)
                )
            ).scalar_one_or_none()
            if not existing:
                try:
                    await sync_market_from_chain(db, cid)
                    logger.info("Synced new market: %s", cid)
                except Exception as exc:
                    logger.warning("Failed to sync market %s: %s", cid, exc)


# ---------------------------------------------------------------------------
# update_market_stats
# ---------------------------------------------------------------------------

@celery.task(name="trading_tasks.update_market_stats")
def update_market_stats():
    """
    Recompute mid-price and volume for all active markets and push to Redis
    so the WebSocket layer can broadcast live price updates to clients.
    """
    _run(_update_market_stats_async())


async def _update_market_stats_async():
    import json as _json
    from sqlalchemy import func, select
    from app.modules.terminal.clob.models import Fill, FillStatus, Market, MarketStatus, Order
    from app.modules.terminal.clob.matching import get_orderbook_snapshot
    from app.database.connection import async_session_factory
    from app.services.redis_service import get_redis

    async with async_session_factory() as db:
        markets = (
            await db.execute(
                select(Market).where(Market.status == MarketStatus.ACTIVE)
            )
        ).scalars().all()

        redis = await get_redis()

        for market in markets:
            if not market.yes_token_id or not market.no_token_id:
                continue
            try:
                ob = await get_orderbook_snapshot(
                    db, market.condition_id, market.yes_token_id, depth=1
                )
                if ob["mid_price"]:
                    market.yes_price = float(ob["mid_price"])
                    market.no_price = round(1.0 - float(ob["mid_price"]), 6)

                fills_q = (
                    select(func.sum(Fill.collateral_amount.cast("numeric")))
                    .join(Order, Fill.maker_order_id == Order.id)
                    .where(
                        Order.condition_id == market.condition_id,
                        Fill.status == FillStatus.SETTLED,
                    )
                )
                vol_result = (await db.execute(fills_q)).scalar()
                if vol_result is not None:
                    market.total_volume = float(vol_result)

                stats = {
                    "condition_id": market.condition_id,
                    "yes_price": str(market.yes_price),
                    "no_price": str(market.no_price),
                    "total_volume": str(market.total_volume),
                }
                await redis.publish(
                    f"market:{market.condition_id}:stats",
                    _json.dumps(stats),
                )
            except Exception as exc:
                logger.warning(
                    "update_market_stats failed for %s: %s", market.condition_id, exc
                )

        await db.commit()
