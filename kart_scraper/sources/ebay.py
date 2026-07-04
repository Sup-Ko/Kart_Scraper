"""eBay scraper (requests + BeautifulSoup).

eBay's search results render server-side and expose the item location, which
makes it the most reliably scrapable source here. We hit the regional site,
parse the result cards, and let the base class handle geocoding + distance.
"""

from __future__ import annotations

import logging
from typing import Optional
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup

from ..config import MAX_RESULTS_PER_SOURCE, REQUEST_TIMEOUT, USER_AGENT
from ..geocode import Coordinates
from ..models import Listing
from ._util import clean_text, parse_price
from .base import BaseSource

log = logging.getLogger(__name__)


class EbaySource(BaseSource):
    name = "ebay"
    label = "eBay"
    base_url = "https://www.ebay.fr/sch/i.html"

    def fetch(self, query: str, center: Optional[Coordinates], radius_km: float,
              max_price: Optional[float]) -> list[Listing]:
        params = {"_nkw": query, "_ipg": "60"}
        if max_price is not None:
            params["_udhi"] = int(max_price)
        url = f"{self.base_url}?{urlencode(params)}"

        self._respect_rate_limit()
        resp = requests.get(
            url,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "fr,en;q=0.8"},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        return self.parse(resp.text)

    def parse(self, html: str) -> list[Listing]:
        """Parse an eBay search results page into listings (also used by tests)."""
        soup = BeautifulSoup(html, "lxml")
        listings: list[Listing] = []
        for card in soup.select("li.s-item"):
            link = card.select_one("a.s-item__link")
            title_el = card.select_one(".s-item__title")
            if not link or not title_el:
                continue
            title = clean_text(title_el.get_text())
            href = link.get("href", "")
            if not href or title.lower() in ("shop on ebay", "nouvelle annonce"):
                continue

            price = parse_price(self._text(card, ".s-item__price"))
            location = (
                self._text(card, ".s-item__location")
                or self._text(card, ".s-item__itemLocation")
            )
            if location:
                location = clean_text(location.replace("Provenance :", "").replace("from", ""))

            img = card.select_one("img")
            image_count = 1 if img and img.get("src") else 0
            subtitle = self._text(card, ".s-item__subtitle")

            listings.append(
                Listing(
                    title=title,
                    url=href,
                    source=self.name,
                    price=price,
                    location=location or None,
                    image_count=image_count,
                    description=subtitle,
                )
            )
            if len(listings) >= MAX_RESULTS_PER_SOURCE:
                break
        return listings

    @staticmethod
    def _text(card, selector: str) -> str:
        el = card.select_one(selector)
        return clean_text(el.get_text()) if el else ""
