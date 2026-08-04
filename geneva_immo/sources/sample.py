"""Offline fixture source.

Returns a fixed set of realistic Geneva listings bundled with the package so
the full pipeline — geocoding, scoring, ranking, reporting — can be
demonstrated and tested without any network access or risk of being blocked.
Selected with ``--source sample``.
"""

from __future__ import annotations

import json
from datetime import datetime
from importlib import resources
from typing import Optional

from ..models import ApartmentListing
from .base import BaseSource


class SampleSource(BaseSource):
    name = "sample"
    label = "Sample (offline)"

    def fetch(self, max_price: Optional[float]) -> list[ApartmentListing]:
        raw = resources.files("geneva_immo.data").joinpath("sample_listings.json")
        records = json.loads(raw.read_text(encoding="utf-8"))
        listings: list[ApartmentListing] = []
        for rec in records:
            posted = rec.get("posted_date")
            listings.append(
                ApartmentListing(
                    title=rec["title"],
                    url=rec["url"],
                    source=self.name,
                    price=rec.get("price"),
                    currency=rec.get("currency", "CHF"),
                    location=rec.get("location"),
                    rooms=rec.get("rooms"),
                    surface_m2=rec.get("surface_m2"),
                    floor=rec.get("floor"),
                    year_built=rec.get("year_built"),
                    posted_date=datetime.strptime(posted, "%Y-%m-%d").date()
                    if posted else None,
                    image_count=rec.get("image_count", 0),
                    description=rec.get("description", ""),
                    features=rec.get("features", {}),
                )
            )
        return listings
