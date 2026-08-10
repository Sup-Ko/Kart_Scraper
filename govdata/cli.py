"""Command-line interface for govdata.

    govdata ingest [--awards|--form4|--ptr]  pull from public endpoints
    govdata pdfs                             download PTR PDFs (queue-safe)
    govdata parse                            extract trades from downloaded PDFs
    govdata lag                              disclosure-lag report, with discards
    govdata conflicts                        trades near federal awards (review list)
    govdata status                           what's in the database
"""

from __future__ import annotations

import argparse

from . import db, sources
from .conflicts import find_conflicts, lag_report


def cmd_ingest(args) -> int:
    conn = db.connect(args.db)
    want_all = not (args.awards or args.form4 or args.ptr)
    if args.ptr or want_all:
        print(f"House PTR index    -> {sources.ingest_house_ptr(conn)} new")
    if args.form4 or want_all:
        print(f"EDGAR Form 4 ({args.days}d) -> {sources.backfill_form4(conn, args.days)} new")
    if args.awards or want_all:
        print(f"USASpending awards -> {sources.ingest_awards(conn)} new")
    return 0


def cmd_pdfs(args) -> int:
    conn = db.connect(args.db)
    print(f"downloaded {sources.download_ptr_pdfs(conn, args.limit)} PDFs")
    print("queue:", db.status_counts(conn))
    return 0


def cmd_parse(args) -> int:
    conn = db.connect(args.db)
    total, flagged = sources.parse_downloaded(conn, args.limit)
    print(f"parsed -> {total} trades ({flagged} filings had unparseable rows)")
    print("queue:", db.status_counts(conn))
    return 0


def cmd_lag(args) -> int:
    conn = db.connect(args.db)
    r = lag_report(conn)
    if not r.get("usable"):
        print("no usable lag data yet")
        print(f"  trades: {r['trades_total']}  parse issues: {r['parse_issues']}")
        return 0
    print("Disclosure lag (days between trade and filing)")
    print(f"  mean            : {r['mean']}")
    print(f"  median          : {r['median']}")
    print(f"  range           : {r['min']} - {r['max']}")
    print(f"  past 45d deadline: {r['over_45_day_deadline']}")
    print("\nData quality (reported, not hidden)")
    print(f"  trades total    : {r['trades_total']}")
    print(f"  usable lags     : {r['usable']}")
    print(f"  missing lag     : {r['missing_lag']}")
    print(f"  implausible lag : {r['implausible_lag']}  <- likely parse errors")
    print(f"  parse issues    : {r['parse_issues']}")
    return 0


def cmd_conflicts(args) -> int:
    conn = db.connect(args.db)
    rows = find_conflicts(conn, window_days=args.window, min_score=args.min_score)
    if not rows:
        print("no candidates found (need both PTR trades and awards ingested)")
        return 0

    have_committees = conn.execute(
        "SELECT COUNT(*) n FROM member_committee"
    ).fetchone()["n"]

    print(f"{len(rows)} candidate(s) for review — coincidences, not findings:\n")
    for c in rows[: args.limit]:
        award = f"${c.award_amount:,.0f}" if c.award_amount else "n/a"
        flag = "  ** committee jurisdiction **" if c.jurisdiction_strength >= 1.0 else ""
        print(f"  {c.member} ({c.state_dst})  {c.tx_type} {c.ticker or c.asset[:30]}{flag}")
        print(f"    trade {c.tx_date} {c.amount_range}")
        print(f"    award {c.award_date} {award} to {c.recipient} [{c.awarding_agency}]")
        if c.jurisdiction:
            print(f"    sits on {c.jurisdiction} — {c.jurisdiction_basis}")
        print(f"    gap {c.gap_days}d · name match {c.name_score} · salience {c.salience}\n")

    if not have_committees:
        print("NOTE: no committee assignments loaded, so these are ranked on timing")
        print("      and name match alone. Add them for the jurisdiction dimension:")
        print("        govdata congress --import assignments.json   (offline)")
        print("        CONGRESS_API_KEY=... govdata congress        (live)")
    print("\nName matching is approximate, amounts are ranges, and many trades are")
    print("made by managed accounts or blind trusts. Verify before concluding anything.")
    return 0


def cmd_congress(args) -> int:
    from . import congress

    conn = db.connect(args.db)
    if args.import_path:
        n = congress.import_assignments_json(conn, args.import_path)
        print(f"imported {n} committee assignments from {args.import_path}")
        return 0
    if not congress.have_key(args.api_key):
        print("No Congress.gov API key set. Either:")
        print("  export CONGRESS_API_KEY=...   (free from api.data.gov)")
        print("  govdata congress --import assignments.json")
        print("\nEverything else still works; conflict analysis will simply rank")
        print("on timing and name match without the jurisdiction dimension.")
        return 1
    members = congress.ingest_members(conn, args.congress, args.api_key)
    seats = congress.ingest_committee_assignments(
        conn, args.congress, args.chamber, args.api_key
    )
    print(f"members -> {members} new,  committee seats -> {seats} new")
    return 0


def cmd_status(args) -> int:
    conn = db.connect(args.db)
    q = lambda s: conn.execute(s).fetchone()[0]
    print(f"  PTR filings : {q('SELECT COUNT(*) FROM ptr_filing')}  {db.status_counts(conn)}")
    print(f"  PTR trades  : {q('SELECT COUNT(*) FROM ptr_trade')}")
    print(f"  Parse issues: {q('SELECT COUNT(*) FROM parse_issue')}")
    print(f"  Form 4      : {q('SELECT COUNT(*) FROM form4')}")
    print(f"  Awards      : {q('SELECT COUNT(*) FROM award')}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="govdata", description=__doc__)
    p.add_argument("--db", default=str(db.DEFAULT_DB))
    sub = p.add_subparsers(dest="command", required=True)

    i = sub.add_parser("ingest", help="pull from public endpoints")
    i.add_argument("--awards", action="store_true")
    i.add_argument("--form4", action="store_true")
    i.add_argument("--ptr", action="store_true")
    i.add_argument("--days", type=int, default=7, help="Form 4 backfill days")
    i.set_defaults(func=cmd_ingest)

    d = sub.add_parser("pdfs", help="download PTR PDFs")
    d.add_argument("--limit", type=int, default=25)
    d.set_defaults(func=cmd_pdfs)

    pa = sub.add_parser("parse", help="extract trades from downloaded PDFs")
    pa.add_argument("--limit", type=int, default=None)
    pa.set_defaults(func=cmd_parse)

    lg = sub.add_parser("lag", help="disclosure-lag report with data-quality counts")
    lg.set_defaults(func=cmd_lag)

    cf = sub.add_parser("conflicts", help="trades near federal awards (review list)")
    cf.add_argument("--window", type=int, default=90, help="max days between trade and award")
    cf.add_argument("--min-score", type=float, default=0.6, dest="min_score")
    cf.add_argument("--limit", type=int, default=25)
    cf.set_defaults(func=cmd_conflicts)

    cg = sub.add_parser("congress", help="member committee assignments (Congress.gov)")
    cg.add_argument("--api-key", dest="api_key", help="api.data.gov key")
    cg.add_argument("--congress", type=int, default=119)
    cg.add_argument("--chamber", default="house", choices=["house", "senate"])
    cg.add_argument("--import", dest="import_path",
                    help="load assignments from a local JSON file instead")
    cg.set_defaults(func=cmd_congress)

    st = sub.add_parser("status", help="what's in the database")
    st.set_defaults(func=cmd_status)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
