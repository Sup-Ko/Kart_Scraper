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


@dataclass
class FactorScenario:
    """A shock expressed in factor space rather than by asset class.

    ``shocks`` maps a factor name to the **return of that factor** (not a yield
    change): ``{"Market": -0.20}`` means the market factor falls 20%. Each
    position then moves by its own estimated sensitivity,
    ``rᵢ = Σ_f βᵢ,f · shock_f``, instead of every equity being assumed to move
    together.
    """

    name: str
    description: str = ""
    shocks: dict[str, float] = field(default_factory=dict)


@dataclass
class FactorScenarioResult:
    name: str
    description: str
    pnl: float
    pnl_pct: float
    shocks: dict[str, float]
    per_position: list[dict]
    unexplained_weight: float = 0.0  # weight with no factor fit
    notes: list[str] = field(default_factory=list)


def apply_factor_scenario(report, factor_report, scenario: FactorScenario
                          ) -> FactorScenarioResult:
    """Propagate a factor shock through each position's fitted betas.

    This is strictly more honest than shocking asset classes directly: a
    low-beta defensive name and a high-beta cyclical do not fall by the same
    amount in a market selloff, and the factor model already measured how much
    each one actually moves.

    Only the *systematic* response is modelled. Idiosyncratic moves — the
    residual the factors do not explain — are not simulated here, so a
    single-name blowup will not appear. That limit is reported rather than
    glossed over.
    """
    fits = {f.ticker: f for f in getattr(factor_report, "assets", [])}
    positions = {p.ticker: p for p in report.positions}

    total_pnl = 0.0
    per_position: list[dict] = []
    unexplained = 0.0

    for ticker, pos in positions.items():
        fit = fits.get(ticker)
        if fit is None:
            unexplained += pos.weight
            continue
        ret = sum(
            fit.betas.get(factor, 0.0) * shock
            for factor, shock in scenario.shocks.items()
        )
        pnl = pos.market_value * ret
        total_pnl += pnl
        per_position.append({
            "ticker": ticker,
            "return": round(ret, 4),
            "pnl": round(pnl, 2),
            "r_squared": fit.r_squared,
        })

    pnl_pct = (total_pnl / report.total_value) if report.total_value else 0.0
    result = FactorScenarioResult(
        name=scenario.name,
        description=scenario.description,
        pnl=round(total_pnl, 2),
        pnl_pct=round(pnl_pct, 4),
        shocks=dict(scenario.shocks),
        per_position=sorted(per_position, key=lambda d: d["pnl"]),
        unexplained_weight=round(unexplained, 4),
    )

    if unexplained > 0.001:
        result.notes.append(
            f"{unexplained:.0%} of the portfolio has no factor fit and is held "
            "flat in this scenario."
        )
    weak = [p for p in per_position if p["r_squared"] < 0.3]
    if weak:
        result.notes.append(
            "Low factor explanatory power for "
            + ", ".join(p["ticker"] for p in weak)
            + " — these move mostly for their own reasons, so their modelled "
              "response understates what could actually happen."
        )
    return result


def default_factor_scenarios() -> list[FactorScenario]:
    """Factor shocks as editable assumptions. Returns of the factor itself."""
    return [
        FactorScenario(
            name="Equity bear market",
            description="Market factor falls 20%; each name moves by its own beta.",
            shocks={"Market": -0.20},
        ),
        FactorScenario(
            name="Flight to quality",
            description="Equities sell off, long bonds rally, gold bid.",
            shocks={"Market": -0.15, "Rates": 0.08, "Gold": 0.10},
        ),
        FactorScenario(
            name="Inflation shock",
            description="Bonds and equities fall together; gold rallies.",
            shocks={"Market": -0.10, "Rates": -0.12, "Gold": 0.15},
        ),
        FactorScenario(
            name="Melt-up",
            description="Risk assets rally hard, safe havens sold.",
            shocks={"Market": 0.15, "Rates": -0.04, "Gold": -0.05},
        ),
    ]


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
