"""Parse SEC Form 4 ownership documents into individual insider transactions.

The daily index only records that a filing exists. The substance — who traded,
in what role, how many shares, at what price, and *why* — lives in the XML
ownership document inside each submission.

The "why" is the part that matters most and is most often ignored. Form 4
covers open-market purchases and routine compensation mechanics with equal
prominence, but they carry completely different information:

    P  open-market purchase   an insider chose to buy with their own money
    S  open-market sale       chose to sell (often pre-scheduled, see below)
    A  grant / award          the company handed them stock; no decision made
    M  option exercise        mechanical
    F  shares withheld for tax   mechanical
    G  gift                   not an economic view

A headline like "insiders bought $40m of stock" built from code A is
meaningless. :func:`is_discretionary` draws that line explicitly, and the
summary reports the two groups separately rather than blending them.

Parsing is pure and works on a string, so it is fully testable offline.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

# Form 4 transaction codes (SEC Table I/II codes)
TX_CODES: dict[str, str] = {
    "P": "open_market_purchase",
    "S": "open_market_sale",
    "A": "grant_or_award",
    "D": "disposition_to_issuer",
    "F": "tax_withholding",
    "M": "option_exercise",
    "C": "conversion",
    "G": "gift",
    "V": "voluntary_early_report",
    "X": "derivative_exercise",
    "J": "other",
    "K": "equity_swap",
    "U": "tender_of_shares",
}

# Codes reflecting a real discretionary decision by the insider.
DISCRETIONARY = {"P", "S"}

_XML_BLOCK = re.compile(
    r"<(?:XML|xml)>\s*(.*?)\s*</(?:XML|xml)>", re.DOTALL
)
_OWNERSHIP = re.compile(
    r"(<ownershipDocument\b.*?</ownershipDocument>)", re.DOTALL | re.IGNORECASE
)


def is_discretionary(code: str) -> bool:
    """True when the code reflects a decision, not compensation mechanics."""
    return (code or "").strip().upper() in DISCRETIONARY


@dataclass
class Form4Transaction:
    security: str = ""
    tx_date: str | None = None
    tx_code: str = ""
    tx_type: str = ""
    shares: float | None = None
    price: float | None = None
    acquired_disposed: str = ""  # "A" acquired / "D" disposed
    shares_owned_after: float | None = None
    is_derivative: bool = False

    @property
    def value(self) -> float | None:
        if self.shares is None or self.price is None:
            return None
        return round(self.shares * self.price, 2)

    @property
    def discretionary(self) -> bool:
        return is_discretionary(self.tx_code)

    @property
    def signed_value(self) -> float | None:
        """Positive when acquired, negative when disposed."""
        v = self.value
        if v is None:
            return None
        return v if self.acquired_disposed.upper() == "A" else -v


@dataclass
class Form4Filing:
    accession: str = ""
    issuer_cik: str = ""
    issuer_name: str = ""
    issuer_symbol: str = ""
    owner_cik: str = ""
    owner_name: str = ""
    is_director: bool = False
    is_officer: bool = False
    is_ten_percent_owner: bool = False
    officer_title: str = ""
    period: str | None = None
    transactions: list[Form4Transaction] = field(default_factory=list)

    @property
    def role(self) -> str:
        roles = []
        if self.is_director:
            roles.append("director")
        if self.is_officer:
            roles.append(self.officer_title or "officer")
        if self.is_ten_percent_owner:
            roles.append("10% owner")
        return ", ".join(roles)

    @property
    def discretionary_value(self) -> float:
        """Net signed value of decision-driven transactions only."""
        return round(
            sum(t.signed_value or 0.0 for t in self.transactions if t.discretionary), 2
        )


def _text(node, path: str) -> str:
    """Read a field, transparently handling the <field><value>x</value></field> shape."""
    if node is None:
        return ""
    el = node.find(path)
    if el is None:
        return ""
    value = el.find("value")
    target = value if value is not None else el
    return "".join(target.itertext()).strip()


def _number(node, path: str) -> float | None:
    raw = _text(node, path)
    if not raw:
        return None
    try:
        return float(raw.replace(",", "").replace("$", ""))
    except ValueError:
        return None


def _flag(node, path: str) -> bool:
    raw = _text(node, path).strip().lower()
    return raw in ("1", "true", "y", "yes")


def extract_xml(payload: str) -> str | None:
    """Pull the ownership XML out of a full submission text file.

    EDGAR ``.txt`` submissions wrap documents in SGML with ``<XML>`` blocks; a
    raw ``.xml`` URL returns the document directly. Both are accepted.
    """
    if not payload:
        return None
    direct = _OWNERSHIP.search(payload)
    if direct:
        return direct.group(1)
    for block in _XML_BLOCK.findall(payload):
        inner = _OWNERSHIP.search(block)
        if inner:
            return inner.group(1)
    return None


def parse_form4(payload: str, accession: str = "") -> Form4Filing | None:
    """Parse a Form 4 submission into a filing with its transactions."""
    xml = extract_xml(payload)
    if not xml:
        return None
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return None

    filing = Form4Filing(accession=accession)
    filing.period = _text(root, "periodOfReport") or None

    issuer = root.find("issuer")
    if issuer is not None:
        filing.issuer_cik = _text(issuer, "issuerCik")
        filing.issuer_name = _text(issuer, "issuerName")
        filing.issuer_symbol = _text(issuer, "issuerTradingSymbol").upper()

    owner = root.find("reportingOwner")
    if owner is not None:
        ident = owner.find("reportingOwnerId")
        if ident is not None:
            filing.owner_cik = _text(ident, "rptOwnerCik")
            filing.owner_name = _text(ident, "rptOwnerName")
        rel = owner.find("reportingOwnerRelationship")
        if rel is not None:
            filing.is_director = _flag(rel, "isDirector")
            filing.is_officer = _flag(rel, "isOfficer")
            filing.is_ten_percent_owner = _flag(rel, "isTenPercentOwner")
            filing.officer_title = _text(rel, "officerTitle")

    for table, derivative in (("nonDerivativeTable", False), ("derivativeTable", True)):
        section = root.find(table)
        if section is None:
            continue
        tag = "derivativeTransaction" if derivative else "nonDerivativeTransaction"
        for node in section.findall(tag):
            coding = node.find("transactionCoding")
            amounts = node.find("transactionAmounts")
            post = node.find("postTransactionAmounts")

            code = _text(coding, "transactionCode") if coding is not None else ""
            code = code.strip().upper()

            filing.transactions.append(
                Form4Transaction(
                    security=_text(node, "securityTitle"),
                    tx_date=_text(node, "transactionDate") or None,
                    tx_code=code,
                    tx_type=TX_CODES.get(code, code or "unknown"),
                    shares=_number(amounts, "transactionShares") if amounts is not None else None,
                    price=_number(amounts, "transactionPricePerShare") if amounts is not None else None,
                    acquired_disposed=(
                        _text(amounts, "transactionAcquiredDisposedCode").strip().upper()
                        if amounts is not None else ""
                    ),
                    shares_owned_after=(
                        _number(post, "sharesOwnedFollowingTransaction")
                        if post is not None else None
                    ),
                    is_derivative=derivative,
                )
            )

    return filing
