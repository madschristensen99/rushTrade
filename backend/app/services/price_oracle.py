"""
price_oracle.py
---------------
Fetches the live BTC/USD price from the Pyth Network Hermes REST API and
computes the five strike prices used for each 60-second BTC market round.

Strike offsets from the mid price at round open:
  index 0  +0.1%   (highest)
  index 1  +0.05%
  index 2   0%     (ATM)
  index 3  -0.05%
  index 4  -0.1%   (lowest)
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

import httpx

PYTH_HERMES_URL = "https://hermes.pyth.network/v2/updates/price/latest"
BTC_USD_FEED_ID = "0xe62df6c8b4a85fe1a67db44dc12de5db330f7ac66b72dc658afedf0f4a415b43"

STRIKE_OFFSETS: list[Decimal] = [
    Decimal("1.001"),    # +0.1%
    Decimal("1.0005"),   # +0.05%
    Decimal("1"),        # ATM
    Decimal("0.9995"),   # -0.05%
    Decimal("0.999"),    # -0.1%
]
STRIKE_LABELS: list[str] = ["+0.1%", "+0.05%", "ATM", "-0.05%", "-0.1%"]

TWO_PLACES = Decimal("0.01")


class PriceOracleError(Exception):
    """Raised when the Pyth price cannot be fetched or parsed."""


async def get_btc_usd_price() -> Decimal:
    """
    Fetch the latest BTC/USD price from Pyth Hermes.

    Returns the price as a Decimal rounded to 2 decimal places.
    Raises PriceOracleError on any HTTP or parse failure.
    """
    params = {"ids[]": BTC_USD_FEED_ID}
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(PYTH_HERMES_URL, params=params)
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise PriceOracleError(f"Pyth HTTP error: {exc}") from exc

    try:
        data = resp.json()
        price_data = data["parsed"][0]["price"]
        raw_price = int(price_data["price"])
        exponent = int(price_data["expo"])
        price = Decimal(raw_price) * Decimal(10) ** exponent
        return price.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
    except (KeyError, IndexError, ValueError, TypeError) as exc:
        raise PriceOracleError(f"Pyth response parse error: {exc}") from exc


def compute_strikes(price: Decimal) -> list[Decimal]:
    """
    Given the BTC price at round open, return the 5 strike prices in
    descending order (highest to lowest), each rounded to 2 decimal places.
    """
    return [
        (price * offset).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        for offset in STRIKE_OFFSETS
    ]
