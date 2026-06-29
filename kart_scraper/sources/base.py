"""Abstract base class for all source scrapers."""

from __future__ import annotations

import abc
import logging
import time
from typing import Optional

from ..config import RATE_LIMIT_SECONDS
from ..geocode import Coordinates, Geocoder, distance_km
from ..models import Listing

log = logging.getLogger(__name__)


class BaseSource(abc.ABC):
    """A marketplace scraper that returns normalized :class:`Listing` objects.

    Subclasses implement :meth:`fetch`. The public :meth:`search` wraps it with
    error isolation, geocoding/distance enrichment, and ``max_price`` filtering
    so individual scrapers stay focused on parsing their site.
    """

    #: short, CLI-facing identifier (e.g. "ebay").
    name: str = "base"
    #: human-readable label for reports.
    label: str = "Base"

    def __init__(self) -> None:
        self._last_request = 0.0

    @abc.abstractmethod
    def fetch(self, query: str, center: Optional[Coordinates], radius_km: float,
              max_price: Optional[float]) -> list[Listing]:
        """Return raw listings for ``query``. Geocoding is done by the base."""

    def search(
        self,
        query: str,
        center: Optional[Coordinates],
        radius_km: float,
        max_price: Optional[float],
        geocoder: Optional[Geocoder] = None,
    ) -> list[Listing]:
        """Run the scraper safely and enrich results with distance.

        Never raises: a failing/blocked source logs a warning and yields ``[]``
        so one bad source can't sink the whole run.
        """
        try:
            listings = self.fetch(query, center, radius_km, max_price)
        except Exception as exc:  # noqa: BLE001 - isolation is the whole point
            log.warning("Source %r failed: %s", self.name, exc)
            return []

        enriched: list[Listing] = []
        for listing in listings:
            listing.source = listing.source or self.name
            if max_price is not None and listing.price is not None and listing.price > max_price:
                continue
            if geocoder is not None and listing.lat is None and listing.location:
                coords = geocoder.geocode(listing.location)
                if coords:
                    listing.lat, listing.lon = coords
            if listing.lat is not None and center is not None:
                listing.distance_km = distance_km(center, (listing.lat, listing.lon))
            enriched.append(listing)
        log.info("Source %r returned %d listing(s)", self.name, len(enriched))
        return enriched

    def _respect_rate_limit(self) -> None:
        """Sleep so successive requests from this source stay polite."""
        elapsed = time.monotonic() - self._last_request
        if elapsed < RATE_LIMIT_SECONDS:
            time.sleep(RATE_LIMIT_SECONDS - elapsed)
        self._last_request = time.monotonic()
