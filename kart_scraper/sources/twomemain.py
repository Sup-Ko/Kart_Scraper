"""2dehands / 2ememain / Marktplaats scraper (Playwright, best-effort).

This is the Adevinta classifieds family covering Belgium and the Netherlands —
a good complement to Leboncoin for buyers near the French/Benelux border. Like
the other browser sources it is best-effort and degrades to 0 results if
blocked or if the markup shifts.
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


class TwoMeMainSource(BaseSource):
    name = "2ememain"
    label = "2ememain"
    base_url = "https://www.2ememain.be/q/"

    def fetch(self, query: str, center: Optional[Coordinates], radius_km: float,
              max_price: Optional[float]) -> list[Listing]:
        url = f"{self.base_url}{quote(query)}/"

        with browser_page() as page:
            if page is None:
                return []
            page.goto(url, wait_until="domcontentloaded")
            try:
                page.wait_for_selector("li.hz-Listing, article", timeout=8000)
            except Exception:  # noqa: BLE001
                log.warning("2ememain: no results rendered (likely blocked).")
                return []
            html = page.content()
        return self.parse(html)

    def parse(self, html: str) -> list[Listing]:
        soup = BeautifulSoup(html, "lxml")
        cards = soup.select("li.hz-Listing, article.hz-Listing")
        listings: list[Listing] = []
        for card in cards:
            title_el = card.select_one(".hz-Listing-title, h3")
            link = card.select_one("a[href]")
            if not title_el or not link:
                continue
            href = link.get("href", "")
            if href.startswith("/"):
                href = "https://www.2ememain.be" + href
            price = parse_price(self._text(card, ".hz-Listing-price"))
            location = self._text(card, ".hz-Listing-location, .hz-Listing-distance-label") or None
            listings.append(
                Listing(
                    title=clean_text(title_el.get_text()),
                    url=href,
                    source=self.name,
                    price=price,
                    location=location,
                    image_count=len(card.select("img")),
                    description=self._text(card, ".hz-Listing-description"),
                )
            )
            if len(listings) >= MAX_RESULTS_PER_SOURCE:
                break
        return listings

    @staticmethod
    def _text(card, selector: str) -> str:
        el = card.select_one(selector)
        return clean_text(el.get_text()) if el else ""
