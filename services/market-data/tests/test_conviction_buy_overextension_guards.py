"""Tests for AUD232-BUY-FROM-TOP-1/2 — two new disqualifiers in _is_conviction_buy() that
close a real gap found while investigating a live user report: 0939.HK fired a BUY conviction
alert on 2026-08-04 while sitting essentially at the same overbought level it had held (and
been correctly blocked at) all week.

Root cause: stoch_rsi_overbought (a hard cutoff, stoch_k > 0.80) flickered from True to False
on a single noisy tick (stoch_k 0.824 -> 0.735 in one 5-min refresh) while RSI (70) and price
(within 1.5% of the 20-day high) barely moved — the disqualifier vanished the instant the
oscillator ticked below the line, even though nothing about the real risk changed.

Fix 1 (stoch_rsi_still_hot): requires the PRIOR bar to have also been below 0.80 before
treating a dip as genuine cooling, not just the current bar's noisy single-tick value.

Fix 2 (near_recent_high_hot): an independent check that doesn't depend on the stochastic at
all — price within 3% of its own 20-day high with RSI still >65 is genuinely still extended.

scheduler.py can't be imported directly in this test environment (apscheduler not installed
locally) — _is_conviction_buy() is pure/dependency-free (reads only a signal_data dict), so
it's loaded directly from source via exec(), matching test_earnings_alert_bodies.py's
established technique exactly.
"""
import pathlib

_scheduler_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
_source = _scheduler_path.read_text()


def _load_function(name: str, namespace: dict | None = None):
    start = _source.index(f"def {name}")
    end = _source.index("\n\n\n", start)
    namespace = namespace if namespace is not None else {}
    exec(_source[start:end], namespace)  # noqa: S102 — isolated eval of one pure function's source
    return namespace[name]


def _load_constant(name: str):
    """Extracts the real module-level constant's own source (not a hand-copied duplicate
    that could silently drift from it — _is_conviction_buy() reads _REGIME_THRESHOLDS
    directly, so the test namespace needs the real dict, not a re-derived one)."""
    start = _source.index(f"{name}: dict")
    end = _source.index("\n}\n", start) + len("\n}")
    namespace: dict = {}
    exec(_source[start:end], namespace)  # noqa: S102
    return namespace[name]


# AUD263-CONVICTION-WEIGHTS-UNGATED: _is_conviction_buy() now calls _load_conviction_edges()
# to extend its soft-fail allowance with calibrated data — stubbed here to return {} (no
# calibration data available), matching this fix's own "empty edge map behaves exactly like
# before the fix" guarantee (see test_conviction_weights_wired_and_gated.py for the dedicated
# tests of that new behavior). _CONVICTION_LAYER_FLAG/_CONVICTION_EDGE_NOISE_THRESHOLD_PCT are
# referenced by _is_conviction_buy()'s body even when the edge map is empty, so both must be
# present in the namespace too.
_namespace = {
    "_REGIME_THRESHOLDS": _load_constant("_REGIME_THRESHOLDS"),
    "_CONVICTION_LAYER_FLAG": {
        "Uptrend": "trend_above_sma50", "OBV": "obv_trend_bullish",
        "ADX": "adx_trending", "MACD": "macd_zero_cross_up",
    },
    "_CONVICTION_EDGE_NOISE_THRESHOLD_PCT": 2.0,
    "_load_conviction_edges": lambda: {},
    # AUD-CHASE-ROC10: read the REAL module-level constant out of scheduler.py rather than
    # hardcoding 10.0 here — same reasoning as _load_constant()'s docstring above. A test that
    # duplicated the value would keep passing if the production threshold drifted.
    "_MAX_ROC10_FOR_ENTRY": float(
        _source.split("_MAX_ROC10_FOR_ENTRY = ")[1].split("\n")[0].strip()
    ),
}
_is_conviction_buy = _load_function("_is_conviction_buy", _namespace)


# ── Baseline fixture: a candidate that clears every OTHER layer cleanly ────────────────
# (so any failure below is unambiguously attributable to the new checks under test, not
# some other pre-existing layer).

def _clean_reasons(**overrides) -> dict:
    base = {
        "market_regime": "bull",
        "sma50_above_sma200": True,
        "trend_above_sma50": True,
        "rsi": 55.0,
        "macd_hist": 0.5,
        "macd_rising": True,
        "macd_zero_cross_up": False,
        "obv_trend_bullish": True,
        "adx_trending": True,
        "adx": 30.0,
        "ml_probability": 0.90,
        "ml_weight": 0.5,
        "rsi_divergence": None,
        "stoch_rsi_overbought": False,
        "stoch_rsi_still_hot": False,
        "near_recent_high_hot": False,
        "pct_from_20d_high": 0.10,
    }
    base.update(overrides)
    return base


def _signal(horizon="SWING", **reason_overrides) -> dict:
    return {"horizon": horizon, "reasons": _clean_reasons(**reason_overrides)}


# ── Baseline sanity check ───────────────────────────────────────────────────────────────

def test_clean_candidate_with_neither_flag_passes_full():
    all_passed, tier, passed, failed = _is_conviction_buy(_signal(), kscore=70.0, regime="bull")
    assert all_passed is True
    assert tier == "full"
    assert failed == []


# ── Fix 1: stoch_rsi_still_hot ───────────────────────────────────────────────────────────

def test_stoch_rsi_still_hot_blocks_even_when_current_bar_not_overbought():
    # This is exactly the 0939.HK case: stoch_rsi_overbought is False THIS bar, but the
    # sustained-hot flag says the prior bar was still overbought — must still block.
    sig = _signal(stoch_rsi_overbought=False, stoch_rsi_still_hot=True)
    all_passed, tier, passed, failed = _is_conviction_buy(sig, kscore=70.0, regime="bull")
    assert all_passed is False
    assert any("barely cooled" in f for f in failed)


def test_stoch_rsi_still_hot_false_does_not_block():
    sig = _signal(stoch_rsi_overbought=False, stoch_rsi_still_hot=False)
    all_passed, tier, passed, failed = _is_conviction_buy(sig, kscore=70.0, regime="bull")
    assert all_passed is True
    assert not any("barely cooled" in f for f in failed)


def test_stoch_rsi_overbought_true_blocks_via_the_original_check_not_double_counted():
    # When stoch_rsi_overbought is True, the ORIGINAL disqualifier already fires — the new
    # still_hot message should not ALSO independently fire (it's an elif of the original).
    sig = _signal(stoch_rsi_overbought=True, stoch_rsi_still_hot=True)
    all_passed, tier, passed, failed = _is_conviction_buy(sig, kscore=70.0, regime="bull")
    assert all_passed is False
    assert sum(1 for f in failed if "overextended" in f or "barely cooled" in f) == 1


# ── Fix 2: near_recent_high_hot ──────────────────────────────────────────────────────────

def test_near_recent_high_hot_blocks_independent_of_stoch_rsi():
    # Both stoch flags clear (i.e. Fix 1 alone would have let this through) — Fix 2 must
    # still catch it on its own.
    sig = _signal(
        stoch_rsi_overbought=False, stoch_rsi_still_hot=False,
        near_recent_high_hot=True, pct_from_20d_high=0.015,
    )
    all_passed, tier, passed, failed = _is_conviction_buy(sig, kscore=70.0, regime="bull")
    assert all_passed is False
    assert any("recent peak" in f for f in failed)
    assert any("1.5%" in f for f in failed)


def test_near_recent_high_hot_false_does_not_block():
    sig = _signal(near_recent_high_hot=False)
    all_passed, tier, passed, failed = _is_conviction_buy(sig, kscore=70.0, regime="bull")
    assert all_passed is True


def test_both_new_disqualifiers_can_fire_together():
    sig = _signal(
        stoch_rsi_overbought=False, stoch_rsi_still_hot=True,
        near_recent_high_hot=True, pct_from_20d_high=0.02,
    )
    all_passed, tier, passed, failed = _is_conviction_buy(sig, kscore=70.0, regime="bull")
    assert all_passed is False
    assert any("barely cooled" in f for f in failed)
    assert any("recent peak" in f for f in failed)


def test_missing_pct_from_20d_high_degrades_to_question_mark_not_a_crash():
    sig = _signal(near_recent_high_hot=True, pct_from_20d_high=None)
    all_passed, tier, passed, failed = _is_conviction_buy(sig, kscore=70.0, regime="bull")
    assert all_passed is False
    assert any("?" in f and "recent peak" in f for f in failed)


# ── Neither new check is in the soft-layer tolerance list ────────────────────────────────

def test_new_disqualifiers_are_hard_failures_not_soft_tolerated():
    # A candidate that fails ONLY a new disqualifier (everything else clean) should NOT
    # reach "near" tier the way a single OBV/ADX/MACD/ML soft-fail would — these are hard
    # blocks, matching stoch_rsi_overbought/rsi_divergence's existing behavior.
    sig = _signal(near_recent_high_hot=True, pct_from_20d_high=0.01)
    all_passed, tier, passed, failed = _is_conviction_buy(sig, kscore=70.0, regime="bull")
    assert tier == "failed"
    assert all_passed is False


# ── AUD-CHASE-ROC10: third overextension disqualifier ────────────────────────────────────
# Independent of both checks above: neither the stochastic nor the 20-day-high distance
# catches a stock that has simply run too far too fast (a stock can be 8% below its 20-day
# high with RSI 58 and still be up 20% in ten days after bouncing off a low).
#
# Measured over post-AUD232 outcomes (n=4,770): return by prior 10-day run-up is monotonic in
# BOTH return and win rate — falling +0.09%/52.9%, mild(0-5) -0.84%/45.4%, strong(5-10)
# -1.13%/41.1%, hot(10-15) -3.27%/35.0%, parabolic(>15) -4.37%/31.6%.

def test_parabolic_runup_is_blocked():
    sig = _signal(roc_10=20.0)
    all_passed, tier, passed, failed = _is_conviction_buy(sig, kscore=70.0, regime="bull")
    assert all_passed is False
    assert any("10 days" in f for f in failed)


def test_moderate_runup_still_passes():
    """5% in ten days is normal participation, not chasing — must not block."""
    all_passed, tier, passed, failed = _is_conviction_buy(
        _signal(roc_10=5.0), kscore=70.0, regime="bull")
    assert all_passed is True
    assert tier == "full"


def test_falling_stock_passes_this_check():
    """The genuinely profitable bucket (+0.09%, 52.9% win) — a real dip entry."""
    all_passed, tier, passed, failed = _is_conviction_buy(
        _signal(roc_10=-6.0), kscore=70.0, regime="bull")
    assert all_passed is True
    assert not any("10 days" in f for f in failed)


def test_threshold_is_inclusive_at_the_limit():
    """>= 10, so exactly 10.0 blocks — pins the boundary against silent drift."""
    all_passed, _, _, failed = _is_conviction_buy(
        _signal(roc_10=10.0), kscore=70.0, regime="bull")
    assert all_passed is False
    assert any("10 days" in f for f in failed)

    all_passed_below, _, _, _ = _is_conviction_buy(
        _signal(roc_10=9.9), kscore=70.0, regime="bull")
    assert all_passed_below is True


def test_missing_roc10_fails_open_and_does_not_block():
    """Signals generated before roc_10 existed in reasons (and any symbol with <11 bars,
    where signals.py publishes None) must not be blocked by a value that isn't there."""
    sig = _signal()
    sig["reasons"].pop("roc_10", None)
    all_passed, tier, _, failed = _is_conviction_buy(sig, kscore=70.0, regime="bull")
    assert all_passed is True
    assert not any("10 days" in f for f in failed)

    sig_none = _signal(roc_10=None)
    all_passed_none, _, _, failed_none = _is_conviction_buy(sig_none, kscore=70.0, regime="bull")
    assert all_passed_none is True
    assert not any("10 days" in f for f in failed_none)


def test_roc10_block_is_a_hard_failure_not_soft_tolerated():
    all_passed, tier, _, _ = _is_conviction_buy(_signal(roc_10=25.0), kscore=70.0, regime="bull")
    assert tier == "failed"
    assert all_passed is False


def test_roc10_is_independent_of_the_other_two_overextension_checks():
    """The whole point of a third check: a stock well off its high, not overbought, with a
    cool stochastic, that has still run 18% in ten days must be caught."""
    sig = _signal(roc_10=18.0, pct_from_20d_high=0.08, rsi=58.0,
                  stoch_rsi_overbought=False, stoch_rsi_still_hot=False,
                  near_recent_high_hot=False)
    all_passed, _, _, failed = _is_conviction_buy(sig, kscore=70.0, regime="bull")
    assert all_passed is False
    assert any("10 days" in f for f in failed)
    assert not any("recent peak" in f for f in failed)
