"""Multi-factor risk model.

Each asset's returns are regressed on a set of factor return series:

    rᵢ,ₜ = αᵢ + Σ_f βᵢ,f · F_f,ₜ + εᵢ,ₜ

From the fitted betas the portfolio's risk splits cleanly into two parts:

    σ²_portfolio = βₚᵀ Σ_F βₚ  (systematic — the factors)
                 + Σᵢ wᵢ² σ²_ε,ᵢ (specific — idiosyncratic, diversifiable)

That decomposition is the point: it tells you *what kind* of risk you are
taking, not just how much. Every step is ordinary least squares you can audit
in ``linalg.py`` — there is no proprietary factor library here, and you choose
the factors yourself.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import stats
from .linalg import ols
from .models import PriceSeries
from .prices import align

# Default factor proxies: liquid, free-to-source tickers standing in for the
# classic risk drivers. Override these — they are assumptions, not truth.
DEFAULT_FACTOR_PROXIES: dict[str, str] = {
    "Market": "SPY",
    "Rates": "TLT",
    "Gold": "GLD",
}


@dataclass
class AssetFactorFit:
    ticker: str
    weight: float
    alpha: float
    betas: dict[str, float]
    r_squared: float
    specific_vol_annual: float


@dataclass
class FactorReport:
    factors: list[str]
    assets: list[AssetFactorFit] = field(default_factory=list)
    portfolio_betas: dict[str, float] = field(default_factory=dict)
    factor_vols_annual: dict[str, float] = field(default_factory=dict)
    systematic_vol_annual: float = 0.0
    specific_vol_annual: float = 0.0
    total_vol_annual: float = 0.0
    systematic_share: float = 0.0  # fraction of VARIANCE from factors
    specific_share: float = 0.0
    factor_risk_contribution: dict[str, float] = field(default_factory=dict)  # % of variance
    notes: list[str] = field(default_factory=list)


def build_factor_returns(
    price_series: dict[str, PriceSeries],
    proxies: dict[str, str] | None = None,
    length: int | None = None,
) -> tuple[list[str], list[list[float]]]:
    """Turn factor proxy price series into aligned factor return series."""
    proxies = proxies or DEFAULT_FACTOR_PROXIES
    available = {name: tic for name, tic in proxies.items() if tic in price_series}
    if not available:
        return [], []

    _, closes = align(price_series, list(available.values()))
    names, series = [], []
    for name, tic in available.items():
        if tic not in closes:
            continue
        r = stats.simple_returns(closes[tic])
        if length is not None:
            r = r[-length:]
        names.append(name)
        series.append(r)

    if series:
        n = min(len(s) for s in series)
        series = [s[-n:] for s in series]
    return names, series


def analyze_factors(
    report,
    price_series: dict[str, PriceSeries],
    proxies: dict[str, str] | None = None,
    extra_factors: dict[str, list[float]] | None = None,
) -> FactorReport:
    """Fit the factor model and decompose portfolio risk.

    ``report`` is a :class:`~risk_desk.analytics.RiskReport` — we reuse its
    position weights so the decomposition matches the valuation on screen.
    """
    proxies = proxies or DEFAULT_FACTOR_PROXIES
    tickers = [p.ticker for p in report.positions]
    _, closes = align(price_series, tickers)

    factor_names, factor_series = build_factor_returns(price_series, proxies)

    # Externally supplied factors (e.g. real yields from FRED) are appended.
    # They are independent of what the portfolio holds, which is the point — but
    # deciding which ETF proxy they supersede is the CALLER's job, made explicit
    # by passing a narrowed ``proxies`` mapping. Keeping both a rates ETF and a
    # real yield series would double-count the same risk and reintroduce
    # collinearity, so the CLI drops the superseded proxies when FRED is on.
    if extra_factors:
        for name, series in extra_factors.items():
            if not series:
                continue
            factor_names.append(name)
            factor_series.append(list(series))

    fr = FactorReport(factors=factor_names)

    if not factor_names:
        fr.notes.append(
            "No factor proxy price history available; skipping factor decomposition."
        )
        return fr
    if not closes:
        fr.notes.append("No overlapping asset price history; skipping factor model.")
        return fr

    n_obs = min(len(s) for s in factor_series)
    for t in closes:
        n_obs = min(n_obs, len(closes[t]) - 1)
    if n_obs < len(factor_names) + 3:
        fr.notes.append(
            f"Only {max(0, n_obs)} overlapping observations for {len(factor_names)} "
            "factors — too few to fit a stable model."
        )
        return fr

    F = [s[-n_obs:] for s in factor_series]

    # --- per-asset regressions ----------------------------------------------
    weights: list[float] = []
    fits: list[AssetFactorFit] = []
    for pos in report.positions:
        if pos.ticker not in closes:
            continue
        r = stats.simple_returns(closes[pos.ticker])[-n_obs:]
        fit = ols(r, F, intercept=True)
        betas = {factor_names[i]: round(fit.betas[i], 3) for i in range(len(factor_names))}
        fits.append(
            AssetFactorFit(
                ticker=pos.ticker,
                weight=pos.weight,
                alpha=round(stats.annualize_return(fit.alpha), 4),
                betas=betas,
                r_squared=round(max(0.0, fit.r_squared), 3),
                specific_vol_annual=round(
                    stats.annualize_vol(fit.resid_var ** 0.5), 4
                ),
            )
        )
        weights.append(pos.weight)

    fr.assets = fits
    if not fits:
        fr.notes.append("No positions could be fitted against the factors.")
        return fr

    # Honesty check: a holding that is ALSO a factor proxy regresses on itself,
    # giving R²=1 and zero specific risk. That is an artifact of the factor
    # choice, not a real absence of idiosyncratic risk — say so plainly rather
    # than let a too-clean number pass as insight.
    external = set(extra_factors or {})
    proxy_tickers = {t.upper() for name, t in proxies.items() if name not in external}
    self_fitted = [f.ticker for f in fits if f.ticker.upper() in proxy_tickers]
    if self_fitted:
        fr.notes.append(
            f"{', '.join(self_fitted)} {'is' if len(self_fitted) == 1 else 'are'} "
            "also used as a factor proxy, so the regression is against itself: "
            "R²=1 and specific risk 0 by construction, not because the position "
            "carries no idiosyncratic risk. Swap in a different proxy (or drop "
            "that factor) for an independent estimate."
        )

    # --- portfolio factor exposures: βₚ,f = Σᵢ wᵢ βᵢ,f -----------------------
    port_betas = {
        f: round(sum(fit.weight * fit.betas[f] for fit in fits), 3) for f in factor_names
    }
    fr.portfolio_betas = port_betas

    # --- systematic variance: βₚᵀ Σ_F βₚ ------------------------------------
    cov_f = stats.covariance_matrix(F)
    beta_vec = [port_betas[f] for f in factor_names]
    systematic_var_daily = stats.quad_form(beta_vec, cov_f)

    # --- specific variance: Σ wᵢ² σ²_ε,ᵢ (residuals assumed uncorrelated) ----
    specific_var_daily = 0.0
    for fit in fits:
        daily_specific_var = (fit.specific_vol_annual ** 2) / stats.TRADING_DAYS
        specific_var_daily += (fit.weight ** 2) * daily_specific_var

    total_var_daily = systematic_var_daily + specific_var_daily

    fr.factor_vols_annual = {
        factor_names[i]: round(stats.annualize_vol(stats.stdev(F[i])), 4)
        for i in range(len(factor_names))
    }
    fr.systematic_vol_annual = round(
        stats.annualize_vol(max(0.0, systematic_var_daily) ** 0.5), 4
    )
    fr.specific_vol_annual = round(
        stats.annualize_vol(max(0.0, specific_var_daily) ** 0.5), 4
    )
    fr.total_vol_annual = round(stats.annualize_vol(max(0.0, total_var_daily) ** 0.5), 4)

    if total_var_daily > 0:
        fr.systematic_share = round(systematic_var_daily / total_var_daily, 4)
        fr.specific_share = round(specific_var_daily / total_var_daily, 4)

        # per-factor contribution to variance: βf · (Σ_F β)f / total variance
        sigma_beta = [
            sum(cov_f[i][j] * beta_vec[j] for j in range(len(beta_vec)))
            for i in range(len(beta_vec))
        ]
        for i, f in enumerate(factor_names):
            contrib = beta_vec[i] * sigma_beta[i]
            fr.factor_risk_contribution[f] = round(100.0 * contrib / total_var_daily, 1)

    return fr
