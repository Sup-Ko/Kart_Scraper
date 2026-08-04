# Kart Scraper 🏎️

Search the internet for **go-karts for sale near you** and rate each listing
with a custom weighted score, so the best-value karts nearby rise to the top.

> Also in this repo: [**geneva-immo**](#geneva-immo--apartments-for-sale-in-geneva-),
> a sibling scraper for apartments for sale in Geneva, built on the same
> infrastructure.

It scrapes several marketplaces in parallel, geocodes every listing to measure
how far it is from you (free, no API key), and ranks the results by a tunable
score that blends **price**, **distance**, **listing quality** and
**freshness**.

## How it works

```
location ─▶ geocode ─┐
query ────▶ sources ─┼─▶ merge + dedupe ─▶ score (weighted) ─▶ table / CSV / HTML
                     │
        ┌────────────┴───────────────────────────────┐
   Leboncoin  Anibis  RacerTrader  RacingJunk  eBay  Google  2ememain
                              (+ offline "sample")
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

| Source        | Tech            | Notes                                                   |
|---------------|-----------------|---------------------------------------------------------|
| `leboncoin`   | Playwright      | FR classifieds; DataDome-protected, best-effort.        |
| `anibis`      | Playwright      | Swiss classifieds (CHF); best-effort.                   |
| `racertrader` | Playwright      | **Specialized** race-kart marketplace (UK/EU, GBP).     |
| `racingjunk`  | Playwright      | **Specialized** racing classifieds, karts category (USD).|
| `ebay`        | requests + bs4  | Most reliable; exposes item location.                   |
| `google`      | Playwright      | Google Shopping vertical; anti-bot, best-effort.        |
| `2ememain`    | Playwright      | BE/NL classifieds (Adevinta), best-effort.              |
| `sample`      | bundled fixtures| Offline demo/test data; opt-in via `-s sample`.         |

`--source all` (the default) runs every live source, but **not** `sample`.

### Currencies

Sources report prices in their native currency (EUR, CHF, GBP, USD). Listings
are always **displayed** in their original currency, but the price sub-score
ranks them on an **approximate EUR-equivalent** (`kart_scraper/money.py`) so a
€2,900 kart and a $3,000 kart are compared fairly. Adjust the static rates in
`money.py` if you need precision.

## Add a new source

Subclass `BaseSource`, implement `fetch(...)` to return `Listing` objects, and
register it in `kart_scraper/sources/__init__.py`. The base class handles error
isolation, geocoding, distance, and `max_price` filtering for you. See
`kart_scraper/sources/ebay.py` for a minimal example.

## geneva-immo — apartments for sale in Geneva 🏠

The `geneva_immo` package reuses the kart scraper's infrastructure (geocoding,
headless-browser helper, Swiss price parsing, error-isolated plugin sources)
to search Swiss property portals for **apartments for sale in and around
Geneva**, and ranks them with a real-estate-specific score.

```bash
# Everything for sale in the canton, ranked by value
python -m geneva_immo

# Family flat: max CHF 1.5M, at least 4 rooms and 90 m²
python -m geneva_immo --max-price 1500000 --min-rooms 4 --min-surface 90

# Studios only (listings advertised as "studio" count as 1 room)
python -m geneva_immo --studios --max-price 600000

# Measure distances from your workplace instead of the city centre
python -m geneva_immo --near "Place des Nations, Genève" --radius 5

# Pick sources, tune weights, export
python -m geneva_immo -s immobilier -s homegate
python -m geneva_immo -w "value=0.5,size=0.2,quality=0.3"
python -m geneva_immo -o html:apartments.html

# Offline demo (no network)
python -m geneva_immo -s sample
```

### The rating

Prices are in CHF; the core value metric is **CHF per m²**, the number every
Geneva buyer compares first. Sub-scores are normalized relative to the other
apartments found:

| Component   | Default weight | Higher score when…                            |
|-------------|:--------------:|-----------------------------------------------|
| `value`     | 0.35           | lower CHF/m² than the other listings          |
| `size`      | 0.15           | more living surface                           |
| `distance`  | 0.15           | closer to `--near` (0 if beyond `--radius`)   |
| `quality`   | 0.25           | photos, description, rooms/surface/floor/year |
| `freshness` | 0.10           | posted more recently (decays over ~90 days)   |

### Sources

| Source        | Tech            | Notes                                            |
|---------------|-----------------|--------------------------------------------------|
| `immobilier`  | requests + bs4  | immobilier.ch — server-rendered, most reliable.  |
| `homegate`    | Playwright      | homegate.ch — largest CH portal, best-effort.    |
| `immoscout24` | Playwright      | immoscout24.ch — agency listings, best-effort.   |
| `anibis`      | Playwright      | anibis.ch — private sellers, best-effort.        |
| `sample`      | bundled fixtures| Offline demo/test data; opt-in via `-s sample`.  |

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
