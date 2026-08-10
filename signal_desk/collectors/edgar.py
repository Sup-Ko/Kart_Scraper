"""SEC EDGAR full-text search as signal items.

Company news is reported second-hand; filings are the primary document. Full-
text search lets you watch for a term appearing anywhere in recent filings —
your own name in a proxy statement, a competitor named in someone's risk
factors, a product line mentioned in an 8-K.

    source: efts.sec.gov full-text search, the endpoint behind EDGAR's own
            search UI. Covers 2001-onward filings.
    lag:    minutes to hours after a filing is accepted.
    key:    none. The SEC requires a descriptive User-Agent identifying the
            requester, which ``govdata.http``-style headers provide here too.
    limits: full-text search indexes the filing documents themselves, so a hit
            means the term appears somewhere in the document — not that the
            filing is *about* it.

Configure with::

    "edgar_queries": ["\\"climate disclosure\\"", "Acme Corp"],
    "edgar_forms": ["8-K", "10-K"]        # optional filter
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from ..models import Item
from .base import Collector

SEARCH = "https://efts.sec.gov/LATEST/search-index"


def build_url(query: str, forms: list[str] | None = None,
              days: int = 30) -> str:
    """Build a full-text search URL. Pure, so the query shape is testable."""
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days)
    parts = [
        f"q={quote(query)}",
        "dateRange=custom",
        f"startdt={start.isoformat()}",
        f"enddt={end.isoformat()}",
    ]
    if forms:
        parts.append("forms=" + quote(",".join(forms)))
    return f"{SEARCH}?" + "&".join(parts)


def parse_hits(payload: dict) -> list[dict]:
    """Extract filing hits from an EDGAR full-text search response."""
    out = []
    hits = ((payload or {}).get("hits") or {}).get("hits") or []
    for h in hits:
        source = h.get("_source") or {}
        raw_id = h.get("_id") or ""
        accession, _, filename = raw_id.partition(":")
        ciks = source.get("ciks") or []
        cik = (ciks[0] if ciks else "").lstrip("0")

        url = ""
        if cik and accession:
            plain = accession.replace("-", "")
            url = (f"https://www.sec.gov/Archives/edgar/data/{cik}/{plain}/"
                   f"{filename}" if filename else
                   f"https://www.sec.gov/Archives/edgar/data/{cik}/{plain}")

        names = source.get("display_names") or []
        out.append({
            "accession": accession,
            "company": (names[0] if names else "").strip(),
            "form": (source.get("file_type") or "").strip(),
            "description": (source.get("file_description") or "").strip(),
            "filed": source.get("file_date"),
            "url": url,
        })
    return out


class EdgarSearchCollector(Collector):
    name = "SEC EDGAR"

    def collect(self) -> list[Item]:
        queries = getattr(self.config, "edgar_queries", []) or []
        if not queries:
            return []
        forms = getattr(self.config, "edgar_forms", []) or []

        items: list[Item] = []
        for query in queries:
            try:
                resp = self._get(build_url(query, forms))
                payload = json.loads(resp.content)
            except Exception:
                continue  # a failing query must not sink the run

            for hit in parse_hits(payload):
                if not hit["accession"]:
                    continue
                published = None
                if hit["filed"]:
                    try:
                        published = datetime.fromisoformat(hit["filed"]).replace(
                            tzinfo=timezone.utc
                        )
                    except ValueError:
                        published = None

                company = hit["company"] or "Unknown filer"
                form = hit["form"] or "filing"
                items.append(
                    Item(
                        source=self.name,
                        channel="topic",
                        topic=query,
                        title=f"{form}: {company}",
                        url=hit["url"],
                        summary=hit["description"] or f"Matched {query!r} in a {form}.",
                        published=published,
                        meta={
                            "weight": 1.1,
                            "form": form,
                            # Filings stay relevant well beyond a news cycle.
                            "half_life_hours": 720,  # ~30 days
                        },
                    )
                )
        return items
