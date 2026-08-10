"""Advanced risk analytics: EWMA vol, Monte Carlo VaR, and VaR attribution.

Everything here is standard risk-management practice, implemented transparently:

* **EWMA volatility** — RiskMetrics-style exponentially weighted vol, which
  reacts to regime changes far faster than an equally weighted window.
* **Monte Carlo VaR** — simulate correlated asset returns via the Cholesky
  factor of the covariance matrix, then read the loss percentile off the
  simulated distribution. Unlike parametric VaR it needs no closed form, and
  unlike historical VaR it is not limited to paths that already happened.
* **Marginal / component / incremental VaR** — the attribution that answers
  "if I trim this position, how much risk actually goes away?"
* **Historical replay** — the worst rolling windows your holdings actually
  lived through, which is the honest complement to hypothetical shocks.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from . import stats
from .linalg import cholesky
from .models import PriceSeries
from .prices import align

RISKMETRICS_LAMBDA = 0.94


@dataclass
class VarAttribution:
    ticker: str
    weight: float
    marginal_var: float  # ∂VaR/∂w — sensitivity to a small weight increase
    component_var: float  # wᵢ · marginal — sums to total VaR
    component_pct: float
    incremental_var: float  # VaR change if the position were removed entirely


@dataclass
class HistoricalWindow:
    start: str
    end: str
    ret: float


@dataclass
class AdvancedReport:
    ewma_vol_annual: float | None = None
    ewma_var: float | None = None
    mc_var: float | None = None
    mc_es: float | None = None
    mc_sims: int = 0
    confidence: float = 0.95
    attribution: list[VarAttribution] = field(default_factory=list)
    worst_windows: dict[int, list[HistoricalWindow]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def ewma_volatility(returns: list[float], lam: float = RISKMETRICS_LAMBDA) -> float:
    """Exponentially weighted daily volatility.

    σ²_t = (1−λ) · Σ λ^k · r²_{t−k}, most recent observation weighted highest.
    """
    if len(returns) < 2:
        return 0.0
    mu = stats.mean(returns)
    var = 0.0
    weight_sum = 0.0
    # iterate newest → oldest so k grows with age
    for k, r in enumerate(reversed(returns)):
        w = (1 - lam) * (lam ** k)
        var += w * (r - mu) ** 2
        weight_sum += w
    if weight_sum <= 0:
        return 0.0
    return (var / weight_sum) ** 0.5


def monte_carlo_var(
    weights: list[float],
    cov_daily: list[list[float]],
    mean_daily: list[float],
    confidence: float = 0.95,
    sims: int = 20000,
    seed: int = 12345,
) -> tuple[float, float]:
    """Simulate correlated daily returns; return (VaR, Expected Shortfall).

    Correlated draws come from z ~ N(0, I) mapped through the Cholesky factor L
    of the covariance matrix: r = μ + L·z has exactly the target covariance.
    """
    n = len(weights)
    if n == 0:
        return 0.0, 0.0
    L = cholesky(cov_daily)
    rng = random.Random(seed)

    port_returns: list[float] = []
    for _ in range(sims):
        z = [rng.gauss(0.0, 1.0) for _ in range(n)]
        total = 0.0
        for i in range(n):
            # rᵢ = μᵢ + Σ_{k≤i} L[i][k]·z[k]
            ri = mean_daily[i] + sum(L[i][k] * z[k] for k in range(i + 1))
            total += weights[i] * ri
        port_returns.append(total)

    var = -stats.percentile(port_returns, 1.0 - confidence)
    beyond = [-r for r in port_returns if -r >= var]
    es = stats.mean(beyond) if beyond else var
    return max(0.0, var), max(0.0, es)


def var_attribution(
    tickers: list[str],
    weights: list[float],
    cov_daily: list[list[float]],
    confidence: float = 0.95,
) -> list[VarAttribution]:
    """Marginal, component, and incremental VaR under the parametric model.

    With VaR = z·σₚ, the gradient is ∂VaR/∂wᵢ = z·(Σw)ᵢ / σₚ. Component VaR is
    wᵢ times that, and by Euler's theorem the components sum exactly to total
    VaR. Incremental VaR re-computes VaR with the position removed (remaining
    weights renormalized), answering the practical "what if I sell it?".
    """
    n = len(weights)
    if n == 0:
        return []
    z = stats.norm_ppf(confidence)
    port_var = stats.quad_form(weights, cov_daily)
    sigma = port_var ** 0.5
    if sigma <= 0:
        return []
    total_var = z * sigma

    sigma_w = [sum(cov_daily[i][j] * weights[j] for j in range(n)) for i in range(n)]

    out: list[VarAttribution] = []
    for i in range(n):
        marginal = z * sigma_w[i] / sigma
        component = weights[i] * marginal

        # VaR of the portfolio with position i removed, weights renormalized
        remaining = [weights[j] for j in range(n) if j != i]
        scale = sum(remaining)
        if scale > 0 and n > 1:
            w2 = [w / scale for w in remaining]
            cov2 = [
                [cov_daily[a][b] for b in range(n) if b != i]
                for a in range(n)
                if a != i
            ]
            sigma2 = stats.quad_form(w2, cov2) ** 0.5
            incremental = z * sigma2 - total_var
        else:
            incremental = -total_var

        out.append(
            VarAttribution(
                ticker=tickers[i],
                weight=round(weights[i], 4),
                marginal_var=round(marginal, 5),
                component_var=round(component, 5),
                component_pct=round(100.0 * component / total_var, 1) if total_var else 0.0,
                incremental_var=round(incremental, 5),
            )
        )
    return out


def historical_replay(
    values: list[float], dates: list[str], horizons: tuple[int, ...] = (1, 5, 20)
) -> dict[int, list[HistoricalWindow]]:
    """The worst rolling-window returns the portfolio actually experienced."""
    out: dict[int, list[HistoricalWindow]] = {}
    for h in horizons:
        if len(values) <= h:
            continue
        windows: list[HistoricalWindow] = []
        for i in range(len(values) - h):
            start_v, end_v = values[i], values[i + h]
            if start_v <= 0:
                continue
            windows.append(
                HistoricalWindow(
                    start=dates[i] if i < len(dates) else "",
                    end=dates[i + h] if i + h < len(dates) else "",
                    ret=round(end_v / start_v - 1.0, 4),
                )
            )
        windows.sort(key=lambda w: w.ret)
        out[h] = windows[:3]
    return out


def analyze_advanced(
    report,
    price_series: dict[str, PriceSeries],
    confidence: float = 0.95,
    sims: int = 20000,
    seed: int = 12345,
) -> AdvancedReport:
    """Run the advanced engine against an existing :class:`RiskReport`."""
    adv = AdvancedReport(confidence=confidence, mc_sims=sims)

    tickers = [p.ticker for p in report.positions]
    dates, closes = align(price_series, tickers)
    fitted = [t for t in tickers if t in closes]
    if len(fitted) == 0 or len(dates) < 5:
        adv.notes.append("Not enough price history for the advanced engine.")
        return adv

    asset_returns = [stats.simple_returns(closes[t]) for t in fitted]
    idx = {p.ticker: p for p in report.positions}
    weights = [idx[t].weight for t in fitted]

    # --- portfolio value path -> EWMA vol and historical replay -------------
    n = len(dates)
    values = [0.0] * n
    for p in report.positions:
        series = closes.get(p.ticker)
        if not series:
            continue
        for i in range(n):
            values[i] += p.quantity * series[i]
    port_returns = stats.simple_returns(values)

    ewma_daily = ewma_volatility(port_returns)
    adv.ewma_vol_annual = round(stats.annualize_vol(ewma_daily), 4)
    adv.ewma_var = round(max(0.0, stats.norm_ppf(confidence) * ewma_daily), 4)

    adv.worst_windows = historical_replay(values, dates)

    # --- covariance-based Monte Carlo & attribution -------------------------
    cov = stats.covariance_matrix(asset_returns)
    means = [stats.mean(r) for r in asset_returns]

    try:
        mc_var, mc_es = monte_carlo_var(
            weights, cov, means, confidence=confidence, sims=sims, seed=seed
        )
        adv.mc_var = round(mc_var, 4)
        adv.mc_es = round(mc_es, 4)
    except ValueError as exc:
        adv.notes.append(f"Monte Carlo skipped: {exc}")

    adv.attribution = var_attribution(fitted, weights, cov, confidence=confidence)
    return adv
