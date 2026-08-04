"""Parsing helpers for real-estate text, on top of the shared kart helpers."""

from __future__ import annotations

import re
from typing import Optional

# Price/whitespace parsing is shared with the kart scraper — it already handles
# Swiss "1'250'000.–" style numbers.
from kart_scraper.sources._util import clean_text, parse_price  # noqa: F401

_ROOMS_RE = re.compile(
    r"(\d{1,2}(?:[.,]\d)?)\s*(?:pi[eè]ces?|pces?|rooms?|zimmer|zi\b)", re.IGNORECASE)
_SURFACE_RE = re.compile(
    r"(\d{2,4}(?:[.,]\d{1,2})?)\s*m(?:²|2\b)", re.IGNORECASE)


def parse_rooms(text: Optional[str]) -> Optional[float]:
    """Extract a Swiss room count from text like ``"3,5 pièces"`` or ``"4.5 rooms"``.

    Handles the unicode half fraction (``"2½ pièces"``) and returns ``None``
    when no plausible count is found. Values above 20 are rejected as parsing
    noise (surface figures, street numbers, …).
    """
    if not text:
        return None
    match = _ROOMS_RE.search(text.replace("½", ".5"))
    if not match:
        return None
    value = float(match.group(1).replace(",", "."))
    return value if 0 < value <= 20 else None


def parse_surface(text: Optional[str]) -> Optional[float]:
    """Extract a living surface in m² from text like ``"82 m²"`` / ``"105m2"``."""
    if not text:
        return None
    match = _SURFACE_RE.search(text)
    if not match:
        return None
    value = float(match.group(1).replace(",", "."))
    return value if 10 <= value <= 2000 else None
