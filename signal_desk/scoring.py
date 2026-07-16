"""Heat scoring: turn recency, source, and content into a 0-100 importance."""

from __future__ import annotations

from datetime import datetime, timezone

from .config import Config
from .models import Item


def _recency_factor(published: datetime | None, half_life_hours: float, now: datetime) -> float:
    """Exponential decay in [0, 1]; 1.0 for "now", 0.5 after one half-life."""
    if published is None:
        return 0.4  # unknown age: assume moderately stale
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    age_hours = max(0.0, (now - published).total_seconds() / 3600.0)
    if half_life_hours <= 0:
        return 1.0
    return 0.5 ** (age_hours / half_life_hours)


def score_item(item: Item, config: Config, now: datetime | None = None) -> float:
    """Compute and assign ``item.heat`` in the range [0, 100].

    heat = 100 * recency * source_weight * channel_weight * priority_boost,
    where each multiplier is capped so no single factor dominates.
    """
    now = now or datetime.now(timezone.utc)
    s = config.scoring

    recency = _recency_factor(item.published, s.recency_half_life_hours, now)

    source_weight = float(item.meta.get("weight", 1.0))

    channel_weight = s.self_weight if item.channel == "self" else 1.0

    haystack = f"{item.title}\n{item.summary}".lower()
    priority = s.priority_boost if any(
        kw.lower() in haystack for kw in config.priority_keywords
    ) else 1.0

    raw = recency * source_weight * channel_weight * priority
    heat = 100.0 * raw / (s.self_weight * s.priority_boost)  # normalise by max multipliers
    item.heat = round(min(100.0, max(0.0, heat)), 1)
    return item.heat


def heat_band(heat: float) -> str:
    """Bucket a heat score into a label used by the dashboard heat map."""
    if heat >= 70:
        return "hot"
    if heat >= 45:
        return "warm"
    if heat >= 20:
        return "cool"
    return "cold"
