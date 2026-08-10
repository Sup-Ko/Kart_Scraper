"""Ingestion from public government endpoints.

Sources, ordered by how fresh — and therefore how useful — the data actually is:

* ``usaspending``  contract awards, public the day they post. Upstream of the
  revenue they represent rather than downstream of a price move.
* ``edgar_form4``  corporate insider filings, due within 2 business days.
* ``house_ptr``    congressional trade disclosures, ~30-45 day statutory lag.
  Treat these as accountability data, not a trading signal: by the time a PTR
  is public the information advantage is long gone (see ``conflicts.py`` for
  what this data is genuinely good for).
"""

from __future__ import annotations

import io
import json
import re
import sqlite3
import xml.etree.ElementTree as ET
import zipfile
from datetime import date, timedelta
from pathlib import Path

from . import db, http
from .ptr_parse import iso_date
from .regions import AMERICAS

PDF_CACHE = Path("cache/ptr_pdfs")

HOUSE_ZIP = "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.zip"
HOUSE_PDF = "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{year}/{doc_id}.pdf"
EDGAR_DAILY = "https://www.sec.gov/Archives/edgar/daily-index/{y}/QTR{q}/form.{ymd}.idx"
USASPENDING = "https://api.usaspending.gov/api/v2/search/spending_by_award/"


# --- 1. House Clerk periodic transaction reports ----------------------------

def ingest_house_ptr(conn: sqlite3.Connection, year: int | None = None) -> int:
    """Record every Periodic Transaction Report in the year's disclosure index."""
    year = year or date.today().year
    raw = http.get(HOUSE_ZIP.format(year=year))
    z = zipfile.ZipFile(io.BytesIO(raw))
    xml_name = next(n for n in z.namelist() if n.endswith(".xml"))
    root = ET.fromstring(z.read(xml_name).decode("utf-8", "ignore"))

    now = db.now_iso()
    new = 0
    for m in root.findall("Member"):
        if (m.findtext("FilingType") or "").strip() != "P":
            continue  # only periodic transaction reports disclose trades
        doc_id = (m.findtext("DocID") or "").strip()
        if not doc_id:
            continue
        filing_year = int(m.findtext("Year") or year)
        cur = conn.execute(
            """INSERT OR IGNORE INTO ptr_filing
               (doc_id, chamber, last_name, first_name, state_dst, year,
                filing_date, pdf_url, status, first_seen)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (doc_id, "house",
             (m.findtext("Last") or "").strip(),
             (m.findtext("First") or "").strip(),
             (m.findtext("StateDst") or "").strip(),
             filing_year,
             iso_date(m.findtext("FilingDate") or ""),
             HOUSE_PDF.format(year=filing_year, doc_id=doc_id),
             db.INDEXED, now),
        )
        new += cur.rowcount
    conn.commit()
    db.log(conn, "house_ptr", new, f"year={year}")
    return new


def download_ptr_pdfs(conn: sqlite3.Connection, limit: int = 25) -> int:
    """Fetch PDFs for filings still awaiting download.

    Only ``indexed`` filings are selected, and every outcome moves the row out
    of that state, so a persistently broken filing can never wedge the queue.
    """
    PDF_CACHE.mkdir(parents=True, exist_ok=True)
    rows = conn.execute(
        "SELECT doc_id, pdf_url FROM ptr_filing WHERE status = ? "
        "ORDER BY filing_date DESC LIMIT ?",
        (db.INDEXED, limit),
    ).fetchall()

    got = 0
    for r in rows:
        dest = PDF_CACHE / f"{r['doc_id']}.pdf"
        if dest.exists():
            conn.execute("UPDATE ptr_filing SET status = ? WHERE doc_id = ?",
                         (db.DOWNLOADED, r["doc_id"]))
            continue
        try:
            dest.write_bytes(http.get(r["pdf_url"]))
            conn.execute("UPDATE ptr_filing SET status = ? WHERE doc_id = ?",
                         (db.DOWNLOADED, r["doc_id"]))
            got += 1
        except Exception as exc:
            db.record_failure(conn, r["doc_id"], f"{type(exc).__name__}: {exc}")
    conn.commit()
    db.log(conn, "ptr_pdf_download", got)
    return got


def parse_downloaded(conn: sqlite3.Connection, limit: int | None = None) -> tuple[int, int]:
    """Parse downloaded filings. Returns (trades added, filings with issues)."""
    from .ptr_parse import compute_lag, parse_pdf

    q = "SELECT doc_id, filing_date FROM ptr_filing WHERE status = ?"
    params: list = [db.DOWNLOADED]
    if limit:
        q += " LIMIT ?"
        params.append(limit)
    rows = conn.execute(q, params).fetchall()

    total, flagged = 0, 0
    for r in rows:
        path = PDF_CACHE / f"{r['doc_id']}.pdf"
        if not path.exists():
            conn.execute("UPDATE ptr_filing SET status = ? WHERE doc_id = ?",
                         (db.INDEXED, r["doc_id"]))
            continue
        try:
            result = parse_pdf(path, r["doc_id"])
        except Exception as exc:
            db.record_failure(conn, r["doc_id"], f"{type(exc).__name__}: {exc}")
            continue

        for t in result.trades:
            t.lag_days = compute_lag(t, r["filing_date"])
        total += db.save_trades(conn, result.trades, result.issues)
        if result.issues:
            flagged += 1
        conn.execute("UPDATE ptr_filing SET status = ? WHERE doc_id = ?",
                     (db.PARSED, r["doc_id"]))
    conn.commit()
    db.log(conn, "ptr_parse", total)
    return total, flagged


# --- 2. SEC EDGAR Form 4 ----------------------------------------------------

def ingest_form4(conn: sqlite3.Connection, day: date | None = None) -> int:
    """Record one day's Form 4 filings from the EDGAR daily index."""
    day = day or date.today()
    if day.weekday() >= 5:
        return 0  # no index on weekends; skip without a request
    q = (day.month - 1) // 3 + 1
    url = EDGAR_DAILY.format(y=day.year, q=q, ymd=day.strftime("%Y%m%d"))

    try:
        text = http.get(url).decode("latin-1")
    except http.NotFound:
        db.log(conn, "form4", 0, f"no index for {day} (weekend/holiday)")
        return 0

    now = db.now_iso()
    new = 0
    for line in text.splitlines():
        if not line.startswith("4 "):
            continue
        parts = re.split(r"\s{2,}", line.strip())
        if len(parts) < 5 or parts[0].strip() != "4":
            continue
        _, company, cik, filed, fname = parts[0], parts[1], parts[2], parts[3], parts[-1]
        accession = fname.rsplit("/", 1)[-1].replace(".txt", "")
        cur = conn.execute(
            """INSERT OR IGNORE INTO form4
               (accession, cik, company, filed_date, url, first_seen)
               VALUES (?,?,?,?,?,?)""",
            (accession, cik.strip(), company.strip(), filed.strip(),
             f"https://www.sec.gov/Archives/{fname.strip()}", now),
        )
        new += cur.rowcount
    conn.commit()
    db.log(conn, "form4", new, str(day))
    return new


def backfill_form4(conn: sqlite3.Connection, days: int = 7) -> int:
    total = 0
    for i in range(days):
        total += ingest_form4(conn, date.today() - timedelta(days=i))
    return total


# --- 3. USASpending contract awards -----------------------------------------

def ingest_awards(conn: sqlite3.Connection, since: date | None = None,
                  min_amount: float = 10_000_000, pages: int = 5) -> int:
    """Pull recent large federal contract awards.

    This is the freshest source in the pipeline: an award is public the day it
    posts, and it maps directly onto a contractor's future revenue.
    """
    since = since or (date.today() - timedelta(days=14))
    now = db.now_iso()
    new = 0

    for page in range(1, pages + 1):
        body = {
            "filters": {
                "award_type_codes": ["A", "B", "C", "D"],
                "time_period": [{
                    "start_date": since.isoformat(),
                    "end_date": date.today().isoformat(),
                }],
                # NOTE: the v2 search filter key is plural ("award_amounts").
                "award_amounts": [{"lower_bound": min_amount}],
            },
            "fields": ["Award ID", "Recipient Name", "Awarding Agency",
                       "Award Amount", "Start Date", "Description", "recipient_id"],
            "page": page,
            "limit": 100,
            "sort": "Award Amount",
            "order": "desc",
        }
        payload = json.loads(http.get(USASPENDING, json_body=body))
        results = payload.get("results", [])
        if not results:
            break

        for r in results:
            cur = conn.execute(
                """INSERT OR IGNORE INTO award
                   (award_id, recipient, recipient_id, awarding_agy, amount,
                    action_date, description, country, region, source, first_seen)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (r.get("Award ID"), r.get("Recipient Name"), r.get("recipient_id"),
                 r.get("Awarding Agency"), r.get("Award Amount"), r.get("Start Date"),
                 (r.get("Description") or "")[:500], "United States",
                 AMERICAS, "usaspending", now),
            )
            new += cur.rowcount

        if not payload.get("page_metadata", {}).get("hasNext"):
            break

    conn.commit()
    db.log(conn, "usaspending", new)
    return new


# --- 4. SEC Form 4 detail (transaction-level) -------------------------------

def save_form4_filing(conn: sqlite3.Connection, filing) -> int:
    """Persist a parsed Form 4's transactions. Idempotent via row hash."""
    import hashlib

    from .form4_parse import is_discretionary

    now = db.now_iso()
    new = 0
    for t in filing.transactions:
        basis = (f"{filing.accession}|{t.security}|{t.tx_date}|{t.tx_code}|"
                 f"{t.shares}|{t.price}|{t.is_derivative}")
        row_hash = hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]
        cur = conn.execute(
            """INSERT OR IGNORE INTO form4_transaction
               (row_hash, accession, issuer_symbol, issuer_name, owner_name, role,
                security, tx_date, tx_code, tx_type, discretionary, shares, price,
                value, acquired_disposed, is_derivative, first_seen)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (row_hash, filing.accession, filing.issuer_symbol, filing.issuer_name,
             filing.owner_name, filing.role, t.security, t.tx_date, t.tx_code,
             t.tx_type, 1 if is_discretionary(t.tx_code) else 0, t.shares, t.price,
             t.value, t.acquired_disposed, 1 if t.is_derivative else 0, now),
        )
        new += cur.rowcount
    conn.commit()
    return new


def parse_form4_details(conn: sqlite3.Connection, limit: int = 50) -> tuple[int, int]:
    """Fetch and parse unparsed Form 4 submissions. Returns (filings, rows)."""
    from .form4_parse import parse_form4

    rows = conn.execute(
        "SELECT accession, url FROM form4 WHERE parsed = 0 "
        "ORDER BY filed_date DESC LIMIT ?", (limit,)
    ).fetchall()

    filings = added = 0
    for r in rows:
        try:
            payload = http.get(r["url"]).decode("utf-8", "replace")
        except Exception:
            # mark as attempted so a permanently broken filing cannot wedge
            # the queue, the same discipline the PTR pipeline uses
            conn.execute("UPDATE form4 SET parsed = -1 WHERE accession = ?",
                         (r["accession"],))
            continue

        filing = parse_form4(payload, r["accession"])
        if filing is None:
            conn.execute("UPDATE form4 SET parsed = -1 WHERE accession = ?",
                         (r["accession"],))
            continue

        added += save_form4_filing(conn, filing)
        filings += 1
        conn.execute("UPDATE form4 SET parsed = 1 WHERE accession = ?",
                     (r["accession"],))
    conn.commit()
    db.log(conn, "form4_detail", added, f"{filings} filings")
    return filings, added


def insider_summary(conn: sqlite3.Connection, days: int = 90,
                    symbols: list[str] | None = None) -> list[dict]:
    """Net insider activity per issuer, separating decisions from mechanics.

    Grants, option exercises and tax withholding are reported apart from
    open-market buys and sells, because blending them produces headline numbers
    that mean nothing.
    """
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    q = """SELECT issuer_symbol,
                  SUM(CASE WHEN discretionary=1 AND acquired_disposed='A'
                           THEN value ELSE 0 END) AS bought,
                  SUM(CASE WHEN discretionary=1 AND acquired_disposed='D'
                           THEN value ELSE 0 END) AS sold,
                  SUM(CASE WHEN discretionary=0 THEN value ELSE 0 END) AS mechanical,
                  COUNT(DISTINCT owner_name) AS insiders,
                  COUNT(*) AS transactions
           FROM form4_transaction
           WHERE tx_date >= ? AND issuer_symbol IS NOT NULL AND issuer_symbol != ''"""
    params: list = [cutoff]
    if symbols:
        q += " AND issuer_symbol IN (%s)" % ",".join("?" * len(symbols))
        params.extend(s.upper() for s in symbols)
    q += " GROUP BY issuer_symbol ORDER BY (bought - sold) DESC"

    out = []
    for r in conn.execute(q, params):
        d = dict(r)
        d["net_discretionary"] = round((d["bought"] or 0) - (d["sold"] or 0), 2)
        out.append(d)
    return out
