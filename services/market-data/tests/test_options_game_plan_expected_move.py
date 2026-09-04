"""Behavioral tests for AUD-DECIDE4-EXPECTEDMOVE's expected_move_pct/expected_move_dte
computation inside compute_options_game_plan_snapshot() (options_game_plan_snapshot.py) —
the real, per-symbol Unusual Whales IV -> expected-move formula, including the defensive
fraction-vs-percent unit normalization this module's own docstring flags as unverified against
live UW data.

Isolates just this one piece (not the full yfinance options-chain fetch, which is unrelated
plumbing already covered elsewhere) by patching compute_options_game_plan() to return a fixed
put/call shape and unusual_whales.get_iv_rank() to control the IV input directly.
"""
from math import sqrt
from unittest.mock import MagicMock, patch

import pytest

from src.services import options_game_plan_snapshot as m


class _FakeIVRank:
    def __init__(self, volatility, iv_rank_1y=None):
        self.volatility = volatility
        self.iv_rank_1y = iv_rank_1y


def _fake_ticker(current_price=100.0, expiries=("2026-10-01",)):
    t = MagicMock()
    t.options = list(expiries)
    hist = MagicMock()
    hist.empty = False
    hist.__getitem__.return_value.iloc.__getitem__.return_value = current_price
    t.history.return_value = hist
    chain = MagicMock()
    chain.puts = []
    chain.calls = []
    t.option_chain.return_value = chain
    return t


@pytest.fixture(autouse=True)
def _stub_chain_and_plan():
    with patch("src.api.routes._goal_current_price", return_value=100.0), \
         patch("src.api.routes._nearest_expiry_in_dte_window", return_value="2026-10-01"), \
         patch("src.api.routes._options_chain_rows", return_value=[]), \
         patch("src.api.routes.compute_options_game_plan", return_value={
             "protective_put": {"strike": 95.0}, "covered_call": {"strike": 105.0},
         }), \
         patch("src.services.paper_trading_engine._build_game_plan_for_style", return_value={
             "stop": 94.0, "take_profit": 110.0,
         }), \
         patch.object(m, "log"):
        yield


def test_expected_move_computed_from_a_real_fractional_iv():
    with patch("yfinance.Ticker", return_value=_fake_ticker()), \
         patch("src.services.unusual_whales.get_iv_rank", return_value=_FakeIVRank(0.35)), \
         patch("sqlalchemy.select"), \
         patch.object(m, "compute_options_game_plan_snapshot", wraps=m.compute_options_game_plan_snapshot):
        session = MagicMock()
        session.execute.return_value.scalars.return_value.first.return_value = None
        result = m.compute_options_game_plan_snapshot(session, stock_id=1, symbol="AAPL")
    assert result is not None
    expected = round(0.35 * sqrt(30 / 365.0) * 100, 4)
    assert result.expected_move_pct == expected
    assert result.expected_move_dte == 30


def test_expected_move_normalizes_a_percent_style_iv_value_above_10():
    """A `volatility` value > 10.0 is treated as already a percent (e.g. 35.0 meaning 35%) and
    divided by 100 first — the defensive unit-ambiguity guard this module's docstring flags."""
    with patch("yfinance.Ticker", return_value=_fake_ticker()), \
         patch("src.services.unusual_whales.get_iv_rank", return_value=_FakeIVRank(35.0)):
        session = MagicMock()
        session.execute.return_value.scalars.return_value.first.return_value = None
        result = m.compute_options_game_plan_snapshot(session, stock_id=1, symbol="AAPL")
    assert result is not None
    expected = round(0.35 * sqrt(30 / 365.0) * 100, 4)
    assert result.expected_move_pct == expected


def test_expected_move_is_none_when_iv_rank_unavailable():
    with patch("yfinance.Ticker", return_value=_fake_ticker()), \
         patch("src.services.unusual_whales.get_iv_rank", return_value=None):
        session = MagicMock()
        session.execute.return_value.scalars.return_value.first.return_value = None
        result = m.compute_options_game_plan_snapshot(session, stock_id=1, symbol="AAPL")
    assert result is not None
    assert result.expected_move_pct is None
    assert result.expected_move_dte is None


def test_expected_move_is_none_when_volatility_is_none_or_zero():
    for vol in (None, 0.0):
        with patch("yfinance.Ticker", return_value=_fake_ticker()), \
             patch("src.services.unusual_whales.get_iv_rank", return_value=_FakeIVRank(vol)):
            session = MagicMock()
            session.execute.return_value.scalars.return_value.first.return_value = None
            result = m.compute_options_game_plan_snapshot(session, stock_id=1, symbol="AAPL")
        assert result is not None
        assert result.expected_move_pct is None


def test_iv_rank_failure_fails_open_leaving_the_rest_of_the_snapshot_intact():
    """A get_iv_rank() exception must only cost the expected_move fields, never the whole
    snapshot (which still has real put/call legs from the options chain)."""
    with patch("yfinance.Ticker", return_value=_fake_ticker()), \
         patch("src.services.unusual_whales.get_iv_rank", side_effect=RuntimeError("boom")):
        session = MagicMock()
        session.execute.return_value.scalars.return_value.first.return_value = None
        result = m.compute_options_game_plan_snapshot(session, stock_id=1, symbol="AAPL")
    assert result is not None
    assert result.expected_move_pct is None
    assert result.iv_rank_1y is None
    assert result.put_strike == 95.0
    assert result.call_strike == 105.0


# ── iv_rank_1y propagation (wired alongside expected_move_pct, same UW fetch, no extra call) ─

def test_iv_rank_1y_is_captured_from_the_same_iv_rank_fetch():
    with patch("yfinance.Ticker", return_value=_fake_ticker()), \
         patch("src.services.unusual_whales.get_iv_rank", return_value=_FakeIVRank(0.35, iv_rank_1y=72.0)):
        session = MagicMock()
        session.execute.return_value.scalars.return_value.first.return_value = None
        result = m.compute_options_game_plan_snapshot(session, stock_id=1, symbol="AAPL")
    assert result is not None
    assert result.iv_rank_1y == 72.0


def test_iv_rank_1y_captured_even_when_volatility_itself_is_none():
    """iv_rank_1y is an independent field from the same fetch -- a symbol with a real IV Rank
    reading but a missing/zero volatility field should still get its IV Rank captured, even
    though expected_move_pct itself stays None in that case."""
    with patch("yfinance.Ticker", return_value=_fake_ticker()), \
         patch("src.services.unusual_whales.get_iv_rank", return_value=_FakeIVRank(None, iv_rank_1y=15.0)):
        session = MagicMock()
        session.execute.return_value.scalars.return_value.first.return_value = None
        result = m.compute_options_game_plan_snapshot(session, stock_id=1, symbol="AAPL")
    assert result is not None
    assert result.expected_move_pct is None
    assert result.iv_rank_1y == 15.0


def test_iv_rank_1y_is_none_when_iv_rank_unavailable():
    with patch("yfinance.Ticker", return_value=_fake_ticker()), \
         patch("src.services.unusual_whales.get_iv_rank", return_value=None):
        session = MagicMock()
        session.execute.return_value.scalars.return_value.first.return_value = None
        result = m.compute_options_game_plan_snapshot(session, stock_id=1, symbol="AAPL")
    assert result is not None
    assert result.iv_rank_1y is None
