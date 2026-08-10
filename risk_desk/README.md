# risk_desk

An **open, local-first portfolio risk cockpit** — an independent take on the
category of institutional risk platforms (think Aladdin), built to be good
exactly where those are not.

> **Not affiliated with BlackRock.** "Aladdin" is BlackRock's trademark.
> risk_desk implements standard, textbook portfolio analytics on your own data.
> It is **not investment advice**.

## Good where they're not

| Institutional risk platforms | risk_desk |
| --- | --- |
| Six/seven-figure cost, institution-only | Free, self-hosted, runs on your laptop |
| **Black-box** proprietary risk models | **Every number shows its formula** — fully auditable |
| Your positions live on a vendor's servers | **Local-first** — data never leaves your machine |
| Run by the world's largest asset manager (conflict of interest) | Independent — nothing to sell you |
| Proprietary formats, lock-in | Open CSV / JSON / SQLite, portable |
| One shared model → herding / monoculture risk | You **see and edit** every assumption |

The transparency is the product. In the cockpit, every metric has an ⓘ that
reveals its formula, and a "How every number is computed" section spells each
one out. `risk_desk/stats.py` is deliberately pure-Python and reads like the
textbook definitions — audit it yourself.

## What it computes

- **Valuation & weights** — market value, position weights, P&L vs cost basis.
- **Volatility** — annualized σ of daily portfolio returns.
- **Value at Risk (1-day)** — both **historical** (percentile of actual losses)
  and **parametric** (Gaussian z·σ − μ), side by side so the model assumption
  is explicit.
- **Expected Shortfall (CVaR)** — average loss beyond VaR: the tail's severity.
- **Max drawdown** — worst peak-to-trough decline.
- **Beta** — sensitivity to a benchmark (default `SPY`).
- **Correlation matrix** — daily-return correlations, as a heatmap.
- **Risk attribution** — each position's *component contribution to volatility*
  (`wᵢ·(Σw)ᵢ / σₚ`), which sum to total risk. You see **which** positions drive
  risk, not just that risk exists.
- **Concentration** — Herfindahl index and effective number of holdings.
- **Exposures** — aggregated by asset class and sector.
- **Stress scenarios** — editable shock sets (2008-style crash, rates +100bp,
  tech selloff, risk-on rally) re-priced against today's market value.

### Advanced risk engine

- **Multi-factor model** — each asset regressed (OLS) on factor returns
  `rᵢ = αᵢ + Σ βᵢ,f·F_f + εᵢ`, with per-asset betas, R², annualized alpha, and
  specific volatility. Default proxies: Market (SPY), Rates (TLT), Gold (GLD) —
  all overridable, because they are *assumptions*, not truth.
- **Systematic vs specific split** — portfolio variance decomposed into
  `βᵀΣ_Fβ` (factor-driven) and `Σwᵢ²σ²_ε,ᵢ` (idiosyncratic, diversifiable),
  plus each factor's share of variance. This says *what kind* of risk you hold.
- **EWMA volatility** — RiskMetrics λ=0.94, which reacts to regime shifts far
  faster than an equal-weighted window.
- **Monte Carlo VaR / ES** — 20,000 simulated days of correlated returns drawn
  as `μ + L·z`, where `L` is the Cholesky factor of the covariance matrix.
- **VaR attribution** — marginal (`∂VaR/∂wᵢ`), component (sums exactly to total
  VaR by Euler's theorem), and **incremental VaR**: how much risk actually
  leaves if you sell a position. That last one is the question people really
  have, and it is usually the hardest to get out of a risk system.
- **Historical replay** — the worst rolling 1/5/20-day windows your holdings
  genuinely lived through, as the honest complement to hypothetical shocks.

### Signal overlay (bridge to Signal Desk)

If a Signal Desk database is present, the cockpit maps news heat onto the
positions it affects and ranks by **attention = news heat × that position's
share of portfolio risk**. A hot story about something carrying 1% of your risk
should not outrank a warm one about the position driving 40% of it. Matching is
whole-word (so `GLD` never matches inside `GOLDMAN`) and extensible with a
`--aliases` file so `AAPL` also matches "Apple".

### On honesty about the model

When a holding is *also* used as a factor proxy, it regresses against itself:
R²=1 and specific risk 0 — an artifact of the factor choice, not a real absence
of idiosyncratic risk. The cockpit **detects this and says so** in the factor
notes. Surfacing a model's own artifacts, instead of letting a suspiciously
clean number pass as insight, is the whole difference from a black box.

## Install

From the repo root (Python ≥ 3.11):

```bash
pip install -e .
```

Adds the `risk-desk` command. Reuses the project's `requests` + `jinja2`; the
math is pure standard library (no numpy), so there's nothing else to install.

## Use

```bash
# 1. write a sample portfolio CSV (edit it with your holdings)
risk-desk init

# 2. render the cockpit. Runs OFFLINE against bundled sample prices by default:
risk-desk report --out cockpit.html

# 3. use your own daily price history instead:
risk-desk report --prices my_prices.json          # {ticker:{dates,closes}}
# ...or fetch free daily history from stooq (network required):
risk-desk report --fetch --benchmark SPY --confidence 0.99

# 4. overlay Signal Desk news onto your positions (aliases so AAPL ~ "Apple"):
risk-desk report --signals signaldesk.db --aliases aliases.json

# tuning: more/fewer Monte Carlo paths, or skip the advanced engine entirely
risk-desk report --sims 50000
risk-desk report --basic
```

`aliases.json` is simply:

```json
{ "AAPL": ["Apple"], "MSFT": ["Microsoft", "Azure"], "XOM": ["Exxon"] }
```

### Portfolio format (`portfolio.csv`)

```csv
ticker,quantity,cost_basis,asset_class,sector,currency
AAPL,40,150.00,Equity,Technology,USD
TLT,60,98.00,Bond,Government,USD
GLD,20,175.00,Commodity,Metals,USD
```

Only `ticker` and `quantity` are required. `asset_class` and `sector` drive the
exposure aggregation and let stress scenarios target groups of positions.

`portfolio.csv`, `*_prices.json`, and `cockpit.html` stay on your machine (they
are git-ignored) — the whole point is that your positions never leave.

## Architecture

```
risk_desk/
  stats.py         pure-Python statistics (returns, cov/corr, VaR z-scores, drawdown)
  linalg.py        Gaussian elimination, Cholesky, OLS regression
  models.py        Holding / Portfolio / PriceSeries
  prices.py        price providers: sample data, JSON, stooq (free, no key)
  loader.py        portfolio CSV import (open format)
  analytics.py     valuation, risk metrics, risk attribution, exposures (+ EXPLAIN)
  factors.py       multi-factor model & systematic/specific decomposition
  advanced.py      EWMA vol, Monte Carlo VaR, VaR attribution, historical replay
  scenarios.py     stress scenarios and P&L
  bridge.py        Signal Desk news overlay (attention scoring)
  report.py        renders the cockpit HTML
  data/
    cockpit_template.html
    sample_prices.json
  cli.py           init / report
```

Adding a metric is local to `analytics.py`; add its plain-English formula to
`EXPLAIN` and it shows up in the cockpit's transparency section automatically.

## How the key risk numbers work

- **Portfolio returns** come from the actual portfolio *value* series
  (`Σ qtyᵢ · priceᵢ,ₜ`), not a constant-weight approximation.
- **VaR (historical)** = the `(1 − confidence)` percentile of daily returns,
  reported as a positive loss. **VaR (parametric)** = `z·σ_daily − μ_daily` with
  `z = Φ⁻¹(confidence)`. Showing both makes the normality assumption visible.
- **Expected Shortfall** = mean of the losses that exceed the historical VaR
  threshold.
- **Risk contributions** use the covariance matrix `Σ`: component risk
  `= wᵢ·(Σw)ᵢ / σₚ`, and `Σᵢ componentᵢ = σₚ` exactly — a true decomposition.

## Roadmap

- Factor-based VaR and scenario propagation *through* the factor model (shock a
  factor, not just an asset class).
- Credit and FX factors; multi-currency portfolios.
- Efficient-frontier / optimization view.
- Backtesting the VaR model itself (Kupiec / breach-count tests) — grading the
  risk engine's own accuracy, which vendors rarely show you.
