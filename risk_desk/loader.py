"""Load a portfolio from a simple CSV — an open, portable format (no lock-in).

Expected columns (header row required):

    ticker,quantity,cost_basis,asset_class,sector,currency,company

Only ``ticker`` and ``quantity`` are mandatory; the rest default sensibly.
``company`` is the legal/company name, used to join external datasets such as
federal award recipients (see ``policy.py``).
"""

from __future__ import annotations

import csv
from pathlib import Path

from .models import Holding, Portfolio


def load_portfolio_csv(path: Path | str, name: str = "My Portfolio") -> Portfolio:
    holdings: list[Holding] = []
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            ticker = (row.get("ticker") or "").strip()
            if not ticker:
                continue
            holdings.append(
                Holding(
                    ticker=ticker.upper(),
                    quantity=float(row.get("quantity") or 0),
                    cost_basis=float(row.get("cost_basis") or 0),
                    asset_class=(row.get("asset_class") or "Equity").strip(),
                    sector=(row.get("sector") or "Unclassified").strip(),
                    currency=(row.get("currency") or "USD").strip(),
                    company=(row.get("company") or "").strip(),
                )
            )
    return Portfolio(name=name, holdings=holdings)


SAMPLE_PORTFOLIO_CSV = """ticker,quantity,cost_basis,asset_class,sector,currency,company
AAPL,40,150.00,Equity,Technology,USD,Apple Inc
MSFT,25,300.00,Equity,Technology,USD,Microsoft Corporation
JPM,30,140.00,Equity,Financials,USD,JPMorgan Chase
XOM,50,95.00,Equity,Energy,USD,Exxon Mobil Corporation
TLT,60,98.00,Bond,Government,USD,
GLD,20,175.00,Commodity,Metals,USD,
"""


def write_sample_portfolio(path: Path | str) -> None:
    Path(path).write_text(SAMPLE_PORTFOLIO_CSV, encoding="utf-8")
