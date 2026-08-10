"""Tests for VaR backtesting — verified against known statistical values."""

from __future__ import annotations

import math
import random

from risk_desk.analytics import analyze
from risk_desk.backtest import (
    backtest_portfolio,
    backtest_var,
    chi2_sf_1df,
    kupiec_pof,
)
from risk_desk.models import Holding, Portfolio
from risk_desk.prices import sample_prices


# ---- chi-square survival function ------------------------------------------

def test_chi2_sf_matches_known_critical_values():
    """chi2(1df) at 3.841 is the classic 5% critical value."""
    assert abs(chi2_sf_1df(3.841) - 0.05) < 1e-3
    assert abs(chi2_sf_1df(6.635) - 0.01) < 1e-3
    assert abs(chi2_sf_1df(2.706) - 0.10) < 1e-3
    assert chi2_sf_1df(0.0) == 1.0


# ---- Kupiec proportion-of-failures test -------------------------------------

def test_kupiec_perfect_calibration_gives_zero_statistic():
    """Exactly the expected number of breaches -> LR = 0, p = 1."""
    lr, p = kupiec_pof(observations=1000, breaches=50, confidence=0.95)
    assert abs(lr) < 1e-6
    assert abs(p - 1.0) < 1e-6


def test_kupiec_flags_far_too_many_breaches():
    """15% breach rate at 95% confidence must be rejected."""
    lr, p = kupiec_pof(observations=1000, breaches=150, confidence=0.95)
    assert lr > 3.841      # beyond the 5% critical value
    assert p < 0.05


def test_kupiec_flags_far_too_few_breaches():
    """Zero breaches in 1000 days is also a failed model (overstates risk)."""
    lr, p = kupiec_pof(observations=1000, breaches=0, confidence=0.95)
    # closed form when x=0: LR = -2·n·ln(1-p)
    assert abs(lr - (-2 * 1000 * math.log(0.95))) < 1e-3
    assert p < 0.05


def test_kupiec_tolerates_mild_deviation():
    """A slightly-off rate should not be rejected — the test isn't hair-trigger."""
    lr, p = kupiec_pof(observations=250, breaches=15, confidence=0.95)  # 6% vs 5%
    assert p > 0.05


def test_kupiec_handles_empty_input():
    assert kupiec_pof(0, 0, 0.95) == (None, None)


# ---- walk-forward backtest --------------------------------------------------

def test_backtest_on_normal_returns_is_well_calibrated():
    """Gaussian returns should pass calibration for the parametric method."""
    rng = random.Random(11)
    returns = [rng.gauss(0.0, 0.01) for _ in range(900)]
    bt = backtest_var(returns, confidence=0.95, window=250)
    by = {r.method: r for r in bt.results}
    assert by["parametric"].verdict == "well calibrated"
    # breach rate should land near the 5% target
    assert abs(by["parametric"].breach_rate - 0.05) < 0.025
    assert by["parametric"].observations == 900 - 250


def test_backtest_detects_understated_risk():
    """A calm estimation window followed by a violent regime must be caught.

    The trailing window sees only tiny moves, so VaR is estimated far too low
    for the turbulent period that follows — the model should be flagged.
    """
    rng = random.Random(5)
    calm = [rng.gauss(0.0, 0.001) for _ in range(300)]
    violent = [rng.gauss(0.0, 0.05) for _ in range(300)]
    bt = backtest_var(calm + violent, confidence=0.95, window=250)
    by = {r.method: r for r in bt.results}
    assert by["historical"].verdict == "understates risk"
    assert by["historical"].breach_rate > 0.05


def test_backtest_expected_breaches_and_bounds():
    rng = random.Random(3)
    returns = [rng.gauss(0.0, 0.01) for _ in range(600)]
    bt = backtest_var(returns, confidence=0.99, window=250)
    for r in bt.results:
        assert r.observations == 350
        assert abs(r.expected_breaches - 350 * 0.01) < 1e-6
        assert 0 <= r.breaches <= r.observations
        assert r.max_consecutive_breaches >= 0
    assert bt.best_method in {"historical", "parametric", "ewma"}


def test_backtest_reports_insufficient_data():
    bt = backtest_var([0.01] * 50, confidence=0.95, window=100)
    assert bt.results == []
    assert any("at least" in n for n in bt.notes)


def test_backtest_portfolio_end_to_end():
    series = sample_prices()
    p = Portfolio(holdings=[
        Holding("AAPL", 40, 150, "Equity", "Technology"),
        Holding("TLT", 60, 98, "Bond", "Government"),
    ])
    report = analyze(p, series, benchmark=series.get("SPY"))
    bt = backtest_portfolio(report, series, confidence=0.95, window=120)
    assert len(bt.results) == 3
    for r in bt.results:
        assert r.observations > 0
        assert r.p_value is not None
