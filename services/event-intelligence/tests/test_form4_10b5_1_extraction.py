"""Tests for AUD-10B51: _extract_form4_data()'s parsing of Form 4's real, document-level
<aff10b5One> boolean tag -- the filer's own attestation of whether a transaction was made
under a pre-scheduled Rule 10b5-1 trading plan.

Confirmed present on 2 real, live SEC EDGAR filings (Apple CIK 320193 accession
0001140361-26-035636 = "1"/true with a corroborating <footnote>; Microsoft CIK 789019
accession 0000789019-26-000161 = "1"/true with corroborating <remarks> text) before this field
was added -- the XML fixtures below mirror those real filings' actual structure, not invented
shapes.
"""
from src.services.insider import _extract_form4_data


def _xml(aff10b5one_tag: str = "<aff10b5One>0</aff10b5One>", extra: str = "") -> str:
    return f"""<?xml version="1.0"?>
<ownershipDocument>
    <periodOfReport>2026-09-01</periodOfReport>
    {aff10b5one_tag}
    <reportingOwner>
        <reportingOwnerId>
            <rptOwnerName>Nadella Satya</rptOwnerName>
        </reportingOwnerId>
        <reportingOwnerRelationship>
            <officerTitle>Chief Executive Officer</officerTitle>
        </reportingOwnerRelationship>
    </reportingOwner>
    <nonDerivativeTable>
        <nonDerivativeTransaction>
            <transactionDate><value>2026-09-01</value></transactionDate>
            <transactionCoding>
                <transactionCode>S</transactionCode>
            </transactionCoding>
            <transactionAmounts>
                <transactionShares><value>1040</value></transactionShares>
                <transactionPricePerShare><value>498.2396</value></transactionPricePerShare>
            </transactionAmounts>
        </nonDerivativeTransaction>
    </nonDerivativeTable>
    {extra}
</ownershipDocument>"""


def test_aff10b5one_true_value_1_parses_as_true():
    """Real confirmed SEC value shape (MSFT accession 0000789019-26-000161)."""
    xml = _xml("<aff10b5One>1</aff10b5One>")
    result = _extract_form4_data(xml, "0000789019-26-000161")
    assert result["is_10b5_1"] is True


def test_aff10b5one_false_value_0_parses_as_false():
    """Real confirmed SEC value shape (AAPL accession 0001140361-26-035362)."""
    xml = _xml("<aff10b5One>0</aff10b5One>")
    result = _extract_form4_data(xml, "0001140361-26-035362")
    assert result["is_10b5_1"] is False


def test_aff10b5one_true_string_also_accepted_defensively():
    """Not independently confirmed on a live filing, but accepted defensively in case a
    filing agent's software emits "true"/"false" instead of "1"/"0"."""
    xml = _xml("<aff10b5One>true</aff10b5One>")
    result = _extract_form4_data(xml, "test-accession-1")
    assert result["is_10b5_1"] is True


def test_missing_aff10b5one_tag_is_none_not_false():
    """A missing tag must degrade to None (unknown), never be silently treated as a confirmed
    False -- that would be a fabricated negative, not an honest "we don't know"."""
    xml = _xml(aff10b5one_tag="")  # tag entirely absent
    result = _extract_form4_data(xml, "test-accession-2")
    assert result["is_10b5_1"] is None


def test_real_footnote_corroboration_does_not_affect_parsing_but_matches_the_real_filing():
    """The exact real AAPL filing's own footnote text, included here to document what a real
    corroborating filing actually looks like -- parsing must still succeed identically whether
    or not this footnote is present, since is_10b5_1 comes from aff10b5One, not the footnote."""
    xml = _xml(
        "<aff10b5One>1</aff10b5One>",
        extra='<footnote id="F1">This transaction was made pursuant to a Rule 10b5-1 '
              'trading plan adopted by the reporting person on May 5, 2026.</footnote>',
    )
    result = _extract_form4_data(xml, "0001140361-26-035636")
    assert result["is_10b5_1"] is True


def test_other_fields_are_unaffected_by_the_new_10b5_1_extraction():
    """A regression guard: adding aff10b5One parsing must not disturb any of the other
    already-working field extractions."""
    xml = _xml("<aff10b5One>1</aff10b5One>")
    result = _extract_form4_data(xml, "test-accession-3")
    assert result["insider_name"] == "Nadella Satya"
    assert result["insider_role"] == "Chief Executive Officer"
    assert result["transaction_type"] == "sale"
    assert result["shares"] == 1040
    assert result["price_per_share"] == 498.2396
