"""Anibis real-estate scraper (Playwright, best-effort).

Anibis (anibis.ch) is a general classifieds site, but private sellers list
apartments there without agency fees — occasionally the best deals in Geneva.
Reuses the same markup conventions as the kart scraper's Anibis source
(detail pages under ``/vi/``); rooms and surface are mined from the free text.
"""

from __future__ import annotations

import logging
from typing import Optional
from urllib.parse import quote

from bs4 import BeautifulSoup

from kart_scraper.sources._browser import browser_page

from ..config import MAX_RESULTS_PER_SOURCE
from ..models import ApartmentListing
from ._util import clean_text, parse_price, parse_rooms, parse_surface
from .base import BaseSource

log = logging.getLogger(__name__)


class AnibisImmoSource(BaseSource):
    name = "anibis"
    label = "Anibis immobilier (CH)"
    query = "appartement à vendre genève"
    base_url = "https://www.anibis.ch/fr/q/"

    def fetch(self, max_price: Optional[float]) -> list[ApartmentListing]:
        url = f"{self.base_url}{quote(self.query)}"

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

    def parse(self, html: str) -> list[ApartmentListing]:
        soup = BeautifulSoup(html, "lxml")
        cards = soup.select(
            "[data-testid='listItem'], article:has(a[href*='/vi/']), a[href*='/vi/']")
        listings: list[ApartmentListing] = []
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
            text = clean_text(card.get_text(" "))
            price = parse_price(self._text(card, "[data-testid='price'], .price, [class*='rice']"))
            location = self._text(card, "[data-testid='location'], [class*='ocation']") or None

            seen.add(href)
            listings.append(
                ApartmentListing(
                    title=title,
                    url=href,
                    source=self.name,
                    price=price,
                    currency="CHF",
                    location=location,
                    rooms=parse_rooms(text),
                    surface_m2=parse_surface(text),
                    image_count=len(card.select("img")),
                )
            )
            if len(listings) >= MAX_RESULTS_PER_SOURCE:
                break
        return listings

    @staticmethod
    def _text(card, selector: str) -> str:
        el = card.select_one(selector)
        return clean_text(el.get_text(" ")) if el else ""
