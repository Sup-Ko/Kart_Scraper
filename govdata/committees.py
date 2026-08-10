"""Committee jurisdiction reference data and matching.

Timing proximity alone is weak evidence: a member buying a defense contractor
two weeks before a Pentagon award may simply own a defense fund. The question
that carries actual weight is whether the member sits on a committee with
**jurisdiction over the agency that made the award** — that is the difference
between a coincidence and a structural conflict.

The committee-to-jurisdiction map below is stable public reference data (House
and Senate standing committees and the agencies they authorize or appropriate
for), so it is bundled rather than fetched. Member *assignments* change every
Congress and come from the Congress.gov API — see ``congress.py``.

This mapping is a deliberate simplification. Real jurisdiction is contested,
overlapping, and set by chamber rules; Appropriations in particular touches
everything. Treat a jurisdiction match as "this is worth a look", exactly as
with everything else in this package.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# committee name (normalized substring) -> (agency keywords, sectors)
COMMITTEE_JURISDICTION: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "armed services": (("defense", "army", "navy", "air force", "marine",
                        "defense logistics", "missile", "space force"),
                       ("defense", "aerospace")),
    "appropriations": ((), ()),  # funds everything; handled specially below
    "energy and commerce": (("energy", "health and human services", "food and drug",
                             "communications", "environmental protection"),
                            ("energy", "healthcare", "pharma", "telecom", "utilities")),
    "financial services": (("treasury", "securities and exchange",
                            "federal reserve", "housing and urban development"),
                           ("financials", "banking", "insurance")),
    "transportation and infrastructure": (("transportation", "federal aviation",
                                           "coast guard", "maritime"),
                                          ("transport", "airlines", "rail")),
    "agriculture": (("agriculture", "farm"), ("agriculture", "food")),
    "homeland security": (("homeland security", "customs", "immigration",
                           "emergency management"), ("defense", "security")),
    "veterans affairs": (("veterans",), ("healthcare",)),
    "science space and technology": (("national aeronautics", "nasa",
                                      "national science", "standards and technology"),
                                     ("aerospace", "technology", "research")),
    "judiciary": (("justice", "federal bureau of investigation", "prisons"),
                  ("legal",)),
    "foreign affairs": (("state", "agency for international development"),
                        ("defense", "international")),
    "natural resources": (("interior", "land management", "national park",
                           "geological survey"), ("energy", "mining")),
    "education and the workforce": (("education", "labor"), ("education",)),
    "ways and means": (("treasury", "internal revenue", "trade representative"),
                       ("financials", "healthcare", "trade")),
    "oversight and accountability": (("general services",), ()),
    "intelligence": (("central intelligence", "national security agency",
                      "national reconnaissance"), ("defense", "security")),
    "small business": (("small business",), ()),
    "commerce science and transportation": (("commerce", "transportation",
                                             "national oceanic", "federal aviation"),
                                            ("transport", "telecom", "technology")),
    "banking housing and urban affairs": (("treasury", "federal reserve",
                                           "housing and urban development"),
                                          ("financials", "banking")),
    "health education labor and pensions": (("health and human services", "education",
                                             "labor", "food and drug"),
                                            ("healthcare", "pharma", "education")),
    "environment and public works": (("environmental protection", "army corps"),
                                     ("utilities", "construction")),
}

# Committees whose jurisdiction is government-wide. A match here is real but
# much weaker evidence than a subject-matter committee, so it is scored lower.
BROAD_COMMITTEES = {"appropriations", "oversight and accountability", "budget", "rules"}

_PUNCT = re.compile(r"[^\w\s]")


def normalize_committee(name: str | None) -> str:
    """Reduce a committee name to a comparable key."""
    if not name:
        return ""
    s = _PUNCT.sub(" ", name.lower())
    s = re.sub(r"\b(committee|subcommittee|on|the|house|senate|select|permanent|"
               r"joint|standing)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def normalize_agency(name: str | None) -> str:
    if not name:
        return ""
    s = _PUNCT.sub(" ", name.lower())
    s = re.sub(r"\b(department|dept|of|the|u\s?s|united states|office|agency)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


@dataclass
class JurisdictionMatch:
    committee: str
    agency: str
    strength: float  # 1.0 subject-matter match, 0.4 government-wide committee
    basis: str


def jurisdiction_over(committee: str, agency: str) -> JurisdictionMatch | None:
    """Does ``committee`` have jurisdiction over ``agency``?

    Returns a graded match or None. Government-wide committees (Appropriations,
    Oversight) match anything at reduced strength, because "they fund every
    agency" is true but far less pointed than sitting on the authorizing
    committee for that specific agency.
    """
    ckey = normalize_committee(committee)
    akey = normalize_agency(agency)
    if not ckey or not akey:
        return None

    # exact-ish committee lookup: find the reference entry contained in the key
    entry = None
    matched_name = ""
    for ref, value in COMMITTEE_JURISDICTION.items():
        if ref in ckey or ckey in ref:
            entry = value
            matched_name = ref
            break

    if matched_name in BROAD_COMMITTEES or ckey in BROAD_COMMITTEES:
        return JurisdictionMatch(
            committee=committee, agency=agency, strength=0.4,
            basis="government-wide committee (funds or oversees all agencies)",
        )
    if entry is None:
        return None

    agencies, _sectors = entry
    for keyword in agencies:
        if keyword in akey:
            return JurisdictionMatch(
                committee=committee, agency=agency, strength=1.0,
                basis=f"committee jurisdiction includes '{keyword}'",
            )
    return None


def sectors_for(committee: str) -> tuple[str, ...]:
    """Market sectors a committee's jurisdiction touches."""
    ckey = normalize_committee(committee)
    for ref, (_agencies, sectors) in COMMITTEE_JURISDICTION.items():
        if ref in ckey or ckey in ref:
            return sectors
    return ()
