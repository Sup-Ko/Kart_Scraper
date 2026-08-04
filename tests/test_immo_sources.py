from pathlib import Path

from geneva_immo.sources import LIVE_SOURCES, get_sources
from geneva_immo.sources.homegate import HomegateSource
from geneva_immo.sources.immobilier_ch import ImmobilierChSource
from geneva_immo.sources.immoscout24 import ImmoScout24Source
from geneva_immo.sources.sample import SampleSource

FIXTURES = Path(__file__).parent / "fixtures"


def test_homegate_parse_fixture():
    html = (FIXTURES / "homegate_results.html").read_text(encoding="utf-8")
    listings = HomegateSource().parse(html)
    # The pagination card (no numeric detail id) is skipped.
    assert len(listings) == 2
    first = listings[0]
    assert first.title == "Bel appartement au coeur des Eaux-Vives"
    assert first.price == 1_650_000.0  # Swiss "1'650'000.–" parsed correctly
    assert first.currency == "CHF"
    assert first.location == "Rue des Eaux-Vives 12, 1207 Genève"
    assert first.url == "https://www.homegate.ch/buy/4001832999"
    assert first.rooms == 4.5
    assert first.surface_m2 == 105.0
    assert first.image_count == 2


def test_immoscout24_parse_fixture():
    html = (FIXTURES / "immoscout24_results.html").read_text(encoding="utf-8")
    listings = ImmoScout24Source().parse(html)
    assert len(listings) == 2  # anchors deduped against their card wrappers
    first = listings[0]
    assert first.title == "Appartement 3,5 pièces à Plainpalais"
    assert first.price == 980_000.0
    assert first.location == "Rue de Carouge 45, 1205 Genève"
    assert first.url == "https://www.immoscout24.ch/fr/d/appartement-acheter-geneve/8123456"
    assert first.rooms == 3.5
    assert first.surface_m2 == 72.0


def test_immobilier_parse_fixture():
    html = (FIXTURES / "immobilier_results.html").read_text(encoding="utf-8")
    listings = ImmobilierChSource().parse(html)
    # The "/page-2" pagination link is not a detail page.
    assert len(listings) == 2
    first = listings[0]
    assert first.title == "Champel — 4 pièces avec balcon"
    assert first.price == 1_390_000.0
    assert first.location == "Champel, Genève"
    assert first.url == ("https://www.immobilier.ch"
                         "/fr/acheter/appartement/geneve/champel/objet-567890")
    assert first.rooms == 4.0
    assert first.surface_m2 == 88.0
    second = listings[1]
    assert second.rooms == 1.0
    assert second.surface_m2 == 30.0


def test_sample_source_loads():
    listings = SampleSource().fetch(None)
    assert len(listings) >= 5
    assert all(l.title and l.url for l in listings)
    assert all(l.currency == "CHF" for l in listings)
    assert all(l.price_per_m2 for l in listings)


def test_get_sources_all_excludes_sample():
    names = [s.name for s in get_sources(["all"])]
    for expected in ("immobilier", "homegate", "immoscout24", "anibis"):
        assert expected in names
    assert "sample" not in names
    assert set(names) == set(LIVE_SOURCES)


def test_get_sources_explicit_and_dedupe():
    names = [s.name for s in get_sources(["sample", "sample", "homegate"])]
    assert names == ["sample", "homegate"]


def test_search_applies_filters():
    src = SampleSource()
    results = src.search(center=None, radius_km=12, max_price=1_500_000,
                         min_rooms=3.0, min_surface=60, geocoder=None)
    assert results
    for l in results:
        assert l.price <= 1_500_000
        assert l.rooms >= 3.0
        assert l.surface_m2 >= 60


def test_search_max_rooms_keeps_studios():
    src = SampleSource()
    results = src.search(center=None, radius_km=12, max_rooms=1.5, geocoder=None)
    assert results  # the bundled data contains studios
    assert all(l.rooms <= 1.5 for l in results)


def test_geocodable_anchors_to_geneva():
    assert SampleSource._geocodable("Champel") == "Champel, Genève, Suisse"
    assert (SampleSource._geocodable("Rue de Berne, 1201 Genève")
            == "Rue de Berne, 1201 Genève, Suisse")
