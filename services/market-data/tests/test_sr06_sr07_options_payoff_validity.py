"""SR-06 and SR-07 — a recommended structure must be economically valid and actually describable.

SR-06. Spread construction checked only `debit > 0`. A debit vertical's maximum payoff at
expiry is the strike WIDTH LESS THE DEBIT, so a debit at or above the width is a structure
whose best case is a loss. The review's witness: underlying 100, call strikes 100/105, leg mids
12 and 2 -> debit 10 against width 5. The module computed max profit **-$500** and still
selected `bull_call_spread` as the primary recommendation for a bullish, no-shares, normal-IV
request. The arithmetic was correct throughout; nothing asked whether the result made sense.
`_mid` also averaged crossed quotes and fell back to an undated last price, so two legs of one
structure could be priced from different moments.

SR-07. The caller selects protective puts at 25-60 DTE and calls at 14-45 DTE, so a collar's
legs routinely expire on different dates — and the matrix reported one fixed max profit, max
loss and breakeven anyway. Witness: stock 100, put 95 expiring Nov 20, call 110 expiring Oct 16,
reported max profit $900/100 shares. If the short call expires worthless at 100 and the stock
reaches 120 by the put's expiry, the position earns $1,900: there is no longer a call capping
that upside. A single common-expiry diagram cannot describe that position.

This module is PURE — no DB, no network, no clock beyond an injected `today` — so these tests
execute the real functions against synthetic chains rather than asserting on source text.

NOTE (O01, 2026-10-08): the hand-built fixtures below now declare `directional_exposure`, as
every real structure does. Without it the separate exposure-compatibility constraint filters
them out — "a structure that does not declare which way it leans cannot be shown to be
compatible" — and these tests would pass or fail for a reason that has nothing to do with the
payoff backstop they exist to check.
"""
import importlib.util
import pathlib
from datetime import date

import pytest

_MOD_PATH = (pathlib.Path(__file__).resolve().parents[1]
             / "src" / "services" / "options_strategies.py")
_spec = importlib.util.spec_from_file_location("options_strategies_under_test", _MOD_PATH)
osx = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(osx)

_TODAY = date(2026, 10, 2)
_CALL_EXP, _PUT_EXP = "2026-10-16", "2026-11-20"


def _c(strike, bid, ask, right="call", last=None):
    return {"strike": strike, "bid": bid, "ask": ask, "right": right,
            "last_price": last, "iv": 0.4, "oi": 500}


def _matrix(**over):
    kw = dict(
        current_price=100.0, stop_loss=95.0, take_profit=105.0, signal="BUY",
        put_rows=[_c(90, 1.0, 1.2, "put"), _c(95, 2.0, 2.2, "put")],
        put_expiry=_CALL_EXP,
        call_rows=[_c(100, 3.0, 3.2), _c(105, 1.0, 1.2)],
        call_expiry=_CALL_EXP, shares=None, iv_rank=50.0, today=_TODAY,
    )
    kw.update(over)
    return osx.build_strategy_matrix(**kw)


# ── SR-06: the witness ──────────────────────────────────────────────────────────────────

def test_a_debit_above_the_strike_width_is_not_offered():
    """THE REVIEW'S WITNESS, run against the real module."""
    m = _matrix(call_rows=[_c(100, 11.9, 12.1), _c(105, 1.9, 2.1)])
    assert "bull_call_spread" not in m["combos"], \
        "a debit of 10 against a width of 5 has a maximum payoff of -$500"


def test_the_witness_is_not_recommended_either():
    m = _matrix(call_rows=[_c(100, 11.9, 12.1), _c(105, 1.9, 2.1)])
    assert m["recommendation"].get("primary") != "bull_call_spread"


def test_a_valid_spread_is_still_offered_and_still_recommended():
    """The control. The fix must not refuse ordinary structures."""
    m = _matrix()
    assert "bull_call_spread" in m["combos"]
    assert m["combos"]["bull_call_spread"]["max_profit_per_contract"] > 0
    assert m["recommendation"]["primary"] == "bull_call_spread"


def test_a_debit_exactly_equal_to_the_width_is_refused():
    """Max payoff is exactly zero: all of the risk, none of the reward."""
    m = _matrix(call_rows=[_c(100, 5.9, 6.1), _c(105, 0.9, 1.1)])
    assert "bull_call_spread" not in m["combos"]


def test_a_debit_a_cent_below_the_width_is_refused_as_costing_more_than_it_can_win():
    m = _matrix(call_rows=[_c(100, 5.99, 6.01), _c(105, 1.0, 1.02)])
    got = m["combos"].get("bull_call_spread")
    assert got is None, "an edge too small to survive costs is not a plan"


def test_the_bear_put_spread_gets_the_same_test():
    m = _matrix(put_rows=[_c(90, 0.9, 1.1, "put"), _c(95, 11.9, 12.1, "put")],
                stop_loss=95.0, shares=10.0)
    assert "bear_put_spread" not in m["combos"]


# ── SR-06: quote validity ───────────────────────────────────────────────────────────────

def test_a_crossed_quote_is_not_a_price():
    assert osx._mid({"bid": 12.0, "ask": 2.0}) is None


def test_an_uncrossed_quote_still_prices():
    assert osx._mid({"bid": 2.0, "ask": 3.0}) == pytest.approx(2.5)


def test_a_locked_market_is_allowed():
    """bid == ask is tight, not crossed."""
    assert osx._mid({"bid": 2.5, "ask": 2.5}) == pytest.approx(2.5)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1.0, 0.0, None, "x"])
def test_non_finite_or_non_positive_prices_never_produce_a_mid(bad):
    assert osx._mid({"bid": bad, "ask": bad}) is None


def test_a_stale_last_price_is_labelled_rather_than_passed_off_as_a_quote():
    leg = osx._leg({"strike": 100.0, "bid": 0, "ask": 0, "last_price": 4.0, "right": "call"},
                   "buy", _CALL_EXP, 14)
    assert leg["price_source"] == "last_trade"
    quoted = osx._leg({"strike": 100.0, "bid": 3.9, "ask": 4.1, "right": "call"},
                      "buy", _CALL_EXP, 14)
    assert quoted["price_source"] == "quote_mid"


def test_a_leg_without_an_expiry_or_strike_is_not_a_leg():
    assert osx._leg({"strike": 100.0, "bid": 1, "ask": 2, "right": "call"}, "buy", None, 14) is None
    assert osx._leg({"strike": None, "bid": 1, "ask": 2, "right": "call"}, "buy", _CALL_EXP, 14) is None


# ── SR-07: the collar's expiries ────────────────────────────────────────────────────────

def test_a_staggered_collar_is_not_offered_with_single_expiry_bounds():
    """THE REVIEW'S WITNESS: put expiring Nov 20, call expiring Oct 16."""
    m = _matrix(shares=100.0, put_expiry=_PUT_EXP, call_expiry=_CALL_EXP)
    assert "collar" not in m["combos"]
    assert "collar" in m["unavailable"]
    reason = m["unavailable"]["collar"]
    assert reason["put_expiry"] == _PUT_EXP and reason["call_expiry"] == _CALL_EXP
    assert "expire together" in reason["reason"]


def test_a_matched_expiry_collar_is_still_offered():
    m = _matrix(shares=100.0, put_expiry=_CALL_EXP, call_expiry=_CALL_EXP)
    assert "collar" in m["combos"]
    assert m["combos"]["collar"]["expiry"] == _CALL_EXP


def test_the_staggered_collar_is_reported_not_silently_dropped():
    """Omitting it without a reason leaves the reader to conclude the chain had nothing."""
    m = _matrix(shares=100.0, put_expiry=_PUT_EXP, call_expiry=_CALL_EXP)
    assert m["unavailable"]["collar"]["name"] == "Collar"
    assert m["recommendation"]["primary"] != "collar"


def test_a_vertical_also_requires_matched_expiries():
    """Same defect, same test: `width - debit` is the payoff of a structure nobody holds if
    the legs expire on different days."""
    assert osx._vertical_is_viable(
        1.0, 5.0, {"expiry": _CALL_EXP}, {"expiry": _CALL_EXP}) is True
    assert osx._vertical_is_viable(
        1.0, 5.0, {"expiry": _CALL_EXP}, {"expiry": _PUT_EXP}) is False


# ── The recommendation backstop ─────────────────────────────────────────────────────────

def test_no_structure_with_a_non_positive_maximum_payoff_can_be_primary():
    rec = osx._recommend(
        singles={}, combos={"bull_call_spread": {"directional_exposure": "bullish", "name": "Bull Call Spread",
                                                 "max_profit_per_contract": -500.0}},
        signal="BUY", iv_rank=50.0, holds_shares=False)
    assert rec["primary"] is None
    assert "bull_call_spread" in rec["rejected_unsound"]


def test_the_backstop_reports_what_it_rejected():
    rec = osx._recommend(
        singles={"long_call": {"directional_exposure": "bullish", "name": "Long Call", "max_profit_per_contract": None}},
        combos={"bull_call_spread": {"directional_exposure": "bullish", "name": "Bull Call Spread",
                                     "max_profit_per_contract": -500.0}},
        signal="BUY", iv_rank=10.0, holds_shares=False)
    assert rec["primary"] == "long_call", "an uncapped payoff is not an unsound one"
    assert rec["rejected_unsound"] == ["bull_call_spread"]


def test_no_valid_plan_is_a_valid_answer():
    rec = osx._recommend(singles={}, combos={}, signal="BUY", iv_rank=50.0, holds_shares=False)
    assert rec["primary"] is None
