"""Currency handling so listings priced in EUR/CHF/GBP/USD can be ranked fairly.

We can't reach a live FX API from every environment, so we use approximate
static rates. They only need to be good enough to compare listings sensibly —
display always keeps the original currency, and only the *ranking* uses the
EUR-equivalent.
"""

from __future__ import annotations

from typing import Optional

# Approximate conversion rates to EUR (1 unit of currency = N EUR).
# Update if you need precise figures; ranking is tolerant of small drift.
CURRENCY_TO_EUR: dict[str, float] = {
    "EUR": 1.0,
    "CHF": 1.04,
    "GBP": 1.17,
    "USD": 0.92,
}


def to_eur(price: Optional[float], currency: str) -> Optional[float]:
    """Convert ``price`` in ``currency`` to an approximate EUR amount."""
    if price is None:
        return None
    rate = CURRENCY_TO_EUR.get((currency or "EUR").upper(), 1.0)
    return price * rate
