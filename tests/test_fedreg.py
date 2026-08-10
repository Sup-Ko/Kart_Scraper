"""Tests for Federal Register intake — parsing and agency matching, offline."""

from __future__ import annotations

from datetime import date, timedelta

from govdata import db
from govdata.fedreg import _build_url, parse_documents, rules_for_agencies

PAYLOAD = {
    "count": 3,
    "results": [
        {
            "document_number": "2026-11111",
            "type": "PRORULE",
            "title": "Proposed Rule on Defense Procurement Thresholds",
            "abstract": "DoD proposes to raise the simplified acquisition threshold.",
            "publication_date": "2026-08-01",
            "effective_on": None,
            "comments_close_on": "2026-09-30",
            "html_url": "https://www.federalregister.gov/d/2026-11111",
            "agencies": [{"name": "Department of Defense"},
                         {"raw_name": "DEFENSE ACQUISITION REGULATIONS SYSTEM"}],
            "regulation_id_numbers": ["0750-AL12"],
            "docket_ids": ["DARS-2026-0001"],
        },
        {
            "document_number": "2026-22222",
            "type": "RULE",
            "title": "Final Rule on Energy Efficiency Standards",
            "abstract": "DOE finalizes standards.",
            "publication_date": "2026-07-15",
            "effective_on": "2026-10-01",
            "comments_close_on": None,
            "html_url": "https://www.federalregister.gov/d/2026-22222",
            "agencies": ["Department of Energy"],   # older shape: plain strings
            "regulation_id_numbers": [],
            "docket_ids": [],
        },
        {"type": "RULE", "title": "No document number, must be skipped"},
    ],
}


def test_parse_documents_handles_both_agency_shapes():
    rows = parse_documents(PAYLOAD)
    assert len(rows) == 2  # the third row has no document_number
    a, b = rows
    assert a["doc_type"] == "PRORULE"
    assert "Department of Defense" in a["agencies"]
    assert "DEFENSE ACQUISITION REGULATIONS SYSTEM" in a["agencies"]
    assert a["rin"] == "0750-AL12"
    assert a["comments_close_on"] == "2026-09-30"
    assert b["agencies"] == "Department of Energy"   # plain-string shape works


def test_parse_documents_empty_payload():
    assert parse_documents({}) == []
    assert parse_documents({"results": None}) == []


def test_build_url_includes_filters():
    url = _build_url(date(2026, 1, 1), ("RULE", "PRORULE"), 100, 1)
    assert "conditions[publication_date][gte]=2026-01-01" in url
    assert "conditions[type][]=RULE" in url
    assert "conditions[type][]=PRORULE" in url
    assert "fields[]=document_number" in url


def _seed(conn, rows):
    for r in rows:
        conn.execute(
            """INSERT OR IGNORE INTO fedreg_doc (document_number, doc_type, title,
               abstract, agencies, rin, docket, publication_date, effective_on,
               comments_close_on, url, first_seen)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (r["document_number"], r["doc_type"], r["title"], r["abstract"],
             r["agencies"], r["rin"], r["docket"], r["publication_date"],
             r["effective_on"], r["comments_close_on"], r["url"], db.now_iso()))
    conn.commit()


def test_rules_for_agencies_matches_across_naming_differences(tmp_path):
    """An award says 'Department of Defense'; matching must not require equality."""
    conn = db.connect(tmp_path / "g.sqlite")
    _seed(conn, parse_documents(PAYLOAD))
    rows = rules_for_agencies(conn, ["Department of Defense"])
    assert len(rows) == 1
    assert rows[0]["document_number"] == "2026-11111"
    # an unrelated agency matches nothing
    assert rules_for_agencies(conn, ["Department of Agriculture"]) == []
    conn.close()


def test_rules_for_agencies_open_comments_filter(tmp_path):
    conn = db.connect(tmp_path / "g.sqlite")
    rows = parse_documents(PAYLOAD)
    # make the DoD comment period clearly open, and check the closed one drops out
    rows[0]["comments_close_on"] = (date.today() + timedelta(days=20)).isoformat()
    _seed(conn, rows)
    open_only = rules_for_agencies(
        conn, ["Department of Defense", "Department of Energy"], open_comments_only=True)
    assert [r["document_number"] for r in open_only] == ["2026-11111"]
    conn.close()


def test_rules_for_agencies_requires_agencies(tmp_path):
    conn = db.connect(tmp_path / "g.sqlite")
    _seed(conn, parse_documents(PAYLOAD))
    assert rules_for_agencies(conn, []) == []
    assert rules_for_agencies(conn, [""]) == []
    conn.close()
