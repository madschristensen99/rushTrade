"""
clob/views.py
-------------
FastAPI routes for the $rush CLOB.

Mounted at /api/v1/clob (see urls.py).

Routes
------
  Markets
    GET  /markets                    List active markets (paginated, filterable by category)
    GET  /markets/{condition_id}     Get a single market
    GET  /markets/{condition_id}/orderbook  Live orderbook (depth configurable)
    POST /markets/sync/{condition_id}       Sync market from chain (admin only)

  Orders
    POST   /orders                   Submit a signed order
    GET    /orders                   Current user's orders
    DELETE /orders/{order_id}        Cancel an order

  Positions
    GET /positions/{wallet}          On-chain CTF token balances for a wallet

  Fills
    GET /fills                       Current user's fill history

  Info
    GET /eip712/{condition_id}       Return EIP-712 domain + type info for frontend signing
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.modules.terminal.clob import service
from app.modules.terminal.clob.schema import (
    BtcRoundResponse,
    FillResponse,
    MarketListResponse,
    MarketResponse,
    OrderCreate,
    OrderResponse,
    OrderbookResponse,
    PositionsResponse,
    StrikeMarketResponse,
)
from app.services.chain_service import chain

router = APIRouter()


# ---------------------------------------------------------------------------
# Markets
# ---------------------------------------------------------------------------

@router.get("/markets", response_model=MarketListResponse, tags=["CLOB – Markets"])
async def list_markets(
    category: Optional[str] = Query(None, description="Filter by category (e.g. Crypto, Sports)"),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """List all active prediction markets."""
    return await service.get_markets(db, category=category, offset=offset, limit=limit)


@router.get("/markets/{condition_id}", response_model=MarketResponse, tags=["CLOB – Markets"])
async def get_market(
    condition_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Get a single market by its CTF condition ID."""
    try:
        return await service.get_market(db, condition_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get(
    "/markets/{condition_id}/orderbook",
    response_model=OrderbookResponse,
    tags=["CLOB – Markets"],
)
async def get_orderbook(
    condition_id: str,
    depth: int = Query(10, ge=1, le=50, description="Number of price levels per side"),
    db: AsyncSession = Depends(get_db),
):
    """
    Return the live CLOB orderbook for a market.

    The orderbook contains aggregated bids/asks for both the YES and NO
    position tokens, with price expressed as collateral-per-token (0.00 – 1.00).
    """
    try:
        return await service.get_orderbook(db, condition_id, depth=depth)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post(
    "/markets/sync/{condition_id}",
    response_model=MarketResponse,
    tags=["CLOB – Markets"],
)
async def sync_market(
    condition_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Pull the latest market data from the MarketFactory contract and
    upsert it into the database.  Used when a new market is created on-chain.
    """
    try:
        return await service.sync_market_from_chain(db, condition_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Chain sync failed: {exc}")


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------

@router.post("/orders", response_model=OrderResponse, status_code=201, tags=["CLOB – Orders"])
async def submit_order(
    payload: OrderCreate,
    db: AsyncSession = Depends(get_db),
):
    """
    Submit a signed prediction-market order.

    The frontend must:
      1. Build the Order struct matching CTFExchange's EIP-712 schema.
      2. Have the user sign it via their wallet (eth_signTypedData_v4).
      3. POST the struct fields + signature here.

    The backend will:
      - Verify the signature.
      - Compute and store the order hash.
      - Run the matching engine.
      - Persist any matches as PENDING fills.
      - The settlement task will submit matched fills on-chain asynchronously.
    """
    try:
        return await service.submit_order(db, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/orders", response_model=list[OrderResponse], tags=["CLOB – Orders"])
async def list_orders(
    maker_address: Optional[str] = Query(None, description="Filter by maker wallet address"),
    status: Optional[str] = Query(None, description="Filter: open, partial, filled, cancelled, expired"),
    condition_id: Optional[str] = Query(None, description="Filter by market condition ID"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """Return order history, optionally filtered by maker wallet address."""
    return await service.get_user_orders(
        db, maker_address=maker_address, status=status, condition_id=condition_id, offset=offset, limit=limit
    )


@router.delete("/orders/{order_id}", response_model=OrderResponse, tags=["CLOB – Orders"])
async def cancel_order(
    order_id: int,
    maker_address: str = Query(..., description="Wallet address that submitted the order"),
    db: AsyncSession = Depends(get_db),
):
    """
    Cancel an open or partially filled order.

    The backend marks it cancelled in the DB and also calls cancelOrder() on the
    CTFExchange contract so the signature can no longer be used to fill on-chain.
    """
    try:
        return await service.cancel_order(db, order_id, maker_address)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ---------------------------------------------------------------------------
# Positions
# ---------------------------------------------------------------------------

@router.get("/positions/{wallet}", response_model=PositionsResponse, tags=["CLOB – Positions"])
async def get_positions(
    wallet: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Return the on-chain conditional token balances for a wallet across all active markets.

    Reads ERC-1155 balances directly from the ConditionalTokens contract.
    """
    if not wallet.startswith("0x") or len(wallet) != 42:
        raise HTTPException(status_code=400, detail="Invalid wallet address")
    try:
        return await service.get_positions(db, wallet)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Chain read failed: {exc}")


# ---------------------------------------------------------------------------
# Fills
# ---------------------------------------------------------------------------

@router.get("/fills", response_model=list[FillResponse], tags=["CLOB – Fills"])
async def list_fills(
    wallet: Optional[str] = Query(None, description="Filter fills by maker or taker wallet address"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """Return fill history, optionally filtered by wallet address."""
    return await service.get_user_fills(db, wallet=wallet, offset=offset, limit=limit)


# ---------------------------------------------------------------------------
# EIP-712 signing info
# ---------------------------------------------------------------------------

@router.get("/eip712/{condition_id}", tags=["CLOB – Info"])
async def get_eip712_info(condition_id: str):
    """
    Return the EIP-712 domain and Order type definition the frontend needs
    to construct and sign an order for this market.

    The frontend should use this with eth_signTypedData_v4 (MetaMask / viem).
    """
    from app.config import get_settings
    s = get_settings()
    return {
        "domain": {
            "name": "CTFExchange",
            "version": "1",
            "chainId": s.MONAD_CHAIN_ID,
            "verifyingContract": s.CTF_EXCHANGE_ADDRESS,
        },
        "types": {
            "Order": [
                {"name": "maker", "type": "address"},
                {"name": "tokenId", "type": "uint256"},
                {"name": "makerAmount", "type": "uint256"},
                {"name": "takerAmount", "type": "uint256"},
                {"name": "expiration", "type": "uint256"},
                {"name": "nonce", "type": "uint256"},
                {"name": "feeRateBps", "type": "uint256"},
                {"name": "side", "type": "uint8"},
                {"name": "signer", "type": "address"},
            ]
        },
        "primaryType": "Order",
        "condition_id": condition_id,
    }


# ---------------------------------------------------------------------------
# BTC Strike Markets
# ---------------------------------------------------------------------------

@router.get("/btc/round/current", response_model=BtcRoundResponse, tags=["CLOB – BTC Markets"])
async def get_current_btc_round(db: AsyncSession = Depends(get_db)):
    """
    Return the current active 60-second BTC/USD round.

    Includes all 5 strike markets with their live 5-level orderbooks.
    YES bids represent buyers of YES tokens.
    NO bids (= YES asks) represent sellers of YES tokens / buyers of NO tokens.

    Returns 404 if no active round exists yet.
    """
    from datetime import datetime, timezone

    from sqlalchemy import select

    from app.modules.terminal.clob.matching import get_orderbook_snapshot
    from app.modules.terminal.clob.models import BtcMarketRound, BtcRoundStatus, Market
    from app.services.price_oracle import STRIKE_LABELS

    active_round = (
        await db.execute(
            select(BtcMarketRound)
            .where(BtcMarketRound.status == BtcRoundStatus.ACTIVE)
            .order_by(BtcMarketRound.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    if active_round is None:
        raise HTTPException(status_code=404, detail="No active BTC round")

    markets = (
        await db.execute(
            select(Market)
            .where(Market.btc_round_id == active_round.id)
            .order_by(Market.strike_index)
        )
    ).scalars().all()

    now = datetime.now(timezone.utc)
    round_end_dt = datetime.fromisoformat(active_round.round_end)
    seconds_remaining = max(0, int((round_end_dt - now).total_seconds()))

    strike_markets: list[StrikeMarketResponse] = []
    for market in markets:
        # Build empty orderbook if token IDs aren't synced yet.
        if market.yes_token_id and market.no_token_id:
            ob = await service.get_orderbook(db, market.condition_id, depth=5)
        else:
            from app.modules.terminal.clob.schema import OrderbookSide, OrderbookResponse
            empty_side = OrderbookSide(bids=[], asks=[])
            ob = OrderbookResponse(
                condition_id=market.condition_id,
                yes=empty_side,
                no=empty_side,
            )

        idx = market.strike_index if market.strike_index is not None else 0
        strike_markets.append(
            StrikeMarketResponse(
                condition_id=market.condition_id,
                strike_price=f"{float(market.strike_price):.2f}",
                strike_label=STRIKE_LABELS[idx],
                strike_index=idx,
                yes_token_id=market.yes_token_id,
                no_token_id=market.no_token_id,
                orderbook=ob,
            )
        )

    return BtcRoundResponse(
        round_id=active_round.id,
        round_start=active_round.round_start,
        round_end=active_round.round_end,
        btc_price_at_open=f"{float(active_round.btc_price_at_open):.2f}",
        status=active_round.status,
        seconds_remaining=seconds_remaining,
        markets=strike_markets,
    )


# ---------------------------------------------------------------------------
# Chain health (debug / monitoring)
# ---------------------------------------------------------------------------

@router.get("/health/chain", tags=["CLOB – Info"])
async def chain_health():
    """Check connectivity to the Monad RPC node."""
    connected = await chain.is_connected()
    return {"connected": connected}
