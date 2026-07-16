"""Command-line interface for Signal Desk.

    signal-desk init        write a starter config file
    signal-desk collect     run collectors, score, and store into the database
    signal-desk dashboard   render the heat-map dashboard from the database
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .collectors import collect_all
from .config import DEFAULT_CONFIG_PATH, DEFAULT_DB_PATH, Config, sample_config_text
from .dashboard import render_dashboard
from .db import Database
from .scoring import score_item


def cmd_init(args: argparse.Namespace) -> int:
    path = Path(args.config)
    if path.exists() and not args.force:
        print(f"{path} already exists. Use --force to overwrite.")
        return 1
    path.write_text(sample_config_text(), encoding="utf-8")
    print(f"Wrote sample config to {path}")
    print("Edit it with your names, emails, topics, and feeds, then run:")
    print("  signal-desk collect")
    return 0


def cmd_collect(args: argparse.Namespace) -> int:
    config = Config.load(args.config)
    print("Collecting…")
    items, errors = collect_all(config)
    for item in items:
        score_item(item, config)
    with Database(args.db) as db:
        new = db.upsert(items)
        total = db.count()
    print(f"Collected {len(items)} items ({new} new). Database now holds {total}.")
    for err in errors:
        print(f"  ! {err}", file=sys.stderr)
    return 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    with Database(args.db) as db:
        rows = db.recent(limit=args.limit, max_age_days=args.max_age_days)
    if not rows:
        print("No items in the database yet. Run `signal-desk collect` first.")
    out = render_dashboard(rows, args.out)
    print(f"Wrote dashboard to {out.resolve()}")
    print(f"Open it in a browser:  file://{out.resolve()}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="signal-desk", description=__doc__)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="config file path")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite database path")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="write a starter config file")
    p_init.add_argument("--force", action="store_true", help="overwrite existing config")
    p_init.set_defaults(func=cmd_init)

    p_collect = sub.add_parser("collect", help="collect, score, and store items")
    p_collect.set_defaults(func=cmd_collect)

    p_dash = sub.add_parser("dashboard", help="render the heat-map dashboard")
    p_dash.add_argument("--out", default="dashboard.html", help="output HTML path")
    p_dash.add_argument("--limit", type=int, default=300, help="max items to render")
    p_dash.add_argument("--max-age-days", type=int, default=None, dest="max_age_days",
                        help="only include items newer than N days")
    p_dash.set_defaults(func=cmd_dashboard)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
