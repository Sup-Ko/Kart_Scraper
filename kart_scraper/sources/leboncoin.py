"""Leboncoin scraper (Playwright, best-effort).

Leboncoin is France's dominant classifieds site and a prime source for karts
for sale, but it is protected by DataDome and frequently blocks automated
traffic. We drive a real headless browser and parse whatever renders; if we are
blocked, the base class turns the resulting empty/failed fetch into a graceful
"0 listings" instead of crashing the run.
"""

from __future__ import annotations

import logging
from typing import Optional
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from ..config import MAX_RESULTS_PER_SOURCE
from ..geocode import Coordinates
from ..models import Listing
from ._browser import browser_page
from ._util import clean_text, parse_price
from .base import BaseSource

log = logging.getLogger(__name__)


class LeboncoinSource(BaseSource):
    name = "leboncoin"
    label = "Leboncoin"
    base_url = "https://www.leboncoin.fr/recherche"

    def fetch(self, query: str, center: Optional[Coordinates], radius_km: float,
              max_price: Optional[float]) -> list[Listing]:
        params = {"text": query}
        if max_price is not None:
            params["price"] = f"min-{int(max_price)}"
        url = f"{self.base_url}?{urlencode(params)}"

        with browser_page() as page:
            if page is None:
                return []
            page.goto(url, wait_until="domcontentloaded")
            try:
                page.wait_for_selector("[data-test-id='ad'], a[data-qa-id='aditem_container']",
                                       timeout=8000)
            except Exception:  # noqa: BLE001 - blocked or empty
                log.warning("Leboncoin: no results rendered (likely blocked).")
                return []
            html = page.content()
        return self.parse(html)

    def parse(self, html: str) -> list[Listing]:
        soup = BeautifulSoup(html, "lxml")
        cards = soup.select("a[data-qa-id='aditem_container'], a[data-test-id='ad']")
        listings: list[Listing] = []
        for card in cards:
            href = card.get("href", "")
            if href.startswith("/"):
                href = "https://www.leboncoin.fr" + href
            title = clean_text(self._attr_text(card, "[data-qa-id='aditem_title']")
                               or card.get("title", ""))
            if not title or not href:
                continue
            price = parse_price(self._attr_text(card, "[data-qa-id='aditem_price']"))
            location = self._attr_text(card, "[data-qa-id='aditem_location']") or None
            image_count = len(card.select("img"))
            listings.append(
                Listing(
                    title=title,
                    url=href,
                    source=self.name,
                    price=price,
                    location=location,
                    image_count=image_count,
                )
            )
            if len(listings) >= MAX_RESULTS_PER_SOURCE:
                break
        return listings

    @staticmethod
    def _attr_text(card, selector: str) -> str:
        el = card.select_one(selector)
        return clean_text(el.get_text()) if el else ""
