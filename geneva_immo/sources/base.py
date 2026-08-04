"""Abstract base class for all property source scrapers."""

from __future__ import annotations

import abc
import logging
import time
from typing import Optional

from kart_scraper.geocode import Coordinates, Geocoder, distance_km

from ..config import RATE_LIMIT_SECONDS
from ..models import ApartmentListing

log = logging.getLogger(__name__)


class BaseSource(abc.ABC):
    """A property portal scraper that returns normalized listings.

    Subclasses implement :meth:`fetch`. The public :meth:`search` wraps it with
    error isolation, geocoding/distance enrichment, and price/rooms/surface
    filtering so individual scrapers stay focused on parsing their site.
    """

    #: short, CLI-facing identifier (e.g. "homegate").
    name: str = "base"
    #: human-readable label for reports.
    label: str = "Base"

    def __init__(self) -> None:
        self._last_request = 0.0

    @abc.abstractmethod
    def fetch(self, max_price: Optional[float]) -> list[ApartmentListing]:
        """Return raw Geneva listings. Geocoding/filtering is done by the base."""

    def search(
        self,
        center: Optional[Coordinates],
        radius_km: float,
        max_price: Optional[float] = None,
        min_rooms: Optional[float] = None,
        min_surface: Optional[float] = None,
        geocoder: Optional[Geocoder] = None,
    ) -> list[ApartmentListing]:
        """Run the scraper safely, filter, and enrich results with distance.

        Never raises: a failing/blocked source logs a warning and yields ``[]``
        so one bad portal can't sink the whole run.
        """
        try:
            listings = self.fetch(max_price)
        except Exception as exc:  # noqa: BLE001 - isolation is the whole point
            log.warning("Source %r failed: %s", self.name, exc)
            return []

        enriched: list[ApartmentListing] = []
        for listing in listings:
            listing.source = listing.source or self.name
            if max_price is not None and listing.price is not None and listing.price > max_price:
                continue
            if min_rooms is not None and listing.rooms is not None and listing.rooms < min_rooms:
                continue
            if (min_surface is not None and listing.surface_m2 is not None
                    and listing.surface_m2 < min_surface):
                continue
            if geocoder is not None and listing.lat is None and listing.location:
                coords = geocoder.geocode(self._geocodable(listing.location))
                if coords:
                    listing.lat, listing.lon = coords
            if listing.lat is not None and center is not None:
                listing.distance_km = distance_km(center, (listing.lat, listing.lon))
            enriched.append(listing)
        log.info("Source %r returned %d listing(s)", self.name, len(enriched))
        return enriched

    @staticmethod
    def _geocodable(location: str) -> str:
        """Anchor bare neighbourhood names to Geneva so Nominatim resolves them
        locally instead of matching a same-named place elsewhere."""
        lowered = location.lower()
        if "genève" in lowered or "geneve" in lowered or "geneva" in lowered:
            return f"{location}, Suisse"
        return f"{location}, Genève, Suisse"

    def _respect_rate_limit(self) -> None:
        """Sleep so successive requests from this source stay polite."""
        elapsed = time.monotonic() - self._last_request
        if elapsed < RATE_LIMIT_SECONDS:
            time.sleep(RATE_LIMIT_SECONDS - elapsed)
        self._last_request = time.monotonic()
