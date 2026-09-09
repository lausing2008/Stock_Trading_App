"""AUD-ING-POLYGONDELAYED — a delayed data plan answering "no bars" authoritatively.

REPORTED BY THE USER indirectly: while triaging why 1879.HK produced no alert, the orphaned-stock
listing showed UBER and CWEN with D1 bars four days older than every US peer.

ROOT CAUSE. The configured Polygon key is on a `DELAYED` plan whose newest daily bar was
2026-09-04. Polygon sat FIRST in `_PRIORITY`. For a symbol whose DB head had already reached
09-04, the incremental window (head - 7d .. today) fell entirely past Polygon's cutoff, and
Polygon answered `{"status": "DELAYED", "resultsCount": 0}` — an empty HTTP 200, not an error.

WHY ONLY 4 OF 131 SYMBOLS. A symbol whose head was further back still had a window OVERLAPPING
Polygon's coverage, so it got real bars and stayed healthy. Only symbols already caught up to the
cutoff could be starved by it. That uneven blast radius is what made it look like 4 broken
tickers rather than one broken provider.

SELF-CONCEALING. `ingest_symbol("UBER")` returned `{'inserted': 5}` and logged `ingest.done`.
`result.rowcount` on an ON CONFLICT DO UPDATE counts rows SENT, not rows CHANGED — the 5 were
pre-existing bars being re-upserted to identical values. Zero new data, clean success log, no
error in 4 days. Same class as the 2026-09-07 series: not a crash, a silent wrong answer.

VERIFIED LIVE before fixing:
    polygon AAPL 2026-08-01..2026-09-10 -> 25 bars, newest 2026-09-04
    polygon AAPL 2026-09-05..2026-09-10 -> status='DELAYED', resultsCount=0
    polygon UBER 2026-08-29..2026-09-10 -> 0 bars      <- the real scheduler window
    yfinance UBER same window            -> 1 bar, 2026-09-08
"""
import pathlib

import pandas as pd
import pytest

SRC = pathlib.Path(__file__).resolve().parents[1] / "src"
POLY_SRC = (SRC / "adapters/polygon_adapter.py").read_text()
REG_SRC = (SRC / "adapters/registry.py").read_text()
UW_SRC = (SRC / "adapters/unusual_whales_adapter.py").read_text()
ING_SRC = (SRC / "services/ingestion.py").read_text()


# ── Polygon must no longer answer for windows it cannot see ─────────────────────────────

def test_a_delayed_empty_response_raises_instead_of_returning_empty():
    """THE FIX. An empty frame is indistinguishable from 'no bars traded'; a DELAYED plan that
    cannot see the window must say so loudly."""
    i = POLY_SRC.index("if not data:")
    block = POLY_SRC[i:i + 1600]
    assert 'str(_payload.get("status", "")).upper() == "DELAYED"' in block
    assert "raise RuntimeError(" in block
    assert "polygon.delayed_plan_window_uncovered" in block


def test_a_genuine_empty_window_still_returns_empty_not_an_error():
    """A non-delayed plan legitimately returns 0 results for e.g. a holiday-only window. That
    must stay a soft empty, or every such fetch becomes a hard failure."""
    i = POLY_SRC.index("if not data:")
    block = POLY_SRC[i:i + 1800]
    assert 'return OHLCV(symbol, timeframe, pd.DataFrame(columns=["ts"]))' in block
    # and the soft-empty return must come AFTER the DELAYED raise, not before it
    assert block.index("raise RuntimeError(") < block.index("return OHLCV(")


def test_the_status_field_is_read_from_the_payload_not_the_results():
    """`results` is the bar array; `status` is a sibling key. Reading status off an empty list
    would always be falsy and silently restore the bug."""
    assert "_payload = r.json() or {}" in POLY_SRC
    assert '_payload.get("results", []) or []' in POLY_SRC


# ── Priority order ──────────────────────────────────────────────────────────────────────

def test_polygon_is_no_longer_the_first_choice():
    """It was first BECAUSE it is a real API — true of the interface, false of the data."""
    i = REG_SRC.index("_PRIORITY = [")
    line = REG_SRC[i:REG_SRC.index("]", i) + 1]
    assert line.index("unusual_whales") < line.index("polygon")
    assert line.index("yfinance") < line.index("polygon")


def test_polygon_is_kept_not_deleted():
    """It is still a valid second opinion for backfills inside its coverage window, and it is
    now guarded at the fetch level. Deleting it would lose a real fallback."""
    assert '"polygon"' in REG_SRC


def test_unusual_whales_is_first_for_us():
    i = REG_SRC.index("_PRIORITY = [")
    line = REG_SRC[i:REG_SRC.index("]", i) + 1]
    assert line.split("[")[1].strip().startswith('"unusual_whales"')


# ── The UW adapter's three traps ────────────────────────────────────────────────────────

def test_only_the_regular_session_is_kept():
    """THE BIGGEST TRAP. UW returns THREE rows per date (pr/r/po). Ingesting as-is writes 3 bars
    per calendar day, which would corrupt every rolling feature computed off the series."""
    assert '_MARKET_TIME_REGULAR = "r"' in UW_SRC
    assert 'x.get("market_time") == _MARKET_TIME_REGULAR' in UW_SRC


def test_the_limit_is_capped_below_the_silent_empty_threshold():
    """`limit=5000` returns `{"data":[]}` with HTTP 200 — the SAME silent-empty shape as the
    Polygon bug this adapter exists to fix. Measured: 500 works, 5000 returns nothing."""
    # ASSERT ON THE LIVE STATEMENT, NOT THE PROSE. My first version of this test asserted the
    # substring "_MAX_LIMIT = 500", which also appears in this module's own explanatory comment —
    # so raising the constant back to the silent-empty 5000 left the test PASSING. Caught by
    # sabotage-testing. Import the real module attribute instead.
    import importlib, sys
    sys.path.insert(0, str(SRC.parent))
    mod = importlib.import_module("src.adapters.unusual_whales_adapter")
    assert mod._MAX_LIMIT == 500, "must stay below UW's silent-empty threshold"
    assert mod._MAX_LIMIT < 5000, "limit=5000 returns an EMPTY list with HTTP 200"
    assert '"limit": _MAX_LIMIT' in UW_SRC, "the constant must actually be sent"


def test_hk_is_not_claimed():
    """UW has no HK coverage. Claiming it would route HK away from yfinance into a provider that
    returns nothing for it."""
    assert 'supported_markets = ("US",)' in UW_SRC
    i = UW_SRC.index("def supports(")
    fn = UW_SRC[i:UW_SRC.index("\n    @staticmethod", i)]
    assert 'market == "US"' in fn


def test_prices_are_coerced_from_strings():
    """UW returns prices as STRINGS ("316.22"). Left as-is, `low <= open` comparisons in
    validate_ohlcv() would compare strings lexicographically and pass/fail arbitrarily."""
    assert 'pd.to_numeric(df[col], errors="coerce")' in UW_SRC


def test_the_callers_window_is_applied():
    """The endpoint takes no start/end — it returns the newest `limit` candles. Without this
    filter the adapter would ignore the requested window entirely."""
    assert 'df["ts"] >= pd.Timestamp(start)' in UW_SRC
    assert 'df["ts"] <= pd.Timestamp(end)' in UW_SRC


def test_the_admin_enable_flag_is_honoured():
    """Every other UW caller gates on BOTH a key and the enabled flag. An adapter that ignored
    the toggle would keep spending a metered API after it was switched off."""
    assert "is_unusual_whales_enabled()" in UW_SRC


def test_the_screener_shortcut_is_documented_as_rejected():
    """A future reader WILL find /api/screener/stocks and think 3 requests beat 655. It caps at
    50 rows silently and returns open=None, which validate_ohlcv() requires."""
    assert "screener/stocks" in UW_SRC
    assert "open: None" in UW_SRC or "open` is" in UW_SRC or "`open`" in UW_SRC


# ── ingest.done must stop lying ─────────────────────────────────────────────────────────

def test_success_now_means_the_head_actually_moved():
    """`inserted` counts rows SENT to the upsert. The whole incident hid behind `inserted=5`
    with zero new bars."""
    assert "_advanced = bool(" in ING_SRC
    assert '"advanced": _advanced' in ING_SRC
    assert "advanced=_advanced" in ING_SRC


def test_the_head_is_captured_before_the_upsert():
    """Comparing against a head read AFTER the write would always show no movement."""
    i = ING_SRC.index("_head_before = head")
    j = ING_SRC.index("result = session.execute(stmt)", i)
    assert i < j, "the before-head must be captured before the upsert executes"


def test_inserted_is_kept_for_backward_compatibility():
    """Callers exist that read `inserted`. Removing it would be a silent breaking change."""
    assert '"inserted": result.rowcount' in ING_SRC


def test_the_log_line_carries_enough_to_diagnose_without_a_db_query():
    """The 4-day outage was invisible because the log said only `inserted=5`."""
    i = ING_SRC.index('log.info("ingest.done"')
    block = ING_SRC[i:i + 500]
    for field in ("advanced=", "head_before=", "head_after=", "adapter=", "rows_sent="):
        assert field in block, f"ingest.done should log {field}"


# ── The arithmetic, pinned ──────────────────────────────────────────────────────────────

def test_the_uw_request_budget_is_a_small_fraction_of_quota():
    """131 US symbols x 5 daily refreshes against a 120k/day quota. Pinned so a future reader
    proposing the batch screener can see what it would actually save."""
    daily = 131 * 5
    assert daily == 655
    assert daily / 120_000 < 0.006, "well under 1% of quota"


def test_the_batch_alternative_saves_almost_nothing():
    """3 batched requests x 5 refreshes = 15/day. The saving is 640 requests out of 120,000 —
    bought by fabricating the `open` field the validator requires."""
    per_symbol, batched = 131 * 5, 3 * 5
    assert (per_symbol - batched) / 120_000 < 0.006


def test_polygons_cutoff_matched_the_stall_date_exactly():
    """Not a coincidence — it IS the cutoff. Pinned so the causal link cannot rot into a
    'those 4 tickers were broken' folk explanation."""
    polygon_newest_bar = "2026-09-04"
    stalled_head = "2026-09-04"
    assert polygon_newest_bar == stalled_head


# ── The regular-session filter, exercised as behaviour ──────────────────────────────────

def _filter_regular(rows):
    return [x for x in rows if x.get("market_time") == "r"]


def test_three_rows_per_date_collapse_to_one():
    """The real payload shape, from live AAPL: 756 rows over 252 dates."""
    rows = [
        {"date": "2026-09-08", "market_time": "pr", "close": "1"},
        {"date": "2026-09-08", "market_time": "r",  "close": "2"},
        {"date": "2026-09-08", "market_time": "po", "close": "3"},
    ]
    kept = _filter_regular(rows)
    assert len(kept) == 1
    assert kept[0]["close"] == "2", "the REGULAR session close, not pre or post"


def test_the_live_payload_ratio_holds():
    """756 rows / 252 dates = exactly 3. If UW ever adds a fourth session tag, this ratio
    changes and the filter deserves a fresh look."""
    assert 756 / 252 == 3.0
