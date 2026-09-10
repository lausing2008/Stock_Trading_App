"""T379-CALENDAR-PRICE — the Events Calendar showed estimates and targets but no price.

USER REQUEST: "And also in the Earning Calendar, show the current price for the stock as well".

The card already showed EPS estimate, revenue/EPS growth, market cap, an analyst target and an
expected move — every one of which is interpreted RELATIVE to the current price, which was the
one number absent. The user's own screenshot makes the gap concrete: the NVDA ex-dividend card
read "Annual rate $1.00 / Yield 44.00%" with no price to sanity-check that against.

IMPLEMENTED AS ONE BULK REDIS READ, deliberately. The obvious version — look up each symbol's
price inside the existing per-stock loop — would add ~120 Redis round-trips to a route that
AUD-UWCAL-NONUS422 had *just* been rescued from a 61-second per-symbol fan-out. Instead this
reads the shared `stockai:live_prices` blob ONCE (the same cache seven every-minute alert
scanners already consume) and indexes it by symbol.

NULL IS NOT ZERO. A symbol with no live quote yields `None`, and the card renders "—". Emitting
0.0 would be an authoritative-looking wrong answer — the AUD-RANK-RSPLACEHOLDER /
AUD-CONVICTION-RSIDIV-NOWRITER error class this codebase keeps re-encountering.
"""
import ast
import pathlib

import pytest

ROUTES = pathlib.Path(__file__).resolve().parents[1] / "src/api/routes.py"
SRC = ROUTES.read_text()
API_TS = (
    pathlib.Path(__file__).resolve().parents[3] / "frontend/src/lib/api.ts"
).read_text()
PAGE_TS = (
    pathlib.Path(__file__).resolve().parents[3] / "frontend/src/pages/earnings.tsx"
).read_text()


def _calendar_body() -> str:
    i = SRC.index("def events_calendar(")
    return SRC[i:SRC.index("\n@router", i + 10)]


# ── The price reaches both card types ───────────────────────────────────────────────────

def test_earnings_events_carry_the_current_price():
    blk = _calendar_body()
    i = blk.index('"type": "earnings"')
    assert '"current_price": _live_by_symbol.get(stock.symbol)' in blk[i:i + 3000]


def test_dividend_events_carry_it_too():
    """The user's screenshot showed an NVDA ex-div card; a fix that only covered earnings would
    leave the very card that motivated the request unchanged."""
    blk = _calendar_body()
    i = blk.index('"type": "dividend"')
    assert '"current_price": _live_by_symbol.get(stock.symbol)' in blk[i:i + 1200]


def test_it_is_populated_from_the_shared_live_price_cache():
    """Not a fresh yfinance/UW call — this route is a page load, and the blob already exists."""
    blk = _calendar_body()
    assert "_LIVE_KEY" in blk


# ── Cost: one read, not one per symbol ──────────────────────────────────────────────────

def test_the_live_cache_is_read_exactly_once():
    """THE POINT. A per-symbol Redis GET inside the existing loop would add ~120 round-trips
    to the route AUD-UWCAL-NONUS422 just fixed. One bulk read, indexed by symbol."""
    blk = _calendar_body()
    assert blk.count("r.get(_LIVE_KEY)") == 1


def test_the_read_happens_before_the_per_stock_loop():
    """If it were inside the loop, `count == 1` above would still pass while the cost was
    per-symbol. Pin the POSITION, not just the occurrence count."""
    blk = _calendar_body()
    assert blk.index("r.get(_LIVE_KEY)") < blk.index("for stock in stocks:")


def test_no_per_symbol_network_call_was_introduced():
    """Guards against the failure mode this route has a documented history of."""
    blk = _calendar_body()
    i = blk.index("for stock in stocks:")
    loop = blk[i:]
    assert "get_live_price(" not in loop
    assert "yfinance" not in loop


# ── Null handling ───────────────────────────────────────────────────────────────────────

def _build_index(blob: str) -> dict:
    """Mirrors the real comprehension; the source assertions below pin the implementation."""
    import json
    try:
        return {
            row["symbol"]: row.get("price")
            for row in json.loads(blob or "[]")
            if row.get("symbol")
        }
    except Exception:
        return {}


def test_the_SOURCE_does_not_default_a_missing_price():
    """A GAP MY OWN SABOTAGE TESTING EXPOSED.

    Every null test below exercises a MIRROR of the index comprehension, so appending
    `or 0.0` to the real `_live_by_symbol.get(...)` call sites left all 22 tests passing while
    shipping exactly the falsy-zero defect this file exists to prevent. A helper that copies
    the logic does not pin the logic — assert on the real source too.
    """
    blk = _calendar_body()
    for bad in ("_live_by_symbol.get(stock.symbol) or", "_live_by_symbol.get(stock.symbol, 0"):
        assert bad not in blk, f"a missing price must stay None, not become 0.0: {bad!r}"
    assert blk.count('"current_price": _live_by_symbol.get(stock.symbol),') == 2


def test_a_symbol_with_no_quote_yields_none_not_zero():
    """NULL vs 0.0 is the distinction. $0.00 renders as a real, wrong price."""
    idx = _build_index('[{"symbol": "AAPL", "price": 220.5}]')
    assert idx.get("ORCL") is None
    assert idx.get("ORCL") != 0.0


def test_a_present_quote_is_returned():
    idx = _build_index('[{"symbol": "ORCL", "price": 246.67}]')
    assert idx["ORCL"] == 246.67


def test_an_explicit_null_price_stays_none():
    """A row present in the blob but carrying price: null must not become 0.0."""
    idx = _build_index('[{"symbol": "ORCL", "price": null}]')
    assert idx["ORCL"] is None


def test_a_zero_price_is_preserved_rather_than_invented():
    """We must not FABRICATE a zero, but if the cache genuinely holds one we pass it through
    rather than silently reinterpreting upstream data."""
    idx = _build_index('[{"symbol": "X", "price": 0}]')
    assert idx["X"] == 0


def test_a_row_with_no_symbol_is_skipped_not_crashed():
    idx = _build_index('[{"price": 1.0}, {"symbol": "OK", "price": 2.0}]')
    assert idx == {"OK": 2.0}


@pytest.mark.parametrize("blob", ["", None, "not json", "{}", "[]"])
def test_a_broken_or_empty_cache_fails_open(blob):
    """A missing live cache must render "—", never break the whole calendar. Redis being cold
    after a restart is a REAL state — it happened twice in this session's own incidents."""
    assert _build_index(blob) == {} or isinstance(_build_index(blob), dict)


def test_the_source_wraps_the_read_in_a_try_except():
    blk = _calendar_body()
    i = blk.index("_live_by_symbol")
    seg = blk[max(0, i - 300):i + 400]
    assert "try:" in seg and "except Exception:" in seg
    assert "_live_by_symbol = {}" in seg, "must fail open to an empty index"


# ── Frontend ────────────────────────────────────────────────────────────────────────────

def test_the_type_declares_it_nullable():
    """`current_price?: number | null` — a non-null type would push the null-vs-zero problem
    into the component."""
    assert "current_price?: number | null;" in API_TS


def test_both_cards_render_a_dash_when_it_is_missing():
    """`!= null` is required, not truthiness: `ev.current_price ? ... : '—'` would render "—"
    for a genuine 0, and more importantly is the habit that produces falsy-zero bugs."""
    assert PAGE_TS.count("ev.current_price != null") == 2, "earnings card AND dividend card"


def test_neither_card_uses_a_falsy_check():
    assert "ev.current_price ?" not in PAGE_TS
    assert "ev.current_price ||" not in PAGE_TS


def test_the_price_is_rendered_before_the_other_stats():
    """It is the number the others are relative to — an EPS estimate or an analyst target
    means little without it."""
    i_price = PAGE_TS.index("ev.current_price != null")
    i_eps = PAGE_TS.index("ev.eps_estimate != null")
    assert i_price < i_eps


def test_the_dividend_card_shows_it_alongside_the_yield():
    """The motivating case: a 44% yield with no price to check it against."""
    i = PAGE_TS.index("Dividend details")
    blk = PAGE_TS[i:i + 1600]
    assert "ev.current_price != null" in blk
    assert "fmtYield(ev.dividend_yield)" in blk
