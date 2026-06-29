"""Default configuration: scoring weights, search defaults, scraper politeness."""

from __future__ import annotations

from dataclasses import dataclass, field


# Default weights for the custom rating. They are normalized at scoring time,
# so the absolute magnitudes do not matter — only their ratios.
DEFAULT_WEIGHTS: dict[str, float] = {
    "price": 0.35,      # cheaper (relative to the result set) scores higher
    "distance": 0.30,   # closer to the user's location scores higher
    "quality": 0.25,    # photos, description length, presence of key specs
    "freshness": 0.10,  # more recently posted scores higher
}

DEFAULT_RADIUS_KM = 150.0
DEFAULT_QUERY = "go kart kart"

# Conservative scraping defaults — be a polite citizen.
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
REQUEST_TIMEOUT = 20  # seconds
RATE_LIMIT_SECONDS = 1.5  # minimum gap between requests to the same source
MAX_RESULTS_PER_SOURCE = 40


@dataclass
class SearchConfig:
    """Resolved configuration for a single search run."""

    location: str
    query: str = DEFAULT_QUERY
    radius_km: float = DEFAULT_RADIUS_KM
    max_price: float | None = None
    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
