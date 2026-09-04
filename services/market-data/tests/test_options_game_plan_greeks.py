"""Behavioral tests for AUD-GREEKS: per-strike Greeks (delta/gamma/theta/vega/vanna/charm)
matched to the exact put/call strike compute_options_game_plan_snapshot() already selects, via
unusual_whales.get_greeks(symbol, expiry).

Isolates just this one piece (not the full yfinance options-chain fetch, covered elsewhere) by
patching compute_options_game_plan() to return a fixed put/call strike shape and
unusual_whales.get_greeks() to control the returned rows directly — same harness convention as
test_options_game_plan_expected_move.py.
"""
from unittest.mock import MagicMock, patch

from src.services import options_game_plan_snapshot as m


class _FakeStrikeGreeks:
    def __init__(self, strike, **kwargs):
        self.strike = strike
        for k, v in kwargs.items():
            setattr(self, k, v)
        for side in ("put", "call"):
            for greek in ("delta", "gamma", "theta", "vega", "vanna", "charm"):
                key = f"{side}_{greek}"
                if key not in kwargs:
                    setattr(self, key, None)


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


def _session():
    session = MagicMock()
    session.execute.return_value.scalars.return_value.first.return_value = None
    return session


def _run(put_strike=95.0, call_strike=105.0, put_exp="2026-10-01", call_exp="2026-10-01", greeks_side_effect=None):
    with patch("src.api.routes._goal_current_price", return_value=100.0), \
         patch("src.api.routes._nearest_expiry_in_dte_window", side_effect=[put_exp, call_exp]), \
         patch("src.api.routes._options_chain_rows", return_value=[]), \
         patch("src.api.routes.compute_options_game_plan", return_value={
             "protective_put": {"strike": put_strike} if put_strike is not None else {},
             "covered_call": {"strike": call_strike} if call_strike is not None else {},
         }), \
         patch("src.services.paper_trading_engine._build_game_plan_for_style", return_value={
             "stop": 94.0, "take_profit": 110.0,
         }), \
         patch("src.services.unusual_whales.get_iv_rank", return_value=None), \
         patch("src.services.unusual_whales.get_greeks", side_effect=greeks_side_effect) as mock_greeks, \
         patch.object(m, "log"), \
         patch("yfinance.Ticker", return_value=_fake_ticker(expiries=(put_exp, call_exp))):
        result = m.compute_options_game_plan_snapshot(_session(), stock_id=1, symbol="AAPL")
    return result, mock_greeks


def test_greeks_matched_to_the_exact_selected_strike():
    rows = [
        _FakeStrikeGreeks(90.0, put_delta=-0.3, call_delta=0.7),
        _FakeStrikeGreeks(95.0, put_delta=-0.45, put_gamma=0.02, put_theta=-0.04, put_vega=0.11, put_vanna=-0.01, put_charm=0.001, call_delta=0.55),
        _FakeStrikeGreeks(105.0, put_delta=-0.2, call_delta=0.4, call_gamma=0.015, call_theta=-0.03, call_vega=0.09, call_vanna=0.008, call_charm=-0.0005),
    ]
    result, _ = _run(greeks_side_effect=lambda sym, exp: rows)
    assert result is not None
    assert result.put_delta == -0.45
    assert result.put_gamma == 0.02
    assert result.put_theta == -0.04
    assert result.put_vega == 0.11
    assert result.put_vanna == -0.01
    assert result.put_charm == 0.001
    assert result.call_delta == 0.4
    assert result.call_gamma == 0.015
    assert result.call_theta == -0.03
    assert result.call_vega == 0.09
    assert result.call_vanna == 0.008
    assert result.call_charm == -0.0005


def test_same_expiry_for_put_and_call_only_fetches_greeks_once():
    """put_exp == call_exp is the common real case -- must not waste a second UW call for the
    same (symbol, expiry) pair."""
    rows = [_FakeStrikeGreeks(95.0, put_delta=-0.45), _FakeStrikeGreeks(105.0, call_delta=0.4)]
    result, mock_greeks = _run(put_exp="2026-10-01", call_exp="2026-10-01", greeks_side_effect=lambda sym, exp: rows)
    assert result is not None
    assert mock_greeks.call_count == 1


def test_different_expiries_for_put_and_call_fetch_greeks_twice():
    put_rows = [_FakeStrikeGreeks(95.0, put_delta=-0.45)]
    call_rows = [_FakeStrikeGreeks(105.0, call_delta=0.4)]
    def _side_effect(sym, exp):
        return put_rows if exp == "2026-10-01" else call_rows
    result, mock_greeks = _run(put_exp="2026-10-01", call_exp="2026-11-01", greeks_side_effect=_side_effect)
    assert result is not None
    assert mock_greeks.call_count == 2
    assert result.put_delta == -0.45
    assert result.call_delta == 0.4


def test_no_matching_strike_in_the_returned_rows_leaves_greeks_null():
    rows = [_FakeStrikeGreeks(999.0, put_delta=-0.99, call_delta=0.99)]
    result, _ = _run(greeks_side_effect=lambda sym, exp: rows)
    assert result is not None
    assert result.put_delta is None
    assert result.call_delta is None


def test_empty_greeks_list_leaves_all_greek_fields_null_never_crashes():
    result, _ = _run(greeks_side_effect=lambda sym, exp: [])
    assert result is not None
    assert result.put_delta is None
    assert result.call_delta is None
    assert result.put_strike == 95.0  # the rest of the snapshot is unaffected
    assert result.call_strike == 105.0


def test_get_greeks_exception_fails_open_leaving_the_rest_of_the_snapshot_intact():
    def _boom(sym, exp):
        raise RuntimeError("boom")
    result, _ = _run(greeks_side_effect=_boom)
    assert result is not None
    assert result.put_delta is None
    assert result.call_delta is None
    assert result.put_strike == 95.0
    assert result.call_strike == 105.0


def test_only_a_put_leg_selected_never_calls_get_greeks_for_the_call_side():
    rows = [_FakeStrikeGreeks(95.0, put_delta=-0.45)]
    result, mock_greeks = _run(put_strike=95.0, call_strike=None, greeks_side_effect=lambda sym, exp: rows)
    assert result is not None
    assert result.put_delta == -0.45
    assert result.call_delta is None
    assert mock_greeks.call_count == 1
