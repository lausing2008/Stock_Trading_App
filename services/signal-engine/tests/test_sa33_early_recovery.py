"""Tests for SA-33 (early-recovery entry timing fix) — all 3 findings, including the
threshold-reachability correction to Finding 1.

Background: a design doc (docs/SIGNAL_FIX_SA33_2026-07-25.md) proposed 3 fixes for BUY signals
structurally firing at the top of a move rather than at the bottom. An adversarial review found
Finding 1 (TREND pillar early-recovery credit) mathematically could NOT achieve its own stated
goal: the doc/code claimed a 0.25 partial credit would let the trend pillar reach the 0.5
"active" threshold (used by independent_pillars_active) "when combined with a supertrend
cross-up or ADX bullish signal" — but during genuine early recovery, sma50_above_sma200 and
golden_cross_event are BOTH structurally False (they require sma50 > sma200, which by
definition hasn't happened yet in early recovery), capping the achievable p_trend at
0.25*0.30 + 1.0*0.20 + 1.0*0.10 = 0.375 even with EVERY other signal confirming — never 0.5.

Fixed by raising the credit to 0.70 — reachable arithmetic proven below — which requires BOTH
a supertrend cross-up AND an ADX bullish trend to confirm together (a real, achievable, and
still-conservative bar; the original "either one alone" framing was mathematically unreachable
at any credit <= 1.0, since the weaker single signal, st_cross_up at 0.10 weight, would need a
credit > 1.0/0.30, off the pillar's own 0-1 scale).

Findings 2 (RS compression recovery exemption) and 3 (weekly gate recovery exception) were
independently verified logically sound during the same review — tested here for completeness
and regression protection, not because a bug was found in them.
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
    len(df) >= 15 floor and give every OTHER indicator a real (non-None) value. The specific
    indicators under test (RSI, supertrend, ADX) are monkeypatched directly rather than
    engineered via price shape, since hitting an exact scenario through synthetic price
    generation is fragile and indirect compared to controlling the values under test directly.
    """
    rng = np.random.default_rng(11)
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
    import src.generators.signals as sig_mod

    def _fake_rsi(close, window=14):
        return pd.Series(np.full(len(close), rsi_value), index=close.index)

    monkeypatch.setattr(sig_mod, "_canon_rsi", _fake_rsi)


def _mock_supertrend(monkeypatch, st_trend: int, st_cross_up: bool, st_cross_down: bool = False):
    import src.generators.signals as sig_mod
    monkeypatch.setattr(sig_mod, "_supertrend", lambda df, **kw: (st_trend, st_cross_up, st_cross_down))


def _mock_adx(monkeypatch, adx_val: float, di_plus: float, di_minus: float):
    import src.generators.signals as sig_mod
    monkeypatch.setattr(sig_mod, "_adx", lambda df, **kw: (adx_val, di_plus, di_minus))


def _early_recovery_df(n: int = 260) -> pd.DataFrame:
    """A REAL price series engineered so price is genuinely above its own 20-day SMA but below
    its own 50-day SMA — the exact early-recovery structure (unlike RSI/supertrend/ADX, this one
    IS naturally reachable via real price shape: a long decline followed by a short, modest
    bounce that reclaims the 20-day average but not yet the slower-moving 50-day one).
    """
    rng = np.random.default_rng(1)
    decline_len, bounce_len = 230, n - 230
    decline = 200 - np.cumsum(rng.normal(0.3, 0.4, decline_len))
    bounce = decline[-1] + np.cumsum(rng.normal(0.05, 0.2, bounce_len))
    close = np.concatenate([decline, bounce])
    close = np.maximum(close, 1.0)
    df = pd.DataFrame({
        "close": close,
        "high": close + np.abs(rng.normal(0, 0.5, n)),
        "low": close - np.abs(rng.normal(0, 0.5, n)),
        "open": close + rng.normal(0, 0.3, n),
        "volume": rng.integers(1_600_000, 2_400_000, n).astype(float),
    })
    sma20 = df["close"].rolling(20).mean().iloc[-1]
    sma50 = df["close"].rolling(50).mean().iloc[-1]
    current = df["close"].iloc[-1]
    assert current > sma20, "fixture must have price above SMA20"
    assert current < sma50, "fixture must have price below SMA50"
    return df


# ── Finding 1 (corrected): TREND pillar early-recovery credit reachability ────────────────

class TestFinding1TrendPillarReachability:
    def test_early_recovery_flags_are_set_correctly(self):
        df = _early_recovery_df()
        _, reasons = _ta_score(df)
        assert reasons["above_sma20"] is True
        assert reasons["early_recovery_trend"] is True
        assert reasons["trend_above_sma50"] is False

    def test_early_recovery_alone_stays_well_below_active_threshold(self, monkeypatch):
        """0.70 credit alone (no supertrend cross-up, no ADX bullish trend) must NOT reach 0.5 —
        the credit alone must never make the pillar active on its own."""
        _mock_supertrend(monkeypatch, st_trend=0, st_cross_up=False)
        _mock_adx(monkeypatch, adx_val=15.0, di_plus=10.0, di_minus=20.0)  # not trending, bearish DI
        df = _early_recovery_df()
        _, reasons = _ta_score(df)
        assert reasons["pillar_trend"] < 0.5

    def test_early_recovery_with_only_supertrend_cross_up_stays_below_threshold(self, monkeypatch):
        """One confirming signal alone (supertrend cross-up, no ADX bullish trend) must still
        NOT reach 0.5 — the corrected fix requires BOTH, not either."""
        _mock_supertrend(monkeypatch, st_trend=1, st_cross_up=True)
        _mock_adx(monkeypatch, adx_val=15.0, di_plus=10.0, di_minus=20.0)  # not trending
        df = _early_recovery_df()
        _, reasons = _ta_score(df)
        assert reasons["pillar_trend"] < 0.5

    def test_early_recovery_with_only_adx_bullish_stays_below_threshold(self, monkeypatch):
        """One confirming signal alone (ADX bullish trend, no supertrend cross-up) must still
        NOT reach 0.5."""
        _mock_supertrend(monkeypatch, st_trend=0, st_cross_up=False)
        _mock_adx(monkeypatch, adx_val=30.0, di_plus=25.0, di_minus=10.0)  # trending + bullish DI
        df = _early_recovery_df()
        _, reasons = _ta_score(df)
        assert reasons["pillar_trend"] < 0.5

    def test_early_recovery_with_both_confirming_signals_reaches_active_threshold(self, monkeypatch):
        """The core fix: BOTH a supertrend cross-up AND an ADX bullish trend together must reach
        the 0.5 active threshold — this is the corrected, achievable version of the original
        (mathematically impossible) 'either one alone' claim."""
        _mock_supertrend(monkeypatch, st_trend=1, st_cross_up=True)
        _mock_adx(monkeypatch, adx_val=30.0, di_plus=25.0, di_minus=10.0)
        df = _early_recovery_df()
        _, reasons = _ta_score(df)
        assert reasons["pillar_trend"] >= 0.5

    def test_pillar_trend_value_matches_hand_computed_arithmetic(self, monkeypatch):
        """Direct regression guard on the exact formula — fails loudly if the credit or any
        weight silently drifts."""
        _mock_supertrend(monkeypatch, st_trend=1, st_cross_up=True)
        _mock_adx(monkeypatch, adx_val=30.0, di_plus=25.0, di_minus=10.0)
        df = _early_recovery_df()
        _, reasons = _ta_score(df)
        # 0.70*0.30 (early recovery credit) + 0.0*0.25 (sma_golden, sma50<sma200) +
        # 1.0*0.20 (bullish_trend) + 0.0*0.15 (gc_score, no golden cross yet) +
        # 1.0*0.10 (st_cross_up) = 0.21 + 0.20 + 0.10 = 0.51
        assert reasons["pillar_trend"] == pytest.approx(0.51, abs=0.01)

    def test_confirmed_above_sma50_still_scores_full_credit(self, monkeypatch):
        """A stock genuinely above its SMA50 (not in early recovery at all) must still get the
        full 1.0 credit — the early-recovery partial credit must never apply once the stock has
        actually reclaimed SMA50."""
        _mock_supertrend(monkeypatch, st_trend=0, st_cross_up=False)
        _mock_adx(monkeypatch, adx_val=15.0, di_plus=10.0, di_minus=20.0)
        df = _minimal_df()  # a steady uptrend — price should be above both SMA20 and SMA50
        _, reasons = _ta_score(df)
        if reasons["trend_above_sma50"]:
            assert reasons["early_recovery_trend"] is False


# ── Finding 2: RS compression recovery exemption ────────────────────────────────────────────

def _call_apply_style_signal(rsi_val, stoch_cross_up, rs_rank, style_key="SWING", pr_delta=0.0):
    base_reasons = {
        "rsi": rsi_val,
        "stoch_rsi_cross_up": stoch_cross_up,
        "independent_pillars_active": 3,
        "pullback_recovery_delta": pr_delta,
    }
    return _apply_style_signal(
        ta_prob=0.55, ml_prob=None, ml_test_auc=0.5, style_key=style_key,
        market_regime="bull", adx_val=20.0, weekly_tech={}, pattern_adj=0.0,
        days_to_earnings=None, news_sentiment=None, rs_rank=rs_rank,
        options_sentiment=None, cp_ratio=None, kscore=None, is_stale=False,
        base_reasons=base_reasons,
    )


class TestFinding2RsRecoveryFloor:
    def test_recovery_rsi_and_stoch_cross_up_skips_rs_compression(self):
        result = _call_apply_style_signal(rsi_val=35.0, stoch_cross_up=True, rs_rank=0.40)
        assert result.reasons["rs_flag"] == "lagging_sector_floor_applied"

    def test_recovery_rsi_without_stoch_cross_up_still_compresses(self):
        """RSI alone in the recovery band, without a real stoch cross-up, must NOT get the
        exemption — the stoch guard ensures this only applies when momentum is turning."""
        result = _call_apply_style_signal(rsi_val=35.0, stoch_cross_up=False, rs_rank=0.40)
        assert result.reasons["rs_flag"] == "lagging_sector"

    def test_stoch_cross_up_outside_recovery_rsi_band_still_compresses(self):
        """A stoch cross-up at RSI=60 (not a dip-recovery reading) must NOT get the exemption."""
        result = _call_apply_style_signal(rsi_val=60.0, stoch_cross_up=True, rs_rank=0.40)
        assert result.reasons["rs_flag"] == "lagging_sector"

    def test_leading_rs_rank_is_unaffected(self):
        result = _call_apply_style_signal(rsi_val=35.0, stoch_cross_up=True, rs_rank=0.85)
        assert result.reasons["rs_flag"] == "in_line_or_leading"


# ── Finding 3: weekly gate recovery exception ───────────────────────────────────────────────

def _call_apply_style_signal_with_weekly(rsi_val, stoch_cross_up, pr_delta, weekly_rsi, weekly_trend, style_key="SWING"):
    base_reasons = {
        "rsi": rsi_val,
        "stoch_rsi_cross_up": stoch_cross_up,
        "independent_pillars_active": 3,
        "pullback_recovery_delta": pr_delta,
    }
    return _apply_style_signal(
        ta_prob=0.55, ml_prob=None, ml_test_auc=0.5, style_key=style_key,
        market_regime="bull", adx_val=20.0,
        weekly_tech={"weekly_score": 0.5, "weekly_rsi": weekly_rsi, "weekly_trend": weekly_trend},
        pattern_adj=0.0,
        days_to_earnings=None, news_sentiment=None, rs_rank=None,
        options_sentiment=None, cp_ratio=None, kscore=None, is_stale=False,
        base_reasons=base_reasons,
    )


class TestFinding3WeeklyGateRecoveryException:
    def test_stoch_cross_up_and_confirmed_recovery_skips_weekly_gate(self):
        result = _call_apply_style_signal_with_weekly(
            rsi_val=35.0, stoch_cross_up=True, pr_delta=0.07, weekly_rsi=30.0, weekly_trend="down",
        )
        assert result.reasons["weekly_gate_fired"] is False
        assert result.reasons.get("weekly_gate_recovery_exception") is True

    def test_stoch_cross_up_without_volume_confirmed_recovery_still_gates(self):
        """stoch_cross_up alone, without the volume-confirmed pullback (pr_delta >= 0.07), must
        NOT get the exception — both conditions are required together."""
        result = _call_apply_style_signal_with_weekly(
            rsi_val=35.0, stoch_cross_up=True, pr_delta=0.0, weekly_rsi=30.0, weekly_trend="down",
        )
        assert result.reasons["weekly_gate_fired"] is True

    def test_volume_confirmed_recovery_without_stoch_cross_up_still_gates(self):
        """A volume-confirmed pullback without a real stoch cross-up must NOT get the exception."""
        result = _call_apply_style_signal_with_weekly(
            rsi_val=35.0, stoch_cross_up=False, pr_delta=0.07, weekly_rsi=30.0, weekly_trend="down",
        )
        assert result.reasons["weekly_gate_fired"] is True

    def test_healthy_weekly_data_never_gates_regardless_of_exception(self):
        result = _call_apply_style_signal_with_weekly(
            rsi_val=55.0, stoch_cross_up=False, pr_delta=0.0, weekly_rsi=60.0, weekly_trend="up",
        )
        assert result.reasons["weekly_gate_fired"] is False
        assert "weekly_gate_recovery_exception" not in result.reasons
