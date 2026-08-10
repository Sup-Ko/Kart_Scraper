"""World Bank major contract awards — global procurement intake.

The single broadest geographic addition available for free. The Bank publishes
every major contract award under its financed projects, covering borrowers
across Africa, Asia, Europe and the Americas, with the supplier, the supplier's
country, the borrowing country and the amount.

That makes it the international counterpart to USASpending, and it flows into
exactly the same ``award`` table, so the policy-exposure lens and the
lobbying-overlap join work on non-US data with no special-casing.

    source: finances.worldbank.org (Socrata open data)
    lag:    weeks — awards post on the Bank's reporting cycle, not same-day
    key:    none required (an app token only raises rate limits)
    limits: **Bank-financed projects only.** This is not a country's own
            domestic procurement, so it under-represents any government that
            funds its purchases itself. Treat it as coverage of development
            finance, not of national spending.

For a specific country's own tenders you want that country's portal — many
publish Open Contracting Data Standard feeds, which is the natural next intake.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta

from . import db, http
from .regions import normalize_region

API = "https://finances.worldbank.org/resource/kdui-wcs3.json"


def _amount(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", "").replace("$", ""))
    except ValueError:
        return None


def _iso_date(value) -> str | None:
    if not value:
        return None
    text = str(value)[:10]
    try:
        date.fromisoformat(text)
        return text
    except ValueError:
        return None


def parse_awards(payload: list) -> list[dict]:
    """Turn a Socrata response into award rows. Pure, so it is testable."""
    out = []
    for r in payload or []:
        supplier = (r.get("supplier") or "").strip()
        if not supplier:
            continue
        contract_id = (r.get("wb_contract_number")
                       or r.get("contract_number") or "").strip()
        project = (r.get("project_id") or "").strip()
        if not contract_id:
            # synthesize a stable id when the feed omits the contract number
            contract_id = f"{project}:{supplier}:{r.get('contract_signing_date')}"

        out.append({
            "award_id": f"WB:{contract_id}"[:200],
            "recipient": supplier,
            "recipient_id": (r.get("supplier_country_code") or "").strip(),
            "awarding_agy": (r.get("project_name") or "World Bank project").strip()[:200],
            "amount": _amount(r.get("total_contract_amount")),
            "action_date": _iso_date(r.get("contract_signing_date")),
            "description": (r.get("procurement_category") or "")[:500],
            "country": (r.get("borrower_country") or "").strip(),
            "region": normalize_region(r.get("region")),
            "source": "worldbank",
        })
    return out


def save_awards(conn: sqlite3.Connection, rows: list[dict]) -> int:
    now = db.now_iso()
    new = 0
    for r in rows:
        cur = conn.execute(
            """INSERT OR IGNORE INTO award
               (award_id, recipient, recipient_id, awarding_agy, amount,
                action_date, description, country, region, source, first_seen)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (r["award_id"], r["recipient"], r["recipient_id"], r["awarding_agy"],
             r["amount"], r["action_date"], r["description"], r["country"],
             r["region"], r["source"], now),
        )
        new += cur.rowcount
    conn.commit()
    return new


def ingest_worldbank_awards(conn: sqlite3.Connection, since: date | None = None,
                            min_amount: float = 1_000_000,
                            pages: int = 3, per_page: int = 500) -> int:
    """Pull recent Bank-financed contract awards above a threshold."""
    since = since or (date.today() - timedelta(days=365))
    new = 0
    for page in range(pages):
        params = [
            f"$limit={per_page}",
            f"$offset={page * per_page}",
            "$order=contract_signing_date DESC",
            (f"$where=contract_signing_date>'{since.isoformat()}'"
             f" AND total_contract_amount>{min_amount}"),
        ]
        url = f"{API}?" + "&".join(params).replace(" ", "%20")
        try:
            payload = json.loads(http.get(url))
        except Exception:
            break
        rows = parse_awards(payload)
        if not rows:
            break
        new += save_awards(conn, rows)
        if len(payload) < per_page:
            break
    db.log(conn, "worldbank", new, f"since={since}")
    return new


def coverage(conn: sqlite3.Connection) -> list[dict]:
    """Award counts and totals by region — what the data actually covers."""
    rows = conn.execute(
        """SELECT COALESCE(NULLIF(region,''), 'unknown') AS region,
                  COALESCE(NULLIF(source,''), 'usaspending') AS source,
                  COUNT(*) n, SUM(amount) total
           FROM award GROUP BY region, source ORDER BY total DESC"""
    ).fetchall()
    return [dict(r) for r in rows]
