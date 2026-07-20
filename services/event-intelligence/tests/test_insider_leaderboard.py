"""Tests for insider.py's _build_insider_leaderboard().

AUD-INSIDERTOPBUYS-NETNEGATIVE: get_insider_leaderboard() is named/consumed everywhere as a
"Top Buys" leaderboard (route /events/insider/leaderboard, reports.tsx's "Insider Top Buys"
card, intelligence.tsx's Overview tab) — but previously sorted ALL stocks by net_value with no
floor at zero, so a stock with heavy net selling (net_value < 0) could still appear under a
"Top Buys" heading whenever fewer than `limit` stocks had genuinely positive net buying in the
window. Fixed by filtering to net_value > 0 before truncating to `limit`.
"""
from src.services.insider import _build_insider_leaderboard


def _txn(stock_id, symbol, transaction_type, total_value, company="Test Co"):
    return {
        "stock_id": stock_id, "symbol": symbol, "company": company,
        "transaction_type": transaction_type, "total_value": total_value,
    }


def test_net_negative_stock_is_excluded_even_when_it_would_otherwise_make_the_cut():
    """The exact bug: a stock with heavy net SELLING must never appear in a 'Top Buys' result,
    even when there are fewer than `limit` genuine buyers to fill the list out."""
    rows = [
        _txn(1, "AAPL", "purchase", 50_000),
        _txn(2, "TSLA", "sale", 500_000),  # net_value = -500,000 — a real net seller
    ]
    result = _build_insider_leaderboard(rows, limit=20)
    symbols = {r["symbol"] for r in result}
    assert "AAPL" in symbols
    assert "TSLA" not in symbols


def test_returns_fewer_than_limit_rows_when_fewer_than_limit_are_genuine_buyers():
    """A window with only 1 genuine net buyer must return 1 row, not pad out to `limit` with
    net sellers to hit the requested count."""
    rows = [
        _txn(1, "AAPL", "purchase", 10_000),
        _txn(2, "TSLA", "sale", 999_999),
        _txn(3, "MSFT", "sale", 1),
    ]
    result = _build_insider_leaderboard(rows, limit=20)
    assert len(result) == 1
    assert result[0]["symbol"] == "AAPL"


def test_exactly_zero_net_value_is_also_excluded():
    """A stock with equal buying and selling nets to exactly zero — not a real 'buy' either,
    matching the strict net_value > 0 filter (not >= 0)."""
    rows = [
        _txn(1, "AAPL", "purchase", 10_000),
        _txn(1, "AAPL", "sale", 10_000),
    ]
    result = _build_insider_leaderboard(rows, limit=20)
    assert result == []


def test_genuine_net_buyers_are_still_sorted_descending_by_net_value():
    """The fix must not disturb the pre-existing sort order among real buyers."""
    rows = [
        _txn(1, "AAPL", "purchase", 10_000),
        _txn(2, "MSFT", "purchase", 50_000),
        _txn(3, "GOOG", "purchase", 30_000),
    ]
    result = _build_insider_leaderboard(rows, limit=20)
    assert [r["symbol"] for r in result] == ["MSFT", "GOOG", "AAPL"]


def test_limit_still_applies_after_filtering_out_net_sellers():
    rows = [
        _txn(1, "A", "purchase", 100),
        _txn(2, "B", "purchase", 200),
        _txn(3, "C", "purchase", 300),
    ]
    result = _build_insider_leaderboard(rows, limit=2)
    assert len(result) == 2
    assert [r["symbol"] for r in result] == ["C", "B"]


def test_purchases_and_sales_counts_are_still_tracked_correctly():
    """The filter only affects which stocks are RETURNED — purchases/sales counts on a
    surviving row must still reflect the real underlying activity, not just net_value."""
    rows = [
        _txn(1, "AAPL", "purchase", 100_000),
        _txn(1, "AAPL", "sale", 20_000),
    ]
    result = _build_insider_leaderboard(rows, limit=20)
    assert len(result) == 1
    assert result[0]["purchases"] == 1
    assert result[0]["sales"] == 1
    assert result[0]["net_value"] == 80_000


def test_none_total_value_treated_as_zero_not_a_crash():
    rows = [_txn(1, "AAPL", "purchase", None)]
    result = _build_insider_leaderboard(rows, limit=20)
    # net_value == 0 → excluded by the net-positive filter, and must not raise.
    assert result == []


def test_empty_input_returns_empty_list():
    assert _build_insider_leaderboard([], limit=20) == []
