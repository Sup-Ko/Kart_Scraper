"""immobilier.ch scraper (plain HTTP, best-effort).

immobilier.ch is the historic French-speaking Swiss portal, strong on Geneva
agency mandates. Its result pages are mostly server-rendered, so a plain
``requests`` fetch usually works — making it the most reliable live source
here, the way eBay is for the kart scraper.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

import requests
from bs4 import BeautifulSoup

from ..config import MAX_RESULTS_PER_SOURCE, REQUEST_TIMEOUT, USER_AGENT
from ..models import ApartmentListing
from ._util import clean_text, parse_price, parse_rooms, parse_surface
from .base import BaseSource

log = logging.getLogger(__name__)


class ImmobilierChSource(BaseSource):
    name = "immobilier"
    label = "immobilier.ch"
    search_url = "https://www.immobilier.ch/fr/acheter/appartement/geneve/page-1"

    def fetch(self, max_price: Optional[float]) -> list[ApartmentListing]:
        self._respect_rate_limit()
        resp = requests.get(
            self.search_url,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "fr-CH,fr;q=0.9"},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        return self.parse(resp.text)

    def parse(self, html: str) -> list[ApartmentListing]:
        soup = BeautifulSoup(html, "lxml")
        # Object cards link to detail pages under /fr/... ; anchor on links that
        # carry an object id-ish path and skip nav/pagination links.
        cards = soup.select(
            "[class*='object']:has(a[href*='/fr/']), article:has(a[href*='/fr/'])")
        if not cards:
            cards = soup.select("a[href*='/fr/']")
        listings: list[ApartmentListing] = []
        seen: set[str] = set()
        for card in cards:
            link = card if card.name == "a" else card.select_one("a[href*='/fr/']")
            if link is None:
                continue
            href = link.get("href", "")
            if not self._looks_like_detail(href):
                continue
            if href.startswith("/"):
                href = "https://www.immobilier.ch" + href
            if href in seen:
                continue

            text = clean_text(card.get_text(" "))
            title = clean_text(self._text(card, "h2, h3, [class*='itle']")) or text[:80]
            if not title:
                continue
            price = parse_price(
                self._text(card, "[class*='rice'], [class*='rix']")
                or self._chf_snippet(text))
            location = self._text(card, "[class*='ddress'], [class*='ocation'], [class*='ieu']") or None

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
                    image_count=len(card.select("img")) if card.name != "a" else 0,
                )
            )
            if len(listings) >= MAX_RESULTS_PER_SOURCE:
                break
        return listings

    @staticmethod
    def _looks_like_detail(href: str) -> bool:
        """Detail URLs embed a numeric object id (e.g. ...-123456)."""
        return bool(re.search(r"/fr/.+\d{4,}", href)) and "/page-" not in href

    @staticmethod
    def _chf_snippet(text: str) -> str:
        match = re.search(r"CHF\s*([\d'’\s.,]+)", text)
        return match.group(1) if match else ""

    @staticmethod
    def _text(card, selector: str) -> str:
        el = card.select_one(selector)
        return clean_text(el.get_text(" ")) if el else ""
