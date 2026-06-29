from datetime import datetime, timedelta

from kart_scraper.config import DEFAULT_WEIGHTS
from kart_scraper.models import Listing
from kart_scraper.scoring import (
    _normalize_lower_better,
    parse_weights,
    score_listings,
)


def _l(**kw):
    base = dict(title="kart", url="https://x/" + kw.get("title", "k"), source="t")
    base.update(kw)
    return Listing(**base)


def test_normalize_lower_better():
    vals = [100.0, 200.0, 300.0]
    assert _normalize_lower_better(100, vals) == 1.0
    assert _normalize_lower_better(300, vals) == 0.0
    assert _normalize_lower_better(200, vals) == 0.5
    # Missing value -> neutral.
    assert _normalize_lower_better(None, vals) == 0.5
    # Single distinct value -> top score.
    assert _normalize_lower_better(5, [5, 5]) == 1.0


def test_cheaper_and_closer_ranks_higher():
    cheap_near = _l(title="a", price=1000, distance_km=10)
    pricey_far = _l(title="b", price=5000, distance_km=200)
    ranked = score_listings([pricey_far, cheap_near], DEFAULT_WEIGHTS, radius_km=300)
    assert ranked[0] is cheap_near
    assert ranked[0].score > ranked[1].score


def test_out_of_radius_zeroes_distance_subscore():
    near = _l(title="a", price=1000, distance_km=10)
    far = _l(title="b", price=1000, distance_km=999)
    ranked = score_listings([near, far], DEFAULT_WEIGHTS, radius_km=100)
    far_item = next(l for l in ranked if l.title == "b")
    assert far_item.subscores["distance"] == 0.0


def test_quality_and_freshness_components():
    recent = _l(title="rich", price=1000, distance_km=10, image_count=4,
                description="x" * 400,
                posted_date=(datetime.now() - timedelta(days=1)).date(),
                specs={"engine": "Rotax", "year": "2020", "chassis": "CRG"})
    score_listings([recent], DEFAULT_WEIGHTS)
    assert recent.subscores["quality"] == 1.0
    assert recent.subscores["freshness"] > 0.9


def test_parse_weights():
    w = parse_weights("price=0.5, distance=0.3,quality=0.2")
    assert w == {"price": 0.5, "distance": 0.3, "quality": 0.2}


def test_empty_input():
    assert score_listings([], DEFAULT_WEIGHTS) == []
