"""Congress.gov API — member records and committee assignments.

Requires a free api.data.gov key (``CONGRESS_API_KEY`` environment variable, or
``--api-key``). Everything here **degrades gracefully**: without a key the
package still works, conflict analysis still runs, and it simply cannot add the
jurisdiction dimension. It says so rather than failing.

An offline import path (:func:`import_assignments_json`) exists so assignments
can be loaded from a hand-maintained file, keeping the feature usable without a
key and making the whole module testable without network access.
"""

from __future__ import annotations

import json
import os
import sqlite3

from . import db, http

API_ROOT = "https://api.congress.gov/v3"


def api_key(explicit: str | None = None) -> str:
    return explicit or os.environ.get("CONGRESS_API_KEY", "")


def have_key(explicit: str | None = None) -> bool:
    return bool(api_key(explicit))


def _get_json(path: str, key: str, **params) -> dict:
    params["api_key"] = key
    params.setdefault("format", "json")
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return json.loads(http.get(f"{API_ROOT}/{path}?{query}"))


def ingest_members(conn: sqlite3.Connection, congress: int = 119,
                   key: str | None = None, limit: int = 250) -> int:
    """Record current members. Returns 0 (and logs) when no API key is set."""
    k = api_key(key)
    if not k:
        db.log(conn, "congress_members", 0, "no CONGRESS_API_KEY; skipped")
        return 0

    new = 0
    offset = 0
    now = db.now_iso()
    while True:
        payload = _get_json(f"member/congress/{congress}", k,
                            limit=min(250, limit), offset=offset)
        members = payload.get("members", [])
        if not members:
            break
        for m in members:
            bioguide = (m.get("bioguideId") or "").strip()
            if not bioguide:
                continue
            name = m.get("name") or ""
            # Congress.gov returns "Last, First" for member names
            last, _, first = name.partition(",")
            terms = (m.get("terms") or {}).get("item") or []
            chamber = (terms[-1].get("chamber") if terms else "") or ""
            cur = conn.execute(
                """INSERT OR IGNORE INTO member
                   (bioguide_id, last_name, first_name, state, district,
                    party, chamber, congress, first_seen)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (bioguide, last.strip(), first.strip(), m.get("state") or "",
                 str(m.get("district") or ""), m.get("partyName") or "",
                 chamber, congress, now),
            )
            new += cur.rowcount
        offset += len(members)
        if offset >= limit or not payload.get("pagination", {}).get("next"):
            break
    conn.commit()
    db.log(conn, "congress_members", new, f"congress={congress}")
    return new


def ingest_committee_assignments(conn: sqlite3.Connection, congress: int = 119,
                                 chamber: str = "house",
                                 key: str | None = None) -> int:
    """Record which members sit on which committees.

    The API exposes committees and their membership per Congress. Where the
    membership roster is unavailable for a committee, that committee is skipped
    rather than guessed at.
    """
    k = api_key(key)
    if not k:
        db.log(conn, "congress_committees", 0, "no CONGRESS_API_KEY; skipped")
        return 0

    payload = _get_json(f"committee/{congress}/{chamber}", k, limit=250)
    committees = payload.get("committees", [])
    now = db.now_iso()
    new = 0

    for c in committees:
        name = c.get("name") or ""
        code = (c.get("systemCode") or "").strip()
        if not code:
            continue
        try:
            detail = _get_json(f"committee/{chamber}/{code}", k)
        except Exception:
            continue
        info = detail.get("committee") or {}
        roster = info.get("members") or []
        for m in roster:
            bioguide = (m.get("bioguideId") or "").strip()
            if not bioguide:
                continue
            cur = conn.execute(
                """INSERT OR IGNORE INTO member_committee
                   (bioguide_id, committee, committee_code, congress, role, first_seen)
                   VALUES (?,?,?,?,?,?)""",
                (bioguide, name, code, congress, m.get("role") or "", now),
            )
            new += cur.rowcount
    conn.commit()
    db.log(conn, "congress_committees", new, f"{chamber} congress={congress}")
    return new


def import_assignments_json(conn: sqlite3.Connection, path: str,
                            congress: int = 119) -> int:
    """Load committee assignments from a local JSON file.

    Format — a list of records, or a mapping of member name to committees::

        [{"last_name": "Smith", "first_name": "Pat", "state_dst": "CA01",
          "committees": ["Armed Services", "Appropriations"]}]

    This keeps the jurisdiction feature usable without an API key, and lets the
    matching logic be exercised offline.
    """
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)

    if isinstance(raw, dict):
        raw = [{"last_name": k, "committees": v} for k, v in raw.items()]

    now = db.now_iso()
    new = 0
    for rec in raw:
        last = (rec.get("last_name") or "").strip()
        first = (rec.get("first_name") or "").strip()
        state_dst = (rec.get("state_dst") or "").strip()
        bioguide = (rec.get("bioguide_id") or "").strip() or \
            f"local:{last}:{first}:{state_dst}".lower()

        conn.execute(
            """INSERT OR IGNORE INTO member
               (bioguide_id, last_name, first_name, state, district, party,
                chamber, congress, first_seen)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (bioguide, last, first, state_dst[:2], state_dst[2:], "",
             rec.get("chamber", "house"), congress, now),
        )
        for committee in rec.get("committees", []):
            cur = conn.execute(
                """INSERT OR IGNORE INTO member_committee
                   (bioguide_id, committee, committee_code, congress, role, first_seen)
                   VALUES (?,?,?,?,?,?)""",
                (bioguide, committee, "", congress, "", now),
            )
            new += cur.rowcount
    conn.commit()
    db.log(conn, "congress_import", new, path)
    return new


def committees_for_member(conn: sqlite3.Connection, last_name: str,
                          first_name: str = "", state_dst: str = "") -> list[str]:
    """Committee names for a member, matched on the fields PTR filings carry.

    PTR filings identify members by name and state/district, not by bioguide id,
    so this join is by name. Narrow with first name and state when available.
    """
    q = """SELECT DISTINCT mc.committee
           FROM member_committee mc JOIN member m ON m.bioguide_id = mc.bioguide_id
           WHERE LOWER(m.last_name) = LOWER(?)"""
    params: list = [last_name]
    if first_name:
        q += " AND (LOWER(m.first_name) = LOWER(?) OR m.first_name = '')"
        params.append(first_name)
    if state_dst:
        q += " AND (m.state = ? OR m.state = '')"
        params.append(state_dst[:2])
    return [r["committee"] for r in conn.execute(q, params).fetchall()]
