"""FEC campaign finance intake — corporate PAC contributions.

This closes a loop the other sources only half-describe. Awards show federal
money flowing *to* a company; committee jurisdiction shows who oversees the
agency paying it. Campaign finance shows money flowing the other way — from
that company's PAC to the members doing the overseeing.

None of that is wrongdoing. Corporate PACs are legal, disclosed, and give to
incumbents on relevant committees as a matter of routine; that is precisely why
the data is published. What the join buys you is the ability to see the whole
triangle at once instead of one leg at a time, and to ask questions of it.

    lag:    contributions appear in periodic reports — monthly or quarterly,
            so expect weeks, not days
    key:    free api.data.gov key (``FEC_API_KEY``); everything degrades
            gracefully without one
    terms:  api.open.fec.gov is a public government API; rate-limited per host
            and sent with a descriptive User-Agent like every other source

Committee type ``Q`` is a qualified multi-candidate PAC — the type a company's
"employees political action committee" registers as — and
``connected_organization_name`` is the field that ties it back to the company.
"""

from __future__ import annotations

import json
import os
import sqlite3

from . import db, http

API_ROOT = "https://api.open.fec.gov/v1"


def api_key(explicit: str | None = None) -> str:
    return explicit or os.environ.get("FEC_API_KEY", "")


def have_key(explicit: str | None = None) -> bool:
    return bool(api_key(explicit))


def _get(path: str, key: str, **params) -> dict:
    params["api_key"] = key
    parts = []
    for k, v in params.items():
        if isinstance(v, (list, tuple)):
            parts.extend(f"{k}={item}" for item in v)
        else:
            parts.append(f"{k}={v}")
    return json.loads(http.get(f"{API_ROOT}/{path}?" + "&".join(parts)))


# --- pure parsers (fixture-testable offline) --------------------------------

def parse_committees(payload: dict) -> list[dict]:
    """Extract PAC committee records from a ``/committees/`` response."""
    out = []
    for c in payload.get("results", []) or []:
        cid = (c.get("committee_id") or "").strip()
        if not cid:
            continue
        out.append({
            "committee_id": cid,
            "name": (c.get("name") or "").strip(),
            "connected_org": (c.get("connected_organization_name") or "").strip(),
            "committee_type": (c.get("committee_type") or "").strip(),
            "designation": (c.get("designation") or "").strip(),
            "state": (c.get("state") or "").strip(),
        })
    return out


def parse_contributions(payload: dict) -> list[dict]:
    """Extract contribution records from a ``/schedules/schedule_a/`` response."""
    out = []
    for r in payload.get("results", []) or []:
        amount = r.get("contribution_receipt_amount")
        if amount is None:
            continue
        recipient = r.get("committee") or {}
        out.append({
            "transaction_id": (r.get("transaction_id")
                               or r.get("sub_id") or "").strip(),
            "contributor_name": (r.get("contributor_name") or "").strip(),
            "contributor_committee_id": (r.get("contributor_id") or "").strip(),
            "recipient_committee_id": (r.get("committee_id")
                                       or recipient.get("committee_id") or "").strip(),
            "recipient_name": (recipient.get("name")
                               or r.get("recipient_name") or "").strip(),
            "candidate_name": (r.get("candidate_name") or "").strip(),
            "amount": float(amount),
            "date": r.get("contribution_receipt_date"),
            "cycle": r.get("two_year_transaction_period"),
        })
    return out


# --- ingestion ---------------------------------------------------------------

def ingest_corporate_pacs(conn: sqlite3.Connection, companies: list[str],
                          key: str | None = None) -> int:
    """Find PACs connected to the given companies. No-op without an API key."""
    k = api_key(key)
    if not k:
        db.log(conn, "fec_pacs", 0, "no FEC_API_KEY; skipped")
        return 0

    now = db.now_iso()
    new = 0
    for company in companies:
        try:
            payload = _get("committees/", k, q=company.replace(" ", "+"),
                           committee_type="Q", per_page=20)
        except Exception:
            continue
        for c in parse_committees(payload):
            cur = conn.execute(
                """INSERT OR IGNORE INTO pac_committee
                   (committee_id, name, connected_org, committee_type,
                    designation, state, first_seen)
                   VALUES (?,?,?,?,?,?,?)""",
                (c["committee_id"], c["name"], c["connected_org"],
                 c["committee_type"], c["designation"], c["state"], now),
            )
            new += cur.rowcount
    conn.commit()
    db.log(conn, "fec_pacs", new)
    return new


def ingest_pac_contributions(conn: sqlite3.Connection, cycle: int = 2026,
                             key: str | None = None, pages: int = 2) -> int:
    """Pull contributions made by the PACs already recorded."""
    k = api_key(key)
    if not k:
        db.log(conn, "fec_contributions", 0, "no FEC_API_KEY; skipped")
        return 0

    pacs = conn.execute("SELECT committee_id, name FROM pac_committee").fetchall()
    now = db.now_iso()
    new = 0
    for pac in pacs:
        for page in range(1, pages + 1):
            try:
                payload = _get("schedules/schedule_a/", k,
                               contributor_id=pac["committee_id"],
                               two_year_transaction_period=cycle,
                               per_page=100, page=page)
            except Exception:
                break
            rows = parse_contributions(payload)
            if not rows:
                break
            new += save_contributions(conn, rows, pac["committee_id"])
            if len(rows) < 100:
                break
    db.log(conn, "fec_contributions", new, f"cycle={cycle}")
    return new


def save_contributions(conn: sqlite3.Connection, rows: list[dict],
                       contributor_committee_id: str = "") -> int:
    """Persist contribution rows. Idempotent on a synthesized identity."""
    import hashlib

    now = db.now_iso()
    new = 0
    for r in rows:
        cid = r.get("contributor_committee_id") or contributor_committee_id
        basis = (f"{r.get('transaction_id')}|{cid}|{r.get('recipient_committee_id')}"
                 f"|{r.get('amount')}|{r.get('date')}")
        row_hash = hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]
        cur = conn.execute(
            """INSERT OR IGNORE INTO pac_contribution
               (row_hash, contributor_committee_id, contributor_name,
                recipient_committee_id, recipient_name, candidate_name,
                amount, contribution_date, cycle, first_seen)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (row_hash, cid, r.get("contributor_name", ""),
             r.get("recipient_committee_id", ""), r.get("recipient_name", ""),
             r.get("candidate_name", ""), r.get("amount"), r.get("date"),
             r.get("cycle"), now),
        )
        new += cur.rowcount
    conn.commit()
    return new


def import_contributions_json(conn: sqlite3.Connection, path: str) -> int:
    """Load contributions from a saved API response or a plain list.

    Keeps the feature usable without a key and makes the join testable offline.
    """
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    rows = parse_contributions(raw) if isinstance(raw, dict) else raw
    n = save_contributions(conn, rows)
    db.log(conn, "fec_import", n, path)
    return n


# --- the join ----------------------------------------------------------------

def contributions_to_member(conn: sqlite3.Connection, last_name: str,
                            company: str = "", cycle: int | None = None) -> list[dict]:
    """Contributions reaching a member, optionally from one company's PAC.

    Recipient records identify a campaign committee ("SMITH FOR CONGRESS") or a
    candidate name, not a bioguide id, so this matches on last name. That is
    approximate — common surnames will over-match — and callers surface it as a
    lead rather than a fact.
    """
    if not last_name:
        return []
    like = f"%{last_name.upper()}%"
    q = """SELECT c.*, COALESCE(p.connected_org, '') AS connected_org
           FROM pac_contribution c
           LEFT JOIN pac_committee p
                  ON p.committee_id = c.contributor_committee_id
           WHERE (UPPER(c.recipient_name) LIKE ? OR UPPER(c.candidate_name) LIKE ?)"""
    params: list = [like, like]
    if cycle:
        q += " AND c.cycle = ?"
        params.append(cycle)
    rows = [dict(r) for r in conn.execute(q, params).fetchall()]

    if company:
        from .company import match_score
        rows = [
            r for r in rows
            if match_score(company, r["contributor_name"]) >= 0.6
            or match_score(company, r.get("connected_org", "")) >= 0.6
        ]
    return rows
