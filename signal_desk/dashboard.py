"""Render the heat-map dashboard from the database into a single HTML file."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .scoring import heat_band

_TEMPLATE_DIR = Path(__file__).parent / "data"
_TEMPLATE_NAME = "dashboard_template.html"


def _relative_age(iso: str | None) -> str:
    if not iso:
        return "unknown"
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return "unknown"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - dt
    secs = delta.total_seconds()
    if secs < 3600:
        return f"{int(secs // 60)}m ago"
    if secs < 86400:
        return f"{int(secs // 3600)}h ago"
    return f"{int(secs // 86400)}d ago"


def render_dashboard(rows: list[dict], out_path: Path | str) -> Path:
    """Render ``rows`` (heat-sorted item dicts) to an HTML dashboard file."""
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    env.filters["age"] = _relative_age
    env.filters["band"] = heat_band
    template = env.get_template(_TEMPLATE_NAME)

    self_items = [r for r in rows if r["channel"] == "self"]
    topic_items = [r for r in rows if r["channel"] == "topic"]

    # heat grid: topic (row) x recency bucket (column)
    buckets = ["<6h", "6-24h", "1-3d", ">3d"]

    def bucket_of(row: dict) -> str:
        age = _relative_age(row.get("published") or row.get("collected_at"))
        # age is "<n>m ago" / "<n>h ago" / "<n>d ago" / "unknown"
        if age.endswith("m ago"):
            return "<6h"
        if age.endswith("h ago"):
            return "<6h" if int(age[:-5]) < 6 else "6-24h"
        if age.endswith("d ago"):
            return "1-3d" if int(age[:-5]) <= 3 else ">3d"
        return ">3d"

    topics = sorted({r["topic"] for r in rows})
    grid = {}
    for t in topics:
        grid[t] = {b: {"count": 0, "max_heat": 0.0} for b in buckets}
    for r in rows:
        cell = grid[r["topic"]][bucket_of(r)]
        cell["count"] += 1
        cell["max_heat"] = max(cell["max_heat"], r["heat"])

    html = template.render(
        generated=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        total=len(rows),
        hottest=rows[:12],
        self_items=self_items[:40],
        topic_items=topic_items[:80],
        grid=grid,
        buckets=buckets,
        topics=topics,
    )
    out_path = Path(out_path)
    out_path.write_text(html, encoding="utf-8")
    return out_path
