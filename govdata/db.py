"""SQLite storage for govdata.

Two schema decisions worth calling out, both fixing real failure modes:

* ``ptr_filing.status`` replaces a bare ``parsed`` flag. With only a boolean,
  a filing that fails to download or parse stays "unparsed" forever and keeps
  occupying the head of a ``LIMIT``-ed work queue — the pipeline stops making
  progress while looking like it is still working. An explicit status plus an
  attempt counter lets failures retire.
* ``ptr_trade`` carries a UNIQUE row hash, so re-parsing a filing updates rows
  instead of silently duplicating every trade.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB = Path("govdata.sqlite")

SCHEMA = """
CREATE TABLE IF NOT EXISTS ptr_filing (
    doc_id       TEXT PRIMARY KEY,
    chamber      TEXT NOT NULL,
    last_name    TEXT,
    first_name   TEXT,
    state_dst    TEXT,
    year         INTEGER,
    filing_date  TEXT,
    pdf_url      TEXT,
    status       TEXT NOT NULL DEFAULT 'indexed',
    attempts     INTEGER NOT NULL DEFAULT 0,
    last_error   TEXT,
    first_seen   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_filing_status ON ptr_filing(status);

CREATE TABLE IF NOT EXISTS ptr_trade (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    row_hash      TEXT NOT NULL UNIQUE,
    doc_id        TEXT NOT NULL REFERENCES ptr_filing(doc_id),
    owner         TEXT,
    asset         TEXT,
    ticker        TEXT,
    tx_type       TEXT,
    tx_date       TEXT,
    notif_date    TEXT,
    amount_low    INTEGER,
    amount_high   INTEGER,
    lag_days      INTEGER
);
CREATE INDEX IF NOT EXISTS ix_trade_ticker ON ptr_trade(ticker);
CREATE INDEX IF NOT EXISTS ix_trade_txdate ON ptr_trade(tx_date);

-- Rows we could not parse are recorded, never silently dropped.
CREATE TABLE IF NOT EXISTS parse_issue (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id   TEXT,
    reason   TEXT,
    raw      TEXT,
    seen_at  TEXT
);

CREATE TABLE IF NOT EXISTS form4 (
    accession    TEXT PRIMARY KEY,
    cik          TEXT,
    company      TEXT,
    filed_date   TEXT,
    url          TEXT,
    parsed       INTEGER NOT NULL DEFAULT 0,
    first_seen   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_form4_parsed ON form4(parsed);

CREATE TABLE IF NOT EXISTS form4_transaction (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    row_hash        TEXT NOT NULL UNIQUE,
    accession       TEXT NOT NULL,
    issuer_symbol   TEXT,
    issuer_name     TEXT,
    owner_name      TEXT,
    role            TEXT,
    security        TEXT,
    tx_date         TEXT,
    tx_code         TEXT,
    tx_type         TEXT,
    discretionary   INTEGER DEFAULT 0,
    shares          REAL,
    price           REAL,
    value           REAL,
    acquired_disposed TEXT,
    is_derivative   INTEGER DEFAULT 0,
    first_seen      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_f4tx_symbol ON form4_transaction(issuer_symbol);
CREATE INDEX IF NOT EXISTS ix_f4tx_date ON form4_transaction(tx_date DESC);
CREATE INDEX IF NOT EXISTS ix_f4tx_disc ON form4_transaction(discretionary);

CREATE TABLE IF NOT EXISTS award (
    award_id       TEXT PRIMARY KEY,
    recipient      TEXT,
    recipient_id   TEXT,
    awarding_agy   TEXT,
    amount         REAL,
    action_date    TEXT,
    description    TEXT,
    country        TEXT,
    region         TEXT,
    source         TEXT NOT NULL DEFAULT 'usaspending',
    first_seen     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_award_date ON award(action_date);
CREATE INDEX IF NOT EXISTS ix_award_region ON award(region);
CREATE INDEX IF NOT EXISTS ix_award_recipient ON award(recipient);

CREATE TABLE IF NOT EXISTS member (
    bioguide_id  TEXT PRIMARY KEY,
    last_name    TEXT,
    first_name   TEXT,
    state        TEXT,
    district     TEXT,
    party        TEXT,
    chamber      TEXT,
    congress     INTEGER,
    first_seen   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_member_last ON member(last_name);

CREATE TABLE IF NOT EXISTS member_committee (
    bioguide_id     TEXT NOT NULL REFERENCES member(bioguide_id),
    committee       TEXT NOT NULL,
    committee_code  TEXT,
    congress        INTEGER,
    role            TEXT,
    first_seen      TEXT NOT NULL,
    UNIQUE (bioguide_id, committee, congress)
);

CREATE TABLE IF NOT EXISTS fedreg_doc (
    document_number   TEXT PRIMARY KEY,
    doc_type          TEXT,
    title             TEXT,
    abstract          TEXT,
    agencies          TEXT,
    rin               TEXT,
    docket            TEXT,
    publication_date  TEXT,
    effective_on      TEXT,
    comments_close_on TEXT,
    url               TEXT,
    first_seen        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_fedreg_pub ON fedreg_doc(publication_date DESC);
CREATE INDEX IF NOT EXISTS ix_fedreg_type ON fedreg_doc(doc_type);

CREATE TABLE IF NOT EXISTS pac_committee (
    committee_id   TEXT PRIMARY KEY,
    name           TEXT,
    connected_org  TEXT,
    committee_type TEXT,
    designation    TEXT,
    state          TEXT,
    first_seen     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_pac_org ON pac_committee(connected_org);

CREATE TABLE IF NOT EXISTS pac_contribution (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    row_hash                 TEXT NOT NULL UNIQUE,
    contributor_committee_id TEXT,
    contributor_name         TEXT,
    recipient_committee_id   TEXT,
    recipient_name           TEXT,
    candidate_name           TEXT,
    amount                   REAL,
    contribution_date        TEXT,
    cycle                    INTEGER,
    first_seen               TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_contrib_recipient ON pac_contribution(recipient_name);
CREATE INDEX IF NOT EXISTS ix_contrib_contributor ON pac_contribution(contributor_name);

CREATE TABLE IF NOT EXISTS lobby_filing (
    filing_uuid    TEXT PRIMARY KEY,
    filing_type    TEXT,
    filing_year    INTEGER,
    filing_period  TEXT,
    registrant     TEXT,
    client         TEXT,
    income         REAL,
    expenses       REAL,
    posted         TEXT,
    first_seen     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_lobby_client ON lobby_filing(client);

CREATE TABLE IF NOT EXISTS lobby_activity (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    filing_uuid   TEXT NOT NULL REFERENCES lobby_filing(filing_uuid),
    issue_code    TEXT,
    issue_display TEXT,
    description   TEXT,
    entities      TEXT,
    first_seen    TEXT NOT NULL,
    UNIQUE (filing_uuid, issue_code, description)
);

CREATE TABLE IF NOT EXISTS ingest_log (
    source     TEXT,
    ran_at     TEXT,
    new_rows   INTEGER,
    note       TEXT
);
"""

# Work-queue states for a filing.
INDEXED = "indexed"      # known from the index, PDF not fetched
DOWNLOADED = "downloaded"  # PDF on disk, not yet parsed
PARSED = "parsed"        # done
FAILED = "failed"        # retired after repeated failures
MAX_ATTEMPTS = 3


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# Columns added to tables that shipped earlier. ``CREATE TABLE IF NOT EXISTS``
# silently leaves an existing table alone, so a new column — and any index over
# it — fails against a database created by a previous version. These are applied
# before the schema script runs.
MIGRATIONS: tuple[tuple[str, str, str], ...] = (
    ("form4", "parsed", "INTEGER NOT NULL DEFAULT 0"),
    # awards became multi-region once non-US sources were added
    ("award", "country", "TEXT"),
    ("award", "region", "TEXT"),
    ("award", "source", "TEXT NOT NULL DEFAULT 'usaspending'"),
)


# Data fixes applied after the column migrations above. Kept separate and
# idempotent: they only touch rows that predate a column and so carry NULL.
BACKFILLS: tuple[tuple[str, str], ...] = (
    # USASpending records are US federal contracts by definition, so rows
    # written before the region column existed can be labelled with certainty.
    ("award",
     "UPDATE award SET country = 'United States', region = 'americas' "
     "WHERE region IS NULL AND (source IS NULL OR source = 'usaspending')"),
)


def _migrate(conn: sqlite3.Connection) -> list[str]:
    """Add any missing columns to pre-existing tables. Returns what changed."""
    applied = []
    for table, column, decl in MIGRATIONS:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not exists:
            continue  # the schema script will create it complete
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column in columns:
            continue
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        applied.append(f"{table}.{column}")
    for table, statement in BACKFILLS:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if exists:
            try:
                conn.execute(statement)
            except sqlite3.OperationalError:
                pass  # column not present yet on a partially-built database
    conn.commit()
    return applied


def connect(path: Path | str = DEFAULT_DB) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    _migrate(conn)
    conn.executescript(SCHEMA)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.commit()
    return conn


def log(conn: sqlite3.Connection, source: str, n: int, note: str = "") -> None:
    conn.execute(
        "INSERT INTO ingest_log (source, ran_at, new_rows, note) VALUES (?,?,?,?)",
        (source, now_iso(), n, note),
    )
    conn.commit()


def record_failure(conn: sqlite3.Connection, doc_id: str, error: str) -> None:
    """Count an attempt and retire the filing once it exceeds the limit."""
    conn.execute(
        "UPDATE ptr_filing SET attempts = attempts + 1, last_error = ? WHERE doc_id = ?",
        (error[:300], doc_id),
    )
    conn.execute(
        "UPDATE ptr_filing SET status = ? WHERE doc_id = ? AND attempts >= ?",
        (FAILED, doc_id, MAX_ATTEMPTS),
    )
    conn.commit()


def save_trades(conn: sqlite3.Connection, trades, issues=()) -> int:
    """Insert trades (idempotent via row_hash) and record any parse issues."""
    new = 0
    for t in trades:
        cur = conn.execute(
            """INSERT OR IGNORE INTO ptr_trade
               (row_hash, doc_id, owner, asset, ticker, tx_type, tx_date,
                notif_date, amount_low, amount_high, lag_days)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (t.row_hash, t.doc_id, t.owner, t.asset, t.ticker, t.tx_type,
             t.tx_date, t.notif_date, t.amount_low, t.amount_high, t.lag_days),
        )
        new += cur.rowcount
    for issue in issues:
        conn.execute(
            "INSERT INTO parse_issue (doc_id, reason, raw, seen_at) VALUES (?,?,?,?)",
            (issue.doc_id, issue.reason, issue.raw[:500], now_iso()),
        )
    conn.commit()
    return new


def status_counts(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        "SELECT status, COUNT(*) n FROM ptr_filing GROUP BY status"
    ).fetchall()
    return {r["status"]: r["n"] for r in rows}
