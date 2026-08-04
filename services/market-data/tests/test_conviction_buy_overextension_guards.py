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


_namespace = {"_REGIME_THRESHOLDS": _load_constant("_REGIME_THRESHOLDS")}
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
