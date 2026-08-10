"""Render the risk cockpit — a single self-contained HTML file."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .analytics import EXPLAIN, RiskReport
from .scenarios import ScenarioResult

_TEMPLATE_DIR = Path(__file__).parent / "data"
_TEMPLATE_NAME = "cockpit_template.html"


def _pct(x: float | None, digits: int = 1) -> str:
    if x is None:
        return "—"
    return f"{x * 100:.{digits}f}%"


def _money(x: float | None, cur: str = "USD") -> str:
    if x is None:
        return "—"
    sign = "-" if x < 0 else ""
    return f"{sign}{cur} {abs(x):,.0f}"


def _corr_color(v: float) -> str:
    """Diverging color: blue (-1) → neutral (0) → red (+1)."""
    if v >= 0:
        return f"rgba(224,49,49,{0.15 + 0.6 * v:.2f})"
    return f"rgba(51,110,255,{0.15 + 0.6 * abs(v):.2f})"


def render_cockpit(
    report: RiskReport,
    scenarios: list[ScenarioResult],
    out_path: Path | str,
    factors=None,
    advanced=None,
    signals=None,
    policy=None,
) -> Path:
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    env.filters["pct"] = _pct
    env.filters["money"] = lambda x: _money(x, report.base_currency)
    env.filters["corr_color"] = _corr_color
    template = env.get_template(_TEMPLATE_NAME)

    html = template.render(
        r=report,
        scenarios=scenarios,
        f=factors,
        adv=advanced,
        signals=signals,
        policy=policy,
        explain=EXPLAIN,
        generated=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    )
    out_path = Path(out_path)
    out_path.write_text(html, encoding="utf-8")
    return out_path
