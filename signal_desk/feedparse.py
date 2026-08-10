"""A tiny, dependency-free RSS 2.0 / Atom feed parser.

Real-world feeds are messier than the specs, but the common shapes (RSS 2.0
``<item>`` and Atom ``<entry>``) cover the overwhelming majority of sources.
Parsing with the standard library keeps Signal Desk installable and unit
testable offline, with no reliance on a third-party feed library.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

_ATOM = "{http://www.w3.org/2005/Atom}"


@dataclass
class FeedEntry:
    title: str
    url: str
    summary: str
    author: str
    published: datetime | None


def _text(el) -> str:
    if el is None:
        return ""
    return "".join(el.itertext()).strip()


_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(value: str) -> str:
    return _TAG_RE.sub("", value).strip()


def _parse_date(value: str) -> datetime | None:
    value = (value or "").strip()
    if not value:
        return None
    # RFC 822 (RSS): "Wed, 02 Oct 2024 13:00:00 GMT"
    try:
        dt = parsedate_to_datetime(value)
        if dt is not None:
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        pass
    # ISO 8601 (Atom): "2024-10-02T13:00:00Z"
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def parse(content: str | bytes) -> list[FeedEntry]:
    """Parse feed ``content`` into a list of :class:`FeedEntry`.

    Returns an empty list if the payload is not well-formed XML rather than
    raising — a flaky source should never take the whole collection run down.
    """
    if isinstance(content, bytes):
        content = content.decode("utf-8", errors="replace")
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return []

    entries: list[FeedEntry] = []

    # RSS 2.0: <rss><channel><item>...
    for item in root.iter("item"):
        entries.append(
            FeedEntry(
                title=_strip_html(_text(item.find("title"))),
                url=_text(item.find("link")),
                summary=_strip_html(_text(item.find("description"))),
                author=_text(item.find("author")) or _text(item.find("{http://purl.org/dc/elements/1.1/}creator")),
                published=_parse_date(_text(item.find("pubDate"))),
            )
        )

    # Atom: <feed><entry>...
    for entry in root.iter(f"{_ATOM}entry"):
        link = ""
        for link_el in entry.findall(f"{_ATOM}link"):
            rel = link_el.get("rel", "alternate")
            if rel == "alternate" or not link:
                link = link_el.get("href", "") or link
        author_el = entry.find(f"{_ATOM}author")
        author = _text(author_el.find(f"{_ATOM}name")) if author_el is not None else ""
        published = _parse_date(
            _text(entry.find(f"{_ATOM}published")) or _text(entry.find(f"{_ATOM}updated"))
        )
        summary = _text(entry.find(f"{_ATOM}summary")) or _text(entry.find(f"{_ATOM}content"))
        entries.append(
            FeedEntry(
                title=_strip_html(_text(entry.find(f"{_ATOM}title"))),
                url=link,
                summary=_strip_html(summary),
                author=author,
                published=published,
            )
        )

    return entries
