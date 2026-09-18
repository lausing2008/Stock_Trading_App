"""T402-OPTIONS-STRATEGY-MATRIX — behavioural tests for all four legs and the combinations.

build_strategy_matrix() is PURE, so unlike the DB-facing options code these are real
behavioural tests against a synthetic chain, not source-text checks. The payoff arithmetic is
the whole product here: a wrong max-loss figure on a hedge is worse than no figure at all,
because it is believed.
"""
from datetime import date

import pytest

from src.services.options_strategies import build_strategy_matrix, _iv_regime

TODAY = date(2026, 9, 18)
PUT_EXP, CALL_EXP = "2026-10-30", "2026-10-16"
SPOT = 110.00


def _chain(strikes, bid, ask):
    """A synthetic chain with an explicit bid/ask per strike, so mid is exact and the tests
    assert real arithmetic rather than a fixture's rounding."""
    return [{"strike": k, "bid": bid(k), "ask": ask(k), "last_price": 0.0, "iv": 0.35, "oi": 500}
            for k in strikes]


def _matrix(**over):
    calls = _chain([100, 105, 110, 115, 120], lambda k: max(0.5, 12 - (k - 100) * 0.9),
                   lambda k: max(0.7, 12.4 - (k - 100) * 0.9))
    puts = _chain([95, 100, 103, 107, 110], lambda k: max(0.5, 1.0 + (k - 95) * 0.8),
                  lambda k: max(0.7, 1.4 + (k - 95) * 0.8))
    kw = dict(current_price=SPOT, stop_loss=107.0, take_profit=115.0, signal="BUY",
              put_rows=puts, put_expiry=PUT_EXP, call_rows=calls, call_expiry=CALL_EXP,
              shares=100, iv_rank=50.0, today=TODAY)
    kw.update(over)
    return build_strategy_matrix(**kw)


# ── all four legs are present ───────────────────────────────────────────────────────────

def test_all_four_single_legs_are_built():
    """The page previously showed only BUY-put and SELL-call. The grid has four corners."""
    s = _matrix()["singles"]
    assert set(s) == {"long_call", "covered_call", "protective_put", "cash_secured_put"}
    assert s["long_call"]["legs"][0]["action"] == "buy"
    assert s["long_call"]["legs"][0]["right"] == "call"
    assert s["cash_secured_put"]["legs"][0]["action"] == "sell"
    assert s["cash_secured_put"]["legs"][0]["right"] == "put"


def test_combos_are_built_from_the_same_chains():
    c = _matrix()["combos"]
    assert {"collar", "bull_call_spread", "bear_put_spread"} <= set(c)
    assert len(c["collar"]["legs"]) == 2
    assert {l["action"] for l in c["collar"]["legs"]} == {"buy", "sell"}


# ── payoff arithmetic ───────────────────────────────────────────────────────────────────

def test_long_call_breakeven_is_strike_plus_debit():
    lc = _matrix()["singles"]["long_call"]
    leg = lc["legs"][0]
    assert lc["breakeven"] == pytest.approx(leg["strike"] + lc["net_per_share"], abs=0.01)
    assert lc["max_loss_per_contract"] == pytest.approx(lc["net_per_share"] * 100, abs=0.01)
    assert lc["max_profit_per_contract"] is None, "a long call's upside is unbounded, not a number"


def test_cash_secured_put_risk_is_the_strike_not_the_premium():
    """The most commonly misunderstood leg: max loss is owning the stock down from the strike,
    NOT the premium collected. Getting this backwards understates the risk by ~20x here."""
    csp = _matrix()["singles"]["cash_secured_put"]
    leg = csp["legs"][0]
    credit = csp["net_per_share"]
    assert csp["max_profit_per_contract"] == pytest.approx(credit * 100, abs=0.01)
    assert csp["max_loss_per_contract"] == pytest.approx((leg["strike"] - credit) * 100, abs=0.01)
    assert csp["max_loss_per_contract"] > csp["max_profit_per_contract"] * 5
    assert csp["effective_entry"] == pytest.approx(leg["strike"] - credit, abs=0.01)
    assert csp["collateral_per_contract"] == pytest.approx(leg["strike"] * 100, abs=0.01)


def test_protective_put_floor_and_max_loss_agree():
    pp = _matrix()["singles"]["protective_put"]
    leg, debit = pp["legs"][0], pp["net_per_share"]
    assert pp["effective_floor"] == pytest.approx(leg["strike"] - debit, abs=0.01)
    assert pp["max_loss_per_contract"] == pytest.approx((SPOT - leg["strike"] + debit) * 100, abs=0.01)
    assert pp["max_profit_per_contract"] is None, "you keep the upside; it is not a number"


def test_covered_call_breakeven_is_spot_less_credit():
    cc = _matrix()["singles"]["covered_call"]
    leg, credit = cc["legs"][0], cc["net_per_share"]
    assert cc["breakeven"] == pytest.approx(SPOT - credit, abs=0.01)
    assert cc["max_profit_per_contract"] == pytest.approx((leg["strike"] - SPOT + credit) * 100, abs=0.01)


def test_vertical_spread_max_profit_is_width_minus_debit():
    bcs = _matrix()["combos"]["bull_call_spread"]
    lo, hi = bcs["legs"]
    width, debit = hi["strike"] - lo["strike"], bcs["net_per_share"]
    assert bcs["max_profit_per_contract"] == pytest.approx((width - debit) * 100, abs=0.01)
    assert bcs["max_loss_per_contract"] == pytest.approx(debit * 100, abs=0.01)
    assert bcs["breakeven"] == pytest.approx(lo["strike"] + debit, abs=0.01)
    # A vertical can never risk more than its width.
    assert bcs["max_loss_per_contract"] <= width * 100 + 0.01


def test_spread_is_cheaper_than_the_outright_call_it_is_built_from():
    m = _matrix()
    assert m["combos"]["bull_call_spread"]["net_per_contract"] < m["singles"]["long_call"]["net_per_contract"]


def test_collar_net_is_put_cost_minus_call_credit():
    col = _matrix()["combos"]["collar"]
    put_leg = next(l for l in col["legs"] if l["action"] == "buy")
    call_leg = next(l for l in col["legs"] if l["action"] == "sell")
    assert col["net_per_share"] == pytest.approx(
        put_leg["price_per_share"] - call_leg["price_per_share"], abs=0.01)
    assert col["max_loss_per_contract"] == pytest.approx(
        (SPOT - put_leg["strike"] + col["net_per_share"]) * 100, abs=0.01)


def test_collar_costs_less_than_the_bare_protective_put():
    """The entire point of a collar: the short call funds the put."""
    m = _matrix()
    assert m["combos"]["collar"]["net_per_contract"] < m["singles"]["protective_put"]["net_per_contract"]


def test_bear_put_spread_leaves_a_gap_below_the_short_strike():
    bps = _matrix()["combos"]["bear_put_spread"]
    hi, lo = bps["legs"]
    assert lo["strike"] < hi["strike"]
    assert bps["max_profit_per_contract"] == pytest.approx(
        ((hi["strike"] - lo["strike"]) - bps["net_per_share"]) * 100, abs=0.01)
    assert "exposed again" in bps["what_it_does"]


# ── the recommendation ──────────────────────────────────────────────────────────────────

def test_holding_no_shares_never_recommends_a_structure_requiring_them():
    """A hard constraint, not a preference — you cannot write a covered call on stock you do
    not own, and recommending one would be nonsense rather than merely suboptimal."""
    rec = _matrix(shares=0)["recommendation"]
    m = _matrix(shares=0)
    chosen = {**m["singles"], **m["combos"]}[rec["primary"]]
    assert chosen["requires_shares"] is False
    for alt in rec["alternatives"]:
        assert {**m["singles"], **m["combos"]}[alt["key"]]["requires_shares"] is False


def test_rich_iv_favours_selling_premium_when_holding_shares():
    rec = _matrix(iv_rank=85.0, shares=100)["recommendation"]
    assert rec["primary"] == "covered_call"
    assert rec["iv_regime"] == "rich"
    assert "expensive" in rec["reason"]


def test_cheap_iv_favours_buying_premium_when_bullish_and_flat():
    rec = _matrix(iv_rank=10.0, shares=0, signal="BUY")["recommendation"]
    assert rec["primary"] == "long_call"
    assert rec["iv_regime"] == "cheap"


def test_rich_iv_with_no_shares_prefers_being_paid_to_wait():
    rec = _matrix(iv_rank=90.0, shares=0, signal="BUY")["recommendation"]
    assert rec["primary"] == "cash_secured_put"


def test_unknown_iv_is_disclosed_not_guessed():
    """An absent IV rank must weaken the claim out loud rather than silently defaulting to
    'normal' and presenting the same confident ranking."""
    m = _matrix(iv_rank=None)
    assert m["iv_regime"] == "unknown"
    assert "unavailable" in m["recommendation"]["iv_note"]


def test_empty_chain_returns_no_recommendation_rather_than_a_fabricated_one():
    m = build_strategy_matrix(
        current_price=SPOT, stop_loss=107.0, take_profit=115.0, signal="BUY",
        put_rows=[], put_expiry=None, call_rows=[], call_expiry=None,
        shares=100, iv_rank=50.0, today=TODAY)
    assert m["singles"] == {} and m["combos"] == {}
    assert m["recommendation"]["primary"] is None


def test_iv_regime_boundaries():
    assert _iv_regime(60.0) == "rich" and _iv_regime(59.9) == "normal"
    assert _iv_regime(30.0) == "cheap" and _iv_regime(30.1) == "normal"
    assert _iv_regime(None) == "unknown"


def test_wide_spreads_are_surfaced_not_buried():
    """A 'cost' quoted at mid is fiction when the spread is enormous; the reader must be able
    to see that."""
    wide = [{"strike": 110, "bid": 1.0, "ask": 3.0, "last_price": 0, "iv": 0.4, "oi": 10}]
    m = build_strategy_matrix(
        current_price=SPOT, stop_loss=None, take_profit=None, signal=None,
        put_rows=[], put_expiry=None, call_rows=wide, call_expiry=CALL_EXP,
        shares=0, iv_rank=None, today=TODAY)
    assert m["singles"]["long_call"]["legs"][0]["spread_pct"] == 100.0
