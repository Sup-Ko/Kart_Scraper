"""Collectors turn configured sources into :class:`~signal_desk.models.Item`s.

Each collector is legal-by-construction: it reads public feeds, public search
feeds, or your own accounts — never anyone else's private data.
"""

from __future__ import annotations

from ..config import Config
from ..models import Item
from .base import Collector
from .googlenews import GoogleNewsCollector
from .govawards import GovAwardsCollector
from .hibp import HibpCollector
from .rss import RssCollector


def build_collectors(config: Config) -> list[Collector]:
    """Assemble the collectors that apply to the given config."""
    collectors: list[Collector] = [
        RssCollector(config),
        GoogleNewsCollector(config),
        GovAwardsCollector(config),
    ]
    if config.hibp_api_key and config.self.emails:
        collectors.append(HibpCollector(config))
    return collectors


def collect_all(config: Config) -> tuple[list[Item], list[str]]:
    """Run every applicable collector. Returns (items, per-collector errors)."""
    items: list[Item] = []
    errors: list[str] = []
    for collector in build_collectors(config):
        try:
            items.extend(collector.collect())
        except Exception as exc:  # a bad source must not sink the run
            errors.append(f"{collector.name}: {exc}")
    return items, errors
