"""Core portfolio data types."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Holding:
    """A single position.

    ``asset_class`` and ``sector`` are free-form labels used to aggregate
    exposures and to map stress scenarios onto positions.
    """

    ticker: str
    quantity: float
    cost_basis: float = 0.0  # per-unit average cost, for P&L
    asset_class: str = "Equity"
    sector: str = "Unclassified"
    currency: str = "USD"


@dataclass
class PriceSeries:
    """Aligned daily close history for one ticker (oldest first)."""

    ticker: str
    dates: list[str]
    closes: list[float]

    @property
    def last(self) -> float:
        return self.closes[-1] if self.closes else 0.0


@dataclass
class Portfolio:
    name: str = "My Portfolio"
    base_currency: str = "USD"
    holdings: list[Holding] = field(default_factory=list)

    def tickers(self) -> list[str]:
        return [h.ticker for h in self.holdings]
