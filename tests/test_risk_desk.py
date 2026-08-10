"""Tests for risk_desk: statistics correctness and end-to-end analytics."""

from __future__ import annotations

import math

from risk_desk import stats
from risk_desk.analytics import analyze
from risk_desk.models import Holding, Portfolio, PriceSeries
from risk_desk.prices import sample_prices
from risk_desk.scenarios import Scenario, apply_scenario


# ---- statistics: assert against hand-computed / known values ----------------

def test_simple_returns():
    r = stats.simple_returns([100, 110, 99])
    assert abs(r[0] - 0.1) < 1e-9 and abs(r[1] - (-0.1)) < 1e-9


def test_mean_variance_stdev():
    xs = [2, 4, 4, 4, 5, 5, 7, 9]
    assert stats.mean(xs) == 5.0
    # population variance of this classic dataset is 4 -> stdev 2
    assert abs(stats.variance(xs, sample=False) - 4.0) < 1e-9
    assert abs(stats.stdev(xs, sample=False) - 2.0) < 1e-9


def test_correlation_perfect():
    xs = [1, 2, 3, 4, 5]
    ys = [2, 4, 6, 8, 10]
    assert abs(stats.correlation(xs, ys) - 1.0) < 1e-9
    zs = [10, 8, 6, 4, 2]
    assert abs(stats.correlation(xs, zs) + 1.0) < 1e-9


def test_norm_ppf_known_zscores():
    assert abs(stats.norm_ppf(0.95) - 1.6448536) < 1e-5
    assert abs(stats.norm_ppf(0.99) - 2.3263479) < 1e-5
    assert abs(stats.norm_ppf(0.5)) < 1e-9


def test_norm_cdf_roundtrip():
    for p in (0.1, 0.5, 0.84, 0.975):
        assert abs(stats.norm_cdf(stats.norm_ppf(p)) - p) < 1e-6


def test_percentile():
    xs = [1, 2, 3, 4, 5]
    assert stats.percentile(xs, 0.0) == 1
    assert stats.percentile(xs, 1.0) == 5
    assert stats.percentile(xs, 0.5) == 3


def test_max_drawdown():
    # peak 100 -> trough 50 = 50% drawdown, then recovery
    assert abs(stats.max_drawdown([100, 120, 60, 80, 200]) - 0.5) < 1e-9
    assert stats.max_drawdown([10, 11, 12]) == 0.0


def test_quad_form_portfolio_variance():
    # two assets, equal weights; cov matrix [[0.04,0.01],[0.01,0.09]]
    cov = [[0.04, 0.01], [0.01, 0.09]]
    w = [0.5, 0.5]
    # wᵀΣw = .25*.04 + .25*.09 + 2*.25*.01 = .01+.0225+.005 = .0375
    assert abs(stats.quad_form(w, cov) - 0.0375) < 1e-9


# ---- analytics: end-to-end on sample data -----------------------------------

def _sample_portfolio() -> Portfolio:
    return Portfolio(
        name="Test",
        holdings=[
            Holding("AAPL", 40, 150, "Equity", "Technology"),
            Holding("TLT", 60, 98, "Bond", "Government"),
            Holding("GLD", 20, 175, "Commodity", "Metals"),
        ],
    )


def test_analyze_produces_coherent_report():
    p = _sample_portfolio()
    series = sample_prices()
    r = analyze(p, series, benchmark=series.get("SPY"), confidence=0.95)

    assert r.total_value > 0
    # weights sum to ~1
    assert abs(sum(pos.weight for pos in r.positions) - 1.0) < 1e-9
    # risk metrics present and in sane ranges
    assert 0.0 < r.ann_vol < 1.0
    assert 0.0 <= r.var_hist < 0.2
    assert r.expected_shortfall >= r.var_hist  # ES is at least as large as VaR
    assert 0.0 <= r.max_drawdown <= 1.0
    # risk contributions sum to ~100%
    total_rc = sum(pos.risk_contribution_pct for pos in r.positions)
    assert abs(total_rc - 100.0) < 1.0
    # correlation matrix is square with unit diagonal
    m = r.correlation["matrix"]
    assert len(m) == len(r.correlation["tickers"])
    for i in range(len(m)):
        assert abs(m[i][i] - 1.0) < 1e-9


def test_scenario_pnl_sign_and_magnitude():
    p = _sample_portfolio()
    r = analyze(p, sample_prices())
    crash = Scenario("Crash", by_asset_class={"Equity": -0.40, "Bond": 0.08})
    res = apply_scenario(r, crash)
    # equity-heavy shock should be a loss overall for this mix
    assert res.pnl < 0
    # pnl_pct consistent with pnl / total value (both are rounded for display)
    assert abs(res.pnl_pct - res.pnl / r.total_value) < 1e-3


def test_low_history_degrades_gracefully():
    p = Portfolio(holdings=[Holding("X", 10)])
    series = {"X": PriceSeries("X", ["d1", "d2"], [100.0, 101.0])}
    r = analyze(p, series)
    assert r.total_value == 1010.0
    assert r.ann_vol is None  # not enough data
    assert any("Not enough" in n for n in r.notes)
