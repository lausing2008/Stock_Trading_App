"""Regression tests for the missing `fused > 0.5` guard on two compression gates in
_apply_style_signal(), found in the 2026-09-06 deep audit (priority item #10).

Every OTHER compression gate in signals.py requires `fused > 0.5` — negative evidence must only
ever compress a BUY-leaning signal toward neutral, never rescue a SELL by also compressing it
toward neutral (that's bearish confirmation, not something to soften). hv_fired, breadth,
sector_headwind, hot_news, hsi_bear_gate, hk_southbound, and hk_liquidity all correctly require
this (see test_hot_news_gate.py for hot_news's own equivalent test suite, whose pattern this
file mirrors exactly).

Bug (a) — news-sentiment compression: had no fused>0.5 guard at all. A clean SELL
(ta_prob=0.30) with strongly negative news got compressed UP toward 0.5 — converted toward WAIT
by the exact evidence that should have reinforced the SELL.

Bug (b) — RS (relative strength) compression: same missing guard, with a compounding wrinkle —
its two escape hatches (rs_absolute_floor: stock up >5% in 20 days; rs_recovery_floor: RSI
28-45 + stoch_rsi_cross_up) are bullish-only by construction. Without the guard they fired
BACKWARDS on the SELL side: a stock up 5%+ in 20 days is exactly the one whose SELL you least
want to weaken, yet the floor protected it from compression while a genuinely lagging laggard
(the real SELL evidence) got compressed toward neutral instead.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.generators.signals import _apply_style_signal  # noqa: E402


def _call(news_sentiment=None, rs_rank=None, ta_prob=0.70, style_key="SWING", base_reasons_extra=None):
    base_reasons = dict(base_reasons_extra or {})
    return _apply_style_signal(
        ta_prob=ta_prob, ml_prob=None, ml_test_auc=0.5, style_key=style_key,
        market_regime="bull", adx_val=20.0, weekly_tech={}, pattern_adj=0.0,
        days_to_earnings=None, news_sentiment=news_sentiment, rs_rank=rs_rank,
        options_sentiment=None, cp_ratio=None, kscore=None, is_stale=False,
        base_reasons=base_reasons,
    )


# ── News-sentiment compression ────────────────────────────────────────────────────────────

class TestNewsSentimentFusedGuard:
    def test_strongly_negative_news_compresses_a_buy_leaning_signal(self):
        """This must remain unchanged — the bug was never about the sign check (only negative
        sentiment compresses at all), only about which SIDE of fused it was allowed to touch."""
        no_news = _call(news_sentiment=None, ta_prob=0.70)
        with_news = _call(news_sentiment=10.0, ta_prob=0.70)
        assert with_news.reasons["news_sentiment_flag"] == "strongly_negative"
        assert with_news.bullish_probability < no_news.bullish_probability
        assert 0.5 < with_news.bullish_probability < 0.70

    def test_strongly_negative_news_is_now_a_no_op_when_signal_already_sell_leaning(self):
        """THE CORE BUG: fused <= 0.5 (a clean SELL) must not be touched by news compression —
        before the fix, strongly negative news compressed the SELL UP toward neutral, i.e.
        converted it toward WAIT by evidence that should have reinforced the SELL."""
        no_news = _call(news_sentiment=None, ta_prob=0.30)
        with_news = _call(news_sentiment=10.0, ta_prob=0.30)
        assert with_news.bullish_probability == pytest.approx(no_news.bullish_probability, abs=1e-9)

    def test_sell_leaning_signal_still_gets_a_news_sentiment_flag_for_observability(self):
        """The diagnostic reasons field must still populate even when the gate is a no-op, so
        the SELL-side sentiment is still visible for calibration/debugging — only the
        COMPRESSION must not apply, not the reporting."""
        with_news = _call(news_sentiment=10.0, ta_prob=0.30)
        assert with_news.reasons["news_sentiment_flag"] == "strongly_negative"

    def test_negative_news_compresses_a_buy_leaning_signal(self):
        no_news = _call(news_sentiment=None, ta_prob=0.70)
        with_news = _call(news_sentiment=30.0, ta_prob=0.70)
        assert with_news.reasons["news_sentiment_flag"] == "negative"
        assert with_news.bullish_probability < no_news.bullish_probability

    def test_negative_news_is_a_no_op_when_sell_leaning(self):
        no_news = _call(news_sentiment=None, ta_prob=0.30)
        with_news = _call(news_sentiment=30.0, ta_prob=0.30)
        assert with_news.bullish_probability == pytest.approx(no_news.bullish_probability, abs=1e-9)

    def test_neutral_or_positive_news_is_always_a_no_op(self):
        no_news = _call(news_sentiment=None, ta_prob=0.70)
        with_news = _call(news_sentiment=60.0, ta_prob=0.70)
        assert with_news.reasons["news_sentiment_flag"] == "neutral_or_positive"
        assert with_news.bullish_probability == pytest.approx(no_news.bullish_probability, abs=1e-9)


# ── RS (relative strength) compression ───────────────────────────────────────────────────────

class TestRSCompressionFusedGuard:
    def test_lagging_rs_compresses_a_buy_leaning_signal(self):
        no_rs = _call(rs_rank=None, ta_prob=0.70)
        with_rs = _call(rs_rank=0.30, ta_prob=0.70)
        assert with_rs.reasons["rs_flag"] == "lagging_sector"
        assert with_rs.bullish_probability < no_rs.bullish_probability

    def test_lagging_rs_is_now_a_no_op_when_signal_already_sell_leaning(self):
        """THE CORE BUG: fused <= 0.5 (a clean SELL) must not be touched by RS compression —
        before the fix, a lagging stock's SELL got compressed UP toward neutral by its own
        confirming evidence (lagging RS IS bearish confirmation on a SELL candidate)."""
        no_rs = _call(rs_rank=None, ta_prob=0.30)
        with_rs = _call(rs_rank=0.30, ta_prob=0.30)
        assert with_rs.bullish_probability == pytest.approx(no_rs.bullish_probability, abs=1e-9)

    def test_sell_leaning_lagging_stock_is_still_correctly_labeled(self):
        """The diagnostic label must still say 'lagging_sector' on the SELL side even though
        the compression itself is now a no-op there — a future reader of `reasons` must still
        see the true RS state, not a mislabel."""
        with_rs = _call(rs_rank=0.30, ta_prob=0.30)
        assert with_rs.reasons["rs_flag"] == "lagging_sector"

    def test_in_line_rs_is_a_no_op_regardless_of_direction(self):
        no_rs = _call(rs_rank=None, ta_prob=0.70)
        with_rs = _call(rs_rank=0.85, ta_prob=0.70)
        assert with_rs.reasons["rs_flag"] == "in_line_or_leading"
        assert with_rs.bullish_probability == pytest.approx(no_rs.bullish_probability, abs=1e-9)

    def test_absolute_return_floor_still_protects_a_buy_leaning_signal(self):
        """rs_absolute_floor (stock up >5% in 20 days) must still exempt a BUY-leaning lagging
        stock from compression — this floor's original, correct purpose is unchanged."""
        floor_reasons = {"stock_20d_return_pct": 8.0}
        with_floor = _call(rs_rank=0.30, ta_prob=0.70, base_reasons_extra=floor_reasons)
        no_rs = _call(rs_rank=None, ta_prob=0.70, base_reasons_extra=floor_reasons)
        assert with_floor.reasons["rs_flag"] == "lagging_sector_floor_applied"
        assert with_floor.bullish_probability == pytest.approx(no_rs.bullish_probability, abs=1e-9)

    def test_absolute_return_floor_no_longer_protects_a_sell_leaning_signal(self):
        """THE COMPOUNDING BUG: on a SELL candidate, rs_absolute_floor's "stock is up >5% in 20
        days" is exactly the stock whose SELL you least want to weaken — the floor must NOT
        exempt it there. Since the whole gate is now fused>0.5-gated, the floor's branch never
        fires on the SELL side, and the stock falls through to the plain 'lagging_sector' label
        with the (correctly) uncompressed SELL — not the floor-protected label."""
        floor_reasons = {"stock_20d_return_pct": 8.0}
        with_floor = _call(rs_rank=0.30, ta_prob=0.30, base_reasons_extra=floor_reasons)
        no_floor = _call(rs_rank=0.30, ta_prob=0.30)
        assert with_floor.reasons["rs_flag"] == "lagging_sector"
        assert with_floor.reasons["rs_flag"] != "lagging_sector_floor_applied"
        assert with_floor.bullish_probability == pytest.approx(no_floor.bullish_probability, abs=1e-9)
