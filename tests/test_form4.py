"""Tests for Form 4 detail parsing — offline, against realistic fixtures."""

from __future__ import annotations

from govdata import db
from govdata.form4_parse import (
    extract_xml,
    is_discretionary,
    parse_form4,
)
from govdata.sources import insider_summary, save_form4_filing

OWNERSHIP_XML = """<?xml version="1.0"?>
<ownershipDocument>
  <schemaVersion>X0508</schemaVersion>
  <documentType>4</documentType>
  <periodOfReport>2026-07-15</periodOfReport>
  <issuer>
    <issuerCik>0000789019</issuerCik>
    <issuerName>MICROSOFT CORP</issuerName>
    <issuerTradingSymbol>MSFT</issuerTradingSymbol>
  </issuer>
  <reportingOwner>
    <reportingOwnerId>
      <rptOwnerCik>0001234567</rptOwnerCik>
      <rptOwnerName>DOE JANE</rptOwnerName>
    </reportingOwnerId>
    <reportingOwnerRelationship>
      <isDirector>1</isDirector>
      <isOfficer>1</isOfficer>
      <officerTitle>Chief Financial Officer</officerTitle>
      <isTenPercentOwner>0</isTenPercentOwner>
    </reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <securityTitle><value>Common Stock</value></securityTitle>
      <transactionDate><value>2026-07-15</value></transactionDate>
      <transactionCoding>
        <transactionFormType>4</transactionFormType>
        <transactionCode>P</transactionCode>
      </transactionCoding>
      <transactionAmounts>
        <transactionShares><value>1000</value></transactionShares>
        <transactionPricePerShare><value>400.50</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
      <postTransactionAmounts>
        <sharesOwnedFollowingTransaction><value>25000</value></sharesOwnedFollowingTransaction>
      </postTransactionAmounts>
    </nonDerivativeTransaction>
    <nonDerivativeTransaction>
      <securityTitle><value>Common Stock</value></securityTitle>
      <transactionDate><value>2026-07-15</value></transactionDate>
      <transactionCoding>
        <transactionCode>A</transactionCode>
      </transactionCoding>
      <transactionAmounts>
        <transactionShares><value>5000</value></transactionShares>
        <transactionPricePerShare><value>0</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
  <derivativeTable>
    <derivativeTransaction>
      <securityTitle><value>Employee Stock Option</value></securityTitle>
      <transactionDate><value>2026-07-16</value></transactionDate>
      <transactionCoding>
        <transactionCode>M</transactionCode>
      </transactionCoding>
      <transactionAmounts>
        <transactionShares><value>2000</value></transactionShares>
        <transactionPricePerShare><value>150.00</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
    </derivativeTransaction>
  </derivativeTable>
</ownershipDocument>"""

# EDGAR .txt submissions wrap documents in SGML with <XML> blocks
SUBMISSION_TXT = f"""-----BEGIN PRIVACY-ENHANCED MESSAGE-----
<SEC-DOCUMENT>0000789019-26-000042.txt : 20260716
<SEC-HEADER>stuff here</SEC-HEADER>
<DOCUMENT>
<TYPE>4
<SEQUENCE>1
<FILENAME>form4.xml
<TEXT>
<XML>
{OWNERSHIP_XML}
</XML>
</TEXT>
</DOCUMENT>
</SEC-DOCUMENT>
"""


def test_extract_xml_from_submission_wrapper():
    xml = extract_xml(SUBMISSION_TXT)
    assert xml is not None and xml.startswith("<ownershipDocument")


def test_extract_xml_from_raw_document():
    assert extract_xml(OWNERSHIP_XML) is not None


def test_extract_xml_returns_none_for_junk():
    assert extract_xml("no xml here") is None
    assert extract_xml("") is None


def test_parse_form4_issuer_and_owner():
    f = parse_form4(SUBMISSION_TXT, "0000789019-26-000042")
    assert f is not None
    assert f.issuer_symbol == "MSFT"
    assert f.issuer_name == "MICROSOFT CORP"
    assert f.owner_name == "DOE JANE"
    assert f.is_director and f.is_officer
    assert "Chief Financial Officer" in f.role
    assert "director" in f.role
    assert f.period == "2026-07-15"


def test_parse_form4_transactions_and_values():
    f = parse_form4(OWNERSHIP_XML)
    assert len(f.transactions) == 3
    buy, grant, option = f.transactions
    assert buy.tx_code == "P" and buy.tx_type == "open_market_purchase"
    assert buy.shares == 1000 and buy.price == 400.50
    assert buy.value == 400500.0
    assert buy.signed_value == 400500.0    # acquired -> positive
    assert buy.shares_owned_after == 25000
    assert not buy.is_derivative
    assert grant.tx_code == "A" and grant.value == 0.0
    assert option.is_derivative and option.tx_code == "M"
    assert option.signed_value == -300000.0  # disposed -> negative


def test_discretionary_separates_decisions_from_mechanics():
    """The core distinction: a grant is not a purchase."""
    assert is_discretionary("P") and is_discretionary("S")
    assert not is_discretionary("A")   # grant
    assert not is_discretionary("M")   # option exercise
    assert not is_discretionary("F")   # tax withholding
    assert not is_discretionary("G")   # gift

    f = parse_form4(OWNERSHIP_XML)
    # only the open-market purchase counts toward the discretionary total
    assert f.discretionary_value == 400500.0


def test_parse_form4_handles_malformed_xml():
    assert parse_form4("<ownershipDocument><broken>") is None
    assert parse_form4("") is None


def test_save_and_summarize(tmp_path):
    conn = db.connect(tmp_path / "g.sqlite")
    f = parse_form4(OWNERSHIP_XML, "ACC-1")
    assert save_form4_filing(conn, f) == 3
    assert save_form4_filing(conn, f) == 0  # idempotent

    rows = insider_summary(conn, days=36500)
    assert len(rows) == 1
    r = rows[0]
    assert r["issuer_symbol"] == "MSFT"
    # bought counts only the discretionary purchase, not the grant
    assert r["bought"] == 400500.0
    assert r["sold"] == 0
    # the grant and option exercise land in the mechanical bucket
    assert r["mechanical"] == 300000.0
    assert r["net_discretionary"] == 400500.0
    conn.close()


def test_insider_summary_symbol_filter(tmp_path):
    conn = db.connect(tmp_path / "g.sqlite")
    save_form4_filing(conn, parse_form4(OWNERSHIP_XML, "ACC-1"))
    assert insider_summary(conn, days=36500, symbols=["MSFT"])
    assert insider_summary(conn, days=36500, symbols=["AAPL"]) == []
    conn.close()
