"""Core data types for Signal Desk."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Item:
    """A single piece of collected information.

    An item belongs to a ``channel`` — either ``"self"`` (information about you,
    e.g. a news mention of your name or a breach of your email) or ``"topic"``
    (public information about a subject you track). ``topic`` names the specific
    subject or, for self items, what was matched (your name, an email, ...).
    """

    source: str  # human name of the collector/source, e.g. "Google News"
    channel: str  # "self" or "topic"
    topic: str  # subject label or the self-identifier that matched
    title: str
    url: str
    summary: str = ""
    author: str = ""
    published: datetime | None = None
    collected_at: datetime = field(default_factory=_now)
    heat: float = 0.0
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def id(self) -> str:
        """Stable identity used for de-duplication.

        Keyed on the URL when present (the natural unique handle for a feed
        item); otherwise on source + title so entries without links still
        de-dupe sensibly.
        """
        basis = self.url.strip() or f"{self.source}:{self.title}".strip()
        return hashlib.sha1(basis.encode("utf-8")).hexdigest()

    def to_row(self) -> dict[str, Any]:
        row = asdict(self)
        row.pop("meta", None)
        row["id"] = self.id
        row["published"] = self.published.isoformat() if self.published else None
        row["collected_at"] = self.collected_at.isoformat()
        import json

        row["meta"] = json.dumps(self.meta, default=str)
        return row
