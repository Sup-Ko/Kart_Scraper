"""Portfolio analytics — valuation, risk metrics, and risk attribution.

Every metric here carries a plain-English explanation in ``EXPLAIN`` so the
cockpit can show exactly how each number was produced. No black boxes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import stats
from .models import Holding, Portfolio, PriceSeries
from .prices import align

EXPLAIN: dict[str, str] = {
    "market_value": "quantity × latest close, summed across holdings.",
    "weight": "each position's market value ÷ total portfolio value.",
    "pnl": "(latest close − cost basis) × quantity.",
    "ann_return": "geometric annualization of the mean daily portfolio return: (1+μ)^252 − 1.",
    "ann_vol": "standard deviation of daily portfolio returns × √252.",
    "var": "1-day loss not expected to be exceeded at the given confidence. Historical = the percentile of actual daily losses; Parametric = z·σ − μ assuming normal returns.",
    "es": "Expected Shortfall (CVaR): the average loss on the days worse than the VaR threshold — the tail's severity, not just its edge.",
    "beta": "cov(portfolio, benchmark) ÷ var(benchmark) — sensitivity to the benchmark.",
    "max_drawdown": "largest peak-to-trough drop of the portfolio value series.",
    "hhi": "Herfindahl index = Σ(weightᵢ²). 1/HHI is the 'effective number of holdings'.",
    "risk_contribution": "component contribution to volatility: wᵢ·(Σw)ᵢ ÷ σₚ. These sum to total volatility, so you see WHICH positions drive risk — not just that risk exists.",
}


@dataclass
class PositionView:
    ticker: str
    quantity: float
    price: float
    market_value: float
    weight: float
    pnl: float
    asset_class: str
    sector: str
    risk_contribution_pct: float = 0.0


@dataclass
class RiskReport:
    portfolio_name: str
    base_currency: str
    total_value: float
    positions: list[PositionView]
    ann_return: float | None = None
    ann_vol: float | None = None
    var_hist: float | None = None
    var_param: float | None = None
    expected_shortfall: float | None = None
    var_currency: float | None = None
    beta: float | None = None
    max_drawdown: float | None = None
    hhi: float | None = None
    effective_holdings: float | None = None
    confidence: float = 0.95
    correlation: dict = field(default_factory=dict)  # {"tickers": [...], "matrix": [[...]]}
    exposures: dict = field(default_factory=dict)  # {"by_asset_class": {...}, "by_sector": {...}}
    notes: list[str] = field(default_factory=list)


def _portfolio_value_series(
    holdings: list[Holding], dates: list[str], closes: dict[str, list[float]]
) -> list[float]:
    n = len(dates)
    values = [0.0] * n
    for h in holdings:
        series = closes.get(h.ticker)
        if not series:
            continue
        for i in range(n):
            values[i] += h.quantity * series[i]
    return values


def analyze(
    portfolio: Portfolio,
    price_series: dict[str, PriceSeries],
    benchmark: PriceSeries | None = None,
    confidence: float = 0.95,
) -> RiskReport:
    tickers = portfolio.tickers()
    dates, closes = align(price_series, tickers)

    # --- valuation & weights -------------------------------------------------
    latest = {t: closes[t][-1] for t in closes} if dates else {}
    positions: list[PositionView] = []
    total = 0.0
    for h in portfolio.holdings:
        price = latest.get(h.ticker, 0.0)
        mv = h.quantity * price
        total += mv
    for h in portfolio.holdings:
        price = latest.get(h.ticker, 0.0)
        mv = h.quantity * price
        positions.append(
            PositionView(
                ticker=h.ticker,
                quantity=h.quantity,
                price=price,
                market_value=mv,
                weight=(mv / total) if total else 0.0,
                pnl=(price - h.cost_basis) * h.quantity if price else 0.0,
                asset_class=h.asset_class,
                sector=h.sector,
            )
        )

    report = RiskReport(
        portfolio_name=portfolio.name,
        base_currency=portfolio.base_currency,
        total_value=total,
        positions=positions,
        confidence=confidence,
    )

    # --- exposures -----------------------------------------------------------
    def agg(attr: str) -> dict[str, float]:
        out: dict[str, float] = {}
        for p, h in zip(positions, portfolio.holdings):
            out[getattr(h, attr)] = out.get(getattr(h, attr), 0.0) + p.weight
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))

    report.exposures = {"by_asset_class": agg("asset_class"), "by_sector": agg("sector")}

    # --- concentration -------------------------------------------------------
    weights = [p.weight for p in positions]
    hhi = sum(w * w for w in weights)
    report.hhi = round(hhi, 4)
    report.effective_holdings = round(1.0 / hhi, 2) if hhi > 0 else None

    if len(dates) < 3:
        report.notes.append(
            "Not enough overlapping price history for time-series risk metrics; "
            "showing valuation, exposures, and concentration only."
        )
        return report

    # --- return series & core risk ------------------------------------------
    values = _portfolio_value_series(portfolio.holdings, dates, closes)
    pr = stats.simple_returns(values)
    daily_mu = stats.mean(pr)
    daily_sigma = stats.stdev(pr)
    report.ann_return = round(stats.annualize_return(daily_mu), 4)
    report.ann_vol = round(stats.annualize_vol(daily_sigma), 4)

    # VaR / ES at the requested confidence (1-day)
    tail = 1.0 - confidence
    var_hist = -stats.percentile(pr, tail)
    z = stats.norm_ppf(confidence)
    var_param = z * daily_sigma - daily_mu
    losses_beyond = [-r for r in pr if -r >= var_hist]
    es = stats.mean(losses_beyond) if losses_beyond else var_hist
    report.var_hist = round(max(0.0, var_hist), 4)
    report.var_param = round(max(0.0, var_param), 4)
    report.expected_shortfall = round(max(0.0, es), 4)
    report.var_currency = round(report.var_hist * total, 2)

    # max drawdown
    report.max_drawdown = round(stats.max_drawdown(values), 4)

    # beta to benchmark
    if benchmark is not None and len(benchmark.closes) >= len(values):
        br = stats.simple_returns(benchmark.closes[-len(values):])
        var_b = stats.variance(br)
        if var_b > 0:
            report.beta = round(stats.covariance(pr, br) / var_b, 3)

    # --- correlation & risk attribution -------------------------------------
    asset_tickers = [t for t in tickers if t in closes]
    asset_returns = [stats.simple_returns(closes[t]) for t in asset_tickers]
    if len(asset_tickers) >= 2:
        corr = stats.correlation_matrix(asset_returns)
        report.correlation = {
            "tickers": asset_tickers,
            "matrix": [[round(c, 2) for c in row] for row in corr],
        }

        # component contribution to volatility: wᵢ·(Σw)ᵢ / σₚ
        cov = stats.covariance_matrix(asset_returns)
        w = []
        idx = {p.ticker: p for p in positions}
        for t in asset_tickers:
            w.append(idx[t].weight)
        port_var = stats.quad_form(w, cov)
        port_sigma = port_var ** 0.5
        if port_sigma > 0:
            sigma_w = stats.mat_vec(cov, w)
            for i, t in enumerate(asset_tickers):
                contrib = w[i] * sigma_w[i] / port_sigma  # sums to σₚ
                idx[t].risk_contribution_pct = round(100.0 * contrib / port_sigma, 1)

    return report
