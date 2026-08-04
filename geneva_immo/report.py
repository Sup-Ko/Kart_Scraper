"""Rendering of ranked apartments: console table, CSV, and HTML report."""

from __future__ import annotations

import csv
from importlib import resources
from pathlib import Path
from typing import Sequence

from rich.console import Console
from rich.table import Table

from .models import ApartmentListing


def _fmt_price(listing: ApartmentListing) -> str:
    if listing.price is None:
        return "—"
    return f"{listing.price:,.0f} {listing.currency}".replace(",", "'")


def _fmt_ppm2(listing: ApartmentListing) -> str:
    ppm2 = listing.price_per_m2
    return "—" if ppm2 is None else f"{ppm2:,.0f}".replace(",", "'")


def _fmt_rooms(listing: ApartmentListing) -> str:
    if listing.rooms is None:
        return "—"
    return f"{listing.rooms:g}"


def _fmt_surface(listing: ApartmentListing) -> str:
    return "—" if listing.surface_m2 is None else f"{listing.surface_m2:g} m²"


def _fmt_distance(listing: ApartmentListing) -> str:
    return "—" if listing.distance_km is None else f"{listing.distance_km:.1f} km"


def print_table(listings: Sequence[ApartmentListing], console: Console | None = None) -> None:
    """Pretty-print the ranked listings to the terminal."""
    console = console or Console()
    if not listings:
        console.print("[yellow]No listings found.[/yellow]")
        return

    table = Table(title="Apartments for sale in Geneva — ranked by score", show_lines=False)
    table.add_column("#", justify="right", style="bold")
    table.add_column("Score", justify="right", style="green")
    table.add_column("Title", overflow="fold", max_width=42)
    table.add_column("Price", justify="right")
    table.add_column("Rooms", justify="right")
    table.add_column("Surface", justify="right")
    table.add_column("CHF/m²", justify="right")
    table.add_column("Distance", justify="right")
    table.add_column("Source")

    for rank, listing in enumerate(listings, start=1):
        table.add_row(
            str(rank),
            f"{listing.score:.0f}" if listing.score is not None else "—",
            listing.title,
            _fmt_price(listing),
            _fmt_rooms(listing),
            _fmt_surface(listing),
            _fmt_ppm2(listing),
            _fmt_distance(listing),
            listing.source,
        )
    console.print(table)
    console.print(
        "[dim]Score combines CHF/m² value, size, distance, listing quality and "
        "freshness (0–100, higher is better).[/dim]"
    )


def write_csv(listings: Sequence[ApartmentListing], path: Path) -> None:
    """Write the ranked listings to a CSV file."""
    fields = ["rank", "score", "title", "price", "currency", "rooms", "surface_m2",
              "price_per_m2", "distance_km", "location", "source", "url",
              "value_score", "size_score", "distance_score", "quality_score",
              "freshness_score"]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for rank, l in enumerate(listings, start=1):
            sub = l.subscores
            writer.writerow({
                "rank": rank,
                "score": l.score,
                "title": l.title,
                "price": l.price,
                "currency": l.currency,
                "rooms": l.rooms,
                "surface_m2": l.surface_m2,
                "price_per_m2": None if l.price_per_m2 is None else round(l.price_per_m2),
                "distance_km": None if l.distance_km is None else round(l.distance_km, 1),
                "location": l.location,
                "source": l.source,
                "url": l.url,
                "value_score": sub.get("value"),
                "size_score": sub.get("size"),
                "distance_score": sub.get("distance"),
                "quality_score": sub.get("quality"),
                "freshness_score": sub.get("freshness"),
            })


def write_html(listings: Sequence[ApartmentListing], path: Path, near: str) -> None:
    """Render an HTML report using the bundled Jinja2 template."""
    from jinja2 import Template

    template_text = (
        resources.files("geneva_immo.data").joinpath("report_template.html")
        .read_text(encoding="utf-8")
    )
    html = Template(template_text).render(
        listings=listings,
        near=near,
        fmt_price=_fmt_price,
        fmt_rooms=_fmt_rooms,
        fmt_surface=_fmt_surface,
        fmt_ppm2=_fmt_ppm2,
        fmt_distance=_fmt_distance,
    )
    path.write_text(html, encoding="utf-8")
