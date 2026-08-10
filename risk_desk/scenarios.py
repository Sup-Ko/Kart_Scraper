"""Stress & scenario analysis.

A scenario is a set of shocks — percentage moves applied by asset class, sector,
or specific ticker. Applying it re-prices the portfolio and reports the P&L. The
built-in scenarios are illustrative, user-editable assumptions, NOT predictions:
you can see and change every shock, unlike a proprietary risk engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .analytics import RiskReport


@dataclass
class Scenario:
    name: str
    description: str = ""
    # shocks are fractional moves, e.g. -0.30 = down 30%
    by_asset_class: dict[str, float] = field(default_factory=dict)
    by_sector: dict[str, float] = field(default_factory=dict)
    by_ticker: dict[str, float] = field(default_factory=dict)


@dataclass
class ScenarioResult:
    name: str
    description: str
    pnl: float
    pnl_pct: float
    per_position: list[dict]


def shock_for(position, scenario: Scenario) -> float:
    """Resolve the shock for a position. Ticker overrides sector overrides class."""
    if position.ticker in scenario.by_ticker:
        return scenario.by_ticker[position.ticker]
    if position.sector in scenario.by_sector:
        return scenario.by_sector[position.sector]
    return scenario.by_asset_class.get(position.asset_class, 0.0)


def apply_scenario(report: RiskReport, scenario: Scenario) -> ScenarioResult:
    total_pnl = 0.0
    per_position = []
    for p in report.positions:
        shock = shock_for(p, scenario)
        pnl = p.market_value * shock
        total_pnl += pnl
        if shock:
            per_position.append(
                {"ticker": p.ticker, "shock": shock, "pnl": round(pnl, 2)}
            )
    pnl_pct = (total_pnl / report.total_value) if report.total_value else 0.0
    return ScenarioResult(
        name=scenario.name,
        description=scenario.description,
        pnl=round(total_pnl, 2),
        pnl_pct=round(pnl_pct, 4),
        per_position=sorted(per_position, key=lambda d: d["pnl"]),
    )


def default_scenarios() -> list[Scenario]:
    """A starter kit of transparent, editable stress scenarios."""
    return [
        Scenario(
            name="2008-style crash",
            description="Broad equity selloff with a flight to bonds.",
            by_asset_class={"Equity": -0.40, "Crypto": -0.55, "Bond": 0.08, "Cash": 0.0},
        ),
        Scenario(
            name="Rates +100bp",
            description="Yields jump; long duration hurts, equities wobble.",
            by_asset_class={"Bond": -0.06, "Equity": -0.05, "Cash": 0.0},
        ),
        Scenario(
            name="Tech selloff",
            description="Rotation out of technology names.",
            by_sector={"Technology": -0.25},
            by_asset_class={"Equity": -0.05},
        ),
        Scenario(
            name="Risk-on rally",
            description="Broad melt-up in risk assets.",
            by_asset_class={"Equity": 0.15, "Crypto": 0.30, "Bond": -0.02},
        ),
    ]
