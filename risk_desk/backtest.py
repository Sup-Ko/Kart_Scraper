"""VaR backtesting — grading the risk engine's own accuracy.

A VaR number is a falsifiable prediction: at 95% confidence, losses should
exceed it on about 5% of days. That makes it testable, and testing it is the
single most useful thing you can do with a risk model — yet risk vendors rarely
show you the score.

This module walks the portfolio's history, re-estimating VaR from a trailing
window at each step and comparing it to the return actually realised the next
day. It counts **breaches** (days the loss exceeded VaR) and runs the
**Kupiec proportion-of-failures test** to decide whether the breach rate is
consistent with the model's stated confidence.

Three VaR methods are graded side by side — historical, parametric, and EWMA —
because which one is best calibrated is an empirical question about *your*
holdings, not a matter of opinion. A model that breaches far too often is
understating risk; one that never breaches is overstating it and costing you
opportunity.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import stats
from .advanced import ewma_volatility
from .models import PriceSeries
from .prices import align

# chi-square(1 df) critical value at the 95% level
CHI2_1DF_95 = 3.841


@dataclass
class MethodResult:
    method: str
    observations: int
    breaches: int
    expected_breaches: float
    breach_rate: float
    kupiec_lr: float | None
    p_value: float | None
    verdict: str
    max_consecutive_breaches: int = 0


@dataclass
class BacktestReport:
    confidence: float = 0.95
    window: int = 100
    results: list[MethodResult] = field(default_factory=list)
    best_method: str = ""
    notes: list[str] = field(default_factory=list)


def chi2_sf_1df(x: float) -> float:
    """Survival function of chi-square with 1 degree of freedom.

    P(X > x) = erfc(sqrt(x/2)) — exact, no table lookup needed.
    """
    if x <= 0:
        return 1.0
    return math.erfc(math.sqrt(x / 2.0))


def kupiec_pof(observations: int, breaches: int, confidence: float
               ) -> tuple[float | None, float | None]:
    """Kupiec proportion-of-failures likelihood-ratio test.

    Tests H0: the true breach probability equals ``1 - confidence``.

        LR = -2·ln[(1-p)^(n-x) · p^x] + 2·ln[(1-π)^(n-x) · π^x],  π = x/n

    LR is asymptotically chi-square with 1 degree of freedom. A small p-value
    means the observed breach rate is implausible under the model — the VaR is
    miscalibrated.
    """
    n, x = observations, breaches
    if n == 0:
        return None, None
    p = 1.0 - confidence
    if p <= 0.0 or p >= 1.0:
        return None, None

    # log-likelihood under the null (rate fixed at p)
    ll_null = (n - x) * math.log(1 - p) + (x * math.log(p) if x > 0 else 0.0)
    # log-likelihood under the alternative (rate estimated as x/n)
    pi = x / n
    if pi <= 0.0 or pi >= 1.0:
        # π=0: (n-x)·ln(1) + 0 = 0.  π=1: 0 + x·ln(1) = 0.  Both give 0.
        ll_alt = 0.0
    else:
        ll_alt = (n - x) * math.log(1 - pi) + x * math.log(pi)

    lr = -2.0 * (ll_null - ll_alt)
    lr = max(0.0, lr)
    return round(lr, 4), round(chi2_sf_1df(lr), 4)


def _verdict(lr: float | None, p_value: float | None, breaches: int,
             expected: float) -> str:
    if lr is None or p_value is None:
        return "insufficient data"
    if p_value >= 0.05:
        return "well calibrated"
    return "understates risk" if breaches > expected else "overstates risk"


def _max_run(flags: list[bool]) -> int:
    best = run = 0
    for f in flags:
        run = run + 1 if f else 0
        best = max(best, run)
    return best


def backtest_var(returns: list[float], confidence: float = 0.95,
                 window: int = 100) -> BacktestReport:
    """Walk-forward backtest of three VaR methods over a return series."""
    out = BacktestReport(confidence=confidence, window=window)
    n = len(returns)
    if n < window + 20:
        out.notes.append(
            f"Need at least {window + 20} return observations to backtest; "
            f"have {n}. Use a shorter --backtest-window or more price history."
        )
        return out

    z = stats.norm_ppf(confidence)
    tail = 1.0 - confidence
    breach_flags: dict[str, list[bool]] = {"historical": [], "parametric": [], "ewma": []}

    for t in range(window, n):
        past = returns[t - window:t]
        actual = returns[t]
        loss = -actual

        # each method estimates VaR using ONLY information available at t
        var_hist = -stats.percentile(past, tail)
        var_param = z * stats.stdev(past) - stats.mean(past)
        var_ewma = z * ewma_volatility(past)

        breach_flags["historical"].append(loss > var_hist)
        breach_flags["parametric"].append(loss > var_param)
        breach_flags["ewma"].append(loss > var_ewma)

    for method, flags in breach_flags.items():
        obs = len(flags)
        breaches = sum(flags)
        expected = obs * tail
        lr, p_value = kupiec_pof(obs, breaches, confidence)
        out.results.append(
            MethodResult(
                method=method,
                observations=obs,
                breaches=breaches,
                expected_breaches=round(expected, 1),
                breach_rate=round(breaches / obs, 4) if obs else 0.0,
                kupiec_lr=lr,
                p_value=p_value,
                verdict=_verdict(lr, p_value, breaches, expected),
                max_consecutive_breaches=_max_run(flags),
            )
        )

    # "best" = breach rate closest to the target, among calibrated models
    calibrated = [r for r in out.results if r.verdict == "well calibrated"]
    pool = calibrated or out.results
    if pool:
        out.best_method = min(pool, key=lambda r: abs(r.breach_rate - tail)).method

    clustered = [r for r in out.results if r.max_consecutive_breaches >= 3]
    if clustered:
        out.notes.append(
            "Breaches cluster on consecutive days for "
            + ", ".join(r.method for r in clustered)
            + " — losses arriving in runs means the model is slow to react to "
              "volatility regime changes, even where the overall rate passes."
        )
    return out


def backtest_portfolio(report, price_series: dict[str, PriceSeries],
                       confidence: float = 0.95, window: int = 100) -> BacktestReport:
    """Backtest VaR on the portfolio's own realised value path."""
    tickers = [p.ticker for p in report.positions]
    dates, closes = align(price_series, tickers)
    if not dates:
        out = BacktestReport(confidence=confidence, window=window)
        out.notes.append("No overlapping price history; cannot backtest.")
        return out

    values = [0.0] * len(dates)
    for p in report.positions:
        series = closes.get(p.ticker)
        if not series:
            continue
        for i in range(len(dates)):
            values[i] += p.quantity * series[i]

    return backtest_var(stats.simple_returns(values), confidence, window)
