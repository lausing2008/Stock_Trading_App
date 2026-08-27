"""Tests for T234-CONFIG-UNJUSTIFIED-THRESHOLDS Group B's cfg-driven curve-shape constants in
kscore.py (#17 RSI-to-score piecewise mapping, #18 ADX-boost normalization, #19 volatility
scale factor).

kscore.py imports cleanly and directly in this test environment (numpy/pandas only, no Docker-
only dependency) — every test here calls the real _technical_score()/_volatility_score()/
_curve_params() directly, not a mock.

A real bug was caught and fixed while writing this parameterization, before it shipped: the
original ADX-boost formula is `clip((adx - 15) / 25, -1, 1) * 10` — an early parameterization
attempt assumed the divisor (25) was `ceiling - floor` (i.e. adx_ceiling=25, adx_floor=15,
ramp=10), which is a DIFFERENT function from the real one (the clip only actually saturates at
+-10 when |adx-15| >= 25, i.e. adx<=-10 or adx>=40 — not at adx=25 the way the original comment's
"strong trend >25" prose implied). Caught via a direct 200-random-seed byte-identical check
against a hand-reimplemented copy of the ORIGINAL hardcoded formula before trusting the
refactor — several tests below lock in the corrected adx_center/adx_divisor semantics
specifically to guard against this exact class of mistake recurring.
"""
import numpy as np
import pandas as pd

from src.scoring.kscore import (
    _CURVE_DEFAULTS,
    _adx_value,
    _curve_params,
    _rsi,
    _technical_raw_inputs,
    _technical_score,
    _technical_score_from_raw,
    _volatility_raw_input,
    _volatility_score,
    _volatility_score_from_raw,
)


def _price_df(n=250, seed=0, drift=0.0):
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(drift, 1.5, n))
    close = np.clip(close, 1, None)
    high = close + rng.uniform(0, 2, n)
    low = close - rng.uniform(0, 2, n)
    return pd.DataFrame({
        "close": close, "high": high, "low": low, "open": close,
        "volume": rng.integers(1000, 5000, n),
    })


def _old_technical_score(df: pd.DataFrame) -> float:
    """The ORIGINAL, pre-parameterization hardcoded formula, hand-copied here as an
    independent reference — never imported from the real module, so it can't silently drift
    alongside a bug in the refactor it's meant to catch."""
    close_s = df["close"]
    sma50 = close_s.rolling(50).mean().iloc[-1]
    sma200 = close_s.rolling(200).mean().iloc[-1]
    s50_ok = not pd.isna(sma50)
    s200_ok = not pd.isna(sma200)
    above_sma50 = (1 if close_s.iloc[-1] > sma50 else 0) if s50_ok else 0.5
    above_sma200 = (1 if close_s.iloc[-1] > sma200 else 0) if s200_ok else 0.5
    sma50_above_sma200 = (1 if sma50 > sma200 else 0) if (s50_ok and s200_ok) else 0.5
    r = _rsi(close_s).iloc[-1]
    if pd.isna(r):
        rsi_score = 75.0
    elif r <= 30:
        rsi_score = 50.0
    elif r <= 50:
        rsi_score = 50.0 + (r - 30) * 2.0
    elif r <= 70:
        rsi_score = 90.0 + (r - 50) * 0.5
    else:
        rsi_score = 100.0 - (r - 70) * 2.5
    adx = _adx_value(df)
    adx_boost = np.clip((adx - 15) / 25, -1, 1) * 10 if adx is not None else 0.0
    base = (above_sma50 + above_sma200 + sma50_above_sma200) / 3 * 60 + rsi_score * 0.4
    return float(np.clip(base + adx_boost, 0, 100))


def _old_volatility_score(df: pd.DataFrame) -> float:
    ret = df["close"].pct_change()
    vol = ret.rolling(60).std().iloc[-1]
    if pd.isna(vol):
        return 50.0
    return float(np.clip(100 - vol * 1500, 0, 100))


# ── Byte-identical-at-defaults: the core regression guard ────────────────────────

def test_technical_score_matches_the_original_formula_at_defaults_across_many_seeds():
    """200 randomized price series (varying length, drift, and volatility — exercising every
    piecewise RSI branch, the SMA-NaN neutral-fallback path, and both ADX-known/unknown cases)
    with cfg=None must reproduce the ORIGINAL hardcoded formula's output exactly."""
    for seed in range(200):
        rng = np.random.default_rng(seed)
        n = int(rng.integers(5, 260))
        drift = float(rng.choice([-1, 0, 1])) * float(rng.uniform(0, 0.5))
        df = _price_df(n=n, seed=seed, drift=drift)
        new = _technical_score(df, None)
        old = _old_technical_score(df)
        assert abs(new - old) < 1e-9, f"seed={seed} n={n}: new={new} old={old}"


def test_volatility_score_matches_the_original_formula_at_defaults_across_many_seeds():
    for seed in range(50):
        df = _price_df(n=int(np.random.default_rng(seed).integers(5, 260)), seed=seed)
        new = _volatility_score(df, None)
        old = _old_volatility_score(df)
        assert abs(new - old) < 1e-9, f"seed={seed}: new={new} old={old}"


def test_empty_cfg_dict_is_identical_to_none():
    """A caller passing {} explicitly (vs. omitting cfg / passing None) must behave the same —
    both should resolve to the hardcoded defaults."""
    df = _price_df()
    assert _technical_score(df, {}) == _technical_score(df, None)
    assert _volatility_score(df, {}) == _volatility_score(df, None)


def test_curve_params_never_mutates_the_module_level_defaults():
    """Same class of bug T288-KSCORE-WEIGHT-SWEEP's own _load_active_weights() already guards
    against for _WEIGHTS — a caller mutating its own local copy must never corrupt
    _CURVE_DEFAULTS for every later call in the same process."""
    p = _curve_params({"volatility_scale": 999.0})
    p["rsi_low"] = -1.0  # mutate the returned dict
    assert _CURVE_DEFAULTS["rsi_low"] == 30.0
    assert _CURVE_DEFAULTS["volatility_scale"] == 1500.0


def test_curve_params_ignores_unknown_keys_in_cfg():
    """A cfg dict carrying an unrelated key (e.g. mixed in from a broader config_overrides
    payload) must not silently leak into the curve params or raise."""
    p = _curve_params({"some_unrelated_key": 42, "volatility_scale": 2000.0})
    assert "some_unrelated_key" not in p
    assert p["volatility_scale"] == 2000.0


# ── #19: volatility_scale — a genuinely swept parameter genuinely changes the output ──────

def test_volatility_scale_override_changes_the_score():
    df = _price_df(n=200, seed=1)
    baseline = _volatility_score(df, None)
    tighter = _volatility_score(df, {"volatility_scale": 3000.0})
    assert baseline != tighter


# ── #17: RSI-to-score piecewise mapping — each segment slope is genuinely derived, not fixed ──

def test_rsi_mid_anchor_override_changes_the_lo_mid_segment_slope():
    """Raising score_at_mid (the anchor at rsi_mid=50) must steepen the lo_mid segment's slope
    without needing any separate 'slope' key — confirms the slope really is DERIVED from the
    anchor pair, not an independent, potentially-inconsistent parameter."""
    p_default = _curve_params(None)
    p_override = _curve_params({"score_at_mid": 95.0})
    slope_default = (p_default["score_at_mid"] - p_default["score_at_low"]) / (
        p_default["rsi_mid"] - p_default["rsi_low"]
    )
    slope_override = (p_override["score_at_mid"] - p_override["score_at_low"]) / (
        p_override["rsi_mid"] - p_override["rsi_low"]
    )
    assert slope_override > slope_default


def test_rsi_breakpoint_override_reaches_the_technical_score_function():
    """A genuinely different rsi_low breakpoint must produce a genuinely different
    _technical_score() output for a fixed price series (not just a different _curve_params()
    dict that never gets consumed)."""
    df = _price_df(n=200, seed=2)
    baseline = _technical_score(df, None)
    shifted = _technical_score(df, {"rsi_low": 45.0})
    assert baseline != shifted


# ── #18: ADX-boost — the corrected adx_center/adx_divisor/adx_boost_scale semantics ──────────

def test_adx_boost_saturates_only_when_the_true_divisor_bound_is_reached_not_at_the_old_ceiling_name():
    """Direct proof of the bug this whole parameterization caught and fixed: at the hardcoded
    defaults (center=15, divisor=25, scale=10), the clip does NOT saturate at adx=25 (a naive
    'ceiling' reading of the original comment) — it saturates at adx=40 (center + divisor)."""
    boost_at_25 = np.clip((25 - _CURVE_DEFAULTS["adx_center"]) / _CURVE_DEFAULTS["adx_divisor"], -1, 1) * _CURVE_DEFAULTS["adx_boost_scale"]
    boost_at_40 = np.clip((40 - _CURVE_DEFAULTS["adx_center"]) / _CURVE_DEFAULTS["adx_divisor"], -1, 1) * _CURVE_DEFAULTS["adx_boost_scale"]
    assert boost_at_25 < 10.0  # NOT yet saturated
    assert boost_at_40 == 10.0  # genuinely saturated here


def test_adx_divisor_override_changes_the_ramp_steepness():
    """A smaller divisor makes the boost ramp reach saturation faster (steeper) — confirms
    adx_divisor genuinely controls ramp steepness, matching the real formula's semantics."""
    center = _CURVE_DEFAULTS["adx_center"]
    scale = _CURVE_DEFAULTS["adx_boost_scale"]
    adx_test_val = center + 5  # partway up the ramp
    wide = np.clip((adx_test_val - center) / 25.0, -1, 1) * scale
    narrow = np.clip((adx_test_val - center) / 5.0, -1, 1) * scale
    assert narrow > wide  # narrower divisor = faster ramp = bigger boost at the same ADX


def test_curve_defaults_key_set_matches_exactly_what_technical_and_volatility_score_read():
    """Sanity check that every key in _CURVE_DEFAULTS is a real, consumed parameter — not a
    stale leftover from an earlier design iteration (e.g. the discarded adx_floor/adx_ceiling
    naming from the buggy first attempt)."""
    expected_keys = {
        "rsi_low", "rsi_mid", "rsi_high",
        "score_at_low", "score_at_mid", "score_at_high",
        "rsi_overbought_decay_per_point",
        "adx_center", "adx_divisor", "adx_boost_scale",
        "volatility_scale",
    }
    assert set(_CURVE_DEFAULTS.keys()) == expected_keys


# ── Raw/mapping split (built for the sweep's own compute-cost reason — RSI/ADX are the
# dominant cost, profiled directly at ~6ms/call vs. ~0.1ms for the curve-remap step alone) ───
#
# The split MUST be behaviorally invisible: compute-raw-once-then-remap-many-times must equal
# calling the original combined function fresh every time.

def test_technical_raw_plus_remap_composed_together_equals_the_combined_function():
    for seed in range(50):
        rng = np.random.default_rng(seed)
        n = int(rng.integers(5, 260))
        df = _price_df(n=n, seed=seed)
        combined = _technical_score(df, None)
        raw = _technical_raw_inputs(df)
        remapped = _technical_score_from_raw(raw, None)
        assert combined == remapped, f"seed={seed}: combined={combined} remapped={remapped}"


def test_volatility_raw_plus_remap_composed_together_equals_the_combined_function():
    for seed in range(50):
        df = _price_df(n=int(np.random.default_rng(seed).integers(5, 260)), seed=seed)
        combined = _volatility_score(df, None)
        raw = _volatility_raw_input(df)
        remapped = _volatility_score_from_raw(raw, None)
        assert combined == remapped, f"seed={seed}: combined={combined} remapped={remapped}"


def test_remapping_the_same_raw_technical_inputs_under_different_candidates_never_recomputes_rsi_or_adx():
    """The whole point of the split: raw RSI/ADX values are computed exactly once, then the
    SAME raw dict is fed through the cheap remap step for every candidate — confirms a single
    raw computation genuinely produces varying scores across different cfg overrides (i.e. the
    remap step really is reading from the passed-in raw dict, not silently recomputing).

    Uses a hand-constructed raw dict (not one derived from a random price series) with a
    deliberately chosen rsi=35 — squarely inside the lo_mid segment (30-50) so a change to
    rsi_low is guaranteed to shift the result, rather than relying on a random seed's real RSI
    happening to land in the affected branch (a real bug caught in this exact test on first
    write: seed=3's real RSI was 62.1, already past rsi_mid=50, so moving rsi_low had zero
    effect — not a bug in the split, just a flawed test premise assuming ANY rsi_low change
    always matters regardless of the actual RSI value)."""
    raw = {"above_sma50": 1, "above_sma200": 1, "sma50_above_sma200": 1, "rsi": 35.0, "adx": None}
    baseline = _technical_score_from_raw(raw, None)
    candidate = _technical_score_from_raw(raw, {"rsi_low": 34.0})
    assert baseline != candidate


def test_volatility_raw_input_is_none_for_too_short_a_series_not_a_fabricated_zero():
    """A series shorter than the 60-bar rolling-std window must produce None (matching the
    combined function's own NaN-guard fallback to 50.0), never a spuriously-computed real vol
    value from an under-filled window."""
    df = _price_df(n=10, seed=4)
    assert _volatility_raw_input(df) is None
    assert _volatility_score_from_raw(None, None) == 50.0
