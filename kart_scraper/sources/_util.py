"""Small parsing helpers shared by the scrapers."""

from __future__ import annotations

import re
from typing import Optional

_PRICE_RE = re.compile(r"(\d[\d\s.,'’]*)")


def parse_price(text: Optional[str]) -> Optional[float]:
    """Extract a numeric price from messy marketplace text.

    Handles European ``1 299,00 €``, US ``$1,299.00`` and Swiss ``2'500.-``
    groupings. Returns ``None`` when no plausible number is found.
    """
    if not text:
        return None
    match = _PRICE_RE.search(text.replace("\xa0", " "))
    if not match:
        return None
    # Apostrophes are Swiss thousands separators (2'500 -> 2500); drop them.
    raw = match.group(1).strip().replace(" ", "").replace("'", "").replace("’", "")
    # Decide which symbol is the decimal separator.
    if "," in raw and "." in raw:
        # The right-most separator is the decimal one.
        if raw.rfind(",") > raw.rfind("."):
            raw = raw.replace(".", "").replace(",", ".")
        else:
            raw = raw.replace(",", "")
    elif "," in raw:
        # Treat a single comma with <=2 trailing digits as a decimal point.
        if len(raw.split(",")[-1]) <= 2:
            raw = raw.replace(",", ".")
        else:
            raw = raw.replace(",", "")
    elif "." in raw:
        # A lone dot with 3 trailing digits is a thousands separator (e.g.
        # "1.250" -> 1250); 1-2 trailing digits is a decimal point ("1.25").
        if len(raw.split(".")[-1]) == 3:
            raw = raw.replace(".", "")
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def clean_text(text: Optional[str]) -> str:
    """Collapse whitespace and strip."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()
