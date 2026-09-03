"""Tests for AUD-SQUEEZE2-BACKTESTNORVOLGATE (Short Squeeze Alerts deep audit, 2026-09-03):
squeeze_alert_backtest() (admin.py) never applied the RVOL confirmation gate the live
check_short_squeeze_alerts() has required since AUD288-SQUEEZE-NO-VOLUME-CONFIRM (2026-08-18) —
the backtest replayed only the short-float + price-move gates, a strategy that hasn't existed
in production for weeks, understating the live alert's real quality.

squeeze_alert_backtest() needs a real DB session (SessionLocal, Price/Stock/FundamentalsSnapshot
tables) that this test environment can't easily construct end-to-end. _trailing_avg_volume() is
a pure, dependency-free nested function — extracted via source-text exec() and tested
behaviorally, matching test_options_flow_alert_backtest.py's own established technique for a
pure helper embedded in a function with heavy, un-importable surrounding dependencies. The
gate's WIRING into the candidate-day loop (where day_volume/avg_volume is actually compared
against _SQUEEZE_RVOL_BASE) is covered via source-text regression checks, matching
test_squeeze_audit_20260725_fixes.py's established pattern for this exact admin.py constraint.
"""
import re
import pathlib

_admin_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "admin.py"
_admin_source = _admin_path.read_text()


def _function_body(name: str, source: str, end_marker: str) -> str:
    start = source.index(f"def {name}(")
    end = source.index(end_marker, start)
    return source[start:end]


_BACKTEST_BODY = _function_body(
    "squeeze_alert_backtest", _admin_source, "\n\n@router.get(\"/options-flow-alert-backtest\")"
)


def _extract_trailing_avg_volume():
    start = _BACKTEST_BODY.index("def _trailing_avg_volume(")
    end = _BACKTEST_BODY.index("\n\n    candidate_days: list", start)
    body = _BACKTEST_BODY[start:end]
    body = re.sub(r"(?m)^    ", "", body)
    namespace = {"_RVOL_TRAILING_DAYS": 20}
    exec(body, namespace)  # noqa: S102 — isolated eval of one pure function's real source
    return namespace["_trailing_avg_volume"]


_trailing_avg_volume = _extract_trailing_avg_volume()


# ── _trailing_avg_volume() — the real trailing-window math ─────────────────────────────────

def test_computes_mean_of_the_trailing_window_excluding_current_day():
    vol_bucket = [(None, v) for v in [100.0, 150.0, 200.0, 250.0, 300.0]]
    # idx=5 (a day AFTER the 5 provided) — trailing window is all 5 prior days, mean=200
    result = _trailing_avg_volume(vol_bucket + [(None, 999.0)], 5)
    assert result == 200.0


def test_current_day_itself_is_excluded_from_its_own_trailing_average():
    """Regression guard: the window must be bucket[start:idx], never including idx itself —
    otherwise a huge current-day volume spike would inflate its own average, making the RVOL
    ratio artificially closer to 1.0 and silently weakening the gate on exactly the days it
    matters most."""
    vol_bucket = [(None, 100.0)] * 5 + [(None, 100_000.0)]
    result = _trailing_avg_volume(vol_bucket, 5)
    assert result == 100.0  # NOT influenced by the 100_000 spike at idx=5


def test_fewer_than_5_valid_trailing_days_returns_none():
    vol_bucket = [(None, 100.0), (None, 200.0)]
    result = _trailing_avg_volume(vol_bucket, 2)
    assert result is None


def test_none_and_zero_volume_days_are_excluded_from_the_window():
    vol_bucket = [(None, 100.0), (None, None), (None, 0.0), (None, 200.0), (None, 300.0), (None, 400.0)]
    result = _trailing_avg_volume(vol_bucket, 6)
    # only 100, 200, 300, 400 are valid (4 days) — still below the 5-day floor
    assert result is None


def test_exactly_5_valid_days_clears_the_floor():
    vol_bucket = [(None, v) for v in [100.0, 200.0, 300.0, 400.0, 500.0]]
    result = _trailing_avg_volume(vol_bucket, 5)
    assert result == 300.0


# ── Wiring regression checks — the gate is actually consulted in the candidate-day loop ────

def test_rvol_base_constant_is_imported():
    assert "_SQUEEZE_RVOL_BASE" in _admin_source
    assert "_squeeze_outcome_lookup_price, _SQUEEZE_OUTCOME_WIN_HURDLE_PCT" in _admin_source


def test_candidate_day_loop_actually_checks_the_rvol_ratio_before_appending():
    """The exact bug site: a candidate day previously appended to candidate_days on the
    price-move check ALONE. Must now also require day_volume / avg_volume >= _SQUEEZE_RVOL_BASE
    before the append."""
    loop_section = _BACKTEST_BODY[
        _BACKTEST_BODY.index("if day_ret >= _SQUEEZE_MIN_INTRADAY_MOVE_PCT:"):
        _BACKTEST_BODY.index("prev_close = close", _BACKTEST_BODY.index("if day_ret >= _SQUEEZE_MIN_INTRADAY_MOVE_PCT:"))
    ]
    assert "day_volume / avg_volume >= _SQUEEZE_RVOL_BASE" in loop_section
    assert "candidate_days.append((stock_id, sym, d, close))" in loop_section


def test_volume_map_is_built_as_a_parallel_structure_not_replacing_price_map():
    """Regression guard: price_map's own 2-tuple (day, close) shape must be left untouched —
    _squeeze_outcome_lookup_price() and other consumers depend on that exact shape."""
    assert 'price_map.setdefault(stock_id, []).append((d, float(close)))' in _admin_source
    assert "volume_map: dict[int, list[tuple]] = {}" in _admin_source
