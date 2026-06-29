"""Google Shopping scraper (Playwright, best-effort).

Google aggregates listings from many shops, which is a useful breadth source
for "karts for sale". It is also heavily anti-bot (consent walls, CAPTCHAs), so
this is best-effort: anything that doesn't render cleanly degrades to 0 results.
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


class GoogleSource(BaseSource):
    name = "google"
    label = "Google Shopping"
    base_url = "https://www.google.com/search"

    def fetch(self, query: str, center: Optional[Coordinates], radius_km: float,
              max_price: Optional[float]) -> list[Listing]:
        # tbm=shop -> Google Shopping vertical.
        params = {"q": f"{query} for sale", "tbm": "shop", "hl": "fr"}
        url = f"{self.base_url}?{urlencode(params)}"

        with browser_page() as page:
            if page is None:
                return []
            page.goto(url, wait_until="domcontentloaded")
            self._dismiss_consent(page)
            try:
                page.wait_for_selector(".sh-dgr__content, .sh-pr__product-results",
                                       timeout=8000)
            except Exception:  # noqa: BLE001
                log.warning("Google Shopping: no results rendered (likely blocked).")
                return []
            html = page.content()
        return self.parse(html)

    @staticmethod
    def _dismiss_consent(page) -> None:
        for label in ("Tout accepter", "Accept all", "J'accepte"):
            try:
                btn = page.get_by_role("button", name=label)
                if btn.count() > 0:
                    btn.first.click(timeout=3000)
                    return
            except Exception:  # noqa: BLE001
                continue

    def parse(self, html: str) -> list[Listing]:
        soup = BeautifulSoup(html, "lxml")
        listings: list[Listing] = []
        for card in soup.select(".sh-dgr__content, .sh-dgr__grid-result"):
            title_el = card.select_one("h3, .tAxDx")
            link = card.select_one("a[href]")
            if not title_el or not link:
                continue
            title = clean_text(title_el.get_text())
            href = link.get("href", "")
            if href.startswith("/"):
                href = "https://www.google.com" + href
            price = parse_price(self._text(card, ".a8Pemb, .kHxwFf"))
            merchant = self._text(card, ".aULzUe, .IuHnof")
            listings.append(
                Listing(
                    title=title,
                    url=href,
                    source=self.name,
                    price=price,
                    location=None,  # Shopping rarely exposes a seller location
                    image_count=len(card.select("img")),
                    description=merchant,
                )
            )
            if len(listings) >= MAX_RESULTS_PER_SOURCE:
                break
        return listings

    @staticmethod
    def _text(card, selector: str) -> str:
        el = card.select_one(selector)
        return clean_text(el.get_text()) if el else ""
