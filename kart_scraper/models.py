"""Normalized data model shared by every source scraper."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional


@dataclass
class Listing:
    """A single go-kart-for-sale listing, normalized across all sources.

    Every source scraper is responsible for converting its site-specific raw
    data into one of these. ``lat``/``lon`` and ``distance_km`` are filled in
    later by the geocoding step; ``score``/``subscores`` by the scoring step.
    """

    title: str
    url: str
    source: str
    price: Optional[float] = None
    currency: str = "EUR"
    location: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    posted_date: Optional[date] = None
    image_count: int = 0
    description: str = ""
    # Free-form extracted specs, e.g. {"year": "2019", "engine": "Rotax"}.
    specs: dict[str, str] = field(default_factory=dict)

    # Filled in by later pipeline stages.
    distance_km: Optional[float] = None
    score: Optional[float] = None
    subscores: dict[str, float] = field(default_factory=dict)

    @property
    def fingerprint(self) -> str:
        """Stable id used to dedupe listings seen across multiple sources."""
        if self.url:
            basis = self.url.split("?")[0].rstrip("/").lower()
        else:
            price = "" if self.price is None else f"{self.price:.0f}"
            basis = f"{self.title.strip().lower()}|{price}|{(self.location or '').lower()}"
        return hashlib.sha1(basis.encode("utf-8")).hexdigest()

    @property
    def age_days(self) -> Optional[int]:
        if self.posted_date is None:
            return None
        return (datetime.now().date() - self.posted_date).days
