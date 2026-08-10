"""Price history: load from disk, fetch from a free source, or generate samples.

Providers are pluggable. The default offline path uses bundled/sample data so
the whole cockpit runs and is testable without a network. On your own machine,
``StooqProvider`` pulls free daily history (no API key) — respect the source's
terms and don't hammer it.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from .models import PriceSeries

_DATA = Path(__file__).parent / "data"


def load_prices_json(path: Path | str) -> dict[str, PriceSeries]:
    """Load a {ticker: {dates, closes}} JSON file into PriceSeries."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return {
        t: PriceSeries(ticker=t, dates=v["dates"], closes=[float(c) for c in v["closes"]])
        for t, v in raw.items()
    }


def sample_prices() -> dict[str, PriceSeries]:
    """The bundled sample price history (offline demo / tests)."""
    return load_prices_json(_DATA / "sample_prices.json")


def align(series: dict[str, PriceSeries], tickers: list[str]) -> tuple[list[str], dict[str, list[float]]]:
    """Align the requested tickers to their common trailing date window.

    Returns (dates, {ticker: closes}) truncated to the shortest history so every
    series is the same length — a precondition for the covariance math.
    """
    available = [t for t in tickers if t in series and series[t].closes]
    if not available:
        return [], {}
    min_len = min(len(series[t].closes) for t in available)
    dates = series[available[0]].dates[-min_len:]
    closes = {t: series[t].closes[-min_len:] for t in available}
    return dates, closes


class StooqProvider:
    """Fetch free daily history from stooq.com (no key). Network required."""

    URL = "https://stooq.com/q/d/l/?s={sym}&i=d"

    def __init__(self, suffix: str = ".us"):
        self.suffix = suffix  # stooq uses e.g. aapl.us for US tickers

    def fetch(self, ticker: str, lookback: int = 260) -> PriceSeries | None:
        import requests

        sym = ticker.lower()
        if "." not in sym:
            sym += self.suffix
        try:
            resp = requests.get(
                self.URL.format(sym=sym),
                headers={"User-Agent": "risk_desk/0.1 (personal use)"},
                timeout=20,
            )
            resp.raise_for_status()
        except Exception:
            return None
        dates, closes = [], []
        reader = csv.DictReader(io.StringIO(resp.text))
        for row in reader:
            close = row.get("Close")
            date = row.get("Date")
            if not close or not date:
                continue
            try:
                closes.append(float(close))
                dates.append(date)
            except ValueError:
                continue
        if not closes:
            return None
        return PriceSeries(ticker=ticker, dates=dates[-lookback:], closes=closes[-lookback:])
