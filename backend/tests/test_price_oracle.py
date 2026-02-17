"""
tests/test_price_oracle.py
--------------------------
Unit tests for app.services.price_oracle.
All HTTP calls are intercepted with unittest.mock so no network is required.
"""

from __future__ import annotations

import json
import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.price_oracle import (
    PriceOracleError,
    STRIKE_LABELS,
    compute_strikes,
    get_btc_usd_price,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pyth_response(price: int, expo: int) -> dict:
    """Build a minimal Pyth Hermes v2 response payload."""
    return {
        "parsed": [
            {
                "price": {
                    "price": str(price),
                    "expo": expo,
                    "conf": "1234567",
                    "publish_time": 1708200000,
                }
            }
        ]
    }


# ---------------------------------------------------------------------------
# get_btc_usd_price
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_parse_pyth_response_positive_price():
    """Positive price with negative exponent is parsed correctly."""
    # price=9723450, expo=-2  →  97234.50
    payload = _pyth_response(9723450, -2)

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = payload

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        result = await get_btc_usd_price()

    assert result == Decimal("97234.50")


@pytest.mark.asyncio
async def test_parse_pyth_response_large_price():
    """BTC price expressed with expo=-8 is parsed and rounded to 2dp."""
    # price=10000000000000, expo=-8  →  100000.00000000  →  rounded 100000.00
    payload = _pyth_response(10_000_000_000_000, -8)

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = payload

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        result = await get_btc_usd_price()

    assert result == Decimal("100000.00")


@pytest.mark.asyncio
async def test_pyth_http_error_raises():
    """HTTP error (e.g. 5xx) raises PriceOracleError."""
    import httpx

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "500 Server Error", request=MagicMock(), response=MagicMock()
            )
        )
        mock_client_cls.return_value = mock_client

        with pytest.raises(PriceOracleError):
            await get_btc_usd_price()


@pytest.mark.asyncio
async def test_pyth_connection_error_raises():
    """Network error raises PriceOracleError."""
    import httpx

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(
            side_effect=httpx.ConnectError("connection refused")
        )
        mock_client_cls.return_value = mock_client

        with pytest.raises(PriceOracleError):
            await get_btc_usd_price()


@pytest.mark.asyncio
async def test_pyth_malformed_response_raises():
    """Response missing 'parsed' key raises PriceOracleError."""
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"unexpected": "data"}

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        with pytest.raises(PriceOracleError):
            await get_btc_usd_price()


# ---------------------------------------------------------------------------
# compute_strikes
# ---------------------------------------------------------------------------

def test_compute_strikes_values_round_number():
    """Round BTC price → strikes are exactly correct offsets."""
    price = Decimal("100000.00")
    strikes = compute_strikes(price)
    assert strikes[0] == Decimal("100100.00")   # +0.1%
    assert strikes[1] == Decimal("100050.00")   # +0.05%
    assert strikes[2] == Decimal("100000.00")   # ATM
    assert strikes[3] == Decimal("99950.00")    # -0.05%
    assert strikes[4] == Decimal("99900.00")    # -0.1%


def test_compute_strikes_ordering():
    """Strikes are returned in strictly descending order."""
    strikes = compute_strikes(Decimal("97234.50"))
    for a, b in zip(strikes, strikes[1:]):
        assert a > b, f"Expected {a} > {b}"


def test_compute_strikes_two_decimal_places():
    """All returned strikes have at most 2 decimal places."""
    strikes = compute_strikes(Decimal("97777.77"))
    for s in strikes:
        # Decimal.as_tuple().exponent gives the scale; -2 means 2dp
        assert s == s.quantize(Decimal("0.01")), f"{s} has more than 2 decimal places"


def test_compute_strikes_count():
    """Always returns exactly 5 strikes."""
    assert len(compute_strikes(Decimal("50000.00"))) == 5


def test_strike_labels_count():
    """STRIKE_LABELS has exactly 5 entries matching strike count."""
    assert len(STRIKE_LABELS) == 5
