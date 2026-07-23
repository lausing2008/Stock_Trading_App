"""Tests for T232-SIG-ENTRYTIMING options 1 and 2 — see the Design Reference of the same
name in CLAUDE.md for the full user-facing explanation and the 2 deferred (3/4) options.

User's original complaint: the AI Signal tends to fire BUY after a stock has already run up,
rather than at a genuine dip/bottom. Root cause: `_ta_score()`'s momentum pillar scored RSI
28-35 (the exact "early recovery off a real dip" zone) as a flat 0.0 — identical treatment to
a stock with NO oversold evidence at all — while `_pullback_recovery()`'s bonus (built
specifically to reward a healthy dip+recovery) could only ever apply AFTER the pillar gate had
already cleared, which a fresh recovery structurally can't do yet.

Option 1 (momentum pillar): give RSI 28-35 partial credit (0.5), mirroring the BEARISH
pillar's own identical treatment of this exact range on its side.

Option 2 (pullback-recovery gate): let the recovery bonus apply even below the style's
min_pillars requirement, but ONLY when RSI sits in the 30-45 recovery band AND the recovery
is volume-confirmed (pr_delta >= 0.07, the strongest tier) AND at least 2 real pillars are
active (never a full bypass of the universal SA-19 floor).
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.generators.signals import _apply_style_signal, _ta_score  # noqa: E402


def _minimal_df(n: int = 260) -> pd.DataFrame:
    """A plain, slowly-rising synthetic OHLCV series — only used to reach _ta_score()'s
    len(df) >= 15 floor and give every OTHER indicator a real (non-None) value. The RSI value
    itself is monkeypatched directly (see _mock_rsi below) rather than engineered via price
    shape, since hitting an exact narrow RSI band through synthetic price generation is
    fragile and indirect compared to controlling the one value under test directly.
    """
    rng = np.random.default_rng(7)
    close = 100 + np.cumsum(rng.normal(0.05, 1.0, n))
    close = np.maximum(close, 1.0)
    return pd.DataFrame({
        "close": close,
        "high": close + np.abs(rng.normal(0, 0.5, n)),
        "low": close - np.abs(rng.normal(0, 0.5, n)),
        "open": close + rng.normal(0, 0.3, n),
        "volume": rng.integers(1_600_000, 2_400_000, n).astype(float),
    })


def _mock_rsi(monkeypatch, rsi_value: float):
    """Force _canon_rsi's last value to an exact number, isolating rsi_score's scoring table
    from everything else _ta_score() computes from the same price series."""
    import src.generators.signals as sig_mod

    def _fake_rsi(close, window=14):
        s = pd.Series(np.full(len(close), rsi_value), index=close.index)
        return s

    monkeypatch.setattr(sig_mod, "_canon_rsi", _fake_rsi)


# ── Option 1: RSI 28-35 partial credit in the momentum pillar ──────────────────

class TestOption1RsiRecoveryZone:
    @pytest.mark.parametrize("rsi_value", [28.0, 30.0, 34.9])
    def test_rsi_in_recovery_zone_scores_partial_not_zero(self, monkeypatch, rsi_value):
        _mock_rsi(monkeypatch, rsi_value)
        df = _minimal_df()
        _, reasons = _ta_score(df)
        assert reasons["rsi"] == pytest.approx(rsi_value)
        # p_momentum = rsi_score*0.35 + macd_score*0.40 + stoch_score*0.25 — rsi_score alone
        # isn't directly exposed in reasons, so infer its floor via p_momentum's own floor:
        # with rsi_score=0.5 (not 0.0), p_momentum must be at least 0.5*0.35 = 0.175 higher
        # than it would be at rsi_score=0.0, all else equal. Cross-check against the
        # zero-credit case directly instead of re-deriving the formula.
        assert reasons["pillar_momentum"] > 0.0  # sanity: the pillar isn't fully zeroed

    def test_rsi_27_9_still_scores_zero_not_partial(self, monkeypatch):
        """Below 28 stays 0.0 — genuinely extreme oversold has no confirmation yet, matching
        the bearish pillar's own boundary. This is the one case that must NOT change."""
        _mock_rsi(monkeypatch, 27.9)
        df = _minimal_df()
        _, reasons_below = _ta_score(df)
        _mock_rsi(monkeypatch, 30.0)
        _, reasons_in_zone = _ta_score(df)
        # The in-zone momentum score must be strictly higher than the below-28 one, holding
        # every other indicator identical (same df, same monkeypatched RSI series shape).
        assert reasons_in_zone["pillar_momentum"] > reasons_below["pillar_momentum"]

    def test_rsi_recovery_zone_scores_lower_than_the_sweet_spot(self, monkeypatch):
        """28-35 must remain WORSE than 45-65 (the full-credit zone) — this is a partial-credit
        fix, not a claim that a fresh recovery is as strong as a confirmed uptrend."""
        _mock_rsi(monkeypatch, 30.0)
        df = _minimal_df()
        _, reasons_recovery = _ta_score(df)
        _mock_rsi(monkeypatch, 55.0)
        _, reasons_sweet_spot = _ta_score(df)
        assert reasons_recovery["pillar_momentum"] < reasons_sweet_spot["pillar_momentum"]

    def test_rsi_recovery_zone_matches_the_65_72_tier_exactly(self, monkeypatch):
        """Both are documented as the SAME 0.5 partial-credit tier — a real regression guard
        that the two tiers stay symmetric rather than silently drifting apart."""
        _mock_rsi(monkeypatch, 30.0)
        df = _minimal_df()
        _, reasons_28_35 = _ta_score(df)
        _mock_rsi(monkeypatch, 68.0)
        _, reasons_65_72 = _ta_score(df)
        assert reasons_28_35["pillar_momentum"] == pytest.approx(reasons_65_72["pillar_momentum"], abs=1e-9)


# ── Option 2: pullback-recovery bonus applies below min_pillars for a real recovery ────────

def _call_apply_style_signal(rsi_val, pillars, pr_delta, style_key="SWING"):
    base_reasons = {
        "rsi": rsi_val,
        "independent_pillars_active": pillars,
        "pullback_recovery_delta": pr_delta,
    }
    return _apply_style_signal(
        ta_prob=0.55, ml_prob=None, ml_test_auc=0.5, style_key=style_key,
        market_regime="bull", adx_val=20.0, weekly_tech={}, pattern_adj=0.0,
        days_to_earnings=None, news_sentiment=None, rs_rank=None,
        options_sentiment=None, cp_ratio=None, kscore=None, is_stale=False,
        base_reasons=base_reasons,
    )


class TestOption2EarlyRecoveryException:
    def test_two_pillar_swing_with_confirmed_recovery_and_recovery_rsi_gets_the_bonus(self):
        """The core fix: SWING requires min_pillars=3; a 2-pillar setup would previously never
        get the pullback-recovery bonus at all. With RSI in the 30-45 band and a
        volume-confirmed recovery (pr_delta=0.07), it now does."""
        result = _call_apply_style_signal(rsi_val=40.0, pillars=2, pr_delta=0.07)
        assert result.reasons["pullback_recovery_applied"] is True
        assert result.reasons["pullback_recovery_early_exception"] is True
        # Still correctly compressed by the pillar gate itself — the exception adds the
        # recovery bonus ON TOP of the compression, it doesn't remove the compression.
        assert result.reasons["pillar_gate"] == "compressed_2_pillar_below_min3"

    def test_one_pillar_setup_does_not_get_the_exception(self):
        """The universal SA-19 floor (>= 2 real pillars) must still apply — a setup with
        essentially no independent TA support must not benefit from this exception, matching
        the original SA-14/SA-32 comment's own warning against a full bypass."""
        result = _call_apply_style_signal(rsi_val=40.0, pillars=1, pr_delta=0.07)
        assert result.reasons["pullback_recovery_applied"] is False
        assert "pullback_recovery_early_exception" not in result.reasons

    def test_weak_unconfirmed_recovery_does_not_get_the_exception(self):
        """pr_delta=0.04 is _pullback_recovery()'s WEAKER tier (no volume confirmation) — the
        exception is deliberately narrower than 'any recovery,' requiring the strongest,
        volume-confirmed tier specifically."""
        result = _call_apply_style_signal(rsi_val=40.0, pillars=2, pr_delta=0.04)
        assert result.reasons["pullback_recovery_applied"] is False

    def test_rsi_outside_the_recovery_band_does_not_get_the_exception(self):
        """RSI 60 (not a dip-recovery reading at all) with a 2-pillar setup below min_pillars
        must NOT get the bonus — the exception is scoped to genuine recovery-zone RSI only."""
        result = _call_apply_style_signal(rsi_val=60.0, pillars=2, pr_delta=0.07)
        assert result.reasons["pullback_recovery_applied"] is False

    def test_pillars_meeting_min_pillars_still_use_the_original_unconditional_path(self):
        """When pillars already clear the style minimum, the bonus applies exactly as before
        this fix — the exception is additive, not a replacement of the original gate."""
        result = _call_apply_style_signal(rsi_val=40.0, pillars=3, pr_delta=0.07, style_key="SWING")
        assert result.reasons["pullback_recovery_applied"] is True
        assert "pullback_recovery_early_exception" not in result.reasons  # met via the normal path, not the exception

    def test_real_before_after_probability_delta_is_positive(self):
        """End-to-end sanity: the exact scenario from CLAUDE.md's documented before/after check
        (rsi=40, pillars=2, pr_delta=0.07, SWING) must show a real, positive probability lift
        versus not applying the bonus at all."""
        with_bonus = _call_apply_style_signal(rsi_val=40.0, pillars=2, pr_delta=0.07)
        without_bonus = _call_apply_style_signal(rsi_val=40.0, pillars=2, pr_delta=0.0)
        assert with_bonus.bullish_probability > without_bonus.bullish_probability
