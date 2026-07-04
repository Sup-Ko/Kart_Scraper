from kart_scraper.geocode import Geocoder, distance_km


def test_distance_km_known_cities():
    lyon = (45.7640, 4.8357)
    paris = (48.8566, 2.3522)
    d = distance_km(lyon, paris)
    assert 380 < d < 420  # ~392 km great-circle


def test_distance_km_handles_none():
    assert distance_km(None, (1, 2)) is None
    assert distance_km((1, 2), None) is None


def test_geocode_uses_cache_without_network(tmp_path):
    cache = tmp_path / "geo.json"
    geo = Geocoder(cache_path=cache)
    # Pre-seed the cache so no network call is attempted.
    geo._cache["lyon, france"] = [45.764, 4.8357]
    assert geo.geocode("Lyon, France") == (45.764, 4.8357)
    # Cached miss (None) is also honored.
    geo._cache["nowhere"] = None
    assert geo.geocode("nowhere") is None


def test_geocode_blank_returns_none(tmp_path):
    geo = Geocoder(cache_path=tmp_path / "g.json")
    assert geo.geocode("") is None
    assert geo.geocode(None) is None
