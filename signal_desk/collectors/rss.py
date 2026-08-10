"""Generic RSS/Atom collector for the feeds listed in the config."""

from __future__ import annotations

from ..feedparse import parse
from ..models import Item
from .base import Collector


class RssCollector(Collector):
    name = "RSS"

    def collect(self) -> list[Item]:
        items: list[Item] = []
        for feed in self.config.feeds:
            try:
                resp = self._get(feed.url)
            except Exception:
                continue  # skip a dead feed, keep the rest
            for entry in parse(resp.content):
                if not entry.title:
                    continue
                items.append(
                    Item(
                        source=feed.name,
                        channel=feed.channel,
                        topic=feed.topic,
                        title=entry.title,
                        url=entry.url,
                        summary=entry.summary,
                        author=entry.author,
                        published=entry.published,
                        meta={"weight": feed.weight},
                    )
                )
        return items
