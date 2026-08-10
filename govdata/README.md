# govdata

Ingestion and analysis of US public-record disclosure data — federal contract
awards, SEC insider filings, and congressional trade disclosures. All sources
are public government endpoints; nothing here needs credentials.

> **What this is for.** Congressional trade data is treated here as
> **accountability data, not a trading signal** — see below for why that
> distinction drives the whole design. Output is a list of *candidates for
> human review*, never a finding.

## Sources, ordered by how useful the data actually is

| Source | Lag | Verdict |
| --- | --- | --- |
| **USASpending awards** | same day | **Strongest.** An award is public the day it posts and maps directly onto a contractor's future revenue — upstream of the move, not downstream of it. |
| **SEC Form 4** | 2 business days | **Strong.** ~17× fresher than a PTR; insider cluster-buys have a real literature behind them. |
| **House PTRs** | ~30–45 days | **Weak as a signal, excellent for accountability.** By the time a PTR is public the information is priced in and every tracker has published it. |

That ordering is the main design opinion in this package. The congressional
data is the most *interesting-sounding* source and the least useful one for
timing — so it is pointed at the question it can actually answer:
**which public officials traded companies receiving federal money?** The
disclosure lag is irrelevant to that question.

## Setup

```bash
pip install pdfplumber          # only needed to parse PTR PDFs
export GOVDATA_CONTACT="Your Name your@email.com"   # SEC requires a real contact
```

## Use

```bash
govdata ingest              # index PTRs, Form 4s, and contract awards
govdata ingest --awards     # or just one source
govdata pdfs                # download PTR PDFs (25 at a time, queue-safe)
govdata parse               # extract trades from downloaded PDFs
govdata lag                 # disclosure-lag report, with discards reported
govdata conflicts           # trades near federal awards — review candidates
govdata status              # what's in the database
```

Everything lands in `govdata.sqlite`. Re-running is safe: inserts are
idempotent and trades carry a unique row hash, so re-parsing a filing updates
rather than duplicates.

## Parsing: why it is column-aware

The obvious way to parse a PTR table row is to join the cells into one string
and regex-scan it. That silently corrupts every holding whose *name* contains a
date — which is what bonds and options always look like:

| Row | Blob-scanning result | Correct |
| --- | --- | --- |
| `US Treasury Note 4.25% due 11/15/2032` | tx date **2032-11-15** | 2025-01-15 |
| `NVIDIA Corp (NVDA) Call $500 exp 06/20/2025` | tx date **2025-06-20** | 2025-01-15 |
| `Blackstone Real Estate Income Trust (REIT)` | ticker **"REIT"** | none |
| short asset + long amount cell | asset **"$1,000,001 - $5,000,000"** | the asset |

So each cell is **classified** by what it *is* — a bare date, a transaction
code, an amount range, or free text — using anchored `fullmatch`, and roles are
assigned from those classes. A date inside a description stays in the
description. Every case above is pinned by a regression test in
`tests/test_govdata.py`.

This matters more than it looks: a corrupted transaction date produces a
nonsense filing lag, and the natural "clean up" of filtering lags to a plausible
range then **hides the bug** — you get a tidy median computed over an unknown
subset. `govdata lag` therefore reports its own discards:

```
Data quality (reported, not hidden)
  trades total    : 5
  usable lags     : 5
  missing lag     : 0
  implausible lag : 0  <- likely parse errors
  parse issues    : 1
```

Rows that cannot be parsed go into a `parse_issue` table with the reason and
the raw text. Nothing is dropped in silence.

## The work queue

Filings move through explicit states (`indexed → downloaded → parsed`, or
`failed`) with an attempt counter. A single boolean "parsed" flag deadlocks a
`LIMIT`-ed queue: a filing that always fails stays unparsed forever, occupies
the head of the queue, and the pipeline stops progressing while still looking
busy. After `MAX_ATTEMPTS` a filing retires to `failed` with its last error.

## Conflict analysis

`govdata conflicts` pairs disclosed trades with federal awards to
similarly-named companies within a time window (either direction — a trade
shortly *before* an award is as interesting as one after):

```
Pat Smith (CA01)  purchase LMT  ** committee jurisdiction **
  trade 2025-01-15 $15,001-$50,000
  award 2025-01-28 $2,400,000,000 to LOCKHEED MARTIN CORPORATION [Department of Defense]
  sits on Committee on Armed Services — committee jurisdiction includes 'defense'
  gap 13d · name match 1.0 · salience 3.278
```

### Jurisdiction beats timing

Timing proximity alone is weak: a member buying a defense contractor two weeks
before a Pentagon award may simply own a defense fund. The question with actual
weight is whether they sit on a committee with **jurisdiction over the agency
that made the award**.

So `salience` weights jurisdiction far above proximity, and a committee-backed
match outranks a closer-in-time coincidence. Committee assignments come from
the Congress.gov API:

```bash
export CONGRESS_API_KEY=...        # free from api.data.gov
govdata congress                   # fetch members + committee rosters

govdata congress --import assignments.json   # or load offline, no key needed
```

Without either, **everything still works** — conflicts are simply ranked on
timing and name match, and the CLI says so rather than silently omitting the
dimension. The committee→agency jurisdiction map is bundled stable reference
data (`committees.py`); government-wide committees like Appropriations match at
reduced strength, since "they fund every agency" is true but far less pointed
than sitting on the authorizing committee.

**Read these as "look here", never "this happened."** The limits are real and
deliberately surfaced in the output:

- No public crosswalk links award recipients to tickers, so matching is by
  normalized company name and is approximate — hence the graded `name match`
  score rather than a yes/no.
- Disclosed amounts are **ranges**, never exact figures. You cannot size a
  position from this data.
- Many trades are made by managed accounts, spouses, or blind trusts.
- A member may have no involvement at all with the awarding decision.

## Integration with the rest of this repo

- **Signal Desk** — `signal_desk/collectors/govawards.py` turns contract awards
  into scored signal items, so awards appear on the heat-map dashboard
  alongside news. Enable by setting `govdata_db` in `signaldesk.config.json`.
- **risk_desk** — a natural next step is a *policy exposure* lens: what share
  of your portfolio's risk sits in names with active federal contract exposure.

## Known limits of PTR data

- Amounts are ranges, never exact figures.
- Options and derivatives are described inconsistently in free text.
- Filings before ~2020 are scanned handwriting and need OCR; they are recorded
  but will produce parse issues rather than trades.
- Some filings land past the 45-day statutory deadline; `govdata lag` counts
  them.

## Not implemented

- **Senate eFD** — requires POSTing an agreement form for a session cookie.
  Check their robots.txt and terms before automating.
- **Form 4 detail parsing** — the `form4` table stores the index only; the
  per-filing XML at each `url` has the actual transaction details.
- **Congress.gov API** — free key from api.data.gov; committee schedules and
  markup calendars are genuinely forward-looking and would sharpen the conflict
  analysis considerably (trade × *committee jurisdiction*, not just timing).
