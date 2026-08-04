"""Defaults for the Geneva apartment search: weights, reference point, limits."""

from __future__ import annotations

# Politeness constants are shared with the kart scraper so both tools behave
# identically toward the sites they visit.
from kart_scraper.config import (  # noqa: F401  (re-exported for sources)
    RATE_LIMIT_SECONDS,
    REQUEST_TIMEOUT,
    USER_AGENT,
)

# Default weights for the rating. Normalized at scoring time, so only the
# ratios matter.
DEFAULT_WEIGHTS: dict[str, float] = {
    "value": 0.35,      # lower CHF/m² (relative to the result set) scores higher
    "size": 0.15,       # more living surface scores higher
    "distance": 0.15,   # closer to the reference address scores higher
    "quality": 0.25,    # photos, description, rooms/surface/floor/year present
    "freshness": 0.10,  # more recently posted scores higher
}

# Reference point distances are measured from (overridable on the CLI, e.g.
# your workplace). Geneva canton fits comfortably in the default radius.
DEFAULT_NEAR = "Genève, Suisse"
DEFAULT_RADIUS_KM = 12.0

MAX_RESULTS_PER_SOURCE = 60
