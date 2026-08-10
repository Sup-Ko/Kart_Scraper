"""Federal Register intake — proposed and final rules.

This is the one source in the package that is genuinely **forward-looking**. A
contract award tells you what already happened; a *proposed* rule tells you
what an agency intends to do, typically with a comment period before it takes
effect. For a portfolio with concentrated exposure to one agency's spending or
regulation, that lead time is the whole point.

    lag:    same day (documents are published on their publication date)
    key:    none required
    terms:  federalregister.gov offers this API publicly and the content is US
            Government work in the public domain. Still rate-limited per host
            and sent with a descriptive User-Agent, like every other source.

Document types kept:

    RULE     a final rule — already binding, effective on a stated date
    PRORULE  a proposed rule — the forward-looking one, usually with a
             comment deadline
    NOTICE   agency notices (opt in via ``doc_types``)
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta

from . import db, http

API = "https://www.federalregister.gov/api/v1/documents.json"

FIELDS = [
    "document_number", "title", "abstract", "type", "publication_date",
    "effective_on", "comments_close_on", "html_url", "agencies",
    "regulation_id_numbers", "docket_ids",
]

DEFAULT_TYPES = ("RULE", "PRORULE")


def _build_url(since: date, doc_types: tuple[str, ...], per_page: int, page: int) -> str:
    parts = [f"per_page={min(1000, per_page)}", f"page={page}", "order=newest"]
    parts += [f"fields[]={f}" for f in FIELDS]
    parts += [f"conditions[type][]={t}" for t in doc_types]
    parts.append(f"conditions[publication_date][gte]={since.isoformat()}")
    return f"{API}?" + "&".join(parts)


def _agency_names(raw) -> str:
    """Flatten the agencies array to a comma-separated string.

    The API returns agency objects; older responses sometimes carry plain
    strings, so both shapes are handled rather than assumed.
    """
    if not raw:
        return ""
    names = []
    for a in raw:
        if isinstance(a, dict):
            name = a.get("name") or a.get("raw_name") or ""
        else:
            name = str(a)
        if name:
            names.append(name.strip())
    return ", ".join(dict.fromkeys(names))  # de-duplicate, keep order


def parse_documents(payload: dict) -> list[dict]:
    """Turn an API payload into row dicts. Pure, so it is testable offline."""
    out = []
    for d in payload.get("results", []) or []:
        number = (d.get("document_number") or "").strip()
        if not number:
            continue
        rins = d.get("regulation_id_numbers") or []
        dockets = d.get("docket_ids") or []
        out.append({
            "document_number": number,
            "doc_type": (d.get("type") or "").strip(),
            "title": (d.get("title") or "")[:500],
            "abstract": (d.get("abstract") or "")[:1000],
            "agencies": _agency_names(d.get("agencies")),
            "rin": ", ".join(str(r) for r in rins),
            "docket": ", ".join(str(x) for x in dockets),
            "publication_date": d.get("publication_date"),
            "effective_on": d.get("effective_on"),
            "comments_close_on": d.get("comments_close_on"),
            "url": d.get("html_url") or "",
        })
    return out


def ingest_federal_register(
    conn: sqlite3.Connection,
    since: date | None = None,
    doc_types: tuple[str, ...] = DEFAULT_TYPES,
    pages: int = 3,
    per_page: int = 100,
) -> int:
    """Pull recent rules and proposed rules into ``fedreg_doc``."""
    since = since or (date.today() - timedelta(days=30))
    now = db.now_iso()
    new = 0

    for page in range(1, pages + 1):
        url = _build_url(since, doc_types, per_page, page)
        try:
            payload = json.loads(http.get(url))
        except http.NotFound:
            break
        rows = parse_documents(payload)
        if not rows:
            break
        for r in rows:
            cur = conn.execute(
                """INSERT OR IGNORE INTO fedreg_doc
                   (document_number, doc_type, title, abstract, agencies, rin,
                    docket, publication_date, effective_on, comments_close_on,
                    url, first_seen)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (r["document_number"], r["doc_type"], r["title"], r["abstract"],
                 r["agencies"], r["rin"], r["docket"], r["publication_date"],
                 r["effective_on"], r["comments_close_on"], r["url"], now),
            )
            new += cur.rowcount
        conn.commit()
        if len(rows) < per_page:
            break

    db.log(conn, "federal_register", new, f"since={since} types={','.join(doc_types)}")
    return new


def rules_for_agencies(conn: sqlite3.Connection, agencies: list[str],
                       limit: int = 50, open_comments_only: bool = False) -> list[dict]:
    """Rules issued by any of the given agencies, newest first.

    Agency naming differs between datasets (an award says "Department of
    Defense", the Register may say "Defense Department"), so matching is on
    normalized keyword overlap rather than equality.
    """
    from .committees import normalize_agency

    rows = conn.execute(
        "SELECT * FROM fedreg_doc ORDER BY publication_date DESC LIMIT 2000"
    ).fetchall()

    wanted = [normalize_agency(a) for a in agencies if a]
    wanted = [w for w in wanted if w]
    if not wanted:
        return []

    today = date.today().isoformat()
    out = []
    for r in rows:
        doc_agencies = normalize_agency(r["agencies"])
        if not doc_agencies:
            continue
        if not any(w in doc_agencies or doc_agencies in w for w in wanted):
            continue
        if open_comments_only:
            close = r["comments_close_on"]
            if not close or close < today:
                continue
        out.append(dict(r))
        if len(out) >= limit:
            break
    return out
