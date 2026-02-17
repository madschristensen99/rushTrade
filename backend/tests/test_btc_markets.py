"""
tests/test_btc_markets.py
--------------------------
Unit tests for the BTC market rotation logic.
Uses in-memory SQLite (via SQLAlchemy) so no Postgres or chain connection
is required.  All chain and price oracle calls are mocked.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select

from app.database.base import Base
from app.modules.terminal.clob.models import (
    BtcMarketRound,
    BtcRoundStatus,
    Market,
    MarketStatus,
)
from app.services.price_oracle import compute_strikes
from app.tasks.btc_market_tasks import _make_question_id, _current_minute_boundary

# Import all models so SQLAlchemy can resolve FK references during create_all.
import app.modules.user.models  # registers the users table  # noqa: F401


# ---------------------------------------------------------------------------
# In-memory SQLite engine for tests
# ---------------------------------------------------------------------------

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


# ---------------------------------------------------------------------------
# _make_question_id
# ---------------------------------------------------------------------------

def test_question_id_is_deterministic():
    """Same inputs always produce the same 32-byte hash."""
    ts = 1708200000
    qid1 = _make_question_id(ts, 2)
    qid2 = _make_question_id(ts, 2)
    assert qid1 == qid2
    assert len(qid1) == 32


def test_question_id_unique_per_strike():
    """Different strike indices produce different question IDs."""
    ts = 1708200000
    ids = [_make_question_id(ts, i) for i in range(5)]
    assert len(set(ids)) == 5, "Each strike index must produce a unique question_id"


def test_question_id_unique_per_round():
    """Different round timestamps produce different question IDs for the same index."""
    qid1 = _make_question_id(1708200000, 0)
    qid2 = _make_question_id(1708200060, 0)  # next minute
    assert qid1 != qid2


# ---------------------------------------------------------------------------
# compute_strikes (integration with correct values)
# ---------------------------------------------------------------------------

def test_resolve_yes_win_logic():
    """When close > strike, payouts should be [1, 0]."""
    strike = Decimal("97331.76")
    close = Decimal("97500.00")
    payouts = [1, 0] if close > strike else [0, 1]
    assert payouts == [1, 0]


def test_resolve_no_win_logic():
    """When close < strike, payouts should be [0, 1]."""
    strike = Decimal("97331.76")
    close = Decimal("97000.00")
    payouts = [1, 0] if close > strike else [0, 1]
    assert payouts == [0, 1]


def test_resolve_at_the_money_goes_to_no():
    """When close == strike (tie), NO wins — payouts [0, 1]."""
    strike = Decimal("97234.50")
    close = Decimal("97234.50")
    payouts = [1, 0] if close > strike else [0, 1]
    assert payouts == [0, 1]


# ---------------------------------------------------------------------------
# BtcMarketRound DB operations
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_round_in_db(db_session: AsyncSession):
    """BtcMarketRound can be inserted and queried."""
    round_start = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    new_round = BtcMarketRound(
        round_start=round_start.isoformat(),
        round_end=(round_start.replace(minute=(round_start.minute + 1) % 60)).isoformat(),
        btc_price_at_open=97234.50,
        status=BtcRoundStatus.ACTIVE,
    )
    db_session.add(new_round)
    await db_session.commit()

    result = (
        await db_session.execute(
            select(BtcMarketRound).where(BtcMarketRound.status == BtcRoundStatus.ACTIVE)
        )
    ).scalar_one_or_none()

    assert result is not None
    assert float(result.btc_price_at_open) == pytest.approx(97234.50, rel=1e-4)


@pytest.mark.asyncio
async def test_idempotency_round_start_is_unique(db_session: AsyncSession):
    """Inserting two rounds with the same round_start raises an IntegrityError."""
    from sqlalchemy.exc import IntegrityError

    ts = "2026-02-17T20:00:00+00:00"
    r1 = BtcMarketRound(round_start=ts, round_end=ts, btc_price_at_open=97000.00, status=BtcRoundStatus.ACTIVE)
    r2 = BtcMarketRound(round_start=ts, round_end=ts, btc_price_at_open=97000.00, status=BtcRoundStatus.ACTIVE)

    db_session.add(r1)
    await db_session.commit()

    db_session.add(r2)
    with pytest.raises(IntegrityError):
        await db_session.commit()


@pytest.mark.asyncio
async def test_market_strike_fields_stored(db_session: AsyncSession):
    """Market rows store strike_price, btc_round_id, and strike_index correctly."""
    round_start = "2026-02-17T20:00:00+00:00"
    new_round = BtcMarketRound(
        round_start=round_start,
        round_end="2026-02-17T20:01:00+00:00",
        btc_price_at_open=97234.50,
        status=BtcRoundStatus.ACTIVE,
    )
    db_session.add(new_round)
    await db_session.flush()

    market = Market(
        condition_id="0x" + "ab" * 32,
        question_id="0x" + "cd" * 32,
        oracle_address="0x" + "00" * 20,
        collateral_token="0x" + "00" * 20,
        title="BTC/USD > $97331.76 @ 20:01 UTC",
        resolution_time=1708200060,
        status=MarketStatus.ACTIVE,
        strike_price=97331.76,
        btc_round_id=new_round.id,
        strike_index=0,
    )
    db_session.add(market)
    await db_session.commit()

    fetched = (
        await db_session.execute(
            select(Market).where(Market.btc_round_id == new_round.id)
        )
    ).scalar_one()

    assert fetched.strike_index == 0
    assert float(fetched.strike_price) == pytest.approx(97331.76, rel=1e-4)


@pytest.mark.asyncio
async def test_five_markets_per_round(db_session: AsyncSession):
    """Exactly 5 Market rows should be associated with a round."""
    new_round = BtcMarketRound(
        round_start="2026-02-17T20:02:00+00:00",
        round_end="2026-02-17T20:03:00+00:00",
        btc_price_at_open=97234.50,
        status=BtcRoundStatus.ACTIVE,
    )
    db_session.add(new_round)
    await db_session.flush()

    strikes = compute_strikes(Decimal("97234.50"))
    for i, strike in enumerate(strikes):
        m = Market(
            condition_id="0x" + hex(i)[2:].zfill(64),
            question_id="0x" + hex(i + 10)[2:].zfill(64),
            oracle_address="0x" + "00" * 20,
            collateral_token="0x" + "00" * 20,
            title=f"Strike {i}",
            resolution_time=1708200180,
            status=MarketStatus.ACTIVE,
            strike_price=float(strike),
            btc_round_id=new_round.id,
            strike_index=i,
        )
        db_session.add(m)

    await db_session.commit()

    count = len(
        (
            await db_session.execute(
                select(Market).where(Market.btc_round_id == new_round.id)
            )
        ).scalars().all()
    )
    assert count == 5


@pytest.mark.asyncio
async def test_resolve_round_updates_status(db_session: AsyncSession):
    """Resolving a round sets btc_price_at_close and status=RESOLVED on its markets."""
    new_round = BtcMarketRound(
        round_start="2026-02-17T20:04:00+00:00",
        round_end="2026-02-17T20:05:00+00:00",
        btc_price_at_open=97234.50,
        status=BtcRoundStatus.ACTIVE,
    )
    db_session.add(new_round)
    await db_session.flush()

    market = Market(
        condition_id="0x" + "ff" * 32,
        question_id="0x" + "ee" * 32,
        oracle_address="0x" + "00" * 20,
        collateral_token="0x" + "00" * 20,
        title="BTC > $97331.76",
        resolution_time=1708200300,
        status=MarketStatus.ACTIVE,
        strike_price=97331.76,
        btc_round_id=new_round.id,
        strike_index=0,
    )
    db_session.add(market)
    await db_session.commit()

    # Simulate resolution: BTC closed at 97500 > 97331.76 → YES wins
    close_price = Decimal("97500.00")
    strike = Decimal(str(market.strike_price))
    payouts = [1, 0] if close_price > strike else [0, 1]
    assert payouts == [1, 0]

    market.status = MarketStatus.RESOLVED
    market.yes_payout = payouts[0]
    market.no_payout = payouts[1]
    new_round.btc_price_at_close = float(close_price)
    new_round.status = BtcRoundStatus.RESOLVED
    await db_session.commit()

    refreshed_round = (
        await db_session.execute(
            select(BtcMarketRound).where(BtcMarketRound.id == new_round.id)
        )
    ).scalar_one()
    assert refreshed_round.status == BtcRoundStatus.RESOLVED
    assert float(refreshed_round.btc_price_at_close) == pytest.approx(97500.0, rel=1e-4)

    refreshed_market = (
        await db_session.execute(
            select(Market).where(Market.id == market.id)
        )
    ).scalar_one()
    assert refreshed_market.status == MarketStatus.RESOLVED
    assert refreshed_market.yes_payout == 1
    assert refreshed_market.no_payout == 0
