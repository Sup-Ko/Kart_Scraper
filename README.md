# Kart Scraper 🏎️

Search the internet for **go-karts for sale near you** and rate each listing
with a custom weighted score, so the best-value karts nearby rise to the top.

It scrapes several marketplaces in parallel, geocodes every listing to measure
how far it is from you (free, no API key), and ranks the results by a tunable
score that blends **price**, **distance**, **listing quality** and
**freshness**.

## How it works

```
location ─▶ geocode ─┐
query ────▶ sources ─┼─▶ merge + dedupe ─▶ score (weighted) ─▶ table / CSV / HTML
                     │
        ┌────────────┴────────────┐
      eBay   Leboncoin  Google  2ememain   (+ offline "sample")
```

Each source is an isolated plugin: if one site is down, changes its markup, or
blocks the scraper, the run continues with whatever the others returned.

## Install

```bash
pip install -e .
# Chromium for the browser-based sources is already installed in this
# environment. Elsewhere, run once:  playwright install chromium
```

## Usage

```bash
# Search everything near Lyon, ranked and printed as a table
python -m kart_scraper "Lyon, France"

# Narrow it down: 100 km radius, max €3000
python -m kart_scraper "Lyon, France" --radius 100 --max-price 3000

# Pick specific sources (repeatable)
python -m kart_scraper "Lyon, France" -s ebay -s leboncoin

# Tune the rating weights
python -m kart_scraper "Lyon, France" -w "price=0.5,distance=0.3,quality=0.2"

# Export
python -m kart_scraper "Lyon, France" -o csv:karts.csv
python -m kart_scraper "Lyon, France" -o html:report.html

# Try it offline (no network), great for a quick demo
python -m kart_scraper "Lyon, France" -s sample
```

## The rating

Each listing gets four sub-scores in `[0, 1]`, normalized **relative to the
other karts found** (so "cheap" and "near" mean cheap/near *compared to your
actual options*), then combined into a final **0–100** score:

| Component   | Default weight | Higher score when…                          |
|-------------|:--------------:|---------------------------------------------|
| `price`     | 0.35           | cheaper than the other listings             |
| `distance`  | 0.30           | closer to your location (0 if beyond radius)|
| `quality`   | 0.25           | more photos, longer description, real specs |
| `freshness` | 0.10           | posted more recently (decays over ~60 days) |

Weights are configurable with `--weights`.

## Sources

| Source      | Tech                | Notes                                              |
|-------------|---------------------|----------------------------------------------------|
| `ebay`      | requests + bs4      | Most reliable; exposes item location.              |
| `leboncoin` | Playwright          | FR classifieds; DataDome-protected, best-effort.   |
| `google`    | Playwright          | Google Shopping vertical; anti-bot, best-effort.   |
| `2ememain`  | Playwright          | BE/NL classifieds (Adevinta), best-effort.         |
| `sample`    | bundled fixtures    | Offline demo/test data; opt-in via `-s sample`.    |

`--source all` (the default) runs every live source, but **not** `sample`.

## Add a new source

Subclass `BaseSource`, implement `fetch(...)` to return `Listing` objects, and
register it in `kart_scraper/sources/__init__.py`. The base class handles error
isolation, geocoding, distance, and `max_price` filtering for you. See
`kart_scraper/sources/ebay.py` for a minimal example.

## Develop

```bash
pip install -e ".[dev]"
pytest
```

## Caveats

- The browser-based scrapers are **best-effort**: sites change their markup and
  actively fight bots, so results vary. The architecture isolates each source
  so a failure never breaks the whole tool.
- Please respect each site's Terms of Service and `robots.txt`, and keep the
  conservative default rate limits. This tool is for personal, low-volume use.
