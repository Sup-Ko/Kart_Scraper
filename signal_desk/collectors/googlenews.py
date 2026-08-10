"""Collect from Google News RSS search.

Google News exposes a public RSS endpoint for arbitrary search queries:

    https://news.google.com/rss/search?q=<query>

This one mechanism serves both channels:

* ``self``  — a query per configured name / handle, so you see what the public
  web is saying about *you*.
* ``topic`` — a query per configured topic (its keywords OR'd together).
"""

from __future__ import annotations

from urllib.parse import quote_plus

from ..feedparse import parse
from ..models import Item
from .base import Collector

_BASE = "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"


class GoogleNewsCollector(Collector):
    name = "Google News"

    def _query(self, query: str) -> list:
        url = _BASE.format(q=quote_plus(query))
        try:
            resp = self._get(url)
        except Exception:
            return []
        return parse(resp.content)

    def collect(self) -> list[Item]:
        items: list[Item] = []

        # self-footprint: exact-phrase search for each identity you own
        identities = [*self.config.self.names, *self.config.self.handles]
        for identity in identities:
            phrase = f'"{identity}"'
            for entry in self._query(phrase):
                if not entry.title:
                    continue
                items.append(
                    Item(
                        source=self.name,
                        channel="self",
                        topic=identity,
                        title=entry.title,
                        url=entry.url,
                        summary=entry.summary,
                        author=entry.author,
                        published=entry.published,
                        meta={"weight": 1.0, "query": phrase},
                    )
                )

        # topics: OR the keywords (fall back to the topic name)
        for topic in self.config.topics:
            terms = topic.keywords or [topic.name]
            query = " OR ".join(f'"{t}"' for t in terms)
            for entry in self._query(query):
                if not entry.title:
                    continue
                items.append(
                    Item(
                        source=self.name,
                        channel="topic",
                        topic=topic.name,
                        title=entry.title,
                        url=entry.url,
                        summary=entry.summary,
                        author=entry.author,
                        published=entry.published,
                        meta={"weight": topic.weight, "query": query},
                    )
                )

        return items
