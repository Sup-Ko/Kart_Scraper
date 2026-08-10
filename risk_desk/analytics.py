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
    "factor_beta": "each asset's return regressed (OLS) on the factor returns: rᵢ = αᵢ + Σ βᵢ,f·F_f + εᵢ. Portfolio beta to a factor is the weighted sum of asset betas.",
    "systematic_vs_specific": "portfolio variance split into βᵀΣ_Fβ (systematic — driven by the factors) and Σwᵢ²σ²_ε,ᵢ (specific — idiosyncratic, the part diversification can remove).",
    "r_squared": "share of an asset's return variance explained by the factors. Low R² means the position moves for its own reasons.",
    "ewma_vol": "RiskMetrics exponentially weighted volatility (λ=0.94): σ²_t = (1−λ)·Σλᵏ·r²_{t−k}. Reacts to regime change much faster than an equal-weighted window.",
    "monte_carlo_var": "20,000 simulated days of correlated returns, drawn as μ + L·z where L is the Cholesky factor of the covariance matrix. VaR is read off the simulated loss distribution.",
    "marginal_var": "∂VaR/∂wᵢ = z·(Σw)ᵢ ÷ σₚ — how much VaR moves if you add a little to this position.",
    "component_var": "wᵢ × marginal VaR. By Euler's theorem these sum exactly to total VaR — a true attribution of risk to positions.",
    "incremental_var": "the change in portfolio VaR if this position were sold entirely and the rest renormalized. Negative = selling it reduces risk.",
    "historical_replay": "the worst rolling 1/5/20-day returns your actual holdings lived through in the sample — real paths, not hypothetical shocks.",
    "factor_scenario": "a shock applied in factor space: each position moves by its OWN fitted betas (rᵢ = Σ βᵢ,f · shock_f) rather than every equity being assumed to move together. Models the systematic response only — idiosyncratic single-name moves are not simulated.",
    "backtest": "walk-forward test of the VaR model against realised returns: at 95% confidence, losses should exceed VaR on ~5% of days. The Kupiec proportion-of-failures test (LR, chi-square 1df) says whether the observed breach rate is consistent with that. This grades the risk engine's own accuracy.",
    "policy_exposure": "share of portfolio RISK (not just value) held in companies with federal contract awards in the lookback window. Risk share is the honest measure: a small position driving large volatility is a bigger policy bet than a large quiet one.",
    "attention": "news heat × that position's share of portfolio risk. Surfaces stories that matter because of what you actually own.",
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
    company: str = ""  # carried from the holding, for external dataset joins
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
                company=h.company,
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
