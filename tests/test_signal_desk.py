"""Offline tests for Signal Desk: feed parsing, scoring, and storage."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from signal_desk import feedparse
from signal_desk.config import Config
from signal_desk.db import Database
from signal_desk.models import Item
from signal_desk.scoring import heat_band, score_item

RSS = """<?xml version="1.0"?>
<rss version="2.0">
  <channel>
    <title>Example</title>
    <item>
      <title>First &amp; foremost</title>
      <link>https://example.com/a</link>
      <description>&lt;p&gt;A big <b>breach</b> happened&lt;/p&gt;</description>
      <pubDate>Wed, 02 Oct 2024 13:00:00 GMT</pubDate>
      <author>jane@example.com</author>
    </item>
    <item>
      <title>Second story</title>
      <link>https://example.com/b</link>
      <description>Nothing much</description>
      <pubDate>Wed, 02 Oct 2024 09:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>"""

ATOM = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom Example</title>
  <entry>
    <title>Atom entry one</title>
    <link rel="alternate" href="https://atom.example/1"/>
    <summary>An atom summary</summary>
    <published>2024-10-02T13:00:00Z</published>
    <author><name>Author Name</name></author>
  </entry>
</feed>"""


def test_parse_rss():
    entries = feedparse.parse(RSS)
    assert len(entries) == 2
    first = entries[0]
    assert first.title == "First & foremost"
    assert first.url == "https://example.com/a"
    assert "breach" in first.summary
    assert "<b>" not in first.summary  # html stripped
    assert first.published.year == 2024


def test_parse_atom():
    entries = feedparse.parse(ATOM)
    assert len(entries) == 1
    e = entries[0]
    assert e.title == "Atom entry one"
    assert e.url == "https://atom.example/1"
    assert e.author == "Author Name"
    assert e.published.tzinfo is not None


def test_parse_garbage_is_empty():
    assert feedparse.parse("not xml at all") == []


def test_score_recency_and_priority():
    config = Config.from_dict({"priority_keywords": ["breach"]})
    now = datetime.now(timezone.utc)

    fresh_priority = Item(
        source="t", channel="topic", topic="x",
        title="A breach was found", url="https://e/1",
        published=now,
    )
    old_plain = Item(
        source="t", channel="topic", topic="x",
        title="Ordinary news", url="https://e/2",
        published=now - timedelta(days=10),
    )
    score_item(fresh_priority, config, now=now)
    score_item(old_plain, config, now=now)
    assert fresh_priority.heat > old_plain.heat
    assert 0 <= old_plain.heat <= 100


def test_self_channel_outranks_topic_when_equal():
    config = Config.from_dict({})
    now = datetime.now(timezone.utc)
    self_item = Item(source="t", channel="self", topic="me", title="x",
                     url="https://e/self", published=now)
    topic_item = Item(source="t", channel="topic", topic="t", title="x",
                      url="https://e/topic", published=now)
    score_item(self_item, config, now=now)
    score_item(topic_item, config, now=now)
    assert self_item.heat > topic_item.heat


def test_heat_band():
    assert heat_band(90) == "hot"
    assert heat_band(50) == "warm"
    assert heat_band(30) == "cool"
    assert heat_band(5) == "cold"


def test_db_upsert_dedupes(tmp_path):
    db = Database(tmp_path / "t.db")
    item = Item(source="s", channel="topic", topic="x", title="T",
                url="https://e/x", heat=50.0)
    assert db.upsert([item]) == 1
    assert db.upsert([item]) == 0  # same URL -> no new row
    assert db.count() == 1
    rows = db.recent()
    assert rows[0]["url"] == "https://e/x"
    db.close()
