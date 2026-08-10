"""Tests for the EDGAR full-text and subreddit collectors — offline."""

from __future__ import annotations

from datetime import datetime, timezone

from signal_desk.collectors.edgar import EdgarSearchCollector, build_url, parse_hits
from signal_desk.config import Config, _subreddit_feeds

PAYLOAD = {
    "hits": {
        "total": {"value": 2},
        "hits": [
            {
                "_id": "0000320193-26-000081:aapl-20260502.htm",
                "_source": {
                    "ciks": ["0000320193"],
                    "display_names": ["Apple Inc.  (AAPL)"],
                    "file_type": "8-K",
                    "file_description": "Results of Operations",
                    "file_date": "2026-05-02",
                },
            },
            {
                "_id": "0000789019-26-000012:msft-10k.htm",
                "_source": {
                    "ciks": ["0000789019"],
                    "display_names": ["MICROSOFT CORP (MSFT)"],
                    "file_type": "10-K",
                    "file_date": "2026-07-30",
                },
            },
        ],
    }
}


def test_build_url_encodes_query_and_forms():
    url = build_url('"climate disclosure"', forms=["8-K", "10-K"], days=30)
    assert url.startswith("https://efts.sec.gov/LATEST/search-index?")
    assert "q=%22climate%20disclosure%22" in url or "q=%22climate+disclosure%22" in url
    assert "forms=8-K%2C10-K" in url
    assert "startdt=" in url and "enddt=" in url


def test_build_url_without_forms():
    assert "forms=" not in build_url("acme", forms=None)


def test_parse_hits_builds_archive_urls():
    rows = parse_hits(PAYLOAD)
    assert len(rows) == 2
    a = rows[0]
    assert a["accession"] == "0000320193-26-000081"
    assert a["form"] == "8-K"
    assert a["company"].startswith("Apple Inc.")
    # leading zeros stripped from CIK, dashes stripped from accession in the path
    assert a["url"] == ("https://www.sec.gov/Archives/edgar/data/320193/"
                        "000032019326000081/aapl-20260502.htm")


def test_parse_hits_empty_payloads():
    assert parse_hits({}) == []
    assert parse_hits({"hits": {}}) == []
    assert parse_hits({"hits": {"hits": None}}) == []


def test_edgar_collector_without_queries_is_inert():
    """No configured queries means no requests at all."""
    c = EdgarSearchCollector(Config.from_dict({}))
    assert c.collect() == []


def test_edgar_items_carry_long_half_life(monkeypatch):
    """Filings must outlive the news half-life, like other slow sources."""
    import json

    class FakeResp:
        content = json.dumps(PAYLOAD).encode()

    c = EdgarSearchCollector(Config.from_dict({"edgar_queries": ["acme"]}))
    monkeypatch.setattr(c, "_get", lambda url: FakeResp())
    items = c.collect()
    assert len(items) == 2
    assert all(i.meta["half_life_hours"] == 720 for i in items)
    assert items[0].topic == "acme"
    assert items[0].published == datetime(2026, 5, 2, tzinfo=timezone.utc)


def test_edgar_collector_survives_a_failing_query(monkeypatch):
    def boom(url):
        raise RuntimeError("network down")

    c = EdgarSearchCollector(Config.from_dict({"edgar_queries": ["a", "b"]}))
    monkeypatch.setattr(c, "_get", boom)
    assert c.collect() == []   # no exception escapes


# ---- subreddit feeds --------------------------------------------------------

def test_subreddit_names_normalize():
    feeds = _subreddit_feeds(["investing", "r/stocks", "/wallstreetbets/", "  "])
    assert [f.name for f in feeds] == ["r/investing", "r/stocks", "r/wallstreetbets"]
    assert feeds[0].url == "https://www.reddit.com/r/investing/.rss"
    # community chatter is weighted below primary sources
    assert all(f.weight < 1.0 for f in feeds)


def test_subreddits_become_ordinary_feeds():
    """Reuses the generic RSS collector rather than adding a second fetch path."""
    cfg = Config.from_dict({"subreddits": ["investing"]})
    assert any(f.url.endswith("/r/investing/.rss") for f in cfg.feeds)


def test_subreddits_merge_with_explicit_feeds():
    cfg = Config.from_dict({
        "feeds": [{"name": "HN", "url": "https://hnrss.org/frontpage"}],
        "subreddits": ["investing"],
    })
    names = {f.name for f in cfg.feeds}
    assert "HN" in names and "r/investing" in names
