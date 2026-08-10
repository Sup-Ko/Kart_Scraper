"""Tests for govdata.

The first group are regression tests for concrete failures in the original
blob-scanning parser: dates inside asset names becoming transaction dates,
entity labels becoming tickers, and amount cells becoming asset names.
"""

from __future__ import annotations

from datetime import date

from govdata import db
from govdata.company import match_score, normalize
from govdata.conflicts import find_conflicts, lag_report
from govdata.ptr_parse import (
    Trade,
    classify,
    compute_lag,
    extract_ticker,
    parse_amount,
    parse_row,
    parse_tables,
)


# ---- cell classification ----------------------------------------------------

def test_classify_distinguishes_date_cell_from_date_in_text():
    assert classify("01/15/2025") == "date"
    assert classify("US Treasury Note 4.25% due 11/15/2032") == "text"
    assert classify("P") == "tx"
    assert classify("$1,001 - $15,000") == "amount"
    assert classify("   ") == "empty"


def test_parse_amount_variants():
    assert parse_amount("$1,001 - $15,000") == (1001, 15000)
    assert parse_amount("$5,000,001 +") == (5000001, None)
    assert parse_amount("Over $50,000") == (50000, None)
    assert parse_amount("not an amount") == (None, None)


# ---- regressions: the bugs the original parser had --------------------------

def test_bond_maturity_does_not_become_transaction_date():
    """REGRESSION: 'due 11/15/2032' previously overwrote the transaction date."""
    trade, _ = parse_row(
        ["US Treasury Note 4.25% due 11/15/2032 [GS]", "P",
         "01/15/2025", "02/03/2025", "$15,001 - $50,000"]
    )
    assert trade.tx_date == "2025-01-15"
    assert trade.notif_date == "2025-02-03"


def test_option_expiry_does_not_become_transaction_date():
    """REGRESSION: 'exp 06/20/2025' previously overwrote the transaction date."""
    trade, _ = parse_row(
        ["NVIDIA Corp (NVDA) Call $500 exp 06/20/2025 [OP]", "P",
         "01/15/2025", "02/03/2025", "$100,001 - $250,000"]
    )
    assert trade.tx_date == "2025-01-15"
    assert trade.ticker == "NVDA"


def test_entity_label_is_not_a_ticker():
    """REGRESSION: '(REIT)' was previously extracted as the ticker 'REIT'."""
    assert extract_ticker("Blackstone Real Estate Income Trust (REIT) [OT]") is None
    assert extract_ticker("Apple Inc. (AAPL) [ST]") == "AAPL"
    assert extract_ticker("Ford Motor Co 6.10% Notes due 08/19/2032 (F)") == "F"


def test_short_asset_name_not_replaced_by_amount_cell():
    """REGRESSION: max(cells, key=len) previously picked the amount string."""
    trade, _ = parse_row(["MSFT", "P", "01/15/2025", "02/03/2025",
                          "$1,000,001 - $5,000,000"])
    assert trade.asset == "MSFT"
    assert trade.amount_low == 1_000_001


def test_amount_inside_asset_name_is_not_the_disclosed_amount():
    """A '$500' strike in the name must not be read as the amount bracket."""
    trade, _ = parse_row(
        ["NVIDIA Corp (NVDA) Call $500 exp 06/20/2025 [OP]", "P",
         "01/15/2025", "02/03/2025", "$100,001 - $250,000"]
    )
    assert (trade.amount_low, trade.amount_high) == (100_001, 250_000)


# ---- parsing behaviour ------------------------------------------------------

def test_owner_and_tx_type_mapping():
    trade, _ = parse_row(["Apple Inc. (AAPL) [ST] [SP]", "S",
                          "01/15/2025", "02/03/2025", "$1,001 - $15,000"])
    assert trade.tx_type == "sale_full"
    assert trade.owner == "SP"


def test_unparseable_row_is_reported_not_dropped():
    trade, issue = parse_row(
        ["Some Holding Inc", "P", "not-a-date", "also-not", "$1,001 - $15,000"]
    )
    assert trade is None
    assert issue is not None and "no transaction date" in issue.reason


def test_header_row_is_skipped_silently():
    trade, issue = parse_row(["Asset", "Transaction Type", "Transaction Date",
                              "Notification Date", "Amount"])
    assert trade is None and issue is None


def test_parse_tables_collects_trades_and_issues():
    tables = [[
        ["Asset", "Transaction Type", "Transaction Date", "Notification Date", "Amount"],
        ["Apple Inc. (AAPL) [ST]", "P", "01/15/2025", "02/03/2025", "$1,001 - $15,000"],
        ["Broken Row Inc", "P", "bad-date", "worse", "$1,001 - $15,000"],
    ]]
    result = parse_tables(tables, "DOC1")
    assert len(result.trades) == 1
    assert len(result.issues) == 1


def test_row_hash_is_stable_and_distinct():
    a = Trade(doc_id="D", asset="Apple Inc.", tx_type="purchase",
              tx_date="2025-01-15", amount_low=1001)
    b = Trade(doc_id="D", asset="Apple Inc.", tx_type="purchase",
              tx_date="2025-01-15", amount_low=1001)
    c = Trade(doc_id="D", asset="Apple Inc.", tx_type="sale_full",
              tx_date="2025-01-15", amount_low=1001)
    assert a.row_hash == b.row_hash
    assert a.row_hash != c.row_hash


def test_compute_lag_returns_none_rather_than_clipping():
    t = Trade(tx_date="2025-01-15")
    assert compute_lag(t, "2025-02-18") == 34
    assert compute_lag(t, None) is None
    assert compute_lag(Trade(tx_date=None), "2025-02-18") is None


# ---- storage: the queue and dedup fixes ------------------------------------

def _seed_filing(conn, doc_id="DOC1", filing_date="2025-02-18"):
    conn.execute(
        """INSERT INTO ptr_filing (doc_id, chamber, last_name, first_name,
           state_dst, year, filing_date, pdf_url, status, first_seen)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (doc_id, "house", "Smith", "Pat", "CA01", 2025, filing_date,
         "http://x", db.INDEXED, db.now_iso()),
    )
    conn.commit()


def test_reparsing_does_not_duplicate_trades(tmp_path):
    conn = db.connect(tmp_path / "g.sqlite")
    _seed_filing(conn)
    t = Trade(doc_id="DOC1", asset="Apple Inc. (AAPL)", ticker="AAPL",
              tx_type="purchase", tx_date="2025-01-15", amount_low=1001,
              amount_high=15000, lag_days=34)
    assert db.save_trades(conn, [t]) == 1
    assert db.save_trades(conn, [t]) == 0  # REGRESSION: previously duplicated
    assert conn.execute("SELECT COUNT(*) n FROM ptr_trade").fetchone()["n"] == 1
    conn.close()


def test_repeated_failures_retire_the_filing(tmp_path):
    """REGRESSION: a permanently broken filing used to wedge the work queue."""
    conn = db.connect(tmp_path / "g.sqlite")
    _seed_filing(conn)
    for _ in range(db.MAX_ATTEMPTS):
        db.record_failure(conn, "DOC1", "HTTP 404")
    row = conn.execute(
        "SELECT status, attempts FROM ptr_filing WHERE doc_id='DOC1'"
    ).fetchone()
    assert row["status"] == db.FAILED
    assert row["attempts"] == db.MAX_ATTEMPTS
    # a retired filing no longer appears in the download queue
    pending = conn.execute(
        "SELECT COUNT(*) n FROM ptr_filing WHERE status = ?", (db.INDEXED,)
    ).fetchone()["n"]
    assert pending == 0
    conn.close()


def test_lag_report_surfaces_discards(tmp_path):
    conn = db.connect(tmp_path / "g.sqlite")
    _seed_filing(conn)
    trades = [
        Trade(doc_id="DOC1", asset="A", tx_date="2025-01-15", lag_days=34),
        Trade(doc_id="DOC1", asset="B", tx_date="2025-01-10", lag_days=40),
        Trade(doc_id="DOC1", asset="C", tx_date="2032-11-15", lag_days=-2800),
        Trade(doc_id="DOC1", asset="D", tx_date="2025-01-01", lag_days=None),
    ]
    db.save_trades(conn, trades)
    r = lag_report(conn)
    assert r["trades_total"] == 4
    assert r["usable"] == 2
    assert r["implausible_lag"] == 1  # the bad one is COUNTED, not hidden
    assert r["missing_lag"] == 1
    assert r["median"] in (34, 40)
    conn.close()


# ---- company matching & conflicts ------------------------------------------

def test_normalize_strips_legal_forms():
    assert normalize("LOCKHEED MARTIN CORPORATION") == "lockheed martin"
    assert normalize("Lockheed Martin Corp (LMT) [ST]") == "lockheed martin"


def test_match_score_grades_confidence():
    assert match_score("Lockheed Martin Corp (LMT)", "LOCKHEED MARTIN CORPORATION") == 1.0
    assert match_score("Lockheed Martin Corp", "LOCKHEED MARTIN AERONAUTICS CO") >= 0.6
    assert match_score("Apple Inc.", "LOCKHEED MARTIN CORPORATION") == 0.0
    assert match_score("", "anything") == 0.0


def test_find_conflicts_pairs_trade_with_award(tmp_path):
    conn = db.connect(tmp_path / "g.sqlite")
    _seed_filing(conn, filing_date="2025-02-18")
    db.save_trades(conn, [
        Trade(doc_id="DOC1", asset="Lockheed Martin Corp (LMT) [ST]", ticker="LMT",
              tx_type="purchase", tx_date="2025-01-15",
              amount_low=15001, amount_high=50000, lag_days=34),
        Trade(doc_id="DOC1", asset="Apple Inc. (AAPL) [ST]", ticker="AAPL",
              tx_type="purchase", tx_date="2025-01-15",
              amount_low=1001, amount_high=15000, lag_days=34),
    ])
    conn.execute(
        """INSERT INTO award (award_id, recipient, recipient_id, awarding_agy,
           amount, action_date, description, first_seen)
           VALUES (?,?,?,?,?,?,?,?)""",
        ("A1", "LOCKHEED MARTIN CORPORATION", "r1", "Department of Defense",
         2.4e9, "2025-01-28", "Aircraft", db.now_iso()),
    )
    conn.commit()

    rows = find_conflicts(conn, window_days=90)
    assert len(rows) == 1
    c = rows[0]
    assert c.ticker == "LMT"
    assert c.member == "Pat Smith"
    assert c.gap_days == 13
    assert c.name_score == 1.0
    assert c.amount_range == "$15,001-$50,000"
    conn.close()


def test_find_conflicts_respects_time_window(tmp_path):
    conn = db.connect(tmp_path / "g.sqlite")
    _seed_filing(conn)
    db.save_trades(conn, [
        Trade(doc_id="DOC1", asset="Lockheed Martin Corp (LMT)", ticker="LMT",
              tx_type="purchase", tx_date="2025-01-15", lag_days=34),
    ])
    conn.execute(
        """INSERT INTO award (award_id, recipient, recipient_id, awarding_agy,
           amount, action_date, description, first_seen)
           VALUES (?,?,?,?,?,?,?,?)""",
        ("A1", "LOCKHEED MARTIN CORPORATION", "r1", "DoD", 1e9,
         "2025-09-01", "Later award", db.now_iso()),
    )
    conn.commit()
    assert find_conflicts(conn, window_days=30) == []
    conn.close()
