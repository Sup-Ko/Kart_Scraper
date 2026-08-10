"""Transparent statistics core — pure Python, no numpy, every formula legible.

This module is deliberately dependency-free and written so each function reads
like its textbook definition. The whole point of risk_desk is that you can audit
the math: nothing here is a black box.
"""

from __future__ import annotations

import math

TRADING_DAYS = 252


def simple_returns(prices: list[float]) -> list[float]:
    """Period-over-period simple returns: r_t = P_t / P_{t-1} - 1."""
    out = []
    for prev, cur in zip(prices, prices[1:]):
        if prev == 0:
            out.append(0.0)
        else:
            out.append(cur / prev - 1.0)
    return out


def mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def variance(xs: list[float], sample: bool = True) -> float:
    """Variance. Sample (n-1) by default; population (n) if sample=False."""
    n = len(xs)
    if n < 2:
        return 0.0
    m = mean(xs)
    ss = sum((x - m) ** 2 for x in xs)
    return ss / (n - 1 if sample else n)


def stdev(xs: list[float], sample: bool = True) -> float:
    return math.sqrt(variance(xs, sample))


def covariance(xs: list[float], ys: list[float], sample: bool = True) -> float:
    """cov(x, y) = E[(x - x̄)(y - ȳ)], sample-corrected by default."""
    n = min(len(xs), len(ys))
    if n < 2:
        return 0.0
    mx, my = mean(xs[:n]), mean(ys[:n])
    s = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
    return s / (n - 1 if sample else n)


def correlation(xs: list[float], ys: list[float]) -> float:
    sx, sy = stdev(xs), stdev(ys)
    if sx == 0 or sy == 0:
        return 0.0
    return covariance(xs, ys) / (sx * sy)


def covariance_matrix(series: list[list[float]]) -> list[list[float]]:
    """Covariance matrix for a list of equal-length return series."""
    n = len(series)
    return [[covariance(series[i], series[j]) for j in range(n)] for i in range(n)]


def correlation_matrix(series: list[list[float]]) -> list[list[float]]:
    n = len(series)
    return [[correlation(series[i], series[j]) for j in range(n)] for i in range(n)]


def annualize_return(daily_mean: float, periods: int = TRADING_DAYS) -> float:
    """Geometric annualization of a mean daily return."""
    return (1.0 + daily_mean) ** periods - 1.0


def annualize_vol(daily_vol: float, periods: int = TRADING_DAYS) -> float:
    """Volatility scales with the square root of time."""
    return daily_vol * math.sqrt(periods)


def percentile(xs: list[float], q: float) -> float:
    """The q-th percentile (q in [0, 1]) via linear interpolation on sorted data."""
    if not xs:
        return 0.0
    s = sorted(xs)
    if len(s) == 1:
        return s[0]
    pos = q * (len(s) - 1)
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return s[int(pos)]
    frac = pos - lo
    return s[lo] * (1 - frac) + s[hi] * frac


def max_drawdown(values: list[float]) -> float:
    """Largest peak-to-trough decline of a value series, as a positive fraction."""
    if not values:
        return 0.0
    peak = values[0]
    worst = 0.0
    for v in values:
        peak = max(peak, v)
        if peak > 0:
            dd = (peak - v) / peak
            worst = max(worst, dd)
    return worst


def norm_cdf(x: float) -> float:
    """Standard normal CDF via the error function."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_ppf(p: float) -> float:
    """Inverse standard normal CDF (Acklam's rational approximation).

    Accurate to ~1e-9 on (0, 1); used for parametric VaR z-scores.
    """
    if p <= 0.0:
        return -math.inf
    if p >= 1.0:
        return math.inf

    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]

    plow = 0.02425
    phigh = 1 - plow
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
                ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
           (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)


def mat_vec(mat: list[list[float]], vec: list[float]) -> list[float]:
    return [sum(mat[i][j] * vec[j] for j in range(len(vec))) for i in range(len(mat))]


def quad_form(vec: list[float], mat: list[list[float]]) -> float:
    """wᵀ M w — the portfolio-variance building block."""
    mv = mat_vec(mat, vec)
    return sum(vec[i] * mv[i] for i in range(len(vec)))
