"""Company-name matching, for joining datasets that share no key.

Award records identify recipients by legal name ("LOCKHEED MARTIN
CORPORATION"); a portfolio identifies holdings by ticker and descriptive name.
No public crosswalk links the two, so matching is by normalized name and is
necessarily approximate.

Approximate is acceptable for surfacing candidates a human then checks — but it
must never be presented as certainty, so :func:`match_score` returns a graded
score rather than a boolean, and callers carry it through to the output.

This is deliberately self-contained: risk_desk stands alone, with no import
dependency on any data-collection package.
"""

from __future__ import annotations

import re

# Legal-form suffixes and filler that carry no identifying information.
SUFFIXES = {
    "corporation", "corp", "incorporated", "inc", "company", "co", "llc", "llp",
    "lp", "ltd", "limited", "plc", "holdings", "holding", "group", "the",
    "technologies", "technology", "systems", "international", "intl", "industries",
    "enterprises", "solutions", "services", "worldwide", "usa", "us", "na",
    "and", "&", "of", "ag", "sa", "se", "nv", "bv", "gmbh", "spa", "oyj", "ab",
}

_PUNCT = re.compile(r"[^\w\s]")
_TICKER_PARENS = re.compile(r"\([A-Z.]{1,7}\)")
_TYPE_BRACKET = re.compile(r"\[[A-Z]{2}\]")


def normalize(name: str | None) -> str:
    """Reduce a company name to its distinguishing tokens."""
    if not name:
        return ""
    s = name.upper()
    s = _TICKER_PARENS.sub(" ", s)
    s = _TYPE_BRACKET.sub(" ", s)
    s = _PUNCT.sub(" ", s)
    tokens = [t for t in s.lower().split() if t and t not in SUFFIXES]
    tokens = [t for t in tokens if len(t) > 1 and not t.isdigit()]
    return " ".join(tokens)


def match_score(a: str | None, b: str | None) -> float:
    """Graded similarity in [0, 1] between two company names.

    1.0  identical normalized names
    0.8  one is a substantial prefix of the other
    0.6+ meaningful token overlap
    0.0  nothing in common
    """
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    if na.startswith(nb) or nb.startswith(na):
        # A shared prefix only means something when it accounts for most of
        # both names. "lockheed martin" vs "lockheed martin aeronautics" is a
        # real match; "apple" vs "apple valley sanitation district" is not.
        ta, tb = na.split(), nb.split()
        ratio = min(len(ta), len(tb)) / max(len(ta), len(tb))
        if ratio >= 0.5:
            return 0.8
        return round(0.5 * ratio, 3)

    ta, tb = set(na.split()), set(nb.split())
    if not (ta & tb):
        return 0.0
    overlap = len(ta & tb) / len(ta | tb)
    return round(min(0.75, 0.6 + overlap * 0.15), 3)
