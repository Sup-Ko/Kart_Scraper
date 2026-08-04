"""Weighted rating for apartment listings.

Each listing gets five sub-scores in [0, 1] — value (CHF/m²), size, distance,
quality and freshness — normalized *relative to the current result set*, then
combined with configurable weights into a final 0–100 score. Scoring relative
to the set means "good value" and "near" are judged against the other
apartments actually on the market, which is what a buyer comparing options
cares about.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional

from .models import ApartmentListing

WEIGHT_KEYS = ("value", "size", "distance", "quality", "freshness")


def _normalize_lower_better(value: Optional[float], values: list[Optional[float]]) -> float:
    """Map a value to [0, 1] where the lowest value in the set scores 1.0."""
    present = [v for v in values if v is not None]
    if value is None or not present:
        return 0.5  # neutral when we cannot compare
    lo, hi = min(present), max(present)
    if hi == lo:
        return 1.0
    return 1.0 - (value - lo) / (hi - lo)


def _normalize_higher_better(value: Optional[float], values: list[Optional[float]]) -> float:
    """Map a value to [0, 1] where the highest value in the set scores 1.0."""
    present = [v for v in values if v is not None]
    if value is None or not present:
        return 0.5
    lo, hi = min(present), max(present)
    if hi == lo:
        return 1.0
    return (value - lo) / (hi - lo)


def _quality_score(listing: ApartmentListing) -> float:
    """Heuristic 0..1 for how complete/credible a listing looks."""
    score = 0.0
    # Photos (up to 0.35): more images, more confidence.
    score += min(listing.image_count, 5) / 5 * 0.35
    # Description length (up to 0.25).
    desc_len = len(listing.description or "")
    score += min(desc_len, 400) / 400 * 0.25
    # Hard facts present (up to 0.4): rooms, surface, floor/year/features.
    facts = 0
    if listing.rooms is not None:
        facts += 1
    if listing.surface_m2 is not None:
        facts += 1
    if listing.floor is not None or listing.year_built is not None or listing.features:
        facts += 1
    score += facts / 3 * 0.4
    return round(min(score, 1.0), 4)


def _freshness_score(listing: ApartmentListing) -> float:
    """0..1 where a just-posted listing scores 1.0, decaying over ~90 days.

    Property listings move slower than second-hand goods, hence the longer
    decay window than the kart scorer's 60 days.
    """
    age = listing.age_days
    if age is None:
        return 0.5
    return max(0.0, 1.0 - min(age, 90) / 90)


def score_listings(
    listings: Iterable[ApartmentListing],
    weights: dict[str, float],
    radius_km: Optional[float] = None,
) -> list[ApartmentListing]:
    """Compute sub-scores + final score for every listing, in place.

    Returns the same listings sorted by descending final score.
    """
    items = list(listings)
    if not items:
        return items

    ppm2 = [l.price_per_m2 for l in items]
    surfaces = [l.surface_m2 for l in items]
    distances = [l.distance_km for l in items]

    total_weight = sum(weights.values()) or 1.0

    for listing in items:
        value_s = _normalize_lower_better(listing.price_per_m2, ppm2)
        size_s = _normalize_higher_better(listing.surface_m2, surfaces)
        distance_s = _normalize_lower_better(listing.distance_km, distances)
        # Hard penalty for listings known to be outside the requested radius.
        if radius_km is not None and listing.distance_km is not None:
            if listing.distance_km > radius_km:
                distance_s = 0.0
        quality_s = _quality_score(listing)
        freshness_s = _freshness_score(listing)

        listing.subscores = {
            "value": round(value_s, 4),
            "size": round(size_s, 4),
            "distance": round(distance_s, 4),
            "quality": round(quality_s, 4),
            "freshness": round(freshness_s, 4),
        }
        weighted = sum(weights.get(k, 0) * listing.subscores[k] for k in WEIGHT_KEYS)
        listing.score = round(weighted / total_weight * 100, 1)

    items.sort(key=lambda l: (l.score is not None, l.score), reverse=True)
    return items


def parse_weights(spec: str) -> dict[str, float]:
    """Parse a CLI weight string like ``value=0.5,size=0.2,quality=0.3``."""
    weights: dict[str, float] = {}
    for part in re.split(r"[,\s]+", spec.strip()):
        if not part:
            continue
        key, _, value = part.partition("=")
        key = key.strip().lower()
        if key not in WEIGHT_KEYS:
            raise ValueError(f"Unknown weight key: {key!r}")
        weights[key] = float(value)
    return weights
