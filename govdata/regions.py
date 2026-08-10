"""Regions and the source registry.

The package began US-only, which was a limitation of what was built first, not
a design choice. Sources are now tagged by region so coverage is visible rather
than implied, and so a user can see at a glance which parts of the world their
data actually covers.

``UNKNOWN`` exists deliberately: a source that cannot say where a record
belongs should say so, not default to the US.
"""

from __future__ import annotations

from dataclasses import dataclass

GLOBAL = "global"
AFRICA = "africa"
AMERICAS = "americas"
ASIA = "asia"
EUROPE = "europe"
OCEANIA = "oceania"
UNKNOWN = "unknown"

REGIONS = (GLOBAL, AFRICA, AMERICAS, ASIA, EUROPE, OCEANIA, UNKNOWN)

# World Bank region strings -> our regions. The Bank groups by lending region,
# so "Latin America & Caribbean" and "North America" both fold into AMERICAS.
WORLD_BANK_REGIONS: dict[str, str] = {
    "AFRICA": AFRICA,
    "AFRICA EAST": AFRICA,
    "AFRICA WEST": AFRICA,
    "EASTERN AND SOUTHERN AFRICA": AFRICA,
    "WESTERN AND CENTRAL AFRICA": AFRICA,
    "MIDDLE EAST AND NORTH AFRICA": AFRICA,
    "EAST ASIA AND PACIFIC": ASIA,
    "SOUTH ASIA": ASIA,
    "EUROPE AND CENTRAL ASIA": EUROPE,
    "LATIN AMERICA AND CARIBBEAN": AMERICAS,
    "NORTH AMERICA": AMERICAS,
    "OTHER": UNKNOWN,
}


def normalize_region(raw: str | None) -> str:
    """Map a source's region label onto ours, defaulting to UNKNOWN."""
    if not raw:
        return UNKNOWN
    key = " ".join(str(raw).upper().replace("&", "AND").split())
    if key in WORLD_BANK_REGIONS:
        return WORLD_BANK_REGIONS[key]
    lowered = key.lower()
    for region in REGIONS:
        if region in lowered:
            return region
    return UNKNOWN


@dataclass(frozen=True)
class Source:
    """A data source and what it honestly offers."""

    key: str
    name: str
    region: str
    kind: str          # awards | filings | rules | finance | lobbying | economic
    lag: str
    needs_key: bool
    limits: str


# The registry is the single source of truth for the coverage table in the
# docs, so documentation cannot drift from what is actually implemented.
SOURCES: tuple[Source, ...] = (
    Source("usaspending", "USASpending contract awards", AMERICAS, "awards",
           "same day", False,
           "US federal contracts only; recipient names have no ticker crosswalk"),
    Source("worldbank", "World Bank major contract awards", GLOBAL, "awards",
           "weeks (posted per fiscal reporting)", False,
           "Bank-financed projects only, not a country's own procurement"),
    Source("house_ptr", "US House periodic transaction reports", AMERICAS, "filings",
           "30-45 days (statutory)", False,
           "amounts are ranges; pre-2020 filings are scans needing OCR"),
    Source("edgar_form4", "SEC Form 4 insider transactions", AMERICAS, "filings",
           "2 business days", False,
           "US-listed issuers only; grants and option exercises are not decisions"),
    Source("federal_register", "US Federal Register rules", AMERICAS, "rules",
           "same day", False,
           "US federal rulemaking only"),
    Source("congress", "Congress.gov members and committees", AMERICAS, "filings",
           "days", True,
           "US Congress only; assignments change each Congress"),
    Source("fec", "FEC campaign finance", AMERICAS, "finance",
           "weeks (periodic reports)", True,
           "US federal candidates; matched to members by name, approximately"),
    Source("lda", "US federal lobbying disclosures", AMERICAS, "lobbying",
           "quarterly", False,
           "income covers a whole registrant-client relationship, not per agency"),
    Source("fred", "FRED economic series", GLOBAL, "economic",
           "1-2 business days", True,
           "levels not returns; mostly US series though some international"),
)


def sources_by_region() -> dict[str, list[Source]]:
    out: dict[str, list[Source]] = {}
    for s in SOURCES:
        out.setdefault(s.region, []).append(s)
    return out


def coverage_gaps() -> list[str]:
    """Regions with no dedicated source yet — stated, not hidden."""
    covered = {s.region for s in SOURCES}
    if GLOBAL in covered:
        covered |= {AFRICA, ASIA, EUROPE, AMERICAS, OCEANIA}
    return [r for r in (AFRICA, AMERICAS, ASIA, EUROPE, OCEANIA) if r not in covered]
