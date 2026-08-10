"""Federal lobbying disclosure intake (Lobbying Disclosure Act filings).

The final leg of the picture. Awards show money going to a company, campaign
finance shows money going to legislators, committee jurisdiction shows who
oversees the agency — and LDA filings show the company *directly petitioning*
named government entities on named issues, quarter by quarter.

The useful question this answers: **does a company you hold lobby the same
agency that awards it contracts?** That overlap is public, entirely legal, and
a real description of how dependent a business is on one relationship.

    source: lda.senate.gov/api — the Senate LDA database, a documented public
            API. Anonymous access works at a lower rate limit; a free key
            raises it.
    lag:    quarterly. Filings are due 20 days after each quarter ends, so
            expect data to be weeks to months behind activity.
    limits: income/expense figures are reported per filing, often rounded, and
            cover the registrant's whole relationship with the client — they
            are not per-agency or per-issue amounts. Do not read them as
            "spent lobbying this agency".

NOTE ON SCOPE: this is the LDA API, which is documented for programmatic use.
It is a different system from the Senate electronic financial disclosure search
(efdsearch.senate.gov), which requires accepting an agreement to obtain a
session cookie and is deliberately NOT implemented here.
"""

from __future__ import annotations

import json
import os
import sqlite3

from . import db, http

API_ROOT = "https://lda.senate.gov/api/v1"


def api_key(explicit: str | None = None) -> str:
    return explicit or os.environ.get("LDA_API_KEY", "")


def _get(path: str, key: str = "", **params) -> dict:
    parts = [f"{k}={v}" for k, v in params.items()]
    url = f"{API_ROOT}/{path}?" + "&".join(parts)
    headers = {"Authorization": f"Token {key}"} if key else {}
    return json.loads(http.get(url, headers=headers))


# --- pure parsing ------------------------------------------------------------

def _entity_names(raw) -> list[str]:
    names = []
    for e in raw or []:
        name = e.get("name") if isinstance(e, dict) else str(e)
        if name:
            names.append(name.strip())
    return list(dict.fromkeys(names))


def _amount(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", "").replace("$", ""))
    except ValueError:
        return None


def parse_filings(payload: dict) -> list[dict]:
    """Turn an LDA filings response into filing + activity rows."""
    out = []
    for f in payload.get("results", []) or []:
        uuid = (f.get("filing_uuid") or "").strip()
        if not uuid:
            continue
        registrant = f.get("registrant") or {}
        client = f.get("client") or {}

        activities = []
        for a in f.get("lobbying_activities", []) or []:
            activities.append({
                "issue_code": (a.get("general_issue_code") or "").strip(),
                "issue_display": (a.get("general_issue_code_display") or "").strip(),
                "description": (a.get("description") or "")[:500],
                "entities": _entity_names(a.get("government_entities")),
            })

        out.append({
            "filing_uuid": uuid,
            "filing_type": (f.get("filing_type") or "").strip(),
            "filing_year": f.get("filing_year"),
            "filing_period": (f.get("filing_period") or "").strip(),
            "registrant": (registrant.get("name") or "").strip(),
            "client": (client.get("name") or "").strip(),
            "income": _amount(f.get("income")),
            "expenses": _amount(f.get("expenses")),
            "posted": (f.get("dt_posted") or "")[:10] or None,
            "activities": activities,
        })
    return out


# --- storage -----------------------------------------------------------------

def save_filings(conn: sqlite3.Connection, filings: list[dict]) -> tuple[int, int]:
    """Persist filings and their activities. Returns (filings, activities)."""
    now = db.now_iso()
    nf = na = 0
    for f in filings:
        cur = conn.execute(
            """INSERT OR IGNORE INTO lobby_filing
               (filing_uuid, filing_type, filing_year, filing_period, registrant,
                client, income, expenses, posted, first_seen)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (f["filing_uuid"], f["filing_type"], f["filing_year"],
             f["filing_period"], f["registrant"], f["client"], f["income"],
             f["expenses"], f["posted"], now),
        )
        nf += cur.rowcount
        for a in f["activities"]:
            cur = conn.execute(
                """INSERT OR IGNORE INTO lobby_activity
                   (filing_uuid, issue_code, issue_display, description,
                    entities, first_seen)
                   VALUES (?,?,?,?,?,?)""",
                (f["filing_uuid"], a["issue_code"], a["issue_display"],
                 a["description"], ", ".join(a["entities"]), now),
            )
            na += cur.rowcount
    conn.commit()
    return nf, na


def ingest_lobbying(conn: sqlite3.Connection, clients: list[str],
                    year: int | None = None, key: str | None = None,
                    pages: int = 2) -> tuple[int, int]:
    """Pull filings for the named clients. Works anonymously, slower."""
    from datetime import date

    k = api_key(key)
    year = year or date.today().year
    tf = ta = 0
    for client in clients:
        for page in range(1, pages + 1):
            try:
                payload = _get("filings/", k,
                               client_name=client.replace(" ", "+"),
                               filing_year=year, page=page)
            except Exception:
                break
            filings = parse_filings(payload)
            if not filings:
                break
            f, a = save_filings(conn, filings)
            tf += f
            ta += a
            if not payload.get("next"):
                break
    db.log(conn, "lobbying", tf, f"year={year} activities={ta}")
    return tf, ta


def import_filings_json(conn: sqlite3.Connection, path: str) -> tuple[int, int]:
    """Load filings from a saved API response — usable without a key."""
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    filings = parse_filings(raw) if isinstance(raw, dict) else raw
    nf, na = save_filings(conn, filings)
    db.log(conn, "lobbying_import", nf, path)
    return nf, na


# --- the join ----------------------------------------------------------------

def agencies_lobbied(conn: sqlite3.Connection, client: str) -> dict[str, int]:
    """Government entities a client lobbied, with how many activities each."""
    from .company import match_score

    rows = conn.execute(
        """SELECT f.client, a.entities FROM lobby_activity a
           JOIN lobby_filing f ON f.filing_uuid = a.filing_uuid"""
    ).fetchall()

    counts: dict[str, int] = {}
    for r in rows:
        if match_score(client, r["client"]) < 0.8:
            continue
        for entity in (r["entities"] or "").split(","):
            entity = entity.strip()
            if entity:
                counts[entity] = counts.get(entity, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))


def lobbying_award_overlap(conn: sqlite3.Connection) -> list[dict]:
    """Companies that lobby an agency which also awards them contracts.

    Overlap is unsurprising — a defense contractor lobbies the Pentagon — but
    quantifying it shows how concentrated a company's dependence on a single
    relationship really is.
    """
    from .committees import normalize_agency
    from .company import match_score

    awards = conn.execute(
        """SELECT recipient, awarding_agy, SUM(amount) total, COUNT(*) n
           FROM award WHERE recipient IS NOT NULL AND awarding_agy IS NOT NULL
           GROUP BY recipient, awarding_agy"""
    ).fetchall()

    out = []
    for a in awards:
        lobbied = agencies_lobbied(conn, a["recipient"])
        if not lobbied:
            continue
        target = normalize_agency(a["awarding_agy"])
        for entity, count in lobbied.items():
            ent = normalize_agency(entity)
            if not ent or not target:
                continue
            if ent in target or target in ent or match_score(entity, a["awarding_agy"]) >= 0.8:
                out.append({
                    "client": a["recipient"],
                    "agency": a["awarding_agy"],
                    "entity_lobbied": entity,
                    "award_total": a["total"],
                    "award_count": a["n"],
                    "lobby_activities": count,
                })
                break
    return sorted(out, key=lambda d: -(d["award_total"] or 0))
