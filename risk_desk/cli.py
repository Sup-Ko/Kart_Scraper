"""Command-line interface for risk_desk.

    risk-desk init                 write a sample portfolio CSV
    risk-desk report [options]     compute risk analytics and render the cockpit

By default ``report`` runs fully offline against bundled sample price history so
you can see the cockpit immediately. Point ``--prices`` at your own JSON, or use
``--fetch`` to pull free daily history from stooq (network required).
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .analytics import analyze
from .loader import load_portfolio_csv, write_sample_portfolio
from .prices import StooqProvider, load_prices_json, sample_prices
from .report import render_cockpit
from .scenarios import apply_scenario, default_scenarios


def cmd_init(args: argparse.Namespace) -> int:
    path = Path(args.portfolio)
    if path.exists() and not args.force:
        print(f"{path} already exists. Use --force to overwrite.")
        return 1
    write_sample_portfolio(path)
    print(f"Wrote sample portfolio to {path}")
    print("Edit it with your holdings, then run:  risk-desk report")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    if Path(args.portfolio).exists():
        portfolio = load_portfolio_csv(args.portfolio)
    else:
        print(f"No portfolio at {args.portfolio}; using the bundled sample holdings.")
        from .loader import SAMPLE_PORTFOLIO_CSV
        tmp = Path(args.portfolio)
        tmp.write_text(SAMPLE_PORTFOLIO_CSV, encoding="utf-8")
        portfolio = load_portfolio_csv(tmp)

    # price history
    if args.prices:
        series = load_prices_json(args.prices)
    elif args.fetch:
        print("Fetching daily history from stooq…")
        provider = StooqProvider()
        series = {}
        for t in portfolio.tickers() + [args.benchmark]:
            ps = provider.fetch(t)
            if ps:
                series[t] = ps
            else:
                print(f"  ! could not fetch {t}")
    else:
        series = sample_prices()

    benchmark = series.get(args.benchmark)
    report = analyze(portfolio, series, benchmark=benchmark, confidence=args.confidence)
    results = [apply_scenario(report, s) for s in default_scenarios()]

    out = render_cockpit(report, results, args.out)
    print(f"Portfolio value: {report.base_currency} {report.total_value:,.0f}")
    if report.ann_vol is not None:
        print(f"Annualized vol: {report.ann_vol*100:.1f}%   "
              f"VaR({int(args.confidence*100)}%,1d): {report.var_hist*100:.1f}%   "
              f"ES: {report.expected_shortfall*100:.1f}%   "
              f"Max drawdown: {report.max_drawdown*100:.1f}%")
    print(f"Wrote cockpit to {out.resolve()}")
    print(f"Open it:  file://{out.resolve()}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="risk-desk", description=__doc__)
    parser.add_argument("--portfolio", default="portfolio.csv", help="portfolio CSV path")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="write a sample portfolio CSV")
    p_init.add_argument("--force", action="store_true")
    p_init.set_defaults(func=cmd_init)

    p_rep = sub.add_parser("report", help="compute analytics and render the cockpit")
    p_rep.add_argument("--prices", help="path to a {ticker:{dates,closes}} JSON file")
    p_rep.add_argument("--fetch", action="store_true", help="fetch daily history from stooq")
    p_rep.add_argument("--benchmark", default="SPY", help="benchmark ticker for beta")
    p_rep.add_argument("--confidence", type=float, default=0.95, help="VaR confidence (0-1)")
    p_rep.add_argument("--out", default="cockpit.html", help="output HTML path")
    p_rep.set_defaults(func=cmd_report)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
