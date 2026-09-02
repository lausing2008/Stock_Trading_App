"""Tests for MPE-OPTIONS-FLOW-ALERT's real historical backtest — options_flow_alert_backtest()
(admin.py) + unusual_whales.get_historical_flow_alerts() — direct follow-up to a user asking
"should I buy same direction or different direction, and when should I enter?" with ZERO live-
resolved outcomes yet.

options_flow_alert_backtest() needs a real DB session (SessionLocal, Price/Stock tables) that
this test environment can't easily construct end-to-end — its own nested scoring closures
(_bucket_stats/_windows_for) are pure and dependency-free, so they're extracted via source-text
exec() and tested behaviorally with real values, matching test_kscore_curve_params.py's/
test_options_flow_alert.py's own established technique for a pure helper embedded in a function
that can't be imported wholesale. The endpoint's own wiring (symbol scoping, is_sweep omission,
bulk price query, reason/note fields) is covered via source-text regression checks, matching
test_squeeze_audit_20260725_fixes.py's established pattern for this exact admin.py constraint.
"""
import pathlib

_admin_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "admin.py"
_admin_source = _admin_path.read_text()


def _function_body(name: str, source: str, end_marker: str) -> str:
    start = source.index(f"def {name}(")
    end = source.index(end_marker, start)
    return source[start:end]


_BACKTEST_BODY = _function_body(
    "options_flow_alert_backtest", _admin_source, "\n\n@router.get(\"/watchlist-rotation-history\")"
)


def _extract_bucket_stats_and_windows_for():
    """_bucket_stats()/_windows_for() are pure, dependency-free nested functions — exec() them
    in isolation against the real source text with the small set of names they close over
    stubbed to plain values, matching this repo's established technique for a pure helper
    embedded in a function with heavy, un-importable surrounding dependencies."""
    import re
    start = _BACKTEST_BODY.index("def _bucket_stats(")
    end = _BACKTEST_BODY.index("\n\n    by_direction = [")
    body = _BACKTEST_BODY[start:end]
    # The sliced body's own `def` lines start flush at column 0 (the slice begins exactly at
    # "def"), but every OTHER line still carries the original 8-space (2-level) indentation
    # from living inside the endpoint function — dedent those by 4 (not 8) so the body settles
    # at a normal, valid 4-space function-body indent, not flush-left against the `def` line.
    body = re.sub(r"(?m)^    ", "", body)
    namespace = {
        "min_samples": 3,
        "_SQUEEZE_OUTCOME_WIN_HURDLE_PCT": 0.005,
        "_SQUEEZE_OUTCOME_WINDOWS": (1, 2, 3, 5, 10, 20),
    }
    exec(body, namespace)
    return namespace["_bucket_stats"], namespace["_windows_for"]


_bucket_stats, _windows_for = _extract_bucket_stats_and_windows_for()


def _row(direction: str, ret_1d: float | None):
    return {"direction": direction, "returns": {1: ret_1d} if ret_1d is not None else {}}


# ── _bucket_stats() — the real win/loss scoring math ────────────────────────────────────────

def test_bullish_wins_on_a_return_above_the_hurdle():
    rows = [_row("bullish", 0.02)] * 5
    stats = _bucket_stats(rows, 1)
    assert stats["n"] == 5
    assert stats["win_rate"] == 1.0


def test_bullish_loses_on_a_return_below_the_hurdle():
    rows = [_row("bullish", -0.02)] * 5
    stats = _bucket_stats(rows, 1)
    assert stats["win_rate"] == 0.0


def test_bearish_wins_on_a_return_below_the_negative_hurdle():
    """A bearish alert implies a SHORT/put thesis — it wins when price actually DROPS, the
    mirror of the bullish case, matching every other outcome evaluator's own direction-aware
    hurdle convention in this codebase."""
    rows = [_row("bearish", -0.02)] * 5
    stats = _bucket_stats(rows, 1)
    assert stats["win_rate"] == 1.0


def test_bearish_loses_when_price_rises():
    rows = [_row("bearish", 0.02)] * 5
    stats = _bucket_stats(rows, 1)
    assert stats["win_rate"] == 0.0


def test_a_return_inside_the_hurdle_band_counts_as_a_loss_not_a_win():
    """A tiny move that never clears the +-0.5% hurdle is a loss for either direction — this
    guards against a naive `ret > 0` check that would wrongly call a 0.1% bullish move a win."""
    rows = [_row("bullish", 0.001)] * 5
    stats = _bucket_stats(rows, 1)
    assert stats["win_rate"] == 0.0


def test_below_min_samples_returns_a_real_n_but_no_fabricated_win_rate():
    """A genuinely thin sample must report its real n (so a caller can see HOW thin) but never
    a fabricated win_rate/avg_return — matching this codebase's own established "None, not 0.0,
    below the sample floor" discipline."""
    rows = [_row("bullish", 0.02)] * 2  # min_samples stubbed to 3 above
    stats = _bucket_stats(rows, 1)
    assert stats["n"] == 2
    assert stats["win_rate"] is None
    assert stats["avg_return_pct"] is None
    assert "sample floor" in stats["note"]


def test_zero_resolved_rows_returns_none_not_a_degenerate_zero_sample_dict():
    """A bucket with literally no rows at all (e.g. no bearish alerts fired in this window) must
    return None, distinguishable from 'some rows, but below the floor'."""
    assert _bucket_stats([], 1) is None


def test_a_row_missing_this_specific_window_is_silently_excluded_not_treated_as_a_loss():
    """A row whose forward-window price hasn't resolved yet (e.g. the 20d window on a very
    recent alert) must be excluded from THIS window's stats entirely, never silently counted
    as a loss just because its `returns` dict has no entry for this window."""
    rows = [_row("bullish", 0.02), _row("bullish", None), _row("bullish", 0.02)]
    stats = _bucket_stats(rows, 1)
    assert stats["n"] == 2  # only the 2 rows with a real 1d return


def test_windows_for_covers_every_real_window_not_a_subset():
    rows = [_row("bullish", 0.02)] * 20
    result = _windows_for(rows)
    assert set(result.keys()) == {"window_1d", "window_2d", "window_3d", "window_5d", "window_10d", "window_20d"}


# ── options_flow_alert_backtest() — wiring / source-text regression checks ─────────────────

def test_gated_entirely_behind_unusual_whales_is_available():
    assert "_uw.is_available()" in _BACKTEST_BODY
    idx = _BACKTEST_BODY.index("_uw.is_available()")
    assert "return" in _BACKTEST_BODY[idx:idx + 200]


def test_scoped_to_the_same_bounded_symbol_set_the_live_job_uses():
    """Must reuse _bounded_options_flow_symbols() — never a fabricated/different universe."""
    assert "_bounded_options_flow_symbols(session)" in _BACKTEST_BODY


def test_uses_the_real_same_direction_derivation_the_live_job_uses():
    assert "_options_flow_alert_direction(" in _BACKTEST_BODY


def test_uses_the_real_live_threshold_defaults_not_hardcoded_literals():
    """Must replay against the SAME min_premium/min_volume_oi_ratio/max_dte the live job uses
    today — a hardcoded literal here could silently drift from a future threshold retune."""
    assert "_OPTIONS_FLOW_ALERT_MIN_PREMIUM" in _BACKTEST_BODY
    assert "_OPTIONS_FLOW_ALERT_MIN_VOLUME_OI_RATIO" in _BACKTEST_BODY
    assert "_OPTIONS_FLOW_ALERT_MAX_DTE" in _BACKTEST_BODY


def test_omits_is_sweep_for_a_genuine_sweep_vs_non_sweep_comparison():
    """AUD-OPTIONSFLOW-BACKTEST-SWEEPFILTER: UW's own is_sweep param is a hard binary filter
    both ways — passing True here (matching the live job's own filter) would make every
    replayed row a sweep by construction, silently collapsing by_sweep's non-sweep bucket to
    zero. Must pass is_sweep=None (omitted) so the comparison is real.

    Anchored on the REAL get_historical_flow_alerts(...) call-site kwarg specifically, not a
    bare substring search — this function's own explanatory comment ABOVE the call also
    mentions "is_sweep=None" in prose, which would let a real regression at the call site
    slip past a naive `"is_sweep=None" in body` check."""
    idx = _BACKTEST_BODY.index("rows = _uw.get_historical_flow_alerts(")
    call_site = _BACKTEST_BODY[idx:idx + 400]
    assert "is_sweep=None" in call_site


def test_entry_price_is_uws_own_underlying_price_not_a_separate_db_lookup():
    """UW already gives us the real price at the alert's own moment — reusing it (rather than
    a T+1 DB lookup, which squeeze_alert_backtest()'s own weekly-snapshot proxy needs since it
    has no such field) is the whole reason this backtest can be a genuine replay."""
    assert '"entry_price": float(row.underlying_price)' in _BACKTEST_BODY


def test_skips_a_row_with_no_real_ask_bid_premium_split():
    assert "ask == 0.0 and bid == 0.0" in _BACKTEST_BODY


def test_uses_one_bulk_price_query_not_a_per_alert_query():
    """Must reuse evaluate_squeeze_alert_outcomes()'s own established pattern — one bulk Price
    query across every involved stock_id, never N individual per-alert queries."""
    assert "Price.stock_id.in_(stock_ids)" in _BACKTEST_BODY
    idx = _BACKTEST_BODY.index("stock_ids = {r[")
    loop_start = _BACKTEST_BODY.index("for r in alert_rows:", idx)
    query_idx = _BACKTEST_BODY.index("bulk_prices = session.execute(", idx)
    assert query_idx < loop_start


def test_reuses_the_shared_squeeze_outcome_lookup_price_helper_and_hurdle_constant():
    """Must reuse the SAME T+1-entry/bisect-nearest-bar helper and win-hurdle constant every
    other outcome evaluator in this file uses — never a re-derived copy that could drift."""
    assert "_squeeze_outcome_lookup_price(" in _BACKTEST_BODY
    assert "_SQUEEZE_OUTCOME_WIN_HURDLE_PCT" in _BACKTEST_BODY
    assert "_SQUEEZE_OUTCOME_WINDOWS" in _BACKTEST_BODY


def test_three_distinct_early_return_reasons_for_the_no_data_cases():
    """Matches this file's own established AUD-SQUEEZE250725-ISSUE6 discipline — distinct
    diagnostic reasons for genuinely different empty-result causes, never one generic 'no data'
    string that can't distinguish them."""
    assert '"reason": "unusual_whales_not_available"' in _BACKTEST_BODY
    assert '"reason": "no_bounded_symbols"' in _BACKTEST_BODY
    assert '"reason": "no_qualifying_historical_alerts"' in _BACKTEST_BODY


def test_days_back_is_capped_within_uws_own_confirmed_retention_window():
    """days_back's own Query() ceiling must not silently promise more history than has actually
    been confirmed live against production (>= 60 real days, capped at 90) — never an
    unverified assumption of deeper retention."""
    sig_start = _admin_source.index("def options_flow_alert_backtest(")
    sig_end = _admin_source.index(")", sig_start)
    sig = _admin_source[sig_start:sig_end]
    assert "le=90" in sig
