"""Tests for LDA lobbying intake and the award-overlap join — offline."""

from __future__ import annotations

import json

from govdata import db
from govdata.lobbying import (
    agencies_lobbied,
    import_filings_json,
    lobbying_award_overlap,
    parse_filings,
    save_filings,
)

PAYLOAD = {
    "results": [
        {
            "filing_uuid": "u-1",
            "filing_type": "Q1",
            "filing_year": 2026,
            "filing_period": "first_quarter",
            "income": "250,000.00",
            "expenses": None,
            "dt_posted": "2026-04-20T12:00:00Z",
            "registrant": {"name": "BIG LOBBY LLP"},
            "client": {"name": "LOCKHEED MARTIN CORPORATION"},
            "lobbying_activities": [
                {
                    "general_issue_code": "DEF",
                    "general_issue_code_display": "Defense",
                    "description": "Appropriations for tactical aircraft programs.",
                    "government_entities": [
                        {"name": "Department of Defense"},
                        {"name": "U.S. Senate"},
                    ],
                },
                {
                    "general_issue_code": "BUD",
                    "general_issue_code_display": "Budget/Appropriations",
                    "description": "FY27 budget request.",
                    "government_entities": [{"name": "Department of Defense"}],
                },
            ],
        },
        {"filing_type": "Q2", "client": {"name": "NO UUID"}},  # skipped
    ]
}


def test_parse_filings_extracts_client_and_activities():
    rows = parse_filings(PAYLOAD)
    assert len(rows) == 1
    f = rows[0]
    assert f["client"] == "LOCKHEED MARTIN CORPORATION"
    assert f["registrant"] == "BIG LOBBY LLP"
    assert f["income"] == 250000.0          # comma-formatted amount parsed
    assert f["expenses"] is None
    assert f["posted"] == "2026-04-20"      # timestamp trimmed to a date
    assert len(f["activities"]) == 2
    assert "Department of Defense" in f["activities"][0]["entities"]


def test_parse_filings_empty():
    assert parse_filings({}) == []
    assert parse_filings({"results": None}) == []


def test_save_filings_is_idempotent(tmp_path):
    conn = db.connect(tmp_path / "g.sqlite")
    rows = parse_filings(PAYLOAD)
    assert save_filings(conn, rows) == (1, 2)
    assert save_filings(conn, rows) == (0, 0)
    conn.close()


def test_import_filings_json(tmp_path):
    conn = db.connect(tmp_path / "g.sqlite")
    path = tmp_path / "l.json"
    path.write_text(json.dumps(PAYLOAD), encoding="utf-8")
    assert import_filings_json(conn, str(path)) == (1, 2)
    conn.close()


def test_agencies_lobbied_counts_activities(tmp_path):
    conn = db.connect(tmp_path / "g.sqlite")
    save_filings(conn, parse_filings(PAYLOAD))
    counts = agencies_lobbied(conn, "Lockheed Martin Corp")
    assert counts["Department of Defense"] == 2   # named in both activities
    assert counts["U.S. Senate"] == 1
    # an unrelated company matches nothing
    assert agencies_lobbied(conn, "Apple Inc") == {}
    conn.close()


def test_lobbying_award_overlap(tmp_path):
    """A contractor lobbying the agency that pays it should be surfaced."""
    conn = db.connect(tmp_path / "g.sqlite")
    save_filings(conn, parse_filings(PAYLOAD))
    conn.execute(
        """INSERT INTO award (award_id,recipient,recipient_id,awarding_agy,amount,
           action_date,description,first_seen) VALUES (?,?,?,?,?,?,?,?)""",
        ("A1", "LOCKHEED MARTIN CORPORATION", "r1", "Department of Defense",
         2.4e9, "2026-01-28", "F-35", db.now_iso()))
    conn.commit()

    overlap = lobbying_award_overlap(conn)
    assert len(overlap) == 1
    o = overlap[0]
    assert o["client"] == "LOCKHEED MARTIN CORPORATION"
    assert o["agency"] == "Department of Defense"
    assert o["award_total"] == 2.4e9
    assert o["lobby_activities"] == 2
    conn.close()


def test_overlap_empty_without_lobbying_data(tmp_path):
    conn = db.connect(tmp_path / "g.sqlite")
    conn.execute(
        """INSERT INTO award (award_id,recipient,recipient_id,awarding_agy,amount,
           action_date,description,first_seen) VALUES (?,?,?,?,?,?,?,?)""",
        ("A1", "SOME CORP", "r1", "Department of Defense", 1e8,
         "2026-01-28", "x", db.now_iso()))
    conn.commit()
    assert lobbying_award_overlap(conn) == []
    conn.close()
