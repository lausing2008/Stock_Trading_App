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
