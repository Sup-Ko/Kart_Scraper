"""Tests for region tagging and global (non-US) award intake."""

from __future__ import annotations

from govdata import db
from govdata.regions import (
    AFRICA,
    AMERICAS,
    ASIA,
    EUROPE,
    GLOBAL,
    SOURCES,
    UNKNOWN,
    coverage_gaps,
    normalize_region,
    sources_by_region,
)
from govdata.worldbank import coverage, parse_awards, save_awards

PAYLOAD = [
    {
        "wb_contract_number": "WB-001",
        "supplier": "SIEMENS AG",
        "supplier_country": "Germany",
        "supplier_country_code": "DE",
        "borrower_country": "Kenya",
        "region": "AFRICA EAST",
        "project_id": "P123456",
        "project_name": "Kenya Electricity Expansion",
        "procurement_category": "Goods",
        "total_contract_amount": "12,500,000",
        "contract_signing_date": "2026-03-15T00:00:00.000",
    },
    {
        "wb_contract_number": "WB-002",
        "supplier": "TATA CONSULTANCY SERVICES",
        "supplier_country_code": "IN",
        "borrower_country": "Bangladesh",
        "region": "SOUTH ASIA",
        "project_id": "P654321",
        "project_name": "Digital Government Program",
        "procurement_category": "Consultant Services",
        "total_contract_amount": "3200000",
        "contract_signing_date": "2026-02-01",
    },
    {"supplier": "", "total_contract_amount": "999"},  # no supplier -> skipped
]


def test_normalize_region_maps_world_bank_labels():
    assert normalize_region("AFRICA EAST") == AFRICA
    assert normalize_region("South Asia") == ASIA
    assert normalize_region("Europe and Central Asia") == EUROPE
    assert normalize_region("Latin America & Caribbean") == AMERICAS


def test_normalize_region_defaults_to_unknown_not_us():
    """An unrecognized label must not silently become a US/Americas record."""
    assert normalize_region(None) == UNKNOWN
    assert normalize_region("") == UNKNOWN
    assert normalize_region("Somewhere Unlabelled") == UNKNOWN


def test_parse_awards_extracts_country_and_region():
    rows = parse_awards(PAYLOAD)
    assert len(rows) == 2
    a, b = rows
    assert a["recipient"] == "SIEMENS AG"
    assert a["country"] == "Kenya"
    assert a["region"] == AFRICA
    assert a["amount"] == 12_500_000.0      # comma-formatted parsed
    assert a["action_date"] == "2026-03-15"  # timestamp trimmed
    assert a["source"] == "worldbank"
    assert b["region"] == ASIA and b["country"] == "Bangladesh"


def test_parse_awards_synthesizes_id_when_contract_number_missing():
    rows = parse_awards([{
        "supplier": "ACME LTD", "project_id": "P1",
        "contract_signing_date": "2026-01-01", "total_contract_amount": "5000",
    }])
    assert rows[0]["award_id"].startswith("WB:P1:ACME LTD")


def test_parse_awards_empty():
    assert parse_awards([]) == []
    assert parse_awards(None) == []


def test_save_awards_is_idempotent(tmp_path):
    conn = db.connect(tmp_path / "g.sqlite")
    rows = parse_awards(PAYLOAD)
    assert save_awards(conn, rows) == 2
    assert save_awards(conn, rows) == 0
    conn.close()


def test_international_awards_coexist_with_us_awards(tmp_path):
    """Non-US awards land in the same table, so existing joins just work."""
    conn = db.connect(tmp_path / "g.sqlite")
    save_awards(conn, parse_awards(PAYLOAD))
    conn.execute(
        """INSERT INTO award (award_id, recipient, recipient_id, awarding_agy,
           amount, action_date, description, country, region, source, first_seen)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        ("A1", "LOCKHEED MARTIN CORPORATION", "r1", "Department of Defense",
         2.4e9, "2026-01-28", "F-35", "United States", AMERICAS,
         "usaspending", db.now_iso()))
    conn.commit()

    rows = coverage(conn)
    regions = {r["region"] for r in rows}
    assert AFRICA in regions and ASIA in regions and AMERICAS in regions
    total = conn.execute("SELECT COUNT(*) FROM award").fetchone()[0]
    assert total == 3
    conn.close()


def test_award_region_columns_migrate_onto_an_old_database(tmp_path):
    """REGRESSION: awards predate the region columns; upgrading must not break."""
    import sqlite3
    path = tmp_path / "old.sqlite"
    old = sqlite3.connect(str(path))
    old.execute("""CREATE TABLE award (award_id TEXT PRIMARY KEY, recipient TEXT,
                   recipient_id TEXT, awarding_agy TEXT, amount REAL,
                   action_date TEXT, description TEXT, first_seen TEXT)""")
    old.execute("INSERT INTO award VALUES ('A1','ACME','r','DoD',1e6,"
                "'2026-01-01','x','now')")
    old.commit()
    old.close()

    conn = db.connect(path)   # must not raise
    cols = {r[1] for r in conn.execute("PRAGMA table_info(award)")}
    assert {"country", "region", "source"} <= cols
    row = conn.execute("SELECT recipient, source FROM award").fetchone()
    assert row["recipient"] == "ACME"
    assert row["source"] == "usaspending"   # pre-existing rows default sensibly
    conn.close()


def test_source_registry_declares_regions_and_limits():
    """Every registered source states region, lag and honest limits."""
    assert SOURCES
    for s in SOURCES:
        assert s.region and s.lag and s.limits
        assert isinstance(s.needs_key, bool)
    by_region = sources_by_region()
    assert AMERICAS in by_region and GLOBAL in by_region


def test_coverage_gaps_reports_regions_without_sources():
    """Gaps are stated rather than implied — global sources close them."""
    gaps = coverage_gaps()
    assert isinstance(gaps, list)
    # a GLOBAL source is registered, so no continent is left uncovered
    assert gaps == []


def test_backfill_labels_legacy_usaspending_rows(tmp_path):
    """Rows written before the region column are US by definition — label them."""
    import sqlite3
    path = tmp_path / "old.sqlite"
    old = sqlite3.connect(str(path))
    old.execute("""CREATE TABLE award (award_id TEXT PRIMARY KEY, recipient TEXT,
                   recipient_id TEXT, awarding_agy TEXT, amount REAL,
                   action_date TEXT, description TEXT, first_seen TEXT)""")
    old.execute("INSERT INTO award VALUES ('A1','ACME','r','DoD',1e6,"
                "'2026-01-01','x','now')")
    old.commit()
    old.close()

    conn = db.connect(path)
    row = conn.execute("SELECT country, region FROM award").fetchone()
    assert row["country"] == "United States"
    assert row["region"] == AMERICAS
    conn.close()


def test_backfill_does_not_relabel_international_rows(tmp_path):
    conn = db.connect(tmp_path / "g.sqlite")
    save_awards(conn, parse_awards(PAYLOAD))
    conn.close()
    conn = db.connect(tmp_path / "g.sqlite")   # reopen: migration runs again
    regions = {r["region"] for r in conn.execute("SELECT region FROM award")}
    assert AFRICA in regions and ASIA in regions
    assert AMERICAS not in regions   # World Bank rows kept their own regions
    conn.close()
