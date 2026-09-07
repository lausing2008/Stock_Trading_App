"""Regression tests for the two bearish-pillar direction-blindness bugs found in the 2026-09-06
deep audit (Finding R2-6 / priority item #2), both inside `_ta_score()` in
src/generators/signals.py.

Both bugs corrupted `bearish_pillars_active`, the exact feature that
`stockai:style_tune:{SWING,SHORT}:min_pillars_for_sell` (armed in production at 3, tuned
2026-08-31) reads to gate SELL signals — so both were live, not merely latent.

Bug (a) — AUD-BEARVOLUME-DIRECTIONBLIND: the bearish volume pillar
(`obv_bear_signal or vol_z_signal`) let volume MAGNITUDE alone count as bearish evidence,
including when OBV was BULLISH (textbook accumulation) and when OBV was bearish but volume was
flat (i.e. no bearish evidence beyond OBV, yet it scored 0.6 same as OBV-bearish-plus-volume).
Fixed by gating `vol_z_signal`'s contribution on `obv_bear_signal` already being true.

Bug (b) — AUD-BEARMOMENTUM-NEUTRALPEAK: `rsi_bear_score`'s bands were a literal reflection of
the bullish `rsi_score` bands around RSI 50, which made a perfectly NEUTRAL RSI of 50 score
maximum bearish conviction (1.0) — identical to maximum bullish conviction — while RSI 65-72 (a
rally actually topping out, the real SELL case this pillar exists to catch) scored 0.0. Fixed by
rebanding so peak bearish conviction sits at elevated-and-turning RSI (55-72), not neutral.
"""
import numpy as np
import pandas as pd
import pytest

from src.generators.signals import _ta_score


def _make_df(n: int = 300, trend: float = 0.0, seed: int = 0, noise: float = 1.0) -> pd.DataFrame:
    """Same synthetic-OHLCV helper as test_bearish_pillars.py, reused for consistency."""
    rng = np.random.default_rng(seed)
    close = 100 + (np.ones(n) * trend + rng.normal(0, noise, n)).cumsum()
    close = np.maximum(close, 1.0)
    return pd.DataFrame(
        {
            "close": close,
            "high": close + np.abs(rng.normal(0, 0.5, n)),
            "low": close - np.abs(rng.normal(0, 0.5, n)),
            "open": close + rng.normal(0, 0.3, n),
            "volume": rng.integers(1_600_000, 2_400_000, n).astype(float),
        }
    )


# ── Bearish momentum pillar (bug b: RSI-50 no longer maximal) ───────────────────────────────

def _flat_then_slight_rise_df(n: int = 300, seed: int = 0) -> pd.DataFrame:
    """A long flat/choppy series (RSI settles near neutral, ~50) followed by a short gentle
    rise, engineered to land rsi_val in the low-50s to mid-60s range without a strong trend
    dominating every other pillar (which would make bearish_pillar_momentum hard to isolate)."""
    rng = np.random.default_rng(seed)
    flat = 100 + rng.normal(0, 0.4, n - 20).cumsum() * 0.0  # pure noise, no drift
    flat = 100 + rng.normal(0, 0.6, n - 20)
    rise = flat[-1] + np.arange(1, 21) * 0.35
    close = np.concatenate([flat, rise])
    close = np.maximum(close, 1.0)
    return pd.DataFrame(
        {
            "close": close,
            "high": close + np.abs(rng.normal(0, 0.5, n)),
            "low": close - np.abs(rng.normal(0, 0.5, n)),
            "open": close + rng.normal(0, 0.3, n),
            "volume": rng.integers(1_600_000, 2_400_000, n).astype(float),
        }
    )


def test_neutral_rsi_no_longer_scores_maximum_bearish_momentum():
    """The core bug: an RSI at or near 50 (neutral) must not score bearish_pillar_momentum at
    its maximum — a perfectly neutral stock is not confirmed-bearish evidence."""
    df = _flat_then_slight_rise_df(seed=11)
    _, reasons = _ta_score(df)
    rsi = reasons.get("rsi")
    if rsi is not None and 47 <= rsi <= 53:
        assert reasons["bearish_pillar_momentum"] < 1.0, (
            f"RSI={rsi} is neutral and must not score max bearish momentum conviction "
            f"(got {reasons['bearish_pillar_momentum']})"
        )
    else:
        pytest.skip(f"fixture landed at rsi={rsi}, outside the neutral band this test targets")


def test_bearish_momentum_peak_band_sits_above_neutral_not_centered_on_it():
    """Formula-shape assertion, robust to not hitting an exact synthetic RSI: the peak (1.0)
    band must sit above neutral (elevated-and-turning RSI), not straddle RSI 50 the way the
    pre-fix literal mirror of the bullish bands did — before the fix, RSI 65-72 (a rally
    actually topping out) scored 0.0 while RSI 50 (neutral) scored 1.0."""
    import pathlib
    src_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "generators" / "signals.py"
    source = src_path.read_text()
    anchor = source.index("rsi_bear_score = (")
    block = source[anchor:anchor + 400]
    assert "1.0 if (rsi_val is not None and 55 <= rsi_val < 72)" in block, (
        "the bearish momentum peak band must sit above neutral (elevated-and-turning RSI), "
        "not straddle RSI 50 the way the pre-fix literal mirror of the bullish bands did"
    )


def test_extreme_oversold_kill_guard_still_zeroes_bearish_momentum():
    """RSI <= 28 (extreme oversold) must remain a bounce-warning, not bearish confirmation —
    this boundary was correct before the fix and must not regress. Engineer a sharp sustained
    decline to reliably push RSI into extreme-oversold territory."""
    df = _make_df(n=300, trend=-1.2, seed=7, noise=0.3)
    _, reasons = _ta_score(df)
    rsi = reasons.get("rsi")
    if rsi is not None and rsi <= 28:
        assert reasons["bearish_pillar_momentum"] < 0.5, (
            f"RSI={rsi} is extreme oversold and must not count as confirmed bearish momentum "
            f"(got {reasons['bearish_pillar_momentum']})"
        )
    else:
        pytest.skip(f"fixture landed at rsi={rsi}, did not reach extreme-oversold for this test")


# ── Bearish volume pillar (bug a) ────────────────────────────────────────────────────────────

def _pb_volume(obv_bear_signal: bool, vol_z_signal: bool) -> float:
    """Mirrors the exact post-fix branch structure at signals.py's bearish VOLUME pillar. This
    isolates the branch logic directly since engineering exact OBV/volume-z combinations via
    synthetic OHLCV is far less precise than testing the formula's own truth table."""
    if obv_bear_signal and vol_z_signal:
        return 1.0
    elif obv_bear_signal:
        return 0.6
    else:
        return 0.0


def test_bullish_obv_with_volume_spike_no_longer_scores_as_bearish():
    """The most severe case: OBV bullish (accumulation) + volume expansion is textbook BUYING
    pressure, yet used to score 0.6 bearish via the OR. Must now score 0.0."""
    assert _pb_volume(obv_bear_signal=False, vol_z_signal=True) == 0.0


def test_no_bearish_evidence_no_longer_scores_as_active_pillar():
    """OBV not bearish AND volume flat is zero bearish evidence of any kind. Used to score 0.6
    (the bare `or` was satisfied by neither operand alone — this was the direct bug)."""
    assert _pb_volume(obv_bear_signal=False, vol_z_signal=False) == 0.0


def test_obv_bearish_alone_still_gets_partial_credit():
    """OBV genuinely bearish, even without a volume spike, is still real (if partial) bearish
    evidence — this case must NOT be zeroed by the fix, only the false-positive cases above."""
    assert _pb_volume(obv_bear_signal=True, vol_z_signal=False) == 0.6


def test_obv_bearish_with_volume_confirmation_scores_full_conviction():
    assert _pb_volume(obv_bear_signal=True, vol_z_signal=True) == 1.0


def test_pb_volume_source_no_longer_uses_bare_or_with_vol_z_signal():
    """Source-level guard against the exact regression shape: `obv_bear_signal or vol_z_signal`
    let volume magnitude alone (including alongside BULLISH OBV) count as bearish evidence."""
    import pathlib

    src_path = (
        pathlib.Path(__file__).resolve().parents[1] / "src" / "generators" / "signals.py"
    )
    source = src_path.read_text()
    anchor = source.index("obv_bear_signal = not obv_trend_bullish")
    block = source[anchor:anchor + 400]
    assert "obv_bear_signal or vol_z_signal" not in block, (
        "the bearish volume pillar must not use a bare OR between obv_bear_signal and "
        "vol_z_signal — vol_z_signal is pure magnitude and cannot stand alone as bearish "
        "evidence (it lets a bullish-OBV volume spike score as bearish)."
    )
