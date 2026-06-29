"""Offline fixture source.

Returns a fixed set of realistic listings bundled with the package so the full
pipeline — geocoding, scoring, ranking, reporting — can be demonstrated and
tested without any network access or risk of being blocked. Selected with
``--source sample``.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from importlib import resources
from typing import Optional

from ..geocode import Coordinates
from ..models import Listing
from .base import BaseSource


class SampleSource(BaseSource):
    name = "sample"
    label = "Sample (offline)"

    def fetch(self, query: str, center: Optional[Coordinates], radius_km: float,
              max_price: Optional[float]) -> list[Listing]:
        raw = resources.files("kart_scraper.data").joinpath("sample_listings.json")
        records = json.loads(raw.read_text(encoding="utf-8"))
        listings: list[Listing] = []
        for rec in records:
            posted = rec.get("posted_date")
            listings.append(
                Listing(
                    title=rec["title"],
                    url=rec["url"],
                    source=self.name,
                    price=rec.get("price"),
                    currency=rec.get("currency", "EUR"),
                    location=rec.get("location"),
                    posted_date=datetime.strptime(posted, "%Y-%m-%d").date()
                    if posted else None,
                    image_count=rec.get("image_count", 0),
                    description=rec.get("description", ""),
                    specs=rec.get("specs", {}),
                )
            )
        return listings
