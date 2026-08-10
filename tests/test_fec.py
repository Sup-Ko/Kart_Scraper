"""Tests for FEC campaign finance intake and the conflict join — offline."""

from __future__ import annotations

import json

from govdata import db
from govdata.conflicts import find_conflicts
from govdata.fec import (
    contributions_to_member,
    have_key,
    import_contributions_json,
    ingest_corporate_pacs,
    ingest_pac_contributions,
    parse_committees,
    parse_contributions,
    save_contributions,
)
from govdata.ptr_parse import Trade

COMMITTEES_PAYLOAD = {
    "results": [
        {
            "committee_id": "C00123456",
            "name": "LOCKHEED MARTIN CORPORATION EMPLOYEES PAC",
            "connected_organization_name": "LOCKHEED MARTIN CORPORATION",
            "committee_type": "Q",
            "designation": "B",
            "state": "MD",
        },
        {"name": "no id, skipped"},
    ]
}

CONTRIB_PAYLOAD = {
    "results": [
        {
            "transaction_id": "TX1",
            "contributor_id": "C00123456",
            "contributor_name": "LOCKHEED MARTIN CORPORATION EMPLOYEES PAC",
            "committee": {"committee_id": "C00999", "name": "SMITH FOR CONGRESS"},
            "candidate_name": "SMITH, PAT",
            "contribution_receipt_amount": 5000.0,
            "contribution_receipt_date": "2025-03-15",
            "two_year_transaction_period": 2026,
        },
        {
            "transaction_id": "TX2",
            "contributor_id": "C00123456",
            "contributor_name": "LOCKHEED MARTIN CORPORATION EMPLOYEES PAC",
            "committee": {"committee_id": "C00888", "name": "JONES FOR CONGRESS"},
            "candidate_name": "JONES, ALEX",
            "contribution_receipt_amount": 2500.0,
            "contribution_receipt_date": "2025-04-02",
            "two_year_transaction_period": 2026,
        },
        {"transaction_id": "TX3", "contribution_receipt_amount": None},  # skipped
    ]
}


def test_parse_committees():
    rows = parse_committees(COMMITTEES_PAYLOAD)
    assert len(rows) == 1
    assert rows[0]["committee_id"] == "C00123456"
    assert rows[0]["connected_org"] == "LOCKHEED MARTIN CORPORATION"
    assert rows[0]["committee_type"] == "Q"


def test_parse_contributions_skips_rows_without_amount():
    rows = parse_contributions(CONTRIB_PAYLOAD)
    assert len(rows) == 2
    assert rows[0]["recipient_name"] == "SMITH FOR CONGRESS"
    assert rows[0]["amount"] == 5000.0
    assert rows[0]["contributor_committee_id"] == "C00123456"


def test_parse_contributions_empty():
    assert parse_contributions({}) == []
    assert parse_contributions({"results": None}) == []


def test_save_contributions_is_idempotent(tmp_path):
    conn = db.connect(tmp_path / "g.sqlite")
    rows = parse_contributions(CONTRIB_PAYLOAD)
    assert save_contributions(conn, rows) == 2
    assert save_contributions(conn, rows) == 0
    conn.close()


def test_import_contributions_json(tmp_path):
    conn = db.connect(tmp_path / "g.sqlite")
    path = tmp_path / "c.json"
    path.write_text(json.dumps(CONTRIB_PAYLOAD), encoding="utf-8")
    assert import_contributions_json(conn, str(path)) == 2
    conn.close()


def test_ingest_without_key_is_a_noop(tmp_path, monkeypatch):
    monkeypatch.delenv("FEC_API_KEY", raising=False)
    conn = db.connect(tmp_path / "g.sqlite")
    assert not have_key()
    assert ingest_corporate_pacs(conn, ["Lockheed Martin"]) == 0  # no network call
    assert ingest_pac_contributions(conn) == 0
    conn.close()


def _seed_pac(conn):
    conn.execute(
        """INSERT OR IGNORE INTO pac_committee (committee_id, name, connected_org,
           committee_type, designation, state, first_seen)
           VALUES (?,?,?,?,?,?,?)""",
        ("C00123456", "LOCKHEED MARTIN CORPORATION EMPLOYEES PAC",
         "LOCKHEED MARTIN CORPORATION", "Q", "B", "MD", db.now_iso()))
    save_contributions(conn, parse_contributions(CONTRIB_PAYLOAD))
    conn.commit()


def test_contributions_to_member_matches_by_last_name(tmp_path):
    conn = db.connect(tmp_path / "g.sqlite")
    _seed_pac(conn)
    smith = contributions_to_member(conn, "Smith")
    assert len(smith) == 1 and smith[0]["amount"] == 5000.0
    assert contributions_to_member(conn, "Nobody") == []
    assert contributions_to_member(conn, "") == []
    conn.close()


def test_contributions_filtered_by_company(tmp_path):
    conn = db.connect(tmp_path / "g.sqlite")
    _seed_pac(conn)
    assert contributions_to_member(conn, "Smith", company="LOCKHEED MARTIN CORPORATION")
    # a different company's PAC must not match
    assert contributions_to_member(conn, "Smith", company="APPLE INC") == []
    conn.close()


def test_conflict_gains_pac_dimension_and_outranks(tmp_path):
    """A funded member should outrank an identical unfunded one."""
    conn = db.connect(tmp_path / "g.sqlite")
    _seed_pac(conn)

    for doc, last, first, sd in (("D1", "Smith", "Pat", "CA01"),
                                 ("D2", "Brown", "Chris", "NY02")):
        conn.execute(
            """INSERT INTO ptr_filing (doc_id,chamber,last_name,first_name,state_dst,
               year,filing_date,pdf_url,status,first_seen) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (doc, "house", last, first, sd, 2025, "2025-02-18", "u",
             db.PARSED, db.now_iso()))
    conn.commit()

    # identical trades; only Smith received PAC money
    db.save_trades(conn, [
        Trade(doc_id="D1", asset="Lockheed Martin Corp (LMT)", ticker="LMT",
              tx_type="purchase", tx_date="2025-01-15", lag_days=34),
        Trade(doc_id="D2", asset="Lockheed Martin Corp (LMT)", ticker="LMT",
              tx_type="purchase", tx_date="2025-01-15", lag_days=34),
    ])
    conn.execute(
        """INSERT INTO award (award_id,recipient,recipient_id,awarding_agy,amount,
           action_date,description,first_seen) VALUES (?,?,?,?,?,?,?,?)""",
        ("A1", "LOCKHEED MARTIN CORPORATION", "r1", "Department of Defense",
         2.4e9, "2025-01-28", "F-35", db.now_iso()))
    conn.commit()

    rows = find_conflicts(conn, window_days=90)
    assert len(rows) == 2
    by = {c.member: c for c in rows}
    assert by["Pat Smith"].pac_count == 1
    assert by["Pat Smith"].pac_total == 5000.0
    assert by["Chris Brown"].pac_count == 0
    # the funded member ranks first
    assert rows[0].member == "Pat Smith"
    assert by["Pat Smith"].salience > by["Chris Brown"].salience
    conn.close()


def test_conflicts_still_work_without_fec_data(tmp_path):
    """Degrading gracefully: no PAC data must not break the analysis."""
    conn = db.connect(tmp_path / "g.sqlite")
    conn.execute(
        """INSERT INTO ptr_filing (doc_id,chamber,last_name,first_name,state_dst,
           year,filing_date,pdf_url,status,first_seen) VALUES (?,?,?,?,?,?,?,?,?,?)""",
        ("D1", "house", "Smith", "Pat", "CA01", 2025, "2025-02-18", "u",
         db.PARSED, db.now_iso()))
    conn.commit()
    db.save_trades(conn, [
        Trade(doc_id="D1", asset="Lockheed Martin Corp (LMT)", ticker="LMT",
              tx_type="purchase", tx_date="2025-01-15", lag_days=34)])
    conn.execute(
        """INSERT INTO award (award_id,recipient,recipient_id,awarding_agy,amount,
           action_date,description,first_seen) VALUES (?,?,?,?,?,?,?,?)""",
        ("A1", "LOCKHEED MARTIN CORPORATION", "r1", "DoD", 1e9,
         "2025-01-28", "x", db.now_iso()))
    conn.commit()
    rows = find_conflicts(conn)
    assert len(rows) == 1 and rows[0].pac_count == 0
    conn.close()
