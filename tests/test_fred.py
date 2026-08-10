"""Tests for FRED intake and external factor injection — offline."""

from __future__ import annotations

from risk_desk.analytics import analyze
from risk_desk.factors import analyze_factors
from risk_desk.fred import (
    align_to_dates,
    build_fred_factors,
    factor_units,
    have_key,
    parse_observations,
    to_factor_series,
)
from risk_desk.models import Holding, Portfolio
from risk_desk.prices import sample_prices

PAYLOAD = {
    "observations": [
        {"date": "2026-01-02", "value": "4.20"},
        {"date": "2026-01-03", "value": "4.35"},
        {"date": "2026-01-04", "value": "."},      # FRED missing marker
        {"date": "2026-01-05", "value": "4.30"},
        {"date": "2026-01-06", "value": "not-a-number"},
    ]
}


def test_parse_observations_drops_missing_markers():
    rows = parse_observations(PAYLOAD)
    assert rows == [("2026-01-02", 4.20), ("2026-01-03", 4.35), ("2026-01-05", 4.30)]


def test_parse_observations_empty():
    assert parse_observations({}) == []
    assert parse_observations({"observations": None}) == []


def test_align_to_dates_forward_fills_gaps():
    """A holiday gap must become 'no change', never an invented move."""
    obs = parse_observations(PAYLOAD)
    dates = ["2026-01-02", "2026-01-03", "2026-01-04", "2026-01-05"]
    aligned = align_to_dates(obs, dates)
    assert aligned == [4.20, 4.35, 4.35, 4.30]   # 01-04 carries 01-03 forward


def test_align_to_dates_before_first_observation():
    obs = [("2026-01-05", 4.0)]
    assert align_to_dates(obs, ["2026-01-01", "2026-01-05"]) == [None, 4.0]


def test_to_factor_series_diff_mode_gives_level_changes():
    """Yields are levels; the factor is the daily change in percentage points."""
    obs = parse_observations(PAYLOAD)
    dates = ["2026-01-02", "2026-01-03", "2026-01-04", "2026-01-05"]
    series = to_factor_series(obs, dates, mode="diff")
    assert len(series) == len(dates) - 1
    assert abs(series[0] - 0.15) < 1e-9    # 4.20 -> 4.35
    assert abs(series[1] - 0.0) < 1e-9     # forward-filled, no move
    assert abs(series[2] - (-0.05)) < 1e-9  # 4.35 -> 4.30


def test_to_factor_series_pct_mode():
    obs = [("2026-01-02", 100.0), ("2026-01-03", 110.0)]
    series = to_factor_series(obs, ["2026-01-02", "2026-01-03"], mode="pct")
    assert abs(series[0] - 0.10) < 1e-9


def test_to_factor_series_unknown_leading_values_are_flat():
    obs = [("2026-01-05", 4.0)]
    series = to_factor_series(obs, ["2026-01-01", "2026-01-02"], mode="diff")
    assert series == [0.0]


def test_build_fred_factors_without_key_is_empty(monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    assert not have_key()
    assert build_fred_factors(["2026-01-02", "2026-01-03"]) == {}   # no network call


def test_factor_units_labels_the_beta_meaning():
    assert "1pp" in factor_units("Rates10Y")
    assert "1%" in factor_units("Dollar")


# ---- the artifact this exists to fix ----------------------------------------

def _portfolio():
    return Portfolio(holdings=[
        Holding("AAPL", 40, 150, "Equity", "Technology"),
        Holding("TLT", 60, 98, "Bond", "Government"),
    ])


def test_etf_proxy_creates_self_regression_artifact():
    """Baseline: TLT as both holding and rates proxy forces R^2 = 1."""
    series = sample_prices()
    report = analyze(_portfolio(), series, benchmark=series.get("SPY"))
    f = analyze_factors(report, series, proxies={"Market": "SPY", "Rates": "TLT"})
    tlt = {a.ticker: a for a in f.assets}["TLT"]
    assert tlt.r_squared > 0.999
    assert tlt.specific_vol_annual == 0.0
    assert any("also used as a factor proxy" in n for n in f.notes)


def test_external_factor_removes_the_artifact():
    """An independent rates series restores real specific risk for TLT."""
    import random

    series = sample_prices()
    report = analyze(_portfolio(), series, benchmark=series.get("SPY"))
    n = len(series["AAPL"].closes) - 1
    rng = random.Random(7)
    rates = [rng.gauss(0.0, 0.03) for _ in range(n)]

    f = analyze_factors(report, series, proxies={"Market": "SPY"},
                        extra_factors={"Rates10Y": rates})
    tlt = {a.ticker: a for a in f.assets}["TLT"]
    assert "Rates10Y" in f.factors
    assert tlt.r_squared < 0.9              # no longer explained by itself
    assert tlt.specific_vol_annual > 0.0    # real idiosyncratic risk again
    # and no self-proxy warning, because no holding is a proxy any more
    assert not any("also used as a factor proxy" in n for n in f.notes)


def test_extra_factors_are_appended_to_proxies():
    series = sample_prices()
    report = analyze(_portfolio(), series, benchmark=series.get("SPY"))
    n = len(series["AAPL"].closes) - 1
    f = analyze_factors(report, series, proxies={"Market": "SPY"},
                        extra_factors={"Rates10Y": [0.001] * n})
    assert f.factors == ["Market", "Rates10Y"]


def test_empty_extra_factors_are_ignored():
    series = sample_prices()
    report = analyze(_portfolio(), series, benchmark=series.get("SPY"))
    f = analyze_factors(report, series, proxies={"Market": "SPY"},
                        extra_factors={"Empty": []})
    assert f.factors == ["Market"]
