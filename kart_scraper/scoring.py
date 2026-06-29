"""Custom weighted rating for kart listings.

Each listing gets four sub-scores in [0, 1] — price, distance, quality and
freshness — normalized *relative to the current result set*, then combined with
configurable weights into a final 0–100 score. Scoring relative to the set
means "cheap" and "near" are judged against the other karts actually found,
which is what a buyer comparing options cares about.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional

from .models import Listing

# Spec keywords that signal a richer, more trustworthy listing.
_SPEC_KEYWORDS = ("year", "annee", "année", "engine", "moteur", "rotax", "iame",
                  "honda", "chassis", "châssis", "cc", "tony", "crg", "birel")


def _normalize_lower_better(value: Optional[float], values: list[float]) -> float:
    """Map a value to [0, 1] where the lowest value in the set scores 1.0."""
    present = [v for v in values if v is not None]
    if value is None or not present:
        return 0.5  # neutral when we cannot compare
    lo, hi = min(present), max(present)
    if hi == lo:
        return 1.0
    return 1.0 - (value - lo) / (hi - lo)


def _quality_score(listing: Listing) -> float:
    """Heuristic 0..1 for how complete/credible a listing looks."""
    score = 0.0
    # Photos (up to 0.4): more images, more confidence.
    score += min(listing.image_count, 4) / 4 * 0.4
    # Description length (up to 0.3).
    desc_len = len(listing.description or "")
    score += min(desc_len, 400) / 400 * 0.3
    # Presence of meaningful specs (up to 0.3).
    haystack = f"{listing.title} {listing.description} {' '.join(listing.specs)}".lower()
    hits = sum(1 for kw in _SPEC_KEYWORDS if kw in haystack)
    score += min(hits, 3) / 3 * 0.3
    return round(min(score, 1.0), 4)


def _freshness_score(listing: Listing) -> float:
    """0..1 where a just-posted listing scores 1.0, decaying over ~60 days."""
    age = listing.age_days
    if age is None:
        return 0.5
    return max(0.0, 1.0 - min(age, 60) / 60)


def score_listings(
    listings: Iterable[Listing],
    weights: dict[str, float],
    radius_km: Optional[float] = None,
) -> list[Listing]:
    """Compute sub-scores + final score for every listing, in place.

    Returns the same listings sorted by descending final score.
    """
    items = list(listings)
    if not items:
        return items

    prices = [l.price for l in items]
    distances = [l.distance_km for l in items]

    total_weight = sum(weights.values()) or 1.0

    for listing in items:
        price_s = _normalize_lower_better(listing.price, prices)
        distance_s = _normalize_lower_better(listing.distance_km, distances)
        # Hard penalty for listings known to be outside the requested radius.
        if radius_km is not None and listing.distance_km is not None:
            if listing.distance_km > radius_km:
                distance_s = 0.0
        quality_s = _quality_score(listing)
        freshness_s = _freshness_score(listing)

        listing.subscores = {
            "price": round(price_s, 4),
            "distance": round(distance_s, 4),
            "quality": round(quality_s, 4),
            "freshness": round(freshness_s, 4),
        }
        weighted = (
            weights.get("price", 0) * price_s
            + weights.get("distance", 0) * distance_s
            + weights.get("quality", 0) * quality_s
            + weights.get("freshness", 0) * freshness_s
        )
        listing.score = round(weighted / total_weight * 100, 1)

    items.sort(key=lambda l: (l.score is not None, l.score), reverse=True)
    return items


def parse_weights(spec: str) -> dict[str, float]:
    """Parse a CLI weight string like ``price=0.5,distance=0.3,quality=0.2``."""
    weights: dict[str, float] = {}
    for part in re.split(r"[,\s]+", spec.strip()):
        if not part:
            continue
        key, _, value = part.partition("=")
        key = key.strip().lower()
        if key not in ("price", "distance", "quality", "freshness"):
            raise ValueError(f"Unknown weight key: {key!r}")
        weights[key] = float(value)
    return weights
