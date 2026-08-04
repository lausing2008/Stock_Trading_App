"""Tests for the market-wide screener (GET /stocks/market-screener).

Closes the gap documented in .claude/CLAUDE.md's "Reports Tab" research: every other
screener/scanner page in this app only searches symbols already in the tracked Stock table.
This uses yfinance's own free yf.screen() to surface candidates BEFORE they're tracked.

routes.py can't be imported directly in this test environment (its import chain pulls in
common.config/db, none of which conftest.py stubs for real) — _rank_screener_quotes()'s real
source is extracted and exec()'d, matching test_options_chain.py's established pattern for
functions in this exact file. This function has zero pandas/DB dependency (plain dicts), so
no fixture library is needed for the exec() namespace itself.
"""
import pathlib

_ROUTES_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "routes.py"
_ROUTES_SOURCE = _ROUTES_PATH.read_text()


def _extract_rank_screener_quotes():
    start = _ROUTES_SOURCE.index("def _rank_screener_quotes(")
    end = _ROUTES_SOURCE.index('\n@router.get("/market-screener")', start)
    func_source = _ROUTES_SOURCE[start:end]
    namespace = {}
    exec(func_source, namespace)  # noqa: S102 — isolated eval of one pure function's real source
    return namespace["_rank_screener_quotes"]


_rank_screener_quotes = _extract_rank_screener_quotes()


def _quote(symbol, **overrides):
    base = {
        "symbol": symbol,
        "longName": f"{symbol} Corp",
        "regularMarketPrice": 10.0,
        "regularMarketChangePercent": 5.0,
        "regularMarketVolume": 1_000_000,
        "averageDailyVolume3Month": 500_000,
        "marketCap": 1_000_000_000,
        "fullExchangeName": "NASDAQ",
    }
    base.update(overrides)
    return base


def test_basic_field_mapping():
    rows = _rank_screener_quotes([_quote("DFNS")], tracked_symbols=set())
    assert len(rows) == 1
    r = rows[0]
    assert r["symbol"] == "DFNS"
    assert r["name"] == "DFNS Corp"
    assert r["price"] == 10.0
    assert r["change_pct"] == 5.0
    assert r["volume"] == 1_000_000
    assert r["market_cap"] == 1_000_000_000
    assert r["exchange"] == "NASDAQ"


def test_already_tracked_flag_reflects_the_passed_in_set():
    rows = _rank_screener_quotes([_quote("DFNS"), _quote("AAPL")], tracked_symbols={"AAPL"})
    by_sym = {r["symbol"]: r for r in rows}
    assert by_sym["DFNS"]["already_tracked"] is False
    assert by_sym["AAPL"]["already_tracked"] is True


def test_rvol_computed_from_volume_and_avg_volume_3month():
    rows = _rank_screener_quotes(
        [_quote("DFNS", regularMarketVolume=2_000_000, averageDailyVolume3Month=500_000)],
        tracked_symbols=set(),
    )
    assert rows[0]["rvol"] == 4.0


def test_missing_volume_or_avg_volume_degrades_rvol_to_none_not_crash():
    rows = _rank_screener_quotes(
        [_quote("DFNS", regularMarketVolume=None, averageDailyVolume3Month=None)],
        tracked_symbols=set(),
    )
    assert rows[0]["rvol"] is None


def test_deduplicates_the_same_symbol_appearing_in_multiple_screens():
    """The real caller runs 3 separate yf.screen() queries and merges results — a symbol
    appearing in more than one (e.g. both small_cap_gainers and most_actives) must appear
    exactly once in the output, not once per query it happened to match."""
    rows = _rank_screener_quotes([_quote("DFNS"), _quote("DFNS")], tracked_symbols=set())
    assert len(rows) == 1


def test_quotes_missing_a_symbol_are_skipped_not_crash():
    rows = _rank_screener_quotes([{"regularMarketChangePercent": 5.0}], tracked_symbols=set())
    assert rows == []


def test_sorted_by_change_pct_descending():
    rows = _rank_screener_quotes(
        [_quote("A", regularMarketChangePercent=3.0), _quote("B", regularMarketChangePercent=15.0), _quote("C", regularMarketChangePercent=8.0)],
        tracked_symbols=set(),
    )
    assert [r["symbol"] for r in rows] == ["B", "C", "A"]


def test_none_change_pct_sorts_last_not_crash():
    rows = _rank_screener_quotes(
        [_quote("A", regularMarketChangePercent=None), _quote("B", regularMarketChangePercent=5.0)],
        tracked_symbols=set(),
    )
    assert rows[0]["symbol"] == "B"
    assert rows[-1]["symbol"] == "A"


def test_falls_back_to_short_name_when_long_name_missing():
    rows = _rank_screener_quotes(
        [_quote("DFNS", longName=None, shortName="DFNS Short")],
        tracked_symbols=set(),
    )
    assert rows[0]["name"] == "DFNS Short"


def test_falls_back_to_symbol_when_no_name_at_all():
    rows = _rank_screener_quotes(
        [_quote("DFNS", longName=None, shortName=None)],
        tracked_symbols=set(),
    )
    assert rows[0]["name"] == "DFNS"


# ── Route-level wiring — source-text regression checks (routes.py can't be imported here) ───

def test_market_screener_route_is_read_only_no_admin_gate():
    """This screener must remain safe for ANY logged-in user — it never mutates the Stock
    table. Adding a new symbol still goes through the existing admin-only /admin/add_stock."""
    start = _ROUTES_SOURCE.index('@router.get("/market-screener")')
    end = _ROUTES_SOURCE.index("\n@router.get", start + 1) if "\n@router.get" in _ROUTES_SOURCE[start + 1:] else len(_ROUTES_SOURCE)
    body = _ROUTES_SOURCE[start:end]
    assert "get_current_user" in body
    assert "get_admin_user" not in body


def test_market_screener_uses_the_bounded_predefined_query_list():
    assert "_MARKET_SCREENER_QUERIES" in _ROUTES_SOURCE
    assert "small_cap_gainers" in _ROUTES_SOURCE
    assert "aggressive_small_caps" in _ROUTES_SOURCE


def test_market_screener_excludes_delisted_and_inactive_stocks_from_tracked_set():
    start = _ROUTES_SOURCE.index('@router.get("/market-screener")')
    end = _ROUTES_SOURCE.index("\n@router.get", start + 1) if "\n@router.get" in _ROUTES_SOURCE[start + 1:] else len(_ROUTES_SOURCE)
    body = _ROUTES_SOURCE[start:end]
    assert "Stock.active.is_(True)" in body
    assert "Stock.delisted.is_(False)" in body
