"""Tests for T323-DARKPOOL's congress.py changes: Unusual Whales as a real, dedicated
Congressional-trade source tried BEFORE the existing EI-CONGRESS1 kadoa fallback, with rows
from either source flowing through the identical upsert loop via _uw_rows_to_kadoa_shape().

Loads the REAL shared/common/uw_congress.py (bypassing conftest.py's blanket MagicMock stub)
so CongressTradeRow is a real dataclass with real attribute values — matching
test_congress_upsert_amendment.py's own "reload the real thing for this one test" technique,
just for uw_congress.py instead of sqlalchemy.
"""
import sys
import importlib.util
import pathlib

if "common.uw_congress" in sys.modules:
    del sys.modules["common.uw_congress"]
_uwc_path = pathlib.Path(__file__).resolve().parents[3] / "shared" / "common" / "uw_congress.py"
_spec = importlib.util.spec_from_file_location("common.uw_congress", _uwc_path)
_uwc = importlib.util.module_from_spec(_spec)
sys.modules["common.uw_congress"] = _uwc
_spec.loader.exec_module(_uwc)
setattr(sys.modules["common"], "uw_congress", _uwc)

from src.services.congress import _uw_rows_to_kadoa_shape

CongressTradeRow = _uwc.CongressTradeRow


def _row(**kw):
    defaults = dict(
        politician_name="Jane Smith", party="D", chamber="House", ticker="AAPL",
        transaction_type="purchase", amount_min=1001.0, amount_max=15000.0,
        trade_date="2026-08-28", disclosure_date="2026-09-01",
    )
    defaults.update(kw)
    return CongressTradeRow(**defaults)


def test_translates_a_single_row_to_the_kadoa_dict_shape():
    result = _uw_rows_to_kadoa_shape([_row()])
    assert len(result) == 1
    d = result[0]
    assert d["branch"] == "congress"
    assert d["filer_name"] == "Jane Smith"
    assert d["party"] == "D"
    assert d["chamber"] == "House"
    assert d["ticker"] == "AAPL"
    assert d["transaction_type"] == "purchase"
    assert d["amount_range_low"] == 1001.0
    assert d["amount_range_high"] == 15000.0
    assert d["transaction_date"] == "2026-08-28"
    assert d["filing_date"] == "2026-09-01"
    assert d["amount_range_label"] is None  # UW's feed has no equivalent label field


def test_branch_is_always_congress_unlike_kadoas_mixed_feed():
    """UW's own congress endpoint is congress-only — every row must map to branch='congress'
    so it survives sync_congress_trades()'s own `if t.get("branch") != "congress": continue`
    filter (needed for the kadoa feed's mixed House+Senate+executive-branch rows, but harmless
    and necessary for UW rows too, since they flow through the identical loop)."""
    result = _uw_rows_to_kadoa_shape([_row(), _row(ticker="MSFT")])
    assert all(d["branch"] == "congress" for d in result)


def test_translates_multiple_rows_independently():
    rows = [_row(ticker="AAPL"), _row(ticker="MSFT", transaction_type="sale")]
    result = _uw_rows_to_kadoa_shape(rows)
    assert len(result) == 2
    assert result[0]["ticker"] == "AAPL"
    assert result[1]["ticker"] == "MSFT"
    assert result[1]["transaction_type"] == "sale"


def test_translates_an_empty_list_to_an_empty_list():
    assert _uw_rows_to_kadoa_shape([]) == []


def test_none_amount_fields_pass_through_as_none_not_fabricated():
    result = _uw_rows_to_kadoa_shape([_row(amount_min=None, amount_max=None)])
    assert result[0]["amount_range_low"] is None
    assert result[0]["amount_range_high"] is None
