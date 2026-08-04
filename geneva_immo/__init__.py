"""geneva_immo — find apartments for sale in Geneva and rate them.

A sibling of :mod:`kart_scraper` that reuses its shared infrastructure
(geocoding, headless-browser helper, price parsing) but targets Swiss real
estate portals for **apartments for sale in and around Geneva**. Listings are
normalized, deduped, and ranked by a weighted score that blends price per m²,
size, distance to a reference point, listing quality and freshness.
"""

__version__ = "0.1.0"
