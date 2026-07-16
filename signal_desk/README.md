# Signal Desk

A small, self-hosted **personal intelligence dashboard** — your own "private
Palantir," built only from information you are legally entitled to.

It collects two kinds of signal into one local database and ranks everything by
a **heat** score so the important things surface first:

- **About you** (`self` channel) — public web mentions of your name/handles, and
  breach exposure of your *own* email addresses.
- **Topics** (`topic` channel) — public news and feeds on subjects you follow.

The result is rendered as a single self-contained **heat-map dashboard** HTML
file you open in a browser. No server, no cloud, no account — a SQLite file and
an HTML file on your machine.

## What it collects (and the legal line)

Signal Desk is **legal-by-construction**. It only ever reads:

- **Public feeds** — any RSS/Atom feed you list (news sites, blogs, Hacker News,
  subreddit RSS, etc.). Respect each source's Terms of Service and rate limits.
- **Google News search RSS** — a public endpoint, one query per topic and one
  per identity you own (for self-footprint monitoring).
- **Your own accounts** — the optional Have I Been Pwned collector checks breach
  exposure for the email addresses *you* declare as yours (needs a HIBP API key).

It does **not** scrape sites that forbid it, build dossiers on other private
individuals, or touch anyone's private data. Keep it that way — that's the
whole point.

## Install

From the repo root (Python ≥ 3.11):

```bash
pip install -e .
```

This adds the `signal-desk` command (alongside `kart-scraper`). It reuses the
project's existing `requests` + `jinja2` dependencies; feeds are parsed with the
standard library, so there's nothing else to install.

## Use

```bash
# 1. write a starter config (edit it with your names, emails, topics, feeds)
signal-desk init

# 2. collect from every configured source, score, and store in signaldesk.db
signal-desk collect

# 3. render the heat-map dashboard to dashboard.html and open it
signal-desk dashboard --out dashboard.html
```

Re-run `collect` on a schedule (cron, `launchd`, Task Scheduler) to keep the
database fresh; each run de-duplicates by URL and re-scores. Then re-run
`dashboard` (or point it at the DB from anywhere).

### Config

`signaldesk.config.json` (created by `init`):

```jsonc
{
  "self": {
    "names":   ["Your Name"],       // exact-phrase Google News searches about you
    "emails":  ["you@example.com"],  // checked against HIBP (your own accounts)
    "handles": ["@yourhandle"]
  },
  "topics": [
    { "name": "AI policy", "keywords": ["EU AI Act", "AI regulation"], "weight": 1.2 }
  ],
  "feeds": [
    { "name": "Hacker News", "url": "https://hnrss.org/frontpage",
      "channel": "topic", "topic": "tech", "weight": 0.8 }
  ],
  "priority_keywords": ["breach", "lawsuit", "acquired", "outage"],
  "scoring": { "recency_half_life_hours": 48, "self_weight": 1.6, "priority_boost": 1.5 },
  "hibp_api_key": ""   // optional; enables the breach-check collector
}
```

`signaldesk.config.json`, `signaldesk.db`, and `dashboard.html` are
git-ignored — they're personal and stay on your machine.

## How heat is scored

```
heat = 100 × recency × source_weight × channel_weight × priority_boost
       (normalised so the maximum multipliers map to 100)
```

- **recency** — exponential decay; halves every `recency_half_life_hours`.
- **source_weight** — per-feed / per-topic weight you set.
- **channel_weight** — `self` items are boosted (`self_weight`); things about
  *you* matter more than general topic noise.
- **priority_boost** — applied when a `priority_keyword` appears in the title or
  summary (breach, lawsuit, …).

## Architecture

```
signal_desk/
  config.py           JSON config (self / topics / feeds / scoring)
  models.py           Item dataclass + URL-based de-dup id
  feedparse.py        dependency-free RSS 2.0 / Atom parser
  scoring.py          heat score + hot/warm/cool/cold bands
  db.py               SQLite storage (upsert, dedupe, query)
  collectors/
    base.py           Collector ABC + polite HTTP helper
    rss.py            generic RSS/Atom feeds
    googlenews.py     Google News search (self + topics)
    hibp.py           Have I Been Pwned (your own emails; optional)
  dashboard.py        renders the heat-map HTML
  data/
    dashboard_template.html
  cli.py              init / collect / dashboard
```

Adding a source is one file: subclass `Collector`, implement `collect()`
returning `Item`s, and register it in `collectors/__init__.py`.

## Ideas to grow it

- More collectors: SEC EDGAR filings, GitHub activity on your repos, subreddit
  RSS, a city open-data portal, your own calendar/email export.
- A natural-language Q&A layer over the SQLite DB (ask questions, get cited
  answers).
- Entity linking: connect people/orgs/events across items into a graph.
