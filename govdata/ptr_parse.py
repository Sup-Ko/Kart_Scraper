"""Extract individual trades from House PTR filings.

The parsing strategy here is deliberately *column-aware*. The obvious approach —
joining a row into one string and regex-scanning it — silently corrupts any
holding whose name contains a date, which is exactly what bonds ("due
11/15/2032") and options ("exp 06/20/2025") always look like. The transaction
date then becomes a maturity date, and the reported filing lag becomes fiction.

So instead each cell is *classified* by what it is (a bare date, a transaction
code, an amount range, or free text) and roles are assigned from those classes.
A date embedded in an asset description stays in the asset description.

Anything that cannot be parsed is recorded as a :class:`ParseIssue` rather than
dropped. A pipeline that quietly discards the rows it cannot handle will report
a clean number computed on an unknown subset — which is worse than no number.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

# --- cell-level patterns. Note the fullmatch anchors: a cell must BE a date /
# amount, not merely contain one. That anchor is the whole fix. ---------------

DATE_ONLY = re.compile(r"\d{1,2}/\d{1,2}/\d{2,4}")
AMOUNT_ONLY = re.compile(
    r"\$?([\d,]+)\s*(?:-|–|—|to)\s*\$?([\d,]+)", re.I
)
AMOUNT_OVER = re.compile(r"(?:\$([\d,]+)\s*\+|over\s+\$([\d,]+))", re.I)
TICKER_IN_PARENS = re.compile(r"\(([A-Z]{1,5}(?:\.[A-Z]{1,2})?)\)")
OWNER_CODE = re.compile(r"\[(SP|DC|JT|HN)\]")

TX_CODES = {
    "P": "purchase",
    "S": "sale_full",
    "S (partial)": "sale_partial",
    "E": "exchange",
}

# Parenthetical tokens that look like tickers but are entity/structure labels.
NOT_TICKERS = {
    "REIT", "LLC", "INC", "LP", "LLP", "PLC", "ETF", "ETN", "NYSE", "AMEX",
    "IRA", "SEP", "LTD", "CO", "CORP", "NA", "US", "USA", "ADR", "CD", "MF",
    "SP", "DC", "JT", "HN", "ST", "GS", "CS", "OP", "OT", "PE", "VC",
}


@dataclass
class ParseIssue:
    doc_id: str
    reason: str
    raw: str


@dataclass
class Trade:
    doc_id: str = ""
    owner: str = ""
    asset: str = ""
    ticker: str | None = None
    tx_type: str = ""
    tx_date: str | None = None
    notif_date: str | None = None
    amount_low: int | None = None
    amount_high: int | None = None
    lag_days: int | None = None

    @property
    def row_hash(self) -> str:
        """Stable identity so re-parsing a filing cannot duplicate rows."""
        basis = f"{self.doc_id}|{self.asset}|{self.tx_type}|{self.tx_date}|{self.amount_low}"
        return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]


@dataclass
class ParseResult:
    trades: list[Trade] = field(default_factory=list)
    issues: list[ParseIssue] = field(default_factory=list)


def clean(s: str | None) -> str:
    """Normalize whitespace and strip the nulls the small-caps font leaves."""
    if not s:
        return ""
    return re.sub(r"\s+", " ", s.replace("\x00", "")).strip()


def iso_date(value: str) -> str | None:
    value = (value or "").strip()
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def classify(cell: str) -> str:
    """Label a cell as 'date', 'tx', 'amount', 'empty' or 'text'.

    Classification uses fullmatch, so "11/15/2032" is a date but "Note due
    11/15/2032" is text — which is precisely the distinction that keeps a bond's
    maturity out of the transaction-date field.
    """
    c = clean(cell)
    if not c:
        return "empty"
    if DATE_ONLY.fullmatch(c):
        return "date"
    if c in ("P", "S", "E") or c.startswith("S (partial)"):
        return "tx"
    if AMOUNT_ONLY.fullmatch(c) or AMOUNT_OVER.fullmatch(c):
        return "amount"
    return "text"


def parse_amount(cell: str) -> tuple[int | None, int | None]:
    c = clean(cell)
    m = AMOUNT_ONLY.fullmatch(c)
    if m:
        return (
            int(m.group(1).replace(",", "")),
            int(m.group(2).replace(",", "")),
        )
    m = AMOUNT_OVER.fullmatch(c)
    if m:
        return int((m.group(1) or m.group(2)).replace(",", "")), None
    return None, None


def extract_ticker(asset: str) -> str | None:
    """Pull a ticker from an asset name, rejecting entity-type labels."""
    for match in TICKER_IN_PARENS.finditer(asset):
        candidate = match.group(1)
        if candidate.upper() not in NOT_TICKERS:
            return candidate
    return None


def _is_header(cells: list[str]) -> bool:
    blob = " ".join(cells).lower()
    return "transaction" in blob and ("notification" in blob or "date" in blob) \
        and not any(classify(c) == "date" for c in cells)


def parse_row(cells: list[str], doc_id: str = "") -> tuple[Trade | None, ParseIssue | None]:
    """Parse one table row into a Trade, or explain why it could not be."""
    cells = [clean(c) for c in cells if c is not None]
    cells = [c for c in cells if c]
    if len(cells) < 3:
        return None, None  # structural filler, not worth reporting

    if _is_header(cells):
        return None, None

    kinds = [classify(c) for c in cells]
    raw = " | ".join(cells)

    dates = [cells[i] for i, k in enumerate(kinds) if k == "date"]
    amounts = [cells[i] for i, k in enumerate(kinds) if k == "amount"]
    txs = [cells[i] for i, k in enumerate(kinds) if k == "tx"]
    texts = [cells[i] for i, k in enumerate(kinds) if k == "text"]

    if not dates:
        return None, ParseIssue(doc_id, "no transaction date cell", raw)
    if not texts:
        return None, ParseIssue(doc_id, "no asset description cell", raw)

    tx_raw = txs[0] if txs else None
    if tx_raw is None:
        # Some layouts glue the code to the date column; recover it narrowly.
        m = re.search(r"\b(P|S \(partial\)|S|E)\b", " ".join(texts))
        tx_raw = m.group(1) if m else None
    if tx_raw is None:
        return None, ParseIssue(doc_id, "no transaction type", raw)

    # The asset is the longest *text* cell — amounts and dates are no longer
    # candidates, so a short ticker-only name can't lose to an amount string.
    asset = max(texts, key=len)

    owner = ""
    om = OWNER_CODE.search(raw)
    if om:
        owner = om.group(1)

    lo, hi = parse_amount(amounts[0]) if amounts else (None, None)

    tx_date = iso_date(dates[0])
    notif_date = iso_date(dates[1]) if len(dates) > 1 else None
    if tx_date is None:
        return None, ParseIssue(doc_id, f"unparseable date {dates[0]!r}", raw)

    return Trade(
        doc_id=doc_id,
        owner=owner,
        asset=asset[:300],
        ticker=extract_ticker(asset),
        tx_type=TX_CODES.get(tx_raw, tx_raw),
        tx_date=tx_date,
        notif_date=notif_date,
        amount_low=lo,
        amount_high=hi,
    ), None


def parse_tables(tables: list[list[list[str]]], doc_id: str = "") -> ParseResult:
    """Parse extracted tables (the pure core — no PDF library needed)."""
    result = ParseResult()
    for table in tables:
        for row in table:
            trade, issue = parse_row(row, doc_id)
            if trade:
                result.trades.append(trade)
            elif issue:
                result.issues.append(issue)
    return result


def parse_pdf(path: Path | str, doc_id: str = "") -> ParseResult:
    """Parse a filing PDF. Requires pdfplumber (optional dependency)."""
    try:
        import pdfplumber
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError(
            "pdfplumber is required to parse PDFs: pip install pdfplumber"
        ) from exc

    path = Path(path)
    doc_id = doc_id or path.stem
    tables: list[list[list[str]]] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            tables.extend(page.extract_tables())
    return parse_tables(tables, doc_id)


def compute_lag(trade: Trade, filing_date: str | None) -> int | None:
    """Days between the transaction and the filing that disclosed it.

    Returns None — never a silently clipped value — when the result is not
    sensible, so the caller can count and report the exclusions.
    """
    if not (trade.tx_date and filing_date):
        return None
    try:
        lag = (date.fromisoformat(filing_date) - date.fromisoformat(trade.tx_date)).days
    except ValueError:
        return None
    return lag
