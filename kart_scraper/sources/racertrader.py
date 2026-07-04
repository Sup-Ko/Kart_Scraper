"""Racer Trader scraper (Playwright, best-effort).

Racer Trader (racertrader.com) is a specialized marketplace for new and used
race karts (Tony Kart, Exprit, Synergy, etc.), so its listings are far more
on-target than a general classifieds site. Prices are in GBP (normalized to EUR
only for ranking). Best-effort: degrades to 0 results if blocked or if the
markup changes.
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


class RacerTraderSource(BaseSource):
    name = "racertrader"
    label = "Racer Trader (karts)"
    base_url = "https://www.racertrader.com"

    def fetch(self, query: str, center: Optional[Coordinates], radius_km: float,
              max_price: Optional[float]) -> list[Listing]:
        # Dedicated karts category, with the query applied as a keyword filter.
        url = f"{self.base_url}/categories/karts/?q={quote(query)}"

        with browser_page() as page:
            if page is None:
                return []
            page.goto(url, wait_until="domcontentloaded")
            try:
                page.wait_for_selector(
                    ".listing, .advert, article, a[href*='/listing']", timeout=8000)
            except Exception:  # noqa: BLE001
                log.warning("Racer Trader: no results rendered (likely blocked).")
                return []
            html = page.content()
        return self.parse(html)

    def parse(self, html: str) -> list[Listing]:
        soup = BeautifulSoup(html, "lxml")
        cards = soup.select(
            ".listing, .advert, .product, article:has(a[href*='/listing'])")
        listings: list[Listing] = []
        seen: set[str] = set()
        for card in cards:
            link = card.select_one("a[href*='/listing'], a[href]")
            if link is None:
                continue
            href = link.get("href", "")
            if href.startswith("/"):
                href = self.base_url + href
            if not href or href in seen:
                continue
            title = clean_text(
                self._text(card, "h2, h3, .title, .listing-title")
                or link.get_text())
            if not title:
                continue
            price = parse_price(self._text(card, ".price, [class*='rice']"))
            location = self._text(card, ".location, [class*='ocation'], .seller-location") or None

            seen.add(href)
            listings.append(
                Listing(
                    title=title,
                    url=href,
                    source=self.name,
                    price=price,
                    currency="GBP",
                    location=location,
                    image_count=len(card.select("img")),
                    description=self._text(card, ".description, .summary"),
                )
            )
            if len(listings) >= MAX_RESULTS_PER_SOURCE:
                break
        return listings

    @staticmethod
    def _text(card, selector: str) -> str:
        el = card.select_one(selector)
        return clean_text(el.get_text()) if el else ""
