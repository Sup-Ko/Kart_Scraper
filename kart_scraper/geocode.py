"""Geocoding (OpenStreetMap Nominatim) and distance helpers.

Nominatim is free and needs no API key, but asks callers to send a real
User-Agent and stay under ~1 request/second. We cache every lookup on disk so
repeated runs (and repeated listing locations) cost nothing.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Optional

from geopy.distance import geodesic
from geopy.geocoders import Nominatim

from . import __version__

log = logging.getLogger(__name__)

Coordinates = tuple[float, float]

_CACHE_PATH = Path.home() / ".cache" / "kart_scraper" / "geocode.json"
_USER_AGENT = f"kart_scraper/{__version__} (+https://github.com/sup-ko/kart_scraper)"
_MIN_INTERVAL = 1.1  # seconds between live Nominatim calls


class Geocoder:
    """Caching wrapper around Nominatim with great-circle distances."""

    def __init__(self, cache_path: Path = _CACHE_PATH):
        self.cache_path = cache_path
        self._cache = self._load_cache()
        self._geolocator = Nominatim(user_agent=_USER_AGENT)
        self._lock = threading.Lock()
        self._last_call = 0.0

    def _load_cache(self) -> dict[str, Optional[list[float]]]:
        try:
            return json.loads(self.cache_path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save_cache(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(self._cache))

    def geocode(self, place: Optional[str]) -> Optional[Coordinates]:
        """Resolve a free-text place to ``(lat, lon)``, or ``None``.

        Results (including failures) are cached so the same string is never
        looked up twice.
        """
        if not place or not place.strip():
            return None
        key = place.strip().lower()
        if key in self._cache:
            cached = self._cache[key]
            return tuple(cached) if cached else None  # type: ignore[return-value]

        coords: Optional[Coordinates] = None
        with self._lock:
            self._respect_rate_limit()
            try:
                loc = self._geolocator.geocode(place, timeout=15)
                if loc is not None:
                    coords = (loc.latitude, loc.longitude)
            except Exception as exc:  # network / service errors are non-fatal
                log.warning("Geocoding failed for %r: %s", place, exc)
                return None

        self._cache[key] = list(coords) if coords else None
        self._save_cache()
        return coords

    def _respect_rate_limit(self) -> None:
        elapsed = time.monotonic() - self._last_call
        if elapsed < _MIN_INTERVAL:
            time.sleep(_MIN_INTERVAL - elapsed)
        self._last_call = time.monotonic()


def distance_km(a: Optional[Coordinates], b: Optional[Coordinates]) -> Optional[float]:
    """Great-circle distance in km between two ``(lat, lon)`` points."""
    if a is None or b is None:
        return None
    return geodesic(a, b).km
