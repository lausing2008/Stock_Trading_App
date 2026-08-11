"""Tests for T258-NEWS-INTELLIGENCE's hot-news gate in _apply_style_signal().

A material negative headline (ingested by the new news-intelligence service, port 8011) should
compress an in-progress BUY signal toward neutral — the same direction-aware compression
pattern already used by sr_flag/rs_flag/sector_headwind elsewhere in this file. Positive/neutral
material news, or any news when the signal is already SELL/HOLD-leaning (fused <= 0.5), must be
a pure no-op on the fused probability (logged into reasons only) — this is a suppression-only
gate, not a new bullish signal source, per the design rationale in signals.py's own comment.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.generators.signals import _apply_style_signal  # noqa: E402


def _call(hot_news, ta_prob=0.70, style_key="SWING"):
    base_reasons = {"hot_news": hot_news}
    return _apply_style_signal(
        ta_prob=ta_prob, ml_prob=None, ml_test_auc=0.5, style_key=style_key,
        market_regime="bull", adx_val=20.0, weekly_tech={}, pattern_adj=0.0,
        days_to_earnings=None, news_sentiment=None, rs_rank=None,
        options_sentiment=None, cp_ratio=None, kscore=None, is_stale=False,
        base_reasons=base_reasons,
    )


class TestHotNewsGate:
    def test_no_hot_news_is_a_no_op(self):
        r = _call(hot_news=None)
        assert r.reasons["hot_news_flag"] == "none"

    def test_material_negative_news_compresses_a_buy_leaning_signal(self):
        no_news = _call(hot_news=None, ta_prob=0.70)
        with_news = _call(hot_news={"headline": "X issues profit warning", "sentiment_label": "negative"}, ta_prob=0.70)
        assert with_news.reasons["hot_news_flag"] == "material_negative"
        assert with_news.bullish_probability < no_news.bullish_probability

    def test_compression_pulls_toward_neutral_not_below_it(self):
        """The 0.70x compression must only narrow the distance from 0.5, never overshoot past
        neutral into SELL territory for a single material headline (a suppression, not a
        reversal)."""
        r = _call(hot_news={"headline": "bad news", "sentiment_label": "negative"}, ta_prob=0.70)
        assert 0.5 < r.bullish_probability < 0.70

    def test_material_positive_news_does_not_boost_fused(self):
        no_news = _call(hot_news=None, ta_prob=0.70)
        with_news = _call(hot_news={"headline": "X wins major contract", "sentiment_label": "positive"}, ta_prob=0.70)
        assert with_news.reasons["hot_news_flag"] == "material_other"
        assert with_news.bullish_probability == pytest.approx(no_news.bullish_probability, abs=1e-9)

    def test_material_neutral_news_does_not_boost_fused(self):
        no_news = _call(hot_news=None, ta_prob=0.70)
        with_news = _call(hot_news={"headline": "X announces routine update", "sentiment_label": "neutral"}, ta_prob=0.70)
        assert with_news.bullish_probability == pytest.approx(no_news.bullish_probability, abs=1e-9)

    def test_negative_news_is_a_no_op_when_signal_already_sell_leaning(self):
        """fused <= 0.5 (SELL/HOLD direction) must not be touched — the gate only ever
        compresses a BUY-leaning fused probability, matching sector_headwind's own
        direction-aware convention (only applies when fused > 0.5)."""
        no_news = _call(hot_news=None, ta_prob=0.30)
        with_news = _call(hot_news={"headline": "bad news", "sentiment_label": "negative"}, ta_prob=0.30)
        assert with_news.bullish_probability == pytest.approx(no_news.bullish_probability, abs=1e-9)
        assert with_news.reasons["hot_news_flag"] == "material_other"

    def test_missing_sentiment_label_treated_as_material_other(self):
        r = _call(hot_news={"headline": "no sentiment field"}, ta_prob=0.70)
        assert r.reasons["hot_news_flag"] == "material_other"


class TestHotNewsAgeDecayAndHorizonScoping:
    """AUD264-HOTNEWS-FLAG-STALE-NO-CLEAR-PATH: the compression now decays with the flag's
    real age (a `ts` field news-intelligence's storage.py now stamps) instead of applying a
    flat 30% for the full 2h TTL window regardless of whether the headline is 2 minutes or
    119 minutes old, and LONG-horizon signals are now exempt entirely, matching
    sector_headwind's own established style-exemption convention."""

    @staticmethod
    def _iso_hours_ago(hours: float) -> str:
        from datetime import datetime, timedelta, timezone
        return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

    def test_a_fresh_flag_within_the_first_hour_gets_the_full_30_pct_compression(self):
        no_news = _call(hot_news=None, ta_prob=0.70)
        with_news = _call(
            hot_news={"headline": "bad news", "sentiment_label": "negative",
                      "ts": self._iso_hours_ago(0.1)},
            ta_prob=0.70,
        )
        # Same real-value comparison the pre-existing sibling test
        # (test_compression_pulls_toward_neutral_not_below_it) already uses — the exact
        # intermediate `fused` value the compression is applied to is not independently
        # predictable from outside _apply_style_signal()'s own pipeline (other gates already
        # move `fused` before this block runs), so this asserts the real, observable
        # DIRECTION/MAGNITUDE property instead of a hand-computed exact float.
        assert with_news.bullish_probability < no_news.bullish_probability
        assert with_news.reasons["hot_news_flag"] == "material_negative"

    def test_a_flag_in_its_second_hour_gets_a_weaker_pull_than_a_fresh_one(self):
        first_hour = _call(
            hot_news={"headline": "bad news", "sentiment_label": "negative",
                      "ts": self._iso_hours_ago(0.1)},
            ta_prob=0.70,
        )
        second_hour = _call(
            hot_news={"headline": "bad news", "sentiment_label": "negative",
                      "ts": self._iso_hours_ago(1.5)},
            ta_prob=0.70,
        )
        # Both still compress (neither reaches the no-news baseline), but the second-hour
        # flag's pull toward neutral must be measurably weaker (a higher bullish_probability)
        # than the first-hour flag's — the real, observable effect of the decay.
        assert second_hour.bullish_probability > first_hour.bullish_probability

    def test_comfortably_below_the_1_hour_boundary_still_uses_the_stronger_compression(self):
        """The boundary check is a strict > 1.0 — a flag comfortably under an hour old must
        produce the SAME result as a fresh flag, not the weaker second-hour compression. Uses
        0.9h rather than exactly 1.0h to avoid a real, floating-point-timing boundary flake:
        the few microseconds of test-execution delay between constructing the ts string and
        the function computing age against "now" could otherwise push an exactly-1.0h fixture
        just past the > 1.0 threshold, matching a class of flakiness already documented
        elsewhere in this codebase's own test history."""
        fresh = _call(
            hot_news={"headline": "bad news", "sentiment_label": "negative",
                      "ts": self._iso_hours_ago(0.1)},
            ta_prob=0.70,
        )
        near_boundary = _call(
            hot_news={"headline": "bad news", "sentiment_label": "negative",
                      "ts": self._iso_hours_ago(0.9)},
            ta_prob=0.70,
        )
        assert near_boundary.bullish_probability == pytest.approx(fresh.bullish_probability, abs=1e-9)

    def test_a_missing_ts_field_falls_back_to_the_original_flat_30_pct_compression(self):
        """A pre-fix flag (still live within its own 2h TTL, written before news-intelligence
        started stamping ts) must not crash or silently skip the gate — it must produce the
        SAME result as a known-fresh (first-hour) flag, i.e. the original, unconditional
        compression strength, not the weaker second-hour one."""
        fresh = _call(
            hot_news={"headline": "bad news", "sentiment_label": "negative",
                      "ts": self._iso_hours_ago(0.1)},
            ta_prob=0.70,
        )
        no_ts = _call(
            hot_news={"headline": "bad news", "sentiment_label": "negative"},  # no ts key at all
            ta_prob=0.70,
        )
        assert no_ts.bullish_probability == pytest.approx(fresh.bullish_probability, abs=1e-9)

    def test_an_unparseable_ts_field_also_falls_back_gracefully(self):
        fresh = _call(
            hot_news={"headline": "bad news", "sentiment_label": "negative",
                      "ts": self._iso_hours_ago(0.1)},
            ta_prob=0.70,
        )
        bad_ts = _call(
            hot_news={"headline": "bad news", "sentiment_label": "negative", "ts": "not-a-real-timestamp"},
            ta_prob=0.70,
        )
        assert bad_ts.bullish_probability == pytest.approx(fresh.bullish_probability, abs=1e-9)

    def test_long_horizon_signals_are_completely_exempt_from_the_gate(self):
        no_news = _call(hot_news=None, ta_prob=0.70, style_key="LONG")
        with_news = _call(
            hot_news={"headline": "bad news", "sentiment_label": "negative", "ts": self._iso_hours_ago(0.1)},
            ta_prob=0.70, style_key="LONG",
        )
        assert with_news.bullish_probability == pytest.approx(no_news.bullish_probability, abs=1e-9)
        assert with_news.reasons["hot_news_flag"] == "material_other"

    def test_short_and_swing_and_growth_are_all_still_covered(self):
        """Regression guard: only LONG is exempt — confirm the other 3 real horizons are
        untouched by the exemption."""
        for style in ("SHORT", "SWING", "GROWTH"):
            no_news = _call(hot_news=None, ta_prob=0.70, style_key=style)
            with_news = _call(
                hot_news={"headline": "bad news", "sentiment_label": "negative", "ts": self._iso_hours_ago(0.1)},
                ta_prob=0.70, style_key=style,
            )
            assert with_news.bullish_probability < no_news.bullish_probability, f"{style} was not compressed"
            assert with_news.reasons["hot_news_flag"] == "material_negative"
