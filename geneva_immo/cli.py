"""Command-line entry point.

Usage:
    python -m geneva_immo --max-price 1500000 --min-rooms 3.5
    python -m geneva_immo --near "Nations, Genève" --source sample -o html:report.html
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from rich.console import Console

from kart_scraper.geocode import Geocoder

from .config import DEFAULT_NEAR, DEFAULT_RADIUS_KM, DEFAULT_WEIGHTS
from .models import ApartmentListing
from .report import print_table, write_csv, write_html
from .scoring import parse_weights, score_listings
from .sources import SOURCES, get_sources


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="geneva-immo",
        description="Search Swiss property portals for apartments for sale in Geneva and rate them.",
    )
    parser.add_argument("-n", "--near", default=DEFAULT_NEAR,
                        help="Reference address distances are measured from, e.g. your "
                             f"workplace (default: {DEFAULT_NEAR!r}).")
    parser.add_argument("-r", "--radius", type=float, default=DEFAULT_RADIUS_KM,
                        help=f"Radius in km around --near (default: {DEFAULT_RADIUS_KM:g}).")
    parser.add_argument("-p", "--max-price", type=float, default=None,
                        help="Maximum price in CHF.")
    parser.add_argument("--min-rooms", type=float, default=None,
                        help="Minimum number of rooms (Swiss 'pièces', e.g. 3.5).")
    parser.add_argument("--min-surface", type=float, default=None,
                        help="Minimum living surface in m².")
    parser.add_argument("-s", "--source", action="append", default=None,
                        help=f"Source(s) to use; repeatable. Options: "
                             f"{', '.join(SOURCES)}, all. Default: all.")
    parser.add_argument("-w", "--weights", default=None,
                        help="Override scoring weights, e.g. "
                             "'value=0.5,size=0.2,quality=0.3'.")
    parser.add_argument("-o", "--output", default="table",
                        help="Output: 'table' (default), 'csv:PATH', or 'html:PATH'.")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging.")
    return parser


def _dedupe(listings: list[ApartmentListing]) -> list[ApartmentListing]:
    """Drop duplicates (same listing surfaced by multiple portals)."""
    seen: dict[str, ApartmentListing] = {}
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
    center = geocoder.geocode(args.near)
    if center is None:
        console.print(
            f"[red]Could not geocode reference point {args.near!r}.[/red] "
            "Distances will be unavailable."
        )
        # Continue anyway: sources still work, distance is just unavailable.

    console.print(
        f"[bold]Searching[/bold] for apartments for sale near {args.near!r} "
        f"using: {', '.join(s.name for s in sources)} …"
    )

    all_listings: list[ApartmentListing] = []
    for source in sources:
        results = source.search(
            center, args.radius, args.max_price, args.min_rooms,
            args.min_surface, geocoder,
        )
        console.print(f"  • {source.label}: {len(results)} listing(s)")
        all_listings.extend(results)

    listings = _dedupe(all_listings)
    ranked = score_listings(listings, weights, radius_km=args.radius)

    return _emit(ranked, args, console)


def _emit(ranked: list[ApartmentListing], args: argparse.Namespace, console: Console) -> int:
    kind, _, target = args.output.partition(":")
    kind = kind.lower()
    if kind == "table":
        print_table(ranked, console)
    elif kind == "csv":
        path = Path(target or "apartments.csv")
        write_csv(ranked, path)
        console.print(f"[green]Wrote {len(ranked)} listing(s) to {path}[/green]")
    elif kind == "html":
        path = Path(target or "apartments.html")
        write_html(ranked, path, args.near)
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
