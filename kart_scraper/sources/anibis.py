"""Anibis scraper (Playwright, best-effort).

Anibis (anibis.ch) is one of Switzerland's largest general classifieds sites —
useful for buyers near the French/Swiss border. It renders results client-side,
so we drive a headless browser. Prices are in CHF (normalized to EUR only for
ranking). Like the other browser sources this is best-effort: if the markup
shifts or we are blocked, the base class degrades it to 0 results.
"""

from __future__ import annotations

import logging
from typing import Optional
from urllib.parse import quote

from bs4 import BeautifulSoup

from ..config import MAX_RESULTS_PER_SOURCE
from ..geocode import Coordinates
from ..models import Listing
from ._browser import browser_page
from ._util import clean_text, parse_price
from .base import BaseSource

log = logging.getLogger(__name__)


class AnibisSource(BaseSource):
    name = "anibis"
    label = "Anibis (CH)"
    base_url = "https://www.anibis.ch/fr/q/"

    def fetch(self, query: str, center: Optional[Coordinates], radius_km: float,
              max_price: Optional[float]) -> list[Listing]:
        url = f"{self.base_url}{quote(query)}"

        with browser_page() as page:
            if page is None:
                return []
            page.goto(url, wait_until="domcontentloaded")
            try:
                page.wait_for_selector(
                    "article, a[href*='/vi/'], [data-testid='listItem']", timeout=8000)
            except Exception:  # noqa: BLE001 - blocked or empty
                log.warning("Anibis: no results rendered (likely blocked).")
                return []
            html = page.content()
        return self.parse(html)

    def parse(self, html: str) -> list[Listing]:
        soup = BeautifulSoup(html, "lxml")
        # Detail pages live under /vi/ ("voir l'annonce"); anchor on those.
        cards = soup.select(
            "[data-testid='listItem'], article:has(a[href*='/vi/']), a[href*='/vi/']")
        listings: list[Listing] = []
        seen: set[str] = set()
        for card in cards:
            link = card if card.name == "a" else card.select_one("a[href*='/vi/']")
            if link is None:
                continue
            href = link.get("href", "")
            if href.startswith("/"):
                href = "https://www.anibis.ch" + href
            if not href or href in seen:
                continue

            title = clean_text(
                self._text(card, "h2, h3, [data-testid='title']")
                or link.get("title", "")
                or link.get_text())
            if not title:
                continue
            price = parse_price(self._text(card, "[data-testid='price'], .price, [class*='rice']"))
            location = self._text(card, "[data-testid='location'], [class*='ocation']") or None

            seen.add(href)
            listings.append(
                Listing(
                    title=title,
                    url=href,
                    source=self.name,
                    price=price,
                    currency="CHF",
                    location=location,
                    image_count=len(card.select("img")),
                )
            )
            if len(listings) >= MAX_RESULTS_PER_SOURCE:
                break
        return listings

    @staticmethod
    def _text(card, selector: str) -> str:
        el = card.select_one(selector)
        return clean_text(el.get_text()) if el else ""
