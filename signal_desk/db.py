"""SQLite persistence for collected items.

A single local file, deduplicated by item id (URL-derived). Re-collecting the
same item refreshes its content and re-scores it, but preserves the original
``collected_at`` so "new since last run" stays meaningful.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .config import DEFAULT_DB_PATH
from .models import Item

_SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id           TEXT PRIMARY KEY,
    source       TEXT NOT NULL,
    channel      TEXT NOT NULL,
    topic        TEXT NOT NULL,
    title        TEXT NOT NULL,
    url          TEXT NOT NULL,
    summary      TEXT,
    author       TEXT,
    published    TEXT,
    collected_at TEXT NOT NULL,
    heat         REAL NOT NULL DEFAULT 0,
    meta         TEXT
);
CREATE INDEX IF NOT EXISTS idx_items_heat ON items(heat DESC);
CREATE INDEX IF NOT EXISTS idx_items_channel ON items(channel);
CREATE INDEX IF NOT EXISTS idx_items_published ON items(published DESC);
"""


class Database:
    def __init__(self, path: Path | str = DEFAULT_DB_PATH):
        self.path = str(path)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def upsert(self, items: Iterable[Item]) -> int:
        """Insert or update items. Returns the count of rows that were new."""
        new_count = 0
        cur = self.conn.cursor()
        for item in items:
            row = item.to_row()
            existing = cur.execute(
                "SELECT collected_at FROM items WHERE id = ?", (row["id"],)
            ).fetchone()
            if existing is None:
                new_count += 1
            else:
                # keep the earliest collected_at so recency-of-discovery is stable
                row["collected_at"] = existing["collected_at"]
            cur.execute(
                """
                INSERT INTO items (id, source, channel, topic, title, url, summary,
                                   author, published, collected_at, heat, meta)
                VALUES (:id, :source, :channel, :topic, :title, :url, :summary,
                        :author, :published, :collected_at, :heat, :meta)
                ON CONFLICT(id) DO UPDATE SET
                    source=excluded.source, channel=excluded.channel,
                    topic=excluded.topic, title=excluded.title, url=excluded.url,
                    summary=excluded.summary, author=excluded.author,
                    published=excluded.published, heat=excluded.heat,
                    meta=excluded.meta
                """,
                row,
            )
        self.conn.commit()
        return new_count

    def recent(self, limit: int = 200, max_age_days: int | None = None) -> list[dict]:
        """Return items ordered by heat, optionally limited to a recency window."""
        query = "SELECT * FROM items"
        params: list = []
        if max_age_days is not None:
            cutoff = datetime.now(timezone.utc).timestamp() - max_age_days * 86400
            # compare on published when present, else collected_at
            query += " WHERE COALESCE(published, collected_at) >= ?"
            params.append(datetime.fromtimestamp(cutoff, timezone.utc).isoformat())
        query += " ORDER BY heat DESC, COALESCE(published, collected_at) DESC LIMIT ?"
        params.append(limit)
        return [dict(r) for r in self.conn.execute(query, params).fetchall()]

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) AS n FROM items").fetchone()["n"]
