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

from app.dependencies import get_current_user, get_db
from app.modules.terminal.clob import service
from app.modules.terminal.clob.schema import (
    FillResponse,
    MarketListResponse,
    MarketResponse,
    OrderCreate,
    OrderResponse,
    OrderbookResponse,
    PositionsResponse,
)
from app.modules.user.models import User
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
    current_user: User = Depends(get_current_user),
):
    """
    Admin: pull the latest market data from the MarketFactory contract and
    upsert it into the database.  Used when a new market is created on-chain.
    """
    if not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Admin only")
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
    current_user: User = Depends(get_current_user),
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
        return await service.submit_order(db, payload, current_user)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/orders", response_model=list[OrderResponse], tags=["CLOB – Orders"])
async def list_orders(
    status: Optional[str] = Query(None, description="Filter: open, partial, filled, cancelled, expired"),
    condition_id: Optional[str] = Query(None, description="Filter by market condition ID"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return the authenticated user's order history."""
    return await service.get_user_orders(
        db, current_user, status=status, condition_id=condition_id, offset=offset, limit=limit
    )


@router.delete("/orders/{order_id}", response_model=OrderResponse, tags=["CLOB – Orders"])
async def cancel_order(
    order_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Cancel an open or partially filled order.

    The backend marks it cancelled in the DB and also calls cancelOrder() on the
    CTFExchange contract so the signature can no longer be used to fill on-chain.
    """
    try:
        return await service.cancel_order(db, order_id, current_user)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ---------------------------------------------------------------------------
# Positions
# ---------------------------------------------------------------------------

@router.get("/positions/{wallet}", response_model=PositionsResponse, tags=["CLOB – Positions"])
async def get_positions(
    wallet: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
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
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return fill history for the authenticated user."""
    return await service.get_user_fills(db, current_user, offset=offset, limit=limit)


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
# Chain health (debug / monitoring)
# ---------------------------------------------------------------------------

@router.get("/health/chain", tags=["CLOB – Info"])
async def chain_health():
    """Check connectivity to the Monad RPC node."""
    connected = await chain.is_connected()
    return {"connected": connected}
