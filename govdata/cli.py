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
from .fedreg import ingest_federal_register, rules_for_agencies
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
    if args.fedreg or want_all:
        print(f"Federal Register   -> {ingest_federal_register(conn)} new")
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
        if c.pac_count:
            print(f"    {c.recipient} PAC gave ${c.pac_total:,.0f} "
                  f"to this member ({c.pac_count} contribution(s))")
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


def cmd_insiders(args) -> int:
    conn = db.connect(args.db)
    if args.fetch:
        filings, rows = sources.parse_form4_details(conn, args.fetch)
        print(f"parsed {filings} Form 4 filings -> {rows} transactions\n")

    summary = sources.insider_summary(conn, days=args.days,
                                      symbols=args.symbol or None)
    if not summary:
        print("No Form 4 transactions. Run:")
        print("  govdata ingest --form4        # index recent filings")
        print("  govdata insiders --fetch 50   # fetch and parse their details")
        return 0

    print(f"Insider activity, last {args.days} days")
    print("Discretionary = open-market buys/sells only. Grants, option exercises")
    print("and tax withholding are counted separately: they are compensation")
    print("mechanics, not an expression of view.\n")
    print(f"  {'sym':<6} {'net disc.':>14} {'bought':>14} {'sold':>14} "
          f"{'mechanical':>14} {'ppl':>4}")
    for r in summary[: args.limit]:
        print(f"  {r['issuer_symbol']:<6} {r['net_discretionary']:>14,.0f} "
              f"{r['bought'] or 0:>14,.0f} {r['sold'] or 0:>14,.0f} "
              f"{r['mechanical'] or 0:>14,.0f} {r['insiders']:>4}")
    return 0


def cmd_fec(args) -> int:
    from . import fec

    conn = db.connect(args.db)
    if args.import_path:
        n = fec.import_contributions_json(conn, args.import_path)
        print(f"imported {n} contributions from {args.import_path}")
        return 0

    if not fec.have_key(args.api_key):
        print("No FEC API key set. Either:")
        print("  export FEC_API_KEY=...        (free from api.data.gov)")
        print("  govdata fec --import contributions.json")
        print("\nEverything else still works; conflict analysis simply omits")
        print("the campaign-finance dimension.")
        return 1

    companies = args.company
    if not companies:
        companies = [r["recipient"] for r in conn.execute(
            "SELECT DISTINCT recipient FROM award WHERE recipient IS NOT NULL LIMIT 25")]
        if not companies:
            print("No companies given and none found in awards. Use --company.")
            return 1
        print(f"Looking up PACs for {len(companies)} award recipients")

    pacs = fec.ingest_corporate_pacs(conn, companies, args.api_key)
    contribs = fec.ingest_pac_contributions(conn, args.cycle, args.api_key)
    print(f"PACs -> {pacs} new,  contributions -> {contribs} new")
    return 0


def cmd_lobbying(args) -> int:
    from . import lobbying

    conn = db.connect(args.db)
    if args.import_path:
        nf, na = lobbying.import_filings_json(conn, args.import_path)
        print(f"imported {nf} filings / {na} activities from {args.import_path}")
    elif args.client:
        nf, na = lobbying.ingest_lobbying(conn, args.client, args.year, args.api_key)
        print(f"filings -> {nf} new, activities -> {na} new")
    else:
        clients = [r["recipient"] for r in conn.execute(
            "SELECT DISTINCT recipient FROM award WHERE recipient IS NOT NULL LIMIT 25")]
        if not clients:
            print("No client given and none found in awards. Use --client.")
            return 1
        print(f"Fetching lobbying filings for {len(clients)} award recipients")
        nf, na = lobbying.ingest_lobbying(conn, clients, args.year, args.api_key)
        print(f"filings -> {nf} new, activities -> {na} new")

    overlap = lobbying.lobbying_award_overlap(conn)
    if overlap:
        print("\nCompanies lobbying an agency that also awards them contracts:")
        for o in overlap[: args.limit]:
            print(f"  {o['client']}")
            print(f"    lobbied '{o['entity_lobbied']}' in {o['lobby_activities']} "
                  f"activity/activities")
            print(f"    received ${o['award_total']:,.0f} across {o['award_count']} "
                  f"award(s) from {o['agency']}")
        print("\nOverlap is expected and lawful -- a contractor petitions the agency")
        print("that buys from it. What it measures is dependence on one relationship.")
    return 0


def cmd_rules(args) -> int:
    conn = db.connect(args.db)
    agencies = args.agency
    if not agencies:
        agencies = [r["awarding_agy"] for r in conn.execute(
            "SELECT DISTINCT awarding_agy FROM award WHERE awarding_agy IS NOT NULL")]
        if not agencies:
            print("No agencies given and none found in awards. "
                  "Use --agency, or run `govdata ingest --awards` first.")
            return 1
        print(f"Agencies from your award data: {', '.join(agencies)}\n")

    rows = rules_for_agencies(conn, agencies, limit=args.limit,
                              open_comments_only=args.open_comments)
    if not rows:
        print("No matching rules. Run `govdata ingest --fedreg` to populate.")
        return 0
    for r in rows:
        kind = "PROPOSED" if r["doc_type"] == "PRORULE" else r["doc_type"]
        print(f"  [{kind}] {r['title'][:90]}")
        print(f"    {r['agencies']}  published {r['publication_date']}")
        if r["comments_close_on"]:
            print(f"    comments close {r['comments_close_on']}")
        if r["effective_on"]:
            print(f"    effective {r['effective_on']}")
        print(f"    {r['url']}\n")
    return 0


def cmd_status(args) -> int:
    conn = db.connect(args.db)
    q = lambda s: conn.execute(s).fetchone()[0]
    print(f"  PTR filings : {q('SELECT COUNT(*) FROM ptr_filing')}  {db.status_counts(conn)}")
    print(f"  PTR trades  : {q('SELECT COUNT(*) FROM ptr_trade')}")
    print(f"  Parse issues: {q('SELECT COUNT(*) FROM parse_issue')}")
    print(f"  Form 4      : {q('SELECT COUNT(*) FROM form4')}")
    print(f"  Awards      : {q('SELECT COUNT(*) FROM award')}")
    print(f"  Form 4 txns : {q('SELECT COUNT(*) FROM form4_transaction')}")
    print(f"  Fed Register: {q('SELECT COUNT(*) FROM fedreg_doc')}")
    print(f"  PAC contribs: {q('SELECT COUNT(*) FROM pac_contribution')}")
    print(f"  Lobby filings: {q('SELECT COUNT(*) FROM lobby_filing')}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="govdata", description=__doc__)
    p.add_argument("--db", default=str(db.DEFAULT_DB))
    sub = p.add_subparsers(dest="command", required=True)

    i = sub.add_parser("ingest", help="pull from public endpoints")
    i.add_argument("--awards", action="store_true")
    i.add_argument("--form4", action="store_true")
    i.add_argument("--ptr", action="store_true")
    i.add_argument("--fedreg", action="store_true")
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

    fr = sub.add_parser("rules", help="Federal Register rules by agency")
    fr.add_argument("--agency", action="append", default=[],
                    help="agency name (repeatable); default: agencies seen in awards")
    fr.add_argument("--open-comments", action="store_true", dest="open_comments",
                    help="only rules whose comment period is still open")
    fr.add_argument("--limit", type=int, default=20)
    fr.set_defaults(func=cmd_rules)

    ins = sub.add_parser("insiders", help="Form 4 insider transactions")
    ins.add_argument("--fetch", type=int, metavar="N",
                     help="fetch and parse detail for N unparsed filings first")
    ins.add_argument("--days", type=int, default=90)
    ins.add_argument("--symbol", action="append", default=[],
                     help="restrict to ticker(s), repeatable")
    ins.add_argument("--limit", type=int, default=20)
    ins.set_defaults(func=cmd_insiders)

    fe = sub.add_parser("fec", help="corporate PAC campaign finance")
    fe.add_argument("--api-key", dest="api_key")
    fe.add_argument("--company", action="append", default=[],
                    help="company name (repeatable); default: award recipients")
    fe.add_argument("--cycle", type=int, default=2026)
    fe.add_argument("--import", dest="import_path",
                    help="load contributions from a saved JSON response")
    fe.set_defaults(func=cmd_fec)

    lb = sub.add_parser("lobbying", help="LDA lobbying disclosures")
    lb.add_argument("--api-key", dest="api_key", help="LDA token (optional)")
    lb.add_argument("--client", action="append", default=[],
                    help="client company (repeatable); default: award recipients")
    lb.add_argument("--year", type=int, default=None)
    lb.add_argument("--import", dest="import_path",
                    help="load filings from a saved JSON response")
    lb.add_argument("--limit", type=int, default=10)
    lb.set_defaults(func=cmd_lobbying)

    st = sub.add_parser("status", help="what's in the database")
    st.set_defaults(func=cmd_status)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
