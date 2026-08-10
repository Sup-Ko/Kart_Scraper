"""Federal contract awards as signal items.

Contract awards are the freshest public-record source worth watching: an award
is published the day it posts and maps directly onto a company's future
revenue, which puts it upstream of the move rather than downstream of it.

Reads from a local ``govdata.sqlite`` produced by the ``govdata`` package, so
Signal Desk stays offline and does no fetching of its own here. Configure with:

    "govdata_db": "govdata.sqlite",
    "govdata_min_amount": 10000000
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ..models import Item
from .base import Collector


class GovAwardsCollector(Collector):
    name = "Federal Contract Awards"

    def collect(self) -> list[Item]:
        db_path = Path(getattr(self.config, "govdata_db", "") or "govdata.sqlite")
        if not db_path.exists():
            return []

        min_amount = float(getattr(self.config, "govdata_min_amount", 0) or 0)
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """SELECT award_id, recipient, awarding_agy, amount,
                          action_date, description
                   FROM award
                   WHERE amount IS NOT NULL AND amount >= ?
                   ORDER BY action_date DESC LIMIT 200""",
                (min_amount,),
            ).fetchall()
        except sqlite3.Error:
            return []
        finally:
            conn.close()

        items: list[Item] = []
        for r in rows:
            published = None
            if r["action_date"]:
                try:
                    published = datetime.fromisoformat(r["action_date"]).replace(
                        tzinfo=timezone.utc
                    )
                except ValueError:
                    published = None

            amount = r["amount"] or 0
            recipient = r["recipient"] or "Unknown recipient"
            # Larger awards carry more weight, capped so a single mega-award
            # cannot dominate the whole board.
            weight = min(2.0, 0.8 + (amount / 1_000_000_000))

            items.append(
                Item(
                    source=self.name,
                    channel="topic",
                    topic=r["awarding_agy"] or "Federal contracts",
                    title=f"{recipient}: ${amount:,.0f} award",
                    url=f"https://www.usaspending.gov/award/{r['award_id']}",
                    summary=(r["description"] or "")[:400],
                    published=published,
                    meta={
                        "weight": weight,
                        "recipient": recipient,
                        "amount": amount,
                        # A large award stays material for a long time; the
                        # default news half-life would score it to zero.
                        "half_life_hours": 2160,  # ~90 days
                    },
                )
            )
        return items
