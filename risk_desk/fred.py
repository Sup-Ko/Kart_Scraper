"""FRED intake — real economic series as risk factors.

Using an ETF as a factor proxy has a flaw the cockpit has been openly flagging:
when TLT is both a *holding* and the rates factor, the regression is against
itself, forcing R²=1 and zero specific risk. Disclosing that was honest; fixing
it is better. Actual Treasury yields and credit spreads are independent of what
you happen to own, so the artifact disappears entirely.

    source: FRED (Federal Reserve Bank of St. Louis) API
    lag:    daily series post with a 1-2 business day delay
    key:    free from fredaccount.stlouisfed.org (``FRED_API_KEY``); everything
            degrades gracefully to the ETF proxies without one
    limits: yield and spread series are published as LEVELS in percent, not
            returns

That last point drives the modelling. A level cannot be mixed with equity
returns, so the factor is the daily **change** in the level, in percentage
points. Betas against it therefore read as "return per 1pp move in the yield" —
a duration-like sensitivity, not a correlation with an ETF's return. That is a
different and more interpretable unit, and it is labelled as such wherever it
surfaces.
"""

from __future__ import annotations

import json
import os

API = "https://api.stlouisfed.org/fred/series/observations"

# Series worth having as factors, with how each should be transformed.
#   "diff"  -> daily change in the level (yields, spreads)
#   "pct"   -> percentage return (index levels such as the dollar index)
DEFAULT_SERIES: dict[str, tuple[str, str]] = {
    "Rates10Y": ("DGS10", "diff"),
    "Rates2Y": ("DGS2", "diff"),
    "CreditHY": ("BAMLH0A0HYM2", "diff"),
    "Dollar": ("DTWEXBGS", "pct"),
}

MISSING = "."  # FRED marks unavailable observations with a literal period


def api_key(explicit: str | None = None) -> str:
    return explicit or os.environ.get("FRED_API_KEY", "")


def have_key(explicit: str | None = None) -> bool:
    return bool(api_key(explicit))


def parse_observations(payload: dict) -> list[tuple[str, float]]:
    """Extract (date, value) pairs, dropping FRED's '.' missing markers."""
    out = []
    for o in (payload or {}).get("observations", []) or []:
        date = (o.get("date") or "").strip()
        raw = (o.get("value") or "").strip()
        if not date or not raw or raw == MISSING:
            continue
        try:
            out.append((date, float(raw)))
        except ValueError:
            continue
    return out


def align_to_dates(observations: list[tuple[str, float]],
                   dates: list[str]) -> list[float | None]:
    """Align a series to the given dates, carrying the last value forward.

    Economic series and market price series do not share a calendar — FRED has
    gaps on holidays the equity market observes and vice versa. Forward-filling
    the most recent published value is what a practitioner does; a gap becomes
    "no change", never a fabricated move.
    """
    lookup = dict(observations)
    ordered = sorted(observations)
    out: list[float | None] = []
    last: float | None = None
    idx = 0
    for d in dates:
        if d in lookup:
            last = lookup[d]
        else:
            # advance through any observations that precede this date
            while idx < len(ordered) and ordered[idx][0] <= d:
                last = ordered[idx][1]
                idx += 1
        out.append(last)
    return out


def to_factor_series(observations: list[tuple[str, float]], dates: list[str],
                     mode: str = "diff") -> list[float]:
    """Turn a level series into a factor series aligned to ``dates``.

    Returns one value per *return period*, i.e. ``len(dates) - 1`` entries, so
    it lines up with asset returns computed from the same dates.
    """
    levels = align_to_dates(observations, dates)
    series: list[float] = []
    for prev, cur in zip(levels, levels[1:]):
        if prev is None or cur is None:
            series.append(0.0)      # unknown -> no move, never invented
        elif mode == "pct":
            series.append((cur / prev - 1.0) if prev else 0.0)
        else:
            series.append(cur - prev)
    return series


class FredProvider:
    """Fetch FRED series. Network required; no-ops cleanly without a key."""

    def __init__(self, key: str | None = None):
        self.key = api_key(key)

    def fetch(self, series_id: str, observations: int = 400
              ) -> list[tuple[str, float]]:
        if not self.key:
            return []
        import requests

        params = {
            "series_id": series_id,
            "api_key": self.key,
            "file_type": "json",
            "sort_order": "desc",
            "limit": observations,
        }
        try:
            resp = requests.get(
                API, params=params,
                headers={"User-Agent": "risk_desk/0.1 (personal use)"},
                timeout=20,
            )
            resp.raise_for_status()
            payload = json.loads(resp.content)
        except Exception:
            return []
        return sorted(parse_observations(payload))


def build_fred_factors(dates: list[str], series: dict[str, tuple[str, str]] | None = None,
                       key: str | None = None) -> dict[str, list[float]]:
    """Fetch and transform the configured FRED series into factor series.

    Returns an empty mapping when no key is available, so callers fall back to
    the ETF proxies rather than failing.
    """
    series = series or DEFAULT_SERIES
    if not have_key(key) or len(dates) < 2:
        return {}
    provider = FredProvider(key)
    out: dict[str, list[float]] = {}
    for name, (series_id, mode) in series.items():
        observations = provider.fetch(series_id)
        if not observations:
            continue
        out[name] = to_factor_series(observations, dates, mode)
    return out


def factor_units(name: str, series: dict[str, tuple[str, str]] | None = None) -> str:
    """How a beta against this factor should be read."""
    series = series or DEFAULT_SERIES
    mode = series.get(name, ("", "diff"))[1]
    return ("return per 1pp change in the level" if mode == "diff"
            else "return per 1% move")
