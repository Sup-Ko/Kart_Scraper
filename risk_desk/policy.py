"""Policy exposure — how much of your risk depends on federal contracting.

Joins federal/public contract award data to the positions in
your portfolio, and answers a question ordinary risk tools do not ask:

    what share of my portfolio's **risk** sits in companies whose revenue
    depends on federal awards?

Risk share, not value share, is the headline. A 2% position that drives 15% of
portfolio volatility is a bigger policy bet than a 10% position that barely
moves. The component risk contributions computed in ``analytics.py`` already
tell us that, so the exposure is weighted by them.

This is a *concentration* lens, not a prediction. It says "this much of your
risk rides on one payer continuing to spend" — which is exactly the kind of
correlated exposure that looks like diversification until the policy changes.

Matching companies to tickers is approximate: no public crosswalk links award
recipients to listed securities, so it is done by normalized company name using
a graded matcher (see ``namematch``). Supply names via the aliases
file (``--aliases``) or a ``company`` column in the portfolio CSV.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from .namematch import match_score

MIN_MATCH = 0.8  # stricter than conflict review: this feeds a risk number


@dataclass
class PositionExposure:
    ticker: str
    company: str
    weight: float
    risk_contribution_pct: float
    award_total: float = 0.0
    award_count: int = 0
    agencies: dict[str, float] = field(default_factory=dict)
    match_score: float = 0.0

    @property
    def exposed(self) -> bool:
        return self.award_count > 0


@dataclass
class PolicyReport:
    positions: list[PositionExposure] = field(default_factory=list)
    exposed_value_share: float = 0.0
    exposed_risk_share: float = 0.0
    total_awards: float = 0.0
    by_agency: dict[str, float] = field(default_factory=dict)
    window_days: int = 365
    notes: list[str] = field(default_factory=list)

    @property
    def any_exposure(self) -> bool:
        return any(p.exposed for p in self.positions)


def load_awards(db_path: Path | str, window_days: int = 365) -> list[dict]:
    """Read recent awards from a govdata database. Empty list if absent."""
    path = Path(db_path)
    if not path.exists():
        return []
    cutoff = (date.today() - timedelta(days=window_days)).isoformat()
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT recipient, awarding_agy, amount, action_date
               FROM award
               WHERE recipient IS NOT NULL AND amount IS NOT NULL
                 AND (action_date IS NULL OR action_date >= ?)""",
            (cutoff,),
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()
    return [dict(r) for r in rows]


def analyze_policy_exposure(
    report,
    awards: list[dict],
    aliases: dict[str, list[str]] | None = None,
    companies: dict[str, str] | None = None,
    window_days: int = 365,
    min_match: float = MIN_MATCH,
) -> PolicyReport:
    """Attribute federal award exposure across the portfolio.

    ``report`` is a :class:`~risk_desk.analytics.RiskReport`; its weights and
    component risk contributions drive the exposure shares.
    """
    aliases = aliases or {}
    companies = companies or {}
    out = PolicyReport(window_days=window_days)

    if not awards:
        out.notes.append(
            "No federal award data found. Run `govdata ingest --awards` to "
            "populate it, then re-run with --govdata."
        )

    for pos in report.positions:
        # Candidate names for this holding: explicit company, aliases, ticker.
        names = []
        if companies.get(pos.ticker):
            names.append(companies[pos.ticker])
        if getattr(pos, "company", "") and pos.company not in names:
            names.append(pos.company)
        names.extend(aliases.get(pos.ticker.upper(), []))
        primary = names[0] if names else pos.ticker

        pe = PositionExposure(
            ticker=pos.ticker,
            company=primary,
            weight=pos.weight,
            risk_contribution_pct=pos.risk_contribution_pct,
        )

        if names:  # a bare ticker is too weak to match recipient legal names
            for a in awards:
                best = max((match_score(n, a["recipient"]) for n in names), default=0.0)
                if best < min_match:
                    continue
                amount = float(a["amount"] or 0.0)
                pe.award_total += amount
                pe.award_count += 1
                pe.match_score = max(pe.match_score, best)
                agency = a["awarding_agy"] or "Unknown agency"
                pe.agencies[agency] = pe.agencies.get(agency, 0.0) + amount

        pe.agencies = dict(sorted(pe.agencies.items(), key=lambda kv: -kv[1]))
        out.positions.append(pe)

    exposed = [p for p in out.positions if p.exposed]
    out.exposed_value_share = round(sum(p.weight for p in exposed), 4)
    out.exposed_risk_share = round(
        sum(p.risk_contribution_pct for p in exposed) / 100.0, 4
    )
    out.total_awards = round(sum(p.award_total for p in exposed), 2)

    by_agency: dict[str, float] = {}
    for p in exposed:
        for agency, amount in p.agencies.items():
            by_agency[agency] = by_agency.get(agency, 0.0) + amount
    out.by_agency = dict(sorted(by_agency.items(), key=lambda kv: -kv[1]))

    out.positions.sort(key=lambda p: -p.risk_contribution_pct)

    if exposed and out.exposed_risk_share > out.exposed_value_share + 0.05:
        out.notes.append(
            f"Policy-exposed names carry {out.exposed_risk_share:.0%} of portfolio "
            f"risk on {out.exposed_value_share:.0%} of its value — this exposure is "
            "concentrated in your higher-volatility positions."
        )
    if not aliases and not companies and out.positions:
        out.notes.append(
            "No company names supplied, so only tickers were available to match "
            "against award recipients. Pass --aliases to improve coverage."
        )
    return out
