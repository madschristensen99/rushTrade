"""
settlement_tasks.py
-------------------
Celery task for settling matched orders on-chain via CTFExchange.

This task:
1. Fetches PENDING fills from the database
2. Groups fills by market
3. Calls CTFExchange.fillOrders() to execute trades on-chain
4. Updates fill status to SETTLED or FAILED
"""

import asyncio
import logging
from typing import List

from celery import Celery
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database.connection import get_db_session
from app.modules.terminal.clob.models import Fill, FillStatus, Order
from app.services.chain_service import chain

settings = get_settings()
logger = logging.getLogger(__name__)

# Celery app
celery = Celery(
    "settlement_tasks",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.redis_url,
)
celery.conf.update(task_serializer="json")


def _run(coro):
    """Helper to run async coroutines in Celery tasks"""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _settle_fills_async() -> None:
    """
    Settle all PENDING fills by calling CTFExchange.fillOrders() on-chain.
    
    Process:
    1. Fetch all PENDING fills
    2. Group by market/condition
    3. For each group, prepare Order structs and signatures
    4. Call CTFExchange.fillOrders() with the batch
    5. Update fill status based on transaction result
    """
    async for db in get_db_session():
        # Fetch all PENDING fills
        result = await db.execute(
            select(Fill)
            .where(Fill.status == FillStatus.PENDING)
            .limit(100)  # Process in batches
        )
        pending_fills = result.scalars().all()
        
        if not pending_fills:
            logger.debug("No pending fills to settle")
            return
        
        logger.info(f"Found {len(pending_fills)} pending fills to settle")
        
        # Group fills by condition (for batch settlement)
        fills_by_condition = {}
        for fill in pending_fills:
            # Get the maker order to find condition_id
            maker_order = await db.get(Order, fill.maker_order_id)
            if not maker_order:
                logger.error(f"Fill {fill.id}: maker order {fill.maker_order_id} not found")
                continue
            
            condition_id = maker_order.condition_id
            if condition_id not in fills_by_condition:
                fills_by_condition[condition_id] = []
            fills_by_condition[condition_id].append((fill, maker_order))
        
        # Settle each condition's fills
        for condition_id, fills_data in fills_by_condition.items():
            await _settle_condition_fills(db, condition_id, fills_data)
        
        await db.commit()


async def _settle_condition_fills(
    db: AsyncSession,
    condition_id: str,
    fills_data: List[tuple]
) -> None:
    """
    Settle fills for a specific condition by calling CTFExchange.fillOrders().
    
    Args:
        db: Database session
        condition_id: The market condition ID
        fills_data: List of (Fill, Order) tuples
    """
    logger.info(f"Settling {len(fills_data)} fills for condition {condition_id}")
    
    # Prepare orders and signatures for CTFExchange
    orders = []
    fill_amounts = []
    signatures = []
    fill_ids = []
    
    for fill, maker_order in fills_data:
        # Get taker order if it exists
        taker_order = None
        if fill.taker_order_id:
            taker_order = await db.get(Order, fill.taker_order_id)
        
        # For now, we'll use the maker order
        # In a full implementation, you'd match maker and taker orders
        
        # Build ChainOrder struct
        from app.services.chain_service import ChainOrder, OrderSide as ChainOrderSide
        
        chain_order = ChainOrder(
            maker=maker_order.maker_address,
            token_id=int(maker_order.token_id),
            maker_amount=int(maker_order.maker_amount),
            taker_amount=int(maker_order.taker_amount),
            expiration=maker_order.expiration,
            nonce=maker_order.nonce,
            fee_rate_bps=maker_order.fee_rate_bps,
            side=ChainOrderSide.BUY if maker_order.side.value == "buy" else ChainOrderSide.SELL,
            signer=maker_order.signer if maker_order.signer != "0x" + "0" * 40 else maker_order.maker_address,
        )
        
        orders.append(chain_order)
        fill_amounts.append(int(fill.token_amount))
        signatures.append(maker_order.signature)
        fill_ids.append(fill.id)
    
    # Call CTFExchange.fillOrders() on-chain
    try:
        logger.info(f"Calling CTFExchange.fillOrders() with {len(orders)} orders")
        
        # Execute on-chain!
        tx_hash = await chain.fill_orders(orders, fill_amounts, signatures)
        logger.info(f"✅ Settlement TX: {tx_hash}")
        
        # Update fills to SETTLED
        for fill_id in fill_ids:
            await db.execute(
                update(Fill)
                .where(Fill.id == fill_id)
                .values(
                    status=FillStatus.SETTLED,
                    tx_hash=tx_hash,
                    settled_at=asyncio.get_event_loop().time()
                )
            )
        
        logger.info(f"✅ Settled {len(fill_ids)} fills with TX {tx_hash}")
        
    except Exception as exc:
        logger.error(f"❌ Failed to settle fills for {condition_id}: {exc}")
        
        # Mark fills as FAILED
        for fill_id in fill_ids:
            await db.execute(
                update(Fill)
                .where(Fill.id == fill_id)
                .values(status=FillStatus.FAILED)
            )


@celery.task(name="settlement_tasks.settle_fills")
def settle_fills():
    """
    Celery task: Settle all pending fills by executing them on-chain.
    
    This should be scheduled to run every few seconds (e.g., every 5-10 seconds)
    to quickly settle matched orders.
    """
    logger.info("Running settlement task...")
    _run(_settle_fills_async())
    logger.info("Settlement task complete")


@celery.task(name="settlement_tasks.settle_fill_by_id")
def settle_fill_by_id(fill_id: int):
    """
    Celery task: Settle a specific fill immediately.
    
    Can be triggered right after a match is created.
    """
    logger.info(f"Settling fill {fill_id}")
    
    async def _settle_one():
        async for db in get_db_session():
            fill = await db.get(Fill, fill_id)
            if not fill or fill.status != FillStatus.PENDING:
                logger.warning(f"Fill {fill_id} not found or not pending")
                return
            
            maker_order = await db.get(Order, fill.maker_order_id)
            if not maker_order:
                logger.error(f"Maker order {fill.maker_order_id} not found")
                return
            
            await _settle_condition_fills(db, maker_order.condition_id, [(fill, maker_order)])
            await db.commit()
    
    _run(_settle_one())
    logger.info(f"Fill {fill_id} settlement complete")
