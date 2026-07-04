"""Rendering of ranked results: console table, CSV, and HTML report."""

from __future__ import annotations

import csv
from importlib import resources
from pathlib import Path
from typing import Sequence

from rich.console import Console
from rich.table import Table

from .models import Listing


def _fmt_price(listing: Listing) -> str:
    if listing.price is None:
        return "—"
    return f"{listing.price:,.0f} {listing.currency}".replace(",", " ")


def _fmt_distance(listing: Listing) -> str:
    return "—" if listing.distance_km is None else f"{listing.distance_km:,.0f} km"


def print_table(listings: Sequence[Listing], console: Console | None = None) -> None:
    """Pretty-print the ranked listings to the terminal."""
    console = console or Console()
    if not listings:
        console.print("[yellow]No listings found.[/yellow]")
        return

    table = Table(title="Karts for sale — ranked by score", show_lines=False)
    table.add_column("#", justify="right", style="bold")
    table.add_column("Score", justify="right", style="green")
    table.add_column("Title", overflow="fold", max_width=48)
    table.add_column("Price", justify="right")
    table.add_column("Distance", justify="right")
    table.add_column("Source")

    for rank, listing in enumerate(listings, start=1):
        table.add_row(
            str(rank),
            f"{listing.score:.0f}" if listing.score is not None else "—",
            listing.title,
            _fmt_price(listing),
            _fmt_distance(listing),
            listing.source,
        )
    console.print(table)
    console.print(
        "[dim]Score combines price, distance, listing quality and freshness "
        "(0–100, higher is better).[/dim]"
    )


def write_csv(listings: Sequence[Listing], path: Path) -> None:
    """Write the ranked listings to a CSV file."""
    fields = ["rank", "score", "title", "price", "currency", "distance_km",
              "location", "source", "url", "price_score", "distance_score",
              "quality_score", "freshness_score"]
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
                "distance_km": None if l.distance_km is None else round(l.distance_km, 1),
                "location": l.location,
                "source": l.source,
                "url": l.url,
                "price_score": sub.get("price"),
                "distance_score": sub.get("distance"),
                "quality_score": sub.get("quality"),
                "freshness_score": sub.get("freshness"),
            })


def write_html(listings: Sequence[Listing], path: Path, location: str) -> None:
    """Render an HTML report using the bundled Jinja2 template."""
    from jinja2 import Template

    template_text = (
        resources.files("kart_scraper.data").joinpath("report_template.html")
        .read_text(encoding="utf-8")
    )
    html = Template(template_text).render(
        listings=listings,
        location=location,
        fmt_price=_fmt_price,
        fmt_distance=_fmt_distance,
    )
    path.write_text(html, encoding="utf-8")
