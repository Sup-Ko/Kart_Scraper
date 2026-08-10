# risk-desk

**An open, auditable portfolio risk cockpit.** Factor models, Value at Risk with
statistical backtesting, Monte Carlo simulation and stress scenarios — running
entirely on your own machine, in pure Python, with the formula behind every
number visible on screen.

Institutional risk platforms are black boxes: proprietary models, six-figure
licences, and your positions living on a vendor's servers. `risk-desk` is the
opposite of each of those.

> Not affiliated with any asset manager. Analytics on your own data — not
> investment advice.

```
Portfolio value: USD 37,374
Annualized vol: 14.5%   VaR(95%,1d): 1.4%   ES: 1.8%   Max drawdown: 6.3%
Factors: βMarket +0.80  βRates +0.18  βGold +0.08   systematic 76% / specific 24%
Monte Carlo VaR: 1.5%   MC ES: 1.9%   EWMA vol: 15.3%
Backtest historical  11 breaches vs  6.6 expected (8.4%)  Kupiec p=0.10  well calibrated
Backtest ewma         6 breaches vs  6.6 expected (4.6%)  Kupiec p=0.82  well calibrated ★
Worst factor scenario: Equity bear market -16.0% (USD -5,966)
```

---

## Engineering notes

The parts of this project I'd actually point at.

**A bug that concealed itself.** Filing rows were parsed by joining cells and
regex-scanning the result, which silently pulled dates out of asset *names* —
so a bond "due 11/15/2032" set its own transaction date to 2032 and produced a
nonsense holding period. The dangerous part wasn't the bug, it was the obvious
cleanup: filtering results to a plausible range *hides* it, leaving a tidy
median computed over an unknown subset. The fix was structural (classify each
cell by what it *is*, using anchored matches) and the reporting now counts its
own discards rather than quietly dropping them.

**A model artifact, disclosed and then removed.** Using TLT as both a holding
and the rates factor proxy makes the regression run against itself — R² = 1.000
and zero specific risk, which looks like a beautifully explained position and
is actually an artifact of the factor choice. The cockpit detects and states
this rather than letting a clean number pass as insight. Real Treasury yields
from FRED then eliminate it at the root: TLT's R² drops below 0.9 and genuine
idiosyncratic risk returns. Paired tests pin both halves.

**A migration bug unit tests structurally could not catch.** Adding a column
worked in every test, because every test built a fresh database. Against a
database from a previous version, `CREATE TABLE IF NOT EXISTS` leaves the old
table alone and the new index fails. Found only by running the CLI against a
real database; fixed with a migration step, with a regression test that opens a
genuinely old schema.

**A false positive that would have manufactured risk.** Name matching scored
`Apple Inc` against `Apple Valley Sanitation District` at 0.8 on a prefix rule
— enough to invent portfolio exposure that doesn't exist. A shared prefix now
only scores highly when it accounts for most of *both* names.

The through-line: the failures worth catching are the ones that produce
plausible-looking numbers.

## What it computes

**Core** — market value, weights, P&L, annualized volatility, **VaR** (historical
*and* parametric, side by side so the normality assumption is visible),
**Expected Shortfall**, max drawdown, beta, correlation matrix, **component risk
attribution**, HHI concentration, exposures by asset class and sector.

**Factor model** — each asset regressed on factor returns
(`rᵢ = αᵢ + Σ βᵢ,f·F_f + εᵢ`) with per-asset betas, R², alpha and specific
volatility. Portfolio variance splits into **systematic** (`βᵀΣ_Fβ`) and
**specific** (`Σwᵢ²σ²_ε`) — telling you *what kind* of risk you hold. Factors
can come from ETF proxies or real FRED yield/credit series.

**Advanced** — RiskMetrics **EWMA volatility**, **Monte Carlo VaR/ES** via
Cholesky-factored correlated draws, **marginal / component / incremental VaR**
(including "how much risk actually leaves if I sell this?"), and **historical
replay** of the worst rolling 1/5/20-day windows.

**VaR backtesting** — the part vendors rarely show. A VaR number is a falsifiable
prediction: at 95% confidence losses should exceed it ~5% of days. Walk-forward
testing re-estimates VaR from a trailing window using only data available at
that point, counts breaches, and runs the **Kupiec proportion-of-failures test**
against an exact chi-square survival function. Three methods are graded side by
side, because which is best calibrated is an empirical question about *your*
holdings.

**Scenarios** — shocks in factor space propagate through each position's own
betas, so a 20% market selloff moves a β=1.20 name −24% and a β=0.68 name −14%
rather than assuming every equity falls together. Only the systematic response
is modelled, and positions without a factor fit are reported, not silently held
flat.

**Policy exposure** — optional: joins public contract-award data to holdings and
reports what share of portfolio **risk** (not just value) sits in names dependent
on government contracting.

## Install

Python ≥ 3.11. Only `requests` and `jinja2` — all the mathematics is standard
library, so it can be read and audited without a numerical stack.

```bash
pip install -e .
```

## Use

```bash
risk-desk init                        # writes a sample portfolio.csv
risk-desk report --out cockpit.html   # runs offline on bundled sample prices

risk-desk report --fetch              # free daily history from stooq
risk-desk report --prices my.json     # or your own {ticker:{dates,closes}}
risk-desk report --fred               # real Treasury/credit factors (free key)
risk-desk report --confidence 0.99 --sims 50000
```

`portfolio.csv` is an open format — only `ticker` and `quantity` are required:

```csv
ticker,quantity,cost_basis,asset_class,sector,currency,company
AAPL,40,150.00,Equity,Technology,USD,Apple Inc
TLT,60,98.00,Bond,Government,USD,
```

Your portfolio, prices and generated cockpit are git-ignored. Nothing leaves
your machine.

## Architecture

```
risk_desk/
  stats.py       returns, covariance/correlation, percentiles, drawdown, normal quantiles
  linalg.py      Gaussian elimination w/ pivoting, Cholesky (ridge fallback), OLS
  models.py      Holding / Portfolio / PriceSeries
  prices.py      providers: bundled sample, JSON, stooq
  loader.py      portfolio CSV import
  analytics.py   valuation, risk metrics, attribution, exposures (+ EXPLAIN)
  factors.py     multi-factor model, systematic/specific decomposition
  advanced.py    EWMA, Monte Carlo VaR, VaR attribution, historical replay
  backtest.py    walk-forward VaR backtest, Kupiec test
  scenarios.py   asset-class and factor-space stress scenarios
  fred.py        real economic series as factors
  policy.py      contract-award exposure lens
  bridge.py      optional overlay of external news/signal data onto positions
  namematch.py   graded company-name matching
  report.py      renders the cockpit
  cli.py         init / report
```

Adding a metric is local to one module; add its plain-English formula to
`EXPLAIN` and it appears in the cockpit's transparency section automatically.

## Testing

64 tests, run with `pytest`. The numerical ones assert against values verified
by hand rather than against previous output:

- chi-square critical points (3.841 → 0.05, 6.635 → 0.01)
- inverse-normal quantiles (Φ⁻¹(0.95) = 1.6448536)
- Cholesky reconstruction `L·Lᵀ = A`
- OLS beta checked against `cov(y,x)/var(x)`
- Euler additivity: component VaR sums exactly to total VaR
- Monte Carlo VaR agreeing with the parametric closed form on Gaussian data
- factor betas recovered from data generated with known betas

Plus regression tests pinning each defect in *Engineering notes* above.

## Honest limits

- **Daily closes only.** No intraday, volume, options or borrow data. This is a
  risk and portfolio-construction tool, not a trading system.
- **Price adjustment depends on your source.** Free feeds are not always split-
  and dividend-adjusted; unadjusted prices produce wrong returns.
- **Single portfolio.** No cross-sectional ranking across a universe.
- **No position sizing or transaction costs.** It measures risk; it does not
  tell you what to trade.
- **Scenarios model the systematic response only** — an idiosyncratic
  single-name blowup will not appear.
- **Company-name matching is approximate**, which is why every match carries a
  graded confidence score rather than a yes/no.
