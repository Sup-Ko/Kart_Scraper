"""Normalized data model shared by every property source scraper."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional


@dataclass
class ApartmentListing:
    """A single apartment-for-sale listing, normalized across all sources.

    Prices are in CHF (every supported portal is Swiss). ``rooms`` uses the
    Swiss "pièces" convention (kitchen/living count, half-rooms allowed, e.g.
    3.5). ``lat``/``lon`` and ``distance_km`` are filled in later by the
    geocoding step; ``score``/``subscores`` by the scoring step.
    """

    title: str
    url: str
    source: str
    price: Optional[float] = None
    currency: str = "CHF"
    location: Optional[str] = None
    rooms: Optional[float] = None
    surface_m2: Optional[float] = None
    floor: Optional[str] = None
    year_built: Optional[int] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    posted_date: Optional[date] = None
    image_count: int = 0
    description: str = ""
    # Free-form extracted features, e.g. {"balcony": "yes", "parking": "1 box"}.
    features: dict[str, str] = field(default_factory=dict)

    # Filled in by later pipeline stages.
    distance_km: Optional[float] = None
    score: Optional[float] = None
    subscores: dict[str, float] = field(default_factory=dict)

    @property
    def price_per_m2(self) -> Optional[float]:
        """CHF per m² of living surface — the core value metric in Geneva."""
        if self.price is None or not self.surface_m2:
            return None
        return self.price / self.surface_m2

    @property
    def fingerprint(self) -> str:
        """Stable id used to dedupe listings seen across multiple portals."""
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
