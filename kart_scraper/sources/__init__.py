"""Pluggable source scrapers.

Each source subclasses :class:`~kart_scraper.sources.base.BaseSource` and is
registered here by name. The CLI selects sources with ``--source`` (or ``all``).
Sources are intentionally isolated: if one raises or gets blocked, the run
continues with whatever the others returned.
"""

from __future__ import annotations

from .base import BaseSource
from .ebay import EbaySource
from .google import GoogleSource
from .leboncoin import LeboncoinSource
from .sample import SampleSource
from .twomemain import TwoMeMainSource

# Registry of selectable sources, in default run order.
SOURCES: dict[str, type[BaseSource]] = {
    EbaySource.name: EbaySource,
    LeboncoinSource.name: LeboncoinSource,
    GoogleSource.name: GoogleSource,
    TwoMeMainSource.name: TwoMeMainSource,
    SampleSource.name: SampleSource,
}

# "all" runs every real (network) source; the offline sample is opt-in only.
LIVE_SOURCES = [name for name in SOURCES if name != SampleSource.name]


def get_sources(selection: list[str]) -> list[BaseSource]:
    """Instantiate sources for a CLI selection (names or the keyword ``all``)."""
    names: list[str] = []
    for item in selection:
        if item == "all":
            names.extend(LIVE_SOURCES)
        elif item in SOURCES:
            names.append(item)
        else:
            raise ValueError(
                f"Unknown source {item!r}. Choose from: "
                f"{', '.join(SOURCES)} or 'all'."
            )
    # De-duplicate while preserving order.
    seen: set[str] = set()
    ordered = [n for n in names if not (n in seen or seen.add(n))]
    return [SOURCES[n]() for n in ordered]


__all__ = ["BaseSource", "SOURCES", "LIVE_SOURCES", "get_sources"]
