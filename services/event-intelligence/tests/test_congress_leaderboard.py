"""Tests for congress.py's _build_congress_leaderboard().

AUD-INSIDERTOPBUYS-NETNEGATIVE: same bug class as insider.py's leaderboard (see that test
file's docstring) — get_congress_leaderboard() is named/consumed as a "Top Buys" leaderboard
(route /events/congress/leaderboard, reports.tsx's "Congress Top Buys" card) but previously had
no floor at zero, so a stock with heavy net SELLING by politicians could still appear under a
"Top Buys" heading. Fixed by filtering to net_amount > 0 before truncating to `limit`.
"""
from src.services.congress import _build_congress_leaderboard


def _trade(stock_id, symbol, transaction_type, amount_min, amount_max, politician, company="Test Co"):
    return {
        "stock_id": stock_id, "symbol": symbol, "company": company,
        "transaction_type": transaction_type,
        "amount_min": amount_min, "amount_max": amount_max,
        "politician_name": politician,
    }


def test_net_negative_stock_is_excluded_even_when_it_would_otherwise_make_the_cut():
    rows = [
        _trade(1, "AAPL", "purchase", 1_000, 15_000, "Rep. A"),
        _trade(2, "TSLA", "sale", 500_000, 1_000_000, "Rep. B"),  # heavy net seller
    ]
    result = _build_congress_leaderboard(rows, limit=20)
    symbols = {r["symbol"] for r in result}
    assert "AAPL" in symbols
    assert "TSLA" not in symbols


def test_returns_fewer_than_limit_rows_when_fewer_than_limit_are_genuine_buyers():
    rows = [
        _trade(1, "AAPL", "purchase", 1_000, 15_000, "Rep. A"),
        _trade(2, "TSLA", "sale", 500_000, 1_000_000, "Rep. B"),
        _trade(3, "MSFT", "sale", 1, 1, "Rep. C"),
    ]
    result = _build_congress_leaderboard(rows, limit=20)
    assert len(result) == 1
    assert result[0]["symbol"] == "AAPL"


def test_exactly_zero_net_amount_is_also_excluded():
    rows = [
        _trade(1, "AAPL", "purchase", 5_000, 5_000, "Rep. A"),
        _trade(1, "AAPL", "sale", 5_000, 5_000, "Rep. A"),
    ]
    result = _build_congress_leaderboard(rows, limit=20)
    assert result == []


def test_genuine_net_buyers_are_still_sorted_descending_by_net_amount():
    rows = [
        _trade(1, "AAPL", "purchase", 1_000, 15_000, "Rep. A"),
        _trade(2, "MSFT", "purchase", 50_000, 100_000, "Rep. B"),
        _trade(3, "GOOG", "purchase", 15_000, 50_000, "Rep. C"),
    ]
    result = _build_congress_leaderboard(rows, limit=20)
    assert [r["symbol"] for r in result] == ["MSFT", "GOOG", "AAPL"]


def test_unique_politicians_count_still_tracked_correctly_on_surviving_rows():
    rows = [
        _trade(1, "AAPL", "purchase", 1_000, 15_000, "Rep. A"),
        _trade(1, "AAPL", "purchase", 1_000, 15_000, "Rep. B"),
    ]
    result = _build_congress_leaderboard(rows, limit=20)
    assert len(result) == 1
    assert result[0]["unique_politicians"] == 2
    assert result[0]["purchases"] == 2


def test_none_amounts_treated_as_zero_not_a_crash():
    rows = [_trade(1, "AAPL", "purchase", None, None, "Rep. A")]
    result = _build_congress_leaderboard(rows, limit=20)
    assert result == []


def test_limit_still_applies_after_filtering_out_net_sellers():
    rows = [
        _trade(1, "A", "purchase", 100, 200, "Rep. A"),
        _trade(2, "B", "purchase", 200, 300, "Rep. B"),
        _trade(3, "C", "purchase", 300, 400, "Rep. C"),
    ]
    result = _build_congress_leaderboard(rows, limit=2)
    assert len(result) == 2
    assert [r["symbol"] for r in result] == ["C", "B"]


def test_empty_input_returns_empty_list():
    assert _build_congress_leaderboard([], limit=20) == []
