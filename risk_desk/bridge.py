"""Bridge: overlay Signal Desk news heat onto portfolio positions.

This is the join that separate risk and market-data silos never give you — one
screen where "what's happening" sits on top of "what I own and what actually
drives my risk".

A news item is attached to a position when any of the position's *aliases*
appears in the item's title, summary, or topic. Aliases default to the ticker
plus its sector, and can be extended per-ticker via a JSON file so "AAPL" also
matches "Apple".

The headline output is the **attention score**: news heat weighted by how much
that position actually contributes to portfolio risk. A hot story about a
position carrying 1% of your risk matters far less than a warm one about the
position carrying 40%.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from .analytics import RiskReport

DEFAULT_SIGNAL_DB = Path("signaldesk.db")


@dataclass
class PositionSignals:
    ticker: str
    risk_contribution_pct: float
    news_heat: float = 0.0  # peak heat among matched items
    item_count: int = 0
    attention: float = 0.0  # news_heat × risk share — what to actually look at
    items: list[dict] = field(default_factory=list)


def load_signals(db_path: Path | str = DEFAULT_SIGNAL_DB, limit: int = 500) -> list[dict]:
    """Read recent items out of a Signal Desk database. Empty list if absent."""
    path = Path(db_path)
    if not path.exists():
        return []
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT title, summary, topic, url, source, heat, published, collected_at "
            "FROM items ORDER BY heat DESC LIMIT ?",
            (limit,),
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()
    return [dict(r) for r in rows]


def load_aliases(path: Path | str | None) -> dict[str, list[str]]:
    """Load {ticker: [alias, ...]} from JSON, if provided."""
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        return {}
    raw = json.loads(p.read_text(encoding="utf-8"))
    return {k.upper(): [str(a) for a in v] for k, v in raw.items()}


def _matches(text: str, alias: str) -> bool:
    """Whole-word, case-insensitive containment.

    Word boundaries stop short tickers from matching inside unrelated words
    (the classic false positive: "GLD" inside "GOLDMAN", "A" inside anything).
    """
    if not alias:
        return False
    return re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", text, re.IGNORECASE) is not None


def overlay(
    report: RiskReport,
    signals: list[dict],
    aliases: dict[str, list[str]] | None = None,
    max_items_per_position: int = 5,
) -> list[PositionSignals]:
    """Attach news items to positions and rank by attention score."""
    aliases = aliases or {}
    out: list[PositionSignals] = []

    for pos in report.positions:
        terms = [pos.ticker, *aliases.get(pos.ticker.upper(), [])]
        # the sector is a useful broad match, but only when it is a real label
        if pos.sector and pos.sector != "Unclassified":
            terms.append(pos.sector)

        ps = PositionSignals(
            ticker=pos.ticker,
            risk_contribution_pct=pos.risk_contribution_pct,
        )

        for item in signals:
            haystack = " ".join(
                str(item.get(f) or "") for f in ("title", "summary", "topic")
            )
            if any(_matches(haystack, t) for t in terms):
                ps.item_count += 1
                heat = float(item.get("heat") or 0.0)
                ps.news_heat = max(ps.news_heat, heat)
                if len(ps.items) < max_items_per_position:
                    ps.items.append(
                        {
                            "title": item.get("title", ""),
                            "url": item.get("url", ""),
                            "heat": heat,
                            "source": item.get("source", ""),
                        }
                    )

        # attention = how hot the news is, scaled by this position's share of risk
        ps.attention = round(ps.news_heat * (ps.risk_contribution_pct / 100.0), 1)
        ps.items.sort(key=lambda d: -d["heat"])
        out.append(ps)

    out.sort(key=lambda p: (-p.attention, -p.news_heat))
    return out
