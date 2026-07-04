from pathlib import Path

from kart_scraper.sources import LIVE_SOURCES, get_sources
from kart_scraper.sources.anibis import AnibisSource
from kart_scraper.sources.ebay import EbaySource
from kart_scraper.sources.racertrader import RacerTraderSource
from kart_scraper.sources.sample import SampleSource

FIXTURES = Path(__file__).parent / "fixtures"


def test_ebay_parse_fixture():
    html = (FIXTURES / "ebay_results.html").read_text(encoding="utf-8")
    listings = EbaySource().parse(html)
    # "Shop on eBay" placeholder card is skipped.
    assert len(listings) == 2
    first = listings[0]
    assert first.title == "Kart Tony Kart Rotax Max 2019"
    assert first.price == 2800.0
    assert first.location == "Lyon, France"
    assert first.url == "https://www.ebay.fr/itm/111"
    assert first.image_count == 1


def test_anibis_parse_fixture():
    html = (FIXTURES / "anibis_results.html").read_text(encoding="utf-8")
    listings = AnibisSource().parse(html)
    assert len(listings) == 2  # anchors deduped against their card wrappers
    first = listings[0]
    assert first.title == "Kart Tony Kart Rotax"
    assert first.price == 2500.0  # Swiss "2'500.-" parsed correctly
    assert first.currency == "CHF"
    assert first.location == "Genève"
    assert first.url == "https://www.anibis.ch/fr/vi/kart-tony-12345"
    assert first.image_count == 2


def test_racertrader_parse_fixture():
    html = (FIXTURES / "racertrader_results.html").read_text(encoding="utf-8")
    listings = RacerTraderSource().parse(html)
    assert len(listings) == 2
    first = listings[0]
    assert first.title == "Tony Kart OTK 2020"
    assert first.price == 3200.0
    assert first.currency == "GBP"
    assert first.url == "https://www.racertrader.com/listing/tony-kart-999"
    # Absolute URL in the fixture is kept as-is.
    assert listings[1].url == "https://www.racertrader.com/listing/exprit-888"


def test_sample_source_loads():
    listings = SampleSource().fetch("kart", None, 100, None)
    assert len(listings) >= 5
    assert all(l.title and l.url for l in listings)
    assert any(l.specs.get("engine") for l in listings)


def test_get_sources_all_excludes_sample():
    names = [s.name for s in get_sources(["all"])]
    # New sources are wired into the default "all" run.
    for expected in ("ebay", "leboncoin", "anibis", "racertrader", "racingjunk"):
        assert expected in names
    assert "sample" not in names
    assert set(names) == set(LIVE_SOURCES)


def test_get_sources_explicit_and_dedupe():
    names = [s.name for s in get_sources(["sample", "sample", "ebay"])]
    assert names == ["sample", "ebay"]


def test_search_filters_max_price_and_distance(monkeypatch):
    src = SampleSource()
    # No geocoder -> distance stays None, but max_price filter still applies.
    results = src.search("kart", center=None, radius_km=100, max_price=1000,
                         geocoder=None)
    assert results
    assert all(l.price is None or l.price <= 1000 for l in results)
