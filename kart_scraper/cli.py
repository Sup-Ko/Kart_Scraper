"""Command-line entry point.

Usage:
    python -m kart_scraper "Lyon, France" --radius 100 --max-price 3000
    python -m kart_scraper "Lyon, France" --source sample --output html:report.html
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from rich.console import Console

from .config import DEFAULT_QUERY, DEFAULT_RADIUS_KM, DEFAULT_WEIGHTS
from .geocode import Geocoder
from .models import Listing
from .report import print_table, write_csv, write_html
from .scoring import parse_weights, score_listings
from .sources import SOURCES, get_sources


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kart-scraper",
        description="Search the internet for go-karts for sale near you and rate them.",
    )
    parser.add_argument("location", help="City / postcode / address to search near, e.g. 'Lyon, France'")
    parser.add_argument("-q", "--query", default=DEFAULT_QUERY,
                        help=f"Search terms (default: {DEFAULT_QUERY!r}).")
    parser.add_argument("-r", "--radius", type=float, default=DEFAULT_RADIUS_KM,
                        help=f"Search radius in km (default: {DEFAULT_RADIUS_KM:g}).")
    parser.add_argument("-p", "--max-price", type=float, default=None,
                        help="Maximum price filter.")
    parser.add_argument("-s", "--source", action="append", default=None,
                        help=f"Source(s) to use; repeatable. Options: "
                             f"{', '.join(SOURCES)}, all. Default: all.")
    parser.add_argument("-w", "--weights", default=None,
                        help="Override scoring weights, e.g. "
                             "'price=0.5,distance=0.3,quality=0.2'.")
    parser.add_argument("-o", "--output", default="table",
                        help="Output: 'table' (default), 'csv:PATH', or 'html:PATH'.")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging.")
    return parser


def _dedupe(listings: list[Listing]) -> list[Listing]:
    """Drop duplicates (same listing surfaced by multiple sources)."""
    seen: dict[str, Listing] = {}
    for listing in listings:
        seen.setdefault(listing.fingerprint, listing)
    return list(seen.values())


def run(args: argparse.Namespace) -> int:
    console = Console()
    weights = dict(DEFAULT_WEIGHTS)
    if args.weights:
        weights.update(parse_weights(args.weights))

    selection = args.source or ["all"]
    try:
        sources = get_sources(selection)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        return 2

    geocoder = Geocoder()
    center = geocoder.geocode(args.location)
    if center is None:
        console.print(
            f"[red]Could not geocode location {args.location!r}.[/red] "
            "Try a more specific place (e.g. 'Lyon, France')."
        )
        # Continue anyway: sources still work, distance is just unavailable.

    console.print(
        f"[bold]Searching[/bold] for {args.query!r} near {args.location!r} "
        f"using: {', '.join(s.name for s in sources)} …"
    )

    all_listings: list[Listing] = []
    for source in sources:
        results = source.search(args.query, center, args.radius, args.max_price, geocoder)
        console.print(f"  • {source.label}: {len(results)} listing(s)")
        all_listings.extend(results)

    listings = _dedupe(all_listings)
    ranked = score_listings(listings, weights, radius_km=args.radius)

    return _emit(ranked, args, console)


def _emit(ranked: list[Listing], args: argparse.Namespace, console: Console) -> int:
    kind, _, target = args.output.partition(":")
    kind = kind.lower()
    if kind == "table":
        print_table(ranked, console)
    elif kind == "csv":
        path = Path(target or "karts.csv")
        write_csv(ranked, path)
        console.print(f"[green]Wrote {len(ranked)} listing(s) to {path}[/green]")
    elif kind == "html":
        path = Path(target or "karts.html")
        write_html(ranked, path, args.location)
        console.print(f"[green]Wrote HTML report to {path}[/green]")
    else:
        console.print(f"[red]Unknown output format: {args.output!r}[/red]")
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    # urllib3 emits a retry warning per attempt when an outbound host is
    # blocked; our geocoder already logs one clean warning, so silence the rest.
    logging.getLogger("urllib3").setLevel(logging.ERROR)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
