"""Have I Been Pwned collector — breach checks for YOUR OWN emails only.

Requires a HIBP API key (set ``hibp_api_key`` in the config). Queries the
``breachedaccount`` endpoint for each email you list under ``self.emails`` and
turns any breach into a high-signal ``self`` item. Only ever checks addresses
you have declared as your own.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..models import Item
from .base import Collector

_API = "https://haveibeenpwned.com/api/v3/breachedaccount/{account}?truncateResponse=false"


class HibpCollector(Collector):
    name = "Have I Been Pwned"

    def collect(self) -> list[Item]:
        items: list[Item] = []
        headers = {"hibp-api-key": self.config.hibp_api_key}
        for email in self.config.self.emails:
            url = _API.format(account=email)
            try:
                resp = self._get(url, headers=headers)
            except Exception as exc:
                # 404 = no breaches (good news); anything else, skip quietly
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status == 404:
                    continue
                continue
            for breach in resp.json():
                name = breach.get("Name", "Unknown breach")
                added = breach.get("BreachDate") or breach.get("AddedDate")
                published = None
                if added:
                    try:
                        published = datetime.fromisoformat(added).replace(
                            tzinfo=timezone.utc
                        )
                    except ValueError:
                        published = None
                data_classes = ", ".join(breach.get("DataClasses", []))
                items.append(
                    Item(
                        source=self.name,
                        channel="self",
                        topic=email,
                        title=f"Breach exposure: {name}",
                        url=f"https://haveibeenpwned.com/PwnedWebsites#{name}",
                        summary=(
                            f"{email} appears in the {name} breach. "
                            f"Exposed data: {data_classes or 'unknown'}."
                        ),
                        published=published,
                        meta={"weight": 2.0, "breach": name},
                    )
                )
        return items
