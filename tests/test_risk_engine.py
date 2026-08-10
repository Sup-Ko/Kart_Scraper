"""Tests for the advanced risk engine: linear algebra, factors, bridge."""

from __future__ import annotations

import math
import sqlite3

from risk_desk import linalg, stats
from risk_desk.advanced import (
    analyze_advanced,
    ewma_volatility,
    historical_replay,
    monte_carlo_var,
    var_attribution,
)
from risk_desk.analytics import analyze
from risk_desk.bridge import load_signals, overlay
from risk_desk.factors import analyze_factors, build_factor_returns
from risk_desk.models import Holding, Portfolio
from risk_desk.prices import sample_prices


# ---- linear algebra: assert against hand-computed values --------------------

def test_solve_linear_system():
    # 2x + y = 5 ; x + 3y = 10  ->  x = 1, y = 3
    x = linalg.solve([[2.0, 1.0], [1.0, 3.0]], [5.0, 10.0])
    assert abs(x[0] - 1.0) < 1e-9
    assert abs(x[1] - 3.0) < 1e-9


def test_solve_requires_pivoting():
    # leading zero forces a row swap
    x = linalg.solve([[0.0, 2.0], [1.0, 1.0]], [4.0, 3.0])
    assert abs(x[0] - 1.0) < 1e-9 and abs(x[1] - 2.0) < 1e-9


def test_cholesky_reconstructs_matrix():
    a = [[4.0, 2.0, 0.6], [2.0, 5.0, 1.0], [0.6, 1.0, 3.0]]
    L = linalg.cholesky(a)
    # L is lower triangular
    for i in range(3):
        for j in range(i + 1, 3):
            assert abs(L[i][j]) < 1e-12
    # L·Lᵀ == A
    rebuilt = linalg.matmul(L, linalg.transpose(L))
    for i in range(3):
        for j in range(3):
            assert abs(rebuilt[i][j] - a[i][j]) < 1e-8


def test_cholesky_handles_semidefinite():
    # perfectly collinear -> singular; the ridge fallback must still return a factor
    a = [[1.0, 1.0], [1.0, 1.0]]
    L = linalg.cholesky(a)
    assert len(L) == 2


def test_ols_recovers_known_coefficients():
    # y = 2 + 3*x1 - 1*x2 exactly
    x1 = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    x2 = [2.0, 1.0, 4.0, 3.0, 6.0, 5.0]
    y = [2 + 3 * a - 1 * b for a, b in zip(x1, x2)]
    fit = linalg.ols(y, [x1, x2])
    assert abs(fit.alpha - 2.0) < 1e-8
    assert abs(fit.betas[0] - 3.0) < 1e-8
    assert abs(fit.betas[1] + 1.0) < 1e-8
    assert abs(fit.r_squared - 1.0) < 1e-9
    assert fit.resid_var < 1e-15


def test_ols_beta_matches_covariance_formula():
    """Single-factor OLS beta must equal cov(y,x)/var(x)."""
    y = [0.01, -0.02, 0.015, 0.03, -0.01, 0.02, 0.0, -0.005]
    x = [0.008, -0.015, 0.02, 0.025, -0.012, 0.018, 0.002, -0.004]
    fit = linalg.ols(y, [x])
    expected = stats.covariance(y, x) / stats.variance(x)
    assert abs(fit.betas[0] - expected) < 1e-9


# ---- advanced engine --------------------------------------------------------

def test_ewma_reacts_to_recent_volatility():
    calm = [0.001, -0.001] * 40
    spiked = calm + [0.05, -0.06, 0.055, -0.045]
    assert ewma_volatility(spiked) > ewma_volatility(calm) * 3


def test_monte_carlo_var_matches_parametric_for_normal_data():
    """With a known covariance, MC VaR should track z·σ closely."""
    cov = [[0.0004, 0.0001], [0.0001, 0.0009]]  # daily
    w = [0.5, 0.5]
    means = [0.0, 0.0]
    var, es = monte_carlo_var(w, cov, means, confidence=0.95, sims=40000, seed=7)
    sigma = stats.quad_form(w, cov) ** 0.5
    parametric = stats.norm_ppf(0.95) * sigma
    assert abs(var - parametric) / parametric < 0.06  # within 6% sampling error
    assert es > var  # tail average is worse than the threshold


def test_component_var_sums_to_total():
    cov = [[0.0004, 0.0001, 0.0], [0.0001, 0.0009, 0.0002], [0.0, 0.0002, 0.0016]]
    w = [0.5, 0.3, 0.2]
    rows = var_attribution(["A", "B", "C"], w, cov, confidence=0.95)
    total = stats.norm_ppf(0.95) * (stats.quad_form(w, cov) ** 0.5)
    # components are rounded to 5dp for display, so allow that much slack
    assert abs(sum(r.component_var for r in rows) - total) < 1e-4
    assert abs(sum(r.component_pct for r in rows) - 100.0) < 0.5


def test_incremental_var_negative_for_risky_position():
    """Dropping the most volatile holding should reduce VaR."""
    cov = [[0.0001, 0.0], [0.0, 0.01]]  # asset B is far riskier
    rows = var_attribution(["A", "B"], [0.5, 0.5], cov, confidence=0.95)
    by = {r.ticker: r for r in rows}
    assert by["B"].incremental_var < 0
    assert by["B"].component_var > by["A"].component_var


def test_historical_replay_finds_worst_window():
    values = [100, 105, 102, 80, 85, 90]
    worst = historical_replay(values, [f"d{i}" for i in range(6)], horizons=(1,))
    # the 105 -> 102 -> 80 stretch contains the worst single step: 102 -> 80
    assert abs(worst[1][0].ret - (80 / 102 - 1)) < 1e-4  # ret is rounded to 4dp
    assert worst[1][0].start == "d2" and worst[1][0].end == "d3"


# ---- factor model on sample data -------------------------------------------

def _portfolio() -> Portfolio:
    return Portfolio(
        name="Test",
        holdings=[
            Holding("AAPL", 40, 150, "Equity", "Technology"),
            Holding("MSFT", 25, 300, "Equity", "Technology"),
            Holding("TLT", 60, 98, "Bond", "Government"),
        ],
    )


def test_build_factor_returns():
    names, series = build_factor_returns(sample_prices())
    assert "Market" in names
    assert len(series) == len(names)
    assert len({len(s) for s in series}) == 1  # all equal length


def test_factor_model_decomposition():
    series = sample_prices()
    report = analyze(_portfolio(), series, benchmark=series.get("SPY"))
    f = analyze_factors(report, series)

    assert f.factors, f.notes
    # equity-heavy portfolio should carry positive market beta
    assert f.portfolio_betas["Market"] > 0.3
    # variance shares are a partition
    assert abs(f.systematic_share + f.specific_share - 1.0) < 1e-6
    # every asset gets an R² in [0, 1]
    for a in f.assets:
        assert 0.0 <= a.r_squared <= 1.0
    # factor + specific variance reconstructs total vol
    recon = math.sqrt(f.systematic_vol_annual ** 2 + f.specific_vol_annual ** 2)
    assert abs(recon - f.total_vol_annual) < 1e-3


def test_bond_has_negative_market_beta():
    """TLT was generated with a negative equity beta; the model must recover it."""
    series = sample_prices()
    p = Portfolio(holdings=[Holding("TLT", 60, 98, "Bond", "Government")])
    report = analyze(p, series, benchmark=series.get("SPY"))
    f = analyze_factors(report, series, proxies={"Market": "SPY"})
    assert f.assets[0].betas["Market"] < 0


def test_analyze_advanced_end_to_end():
    series = sample_prices()
    report = analyze(_portfolio(), series, benchmark=series.get("SPY"))
    adv = analyze_advanced(report, series, sims=4000)

    assert adv.ewma_vol_annual > 0
    assert adv.mc_var > 0
    assert adv.mc_es >= adv.mc_var
    assert abs(sum(a.component_pct for a in adv.attribution) - 100.0) < 1.0
    assert 1 in adv.worst_windows and adv.worst_windows[1][0].ret < 0


# ---- signal bridge ----------------------------------------------------------

def _make_signal_db(path, rows):
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE items (id TEXT PRIMARY KEY, source TEXT, channel TEXT, topic TEXT,"
        " title TEXT, url TEXT, summary TEXT, author TEXT, published TEXT,"
        " collected_at TEXT, heat REAL, meta TEXT)"
    )
    for i, (title, topic, heat) in enumerate(rows):
        conn.execute(
            "INSERT INTO items VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (str(i), "Test", "topic", topic, title, f"https://e/{i}", "", "",
             None, "2025-01-01T00:00:00+00:00", heat, "{}"),
        )
    conn.commit()
    conn.close()


def test_load_signals_missing_db_is_empty(tmp_path):
    assert load_signals(tmp_path / "nope.db") == []


def test_overlay_matches_and_scores_attention(tmp_path):
    db = tmp_path / "signaldesk.db"
    _make_signal_db(db, [
        ("AAPL faces antitrust probe", "Technology", 90.0),
        ("Unrelated shipping news", "Logistics", 80.0),
    ])
    series = sample_prices()
    report = analyze(_portfolio(), series, benchmark=series.get("SPY"))
    rows = overlay(report, load_signals(db))

    by = {r.ticker: r for r in rows}
    assert by["AAPL"].item_count == 1
    assert by["AAPL"].news_heat == 90.0
    # attention scales heat by the position's risk share
    expected = 90.0 * (by["AAPL"].risk_contribution_pct / 100.0)
    assert abs(by["AAPL"].attention - round(expected, 1)) < 0.2
    # the logistics story matches nothing we hold
    assert by["TLT"].item_count == 0


def test_overlay_avoids_substring_false_positives(tmp_path):
    """'GLD' must not match inside 'GOLDMAN'."""
    db = tmp_path / "s.db"
    _make_signal_db(db, [("GOLDMAN raises forecast", "Financials", 70.0)])
    p = Portfolio(holdings=[Holding("GLD", 20, 175, "Commodity", "Metals")])
    report = analyze(p, sample_prices())
    rows = overlay(report, load_signals(db))
    assert rows[0].item_count == 0


def test_overlay_uses_aliases(tmp_path):
    db = tmp_path / "s.db"
    _make_signal_db(db, [("Apple unveils new chip", "Technology", 60.0)])
    p = Portfolio(holdings=[Holding("AAPL", 40, 150, "Equity", "Technology")])
    report = analyze(p, sample_prices())
    rows = overlay(report, load_signals(db), aliases={"AAPL": ["Apple"]})
    assert rows[0].item_count == 1


def test_flags_holding_used_as_its_own_factor_proxy():
    """A holding that is also a factor proxy must be called out, not passed off."""
    series = sample_prices()
    p = Portfolio(holdings=[Holding("TLT", 60, 98, "Bond", "Government")])
    report = analyze(p, series, benchmark=series.get("SPY"))
    f = analyze_factors(report, series, proxies={"Market": "SPY", "Rates": "TLT"})
    assert any("also used as a factor proxy" in n for n in f.notes)
    # and the artifact itself is real: R²=1 against itself
    assert f.assets[0].r_squared > 0.999


# ---- factor-space scenarios -------------------------------------------------

def test_factor_scenario_uses_per_asset_betas():
    """A high-beta and a negative-beta name must move differently, by their betas."""
    from risk_desk.scenarios import FactorScenario, apply_factor_scenario
    series = sample_prices()
    p = Portfolio(holdings=[
        Holding("AAPL", 40, 150, "Equity", "Technology"),
        Holding("TLT", 60, 98, "Bond", "Government"),
    ])
    report = analyze(p, series, benchmark=series.get("SPY"))
    f = analyze_factors(report, series, proxies={"Market": "SPY"})

    res = apply_factor_scenario(
        report, f, FactorScenario("Bear", shocks={"Market": -0.20}))
    by = {d["ticker"]: d for d in res.per_position}
    # AAPL has positive market beta -> falls; TLT has negative beta -> rises
    assert by["AAPL"]["return"] < 0
    assert by["TLT"]["return"] > 0
    # and the per-position return equals beta x shock
    aapl_beta = {a.ticker: a for a in f.assets}["AAPL"].betas["Market"]
    assert abs(by["AAPL"]["return"] - aapl_beta * -0.20) < 1e-3


def test_factor_scenario_pnl_matches_position_sum():
    from risk_desk.scenarios import FactorScenario, apply_factor_scenario
    series = sample_prices()
    report = analyze(_portfolio(), series, benchmark=series.get("SPY"))
    f = analyze_factors(report, series)
    res = apply_factor_scenario(
        report, f, FactorScenario("Bear", shocks={"Market": -0.20}))
    assert abs(res.pnl - sum(d["pnl"] for d in res.per_position)) < 1.0
    assert abs(res.pnl_pct - res.pnl / report.total_value) < 1e-3


def test_factor_scenario_reports_unfitted_weight():
    """Positions with no factor fit are held flat and the gap is disclosed."""
    from risk_desk.scenarios import FactorScenario, apply_factor_scenario
    from risk_desk.models import PriceSeries
    series = sample_prices()
    # ZZZ has price history but will not be in the factor fit set
    p = Portfolio(holdings=[Holding("AAPL", 40, 150), Holding("ZZZ", 10, 100)])
    series = dict(series)
    series["ZZZ"] = PriceSeries("ZZZ", series["AAPL"].dates, series["AAPL"].closes)
    report = analyze(p, series, benchmark=series.get("SPY"))
    f = analyze_factors(report, series, proxies={"Market": "SPY"})
    # drop ZZZ's fit to simulate an unfitted holding
    f.assets = [a for a in f.assets if a.ticker != "ZZZ"]
    res = apply_factor_scenario(
        report, f, FactorScenario("Bear", shocks={"Market": -0.20}))
    assert res.unexplained_weight > 0
    assert any("no factor fit" in n for n in res.notes)


def test_default_factor_scenarios_cover_both_directions():
    from risk_desk.scenarios import default_factor_scenarios
    names = [s.name for s in default_factor_scenarios()]
    assert "Equity bear market" in names and "Melt-up" in names
    for s in default_factor_scenarios():
        assert s.shocks  # every scenario actually shocks something
