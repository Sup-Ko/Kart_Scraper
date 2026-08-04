from datetime import date, timedelta

import pytest

from geneva_immo.models import ApartmentListing
from geneva_immo.scoring import parse_weights, score_listings

WEIGHTS = {"value": 0.35, "size": 0.15, "distance": 0.15, "quality": 0.25,
           "freshness": 0.10}


def _listing(**kwargs) -> ApartmentListing:
    base = dict(title="Test", url="https://example.com/a", source="test")
    base.update(kwargs)
    return ApartmentListing(**base)


def test_price_per_m2():
    listing = _listing(price=1_000_000, surface_m2=100)
    assert listing.price_per_m2 == 10_000
    assert _listing(price=1_000_000).price_per_m2 is None
    assert _listing(surface_m2=100).price_per_m2 is None


def test_best_value_scores_highest_on_value():
    cheap = _listing(url="https://example.com/cheap", price=800_000, surface_m2=100)
    dear = _listing(url="https://example.com/dear", price=2_000_000, surface_m2=100)
    ranked = score_listings([dear, cheap], {"value": 1.0})
    assert ranked[0] is cheap
    assert ranked[0].subscores["value"] == 1.0
    assert ranked[1].subscores["value"] == 0.0


def test_bigger_flat_scores_higher_on_size():
    small = _listing(url="https://example.com/s", surface_m2=40)
    big = _listing(url="https://example.com/b", surface_m2=140)
    ranked = score_listings([small, big], {"size": 1.0})
    assert ranked[0] is big


def test_outside_radius_zeroes_distance():
    near = _listing(url="https://example.com/near", distance_km=2.0)
    far = _listing(url="https://example.com/far", distance_km=30.0)
    score_listings([near, far], WEIGHTS, radius_km=12.0)
    assert far.subscores["distance"] == 0.0
    assert near.subscores["distance"] == 1.0


def test_freshness_decays():
    fresh = _listing(url="https://example.com/f", posted_date=date.today())
    stale = _listing(url="https://example.com/o",
                     posted_date=date.today() - timedelta(days=200))
    score_listings([fresh, stale], WEIGHTS)
    assert fresh.subscores["freshness"] == 1.0
    assert stale.subscores["freshness"] == 0.0


def test_missing_data_stays_neutral():
    lone = _listing()
    score_listings([lone], WEIGHTS)
    assert lone.subscores["value"] == 0.5
    assert lone.subscores["size"] == 0.5
    assert lone.subscores["distance"] == 0.5
    assert 0 <= lone.score <= 100


def test_scores_are_0_to_100_and_sorted():
    listings = [
        _listing(url=f"https://example.com/{i}", price=500_000 + i * 100_000,
                 surface_m2=50 + i * 10, distance_km=float(i))
        for i in range(5)
    ]
    ranked = score_listings(listings, WEIGHTS, radius_km=12.0)
    scores = [l.score for l in ranked]
    assert scores == sorted(scores, reverse=True)
    assert all(0 <= s <= 100 for s in scores)


def test_parse_weights():
    assert parse_weights("value=0.5,size=0.2") == {"value": 0.5, "size": 0.2}


def test_parse_weights_rejects_unknown_key():
    with pytest.raises(ValueError):
        parse_weights("price=0.5")  # kart key, not an immo key
