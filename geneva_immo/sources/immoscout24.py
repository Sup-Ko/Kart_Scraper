"""ImmoScout24 scraper (Playwright, best-effort).

ImmoScout24 (immoscout24.ch) shares its backend group with Homegate but often
carries agency listings the other misses. Detail pages live under ``/d/``.
Client-side rendered and anti-bot protected, so this is best-effort.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from bs4 import BeautifulSoup

from kart_scraper.sources._browser import browser_page

from ..config import MAX_RESULTS_PER_SOURCE
from ..models import ApartmentListing
from ._util import clean_text, parse_price, parse_rooms, parse_surface
from .base import BaseSource

log = logging.getLogger(__name__)


class ImmoScout24Source(BaseSource):
    name = "immoscout24"
    label = "ImmoScout24 (CH)"
    search_url = "https://www.immoscout24.ch/fr/appartement/acheter/canton-geneve"

    def fetch(self, max_price: Optional[float]) -> list[ApartmentListing]:
        url = self.search_url
        if max_price is not None:
            url += f"?pt={int(max_price)}"  # pt = price-to filter

        with browser_page() as page:
            if page is None:
                return []
            page.goto(url, wait_until="domcontentloaded")
            try:
                page.wait_for_selector(
                    "[data-test='result-list-item'], a[href*='/d/']", timeout=10000)
            except Exception:  # noqa: BLE001 - blocked or empty
                log.warning("ImmoScout24: no results rendered (likely blocked).")
                return []
            html = page.content()
        return self.parse(html)

    def parse(self, html: str) -> list[ApartmentListing]:
        soup = BeautifulSoup(html, "lxml")
        cards = soup.select(
            "[data-test='result-list-item'], article:has(a[href*='/d/']), "
            "div:not(:has(article)) > a[href*='/d/']")
        listings: list[ApartmentListing] = []
        seen: set[str] = set()
        for card in cards:
            link = card if card.name == "a" else card.select_one("a[href*='/d/']")
            if link is None:
                continue
            href = link.get("href", "")
            if "/d/" not in href:
                continue
            if href.startswith("/"):
                href = "https://www.immoscout24.ch" + href
            if href in seen:
                continue

            text = clean_text(card.get_text(" "))
            title = clean_text(self._text(card, "h2, h3, [class*='itle']")) or text[:80]
            if not title:
                continue
            price = parse_price(
                self._text(card, "[data-test*='price'], [class*='rice']")
                or self._chf_snippet(text))
            location = self._text(card, "address, [class*='ddress'], [class*='ocation']") or None

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
    def _chf_snippet(text: str) -> str:
        match = re.search(r"CHF\s*([\d'’\s.,]+)", text)
        return match.group(1) if match else ""

    @staticmethod
    def _text(card, selector: str) -> str:
        el = card.select_one(selector)
        return clean_text(el.get_text(" ")) if el else ""
