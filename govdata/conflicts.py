"""Conflict-of-interest analysis: trades in companies receiving federal money.

This is what congressional disclosure data is actually good for. As a trading
signal the ~30-45 day statutory lag makes a PTR worthless — by the time it is
public, the information is priced in and every tracker has already published
it. But the same filing is *excellent* accountability data, because the lag
does not matter at all for the question "did a member trade a company their
committee funds?"

What this module produces is a list of **candidates for human review**, not
accusations. A match here means a member's disclosed trade in company X is
close in time to a federal award to a company whose name resembles X. That is
a coincidence worth a look, and nothing more:

* name matching across datasets with no shared key is approximate (the score
  is carried through so weak matches are visible as weak);
* disclosed amounts are broad ranges, so position size is unknowable;
* many trades are made by managed accounts, spouses, or blind trusts;
* a member may have no involvement whatsoever with the awarding decision.

Treat the output as "look here", never as "this happened".
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from .committees import jurisdiction_over
from .company import match_score
from .congress import committees_for_member
from .fec import contributions_to_member

MIN_SCORE = 0.6


@dataclass
class Conflict:
    member: str
    state_dst: str
    ticker: str | None
    asset: str
    tx_type: str
    tx_date: str
    amount_low: int | None
    amount_high: int | None
    recipient: str
    awarding_agency: str
    award_amount: float | None
    award_date: str
    gap_days: int
    name_score: float
    committees: list[str] = field(default_factory=list)
    jurisdiction: str = ""  # committee with jurisdiction over the awarding agency
    jurisdiction_basis: str = ""
    jurisdiction_strength: float = 0.0
    pac_total: float = 0.0      # from THIS company's PAC to THIS member
    pac_count: int = 0

    @property
    def amount_range(self) -> str:
        if self.amount_low is None:
            return "undisclosed"
        if self.amount_high is None:
            return f"${self.amount_low:,}+"
        return f"${self.amount_low:,}-${self.amount_high:,}"

    @property
    def salience(self) -> float:
        """Ranking score. Jurisdiction dominates timing, deliberately.

        A trade in a company overseen by the member's own committee is
        structurally more interesting than one that merely happened to fall
        near an award date, so jurisdiction is weighted far above proximity.
        """
        proximity = max(0.0, 1.0 - self.gap_days / 180.0)
        # A recorded contribution from the same company's PAC is another
        # structural link, weighted below jurisdiction but above timing.
        funded = 0.5 if self.pac_count else 0.0
        return round(
            self.name_score * (1.0 + 2.0 * self.jurisdiction_strength)
            + funded + 0.3 * proximity,
            3,
        )


def find_conflicts(
    conn: sqlite3.Connection,
    window_days: int = 90,
    min_score: float = MIN_SCORE,
    limit: int = 200,
) -> list[Conflict]:
    """Pair disclosed trades with federal awards to similarly-named companies.

    ``window_days`` is the maximum separation, in either direction, between the
    trade and the award — a trade shortly *before* an award is as interesting
    as one shortly after.
    """
    trades = conn.execute(
        """SELECT t.asset, t.ticker, t.tx_type, t.tx_date, t.amount_low,
                  t.amount_high, f.last_name, f.first_name, f.state_dst
           FROM ptr_trade t JOIN ptr_filing f ON f.doc_id = t.doc_id
           WHERE t.tx_date IS NOT NULL"""
    ).fetchall()
    awards = conn.execute(
        """SELECT recipient, awarding_agy, amount, action_date
           FROM award WHERE action_date IS NOT NULL AND recipient IS NOT NULL"""
    ).fetchall()
    if not trades or not awards:
        return []

    from datetime import date

    def as_date(value):
        try:
            return date.fromisoformat(value)
        except (ValueError, TypeError):
            return None

    award_rows = [(a, as_date(a["action_date"])) for a in awards]
    award_rows = [(a, d) for a, d in award_rows if d]

    _committee_cache: dict[str, list[str]] = {}
    _pac_cache: dict[tuple, list] = {}
    found: list[Conflict] = []
    for t in trades:
        td = as_date(t["tx_date"])
        if not td:
            continue
        for a, ad in award_rows:
            gap = abs((ad - td).days)
            if gap > window_days:
                continue
            score = match_score(t["asset"], a["recipient"])
            if score < min_score:
                continue
            member = f"{t['first_name']} {t['last_name']}".strip()

            # Committee jurisdiction over the awarding agency, when we have
            # assignment data. Absent it, this stays empty and the conflict is
            # reported on timing alone — never silently treated as no conflict.
            committees = _committee_cache.get(t["last_name"])
            if committees is None:
                committees = committees_for_member(
                    conn, t["last_name"] or "", t["first_name"] or "",
                    t["state_dst"] or "",
                )
                _committee_cache[t["last_name"]] = committees

            jurisdiction = ""
            basis = ""
            strength = 0.0
            for committee in committees:
                jm = jurisdiction_over(committee, a["awarding_agy"] or "")
                if jm and jm.strength > strength:
                    jurisdiction, basis, strength = committee, jm.basis, jm.strength

            # Money flowing the other way: did this company's PAC give to
            # this member? Legal and routine, but part of the same picture.
            pac_key = (t["last_name"], a["recipient"])
            contributions = _pac_cache.get(pac_key)
            if contributions is None:
                contributions = contributions_to_member(
                    conn, t["last_name"] or "", company=a["recipient"] or "")
                _pac_cache[pac_key] = contributions

            found.append(
                Conflict(
                    member=member,
                    state_dst=t["state_dst"] or "",
                    ticker=t["ticker"],
                    asset=t["asset"],
                    tx_type=t["tx_type"] or "",
                    tx_date=t["tx_date"],
                    amount_low=t["amount_low"],
                    amount_high=t["amount_high"],
                    recipient=a["recipient"],
                    awarding_agency=a["awarding_agy"] or "",
                    award_amount=a["amount"],
                    award_date=a["action_date"],
                    gap_days=gap,
                    name_score=score,
                    committees=committees,
                    jurisdiction=jurisdiction,
                    jurisdiction_basis=basis,
                    jurisdiction_strength=strength,
                    pac_total=round(sum(c["amount"] or 0 for c in contributions), 2),
                    pac_count=len(contributions),
                )
            )

    # jurisdiction-backed matches first, then name confidence and proximity
    found.sort(key=lambda c: -c.salience)
    return found[:limit]


def lag_report(conn: sqlite3.Connection) -> dict:
    """Disclosure-lag statistics, including what was excluded and why.

    Reporting the discards alongside the statistic is the point. A lag computed
    only over rows that happened to parse cleanly, with the rest silently
    dropped, is a number with an unknown denominator.
    """
    rows = conn.execute(
        "SELECT lag_days FROM ptr_trade WHERE lag_days IS NOT NULL"
    ).fetchall()
    lags = sorted(r["lag_days"] for r in rows)
    total_trades = conn.execute("SELECT COUNT(*) n FROM ptr_trade").fetchone()["n"]
    missing = total_trades - len(lags)

    # Out-of-range values indicate a parsing problem, not a real filing lag.
    # Count them explicitly instead of filtering them away.
    implausible = [x for x in lags if x < 0 or x > 400]
    usable = [x for x in lags if 0 <= x <= 400]

    issues = conn.execute("SELECT COUNT(*) n FROM parse_issue").fetchone()["n"]

    out = {
        "trades_total": total_trades,
        "with_lag": len(lags),
        "missing_lag": missing,
        "implausible_lag": len(implausible),
        "usable": len(usable),
        "parse_issues": issues,
    }
    if usable:
        out.update({
            "mean": round(sum(usable) / len(usable), 1),
            "median": usable[len(usable) // 2],
            "min": usable[0],
            "max": usable[-1],
            "over_45_day_deadline": sum(1 for x in usable if x > 45),
        })
    return out
