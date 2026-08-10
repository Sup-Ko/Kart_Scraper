"""Federal Register rules as signal items.

Regulatory activity is the forward-looking half of policy risk: a *proposed*
rule carries a comment deadline and an effective date, both of which sit in the
future. Surfacing them alongside news means the heat map covers what an agency
intends to do, not only what has already been reported.

Reads from a local ``govdata.sqlite`` populated by ``govdata ingest --fedreg``,
so Signal Desk performs no fetching of its own here. Configure with::

    "govdata_db": "govdata.sqlite",
    "fedreg_agencies": ["Department of Defense"]   # optional filter
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ..models import Item
from .base import Collector


class FederalRegisterCollector(Collector):
    name = "Federal Register"

    def collect(self) -> list[Item]:
        db_path = Path(getattr(self.config, "govdata_db", "") or "govdata.sqlite")
        if not db_path.exists():
            return []

        wanted = [a.lower() for a in getattr(self.config, "fedreg_agencies", []) or []]

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """SELECT document_number, doc_type, title, abstract, agencies,
                          publication_date, effective_on, comments_close_on, url
                   FROM fedreg_doc
                   ORDER BY publication_date DESC LIMIT 300"""
            ).fetchall()
        except sqlite3.Error:
            return []
        finally:
            conn.close()

        today = datetime.now(timezone.utc).date().isoformat()
        items: list[Item] = []
        for r in rows:
            agencies = r["agencies"] or ""
            if wanted and not any(w in agencies.lower() for w in wanted):
                continue

            published = None
            if r["publication_date"]:
                try:
                    published = datetime.fromisoformat(
                        r["publication_date"]
                    ).replace(tzinfo=timezone.utc)
                except ValueError:
                    published = None

            # A proposed rule with an open comment window is the most actionable
            # thing here — there is still time to respond to it.
            proposed = r["doc_type"] == "PRORULE"
            open_comments = bool(
                r["comments_close_on"] and r["comments_close_on"] >= today
            )
            weight = 1.0
            if proposed:
                weight = 1.6 if open_comments else 1.2

            prefix = "Proposed rule" if proposed else "Final rule"
            summary = (r["abstract"] or "")[:400]
            if open_comments:
                summary = f"[comments close {r['comments_close_on']}] {summary}"
            elif r["effective_on"]:
                summary = f"[effective {r['effective_on']}] {summary}"

            items.append(
                Item(
                    source=self.name,
                    channel="topic",
                    topic=agencies.split(",")[0].strip() or "Federal Register",
                    title=f"{prefix}: {r['title']}",
                    url=r["url"] or "",
                    summary=summary,
                    published=published,
                    meta={
                        "weight": weight,
                        "doc_type": r["doc_type"],
                        "open_comments": open_comments,
                        # Rules matter until they take effect or the comment
                        # window shuts, not for a couple of days.
                        "half_life_hours": 1440,  # ~60 days
                    },
                )
            )
        return items
