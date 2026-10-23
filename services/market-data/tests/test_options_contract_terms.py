"""Audit R05 (2026-10-22): adjusted-contract protection must reach the strategy calculations.

Before: the flow card withheld turnover for an adjusted root, but `_options_chain_rows` dropped
`option_symbol`, so the game plan and the strategy matrix priced every contract at x100,
including adjusted ones whose deliverable may be other than 100 shares (or shares plus cash).
"""
import pandas as pd
import pytest

from src.api import routes
from src.services import options_contract as oc
from src.services import options_strategies as osx

STD_SYM, ADJ_SYM = "MU261120P00100000", "MU1261120P00100000"   # MU1: adjusted after a corporate action
STD_CALL, ADJ_CALL = "MU261120C00120000", "MU1261120C00120000"


# ── the shared definition ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("sym,under,status,supported", [
    (STD_SYM, "MU", oc.STANDARD, True),
    (ADJ_SYM, "MU", oc.NONSTANDARD, False),
    ("SPXW261120P05000000", "SPX", oc.STANDARD, False),     # weekly index root, same product
    (None, "MU", oc.UNVERIFIED, False),
    ("not-an-occ-symbol", "MU", oc.UNVERIFIED, False),
    (STD_SYM, None, oc.UNVERIFIED, False),
])
def test_terms_by_identity(sym, under, status, supported):
    t = oc.contract_terms(sym, under)
    assert t["status"] == status and t["automatic_payoff_supported"] is supported
    # An unsupported contract has NO multiplier to assume — never a default 100.
    assert t["premium_multiplier"] == (100 if supported else None)
    assert t["deliverable_shares"] == (100 if supported else None)


def test_premium_multiplier_and_deliverable_are_separate_fields():
    t = oc.contract_terms(STD_SYM, "MU")
    assert "premium_multiplier" in t and "deliverable_shares" in t
    assert "not an exchange deliverable record" in t["basis"]


def test_routes_status_delegates_to_the_shared_definition():
    for sym in (STD_SYM, ADJ_SYM, None):
        assert routes.contract_multiplier_status(sym, "MU") == oc.contract_status(sym, "MU")


# ── chain rows keep identity end to end ────────────────────────────────────────────────────

def _frame(*rows):
    return pd.DataFrame([{"strike": k, "bid": 2.0, "ask": 2.2, "lastPrice": 0.0, "volume": 10, "openInterest": 500,
                          "impliedVolatility": 0.3, "inTheMoney": None, "option_symbol": sym} for k, sym in rows])


def test_chain_rows_keep_the_symbol_and_its_terms():
    rows = routes._options_chain_rows(_frame((100.0, STD_SYM), (100.0, ADJ_SYM)), "MU")
    by = {r["option_symbol"]: r for r in rows}
    assert by[STD_SYM]["contract_terms"]["automatic_payoff_supported"] is True
    assert by[ADJ_SYM]["contract_terms"]["status"] == oc.NONSTANDARD


def test_chain_rows_without_an_underlying_are_unverified_not_standard():
    row = routes._options_chain_rows(_frame((100.0, STD_SYM)))[0]
    assert row["contract_terms"]["automatic_payoff_supported"] is False


# ── every automatic entry point refuses adjusted / unidentified contracts ─────────────────

from datetime import date, timedelta
TODAY = date(2026, 10, 22)
PUT_EXP = (TODAY + timedelta(days=35)).isoformat()
CALL_EXP = (TODAY + timedelta(days=21)).isoformat()


def _rows(df):
    return routes._options_chain_rows(df, "MU")


def _plan(put_syms, call_syms):
    puts = _rows(_frame(*[(100.0, s) for s in put_syms]))
    calls = _rows(_frame(*[(120.0, s) for s in call_syms]))
    return routes.compute_options_game_plan(
        current_price=110.0, stop_loss=100.0, take_profit=120.0, signal="BUY",
        put_expiries=[PUT_EXP], put_rows=puts, call_expiries=[CALL_EXP], call_rows=calls,
        shares=200, today=TODAY)


def test_game_plan_refuses_an_adjusted_contract_with_a_usable_quote():
    plan = _plan([ADJ_SYM], [ADJ_CALL])
    assert plan["protective_put"] is None and plan["covered_call"] is None
    assert plan["contract_terms_refused"] == {"puts": {oc.NONSTANDARD: 1}, "calls": {oc.NONSTANDARD: 1}}
    m = plan["strategy_matrix"] or {}
    assert not (m.get("singles") or {}) and not (m.get("combos") or {})


def test_game_plan_refuses_rows_with_no_identity():
    puts = routes._options_chain_rows(_frame((100.0, None)), "MU")
    plan = routes.compute_options_game_plan(current_price=110.0, stop_loss=100.0, take_profit=None, signal="BUY",
        put_expiries=[PUT_EXP], put_rows=puts, call_expiries=[], call_rows=[], today=TODAY)
    assert plan["protective_put"] is None
    assert plan["contract_terms_refused"]["puts"] == {oc.UNVERIFIED: 1}


def test_standard_contracts_keep_their_existing_results_and_carry_identity():
    plan = _plan([STD_SYM], [STD_CALL])
    pp, cc = plan["protective_put"], plan["covered_call"]
    assert pp["cost_per_contract"] == 210.0 and pp["option_symbol"] == STD_SYM      # 2.10 x 100
    assert cc["credit_per_contract"] == 210.0 and cc["option_symbol"] == STD_CALL
    assert plan["contract_terms_refused"] == {"puts": {}, "calls": {}}


def test_a_mixed_chain_prices_only_the_standard_contract():
    plan = _plan([ADJ_SYM, STD_SYM], [STD_CALL])
    assert plan["protective_put"]["option_symbol"] == STD_SYM
    assert plan["contract_terms_refused"]["puts"] == {oc.NONSTANDARD: 1}


def test_strategy_leg_refuses_a_stock_plus_cash_deliverable():
    # A cash-and-stock merger adjustment arrives as an adjusted root: its terms are unknown here.
    adj = {**_rows(_frame((100.0, ADJ_SYM)))[0], "right": "put"}
    assert osx._leg(adj, "buy", PUT_EXP, 35) is None
    std = {**_rows(_frame((100.0, STD_SYM)))[0], "right": "put"}
    leg = osx._leg(std, "buy", PUT_EXP, 35)
    assert leg["cost_per_contract"] == 210.0 and leg["contract_terms"]["premium_multiplier"] == 100


def test_a_leg_with_explicit_supported_terms_is_priced():
    explicit = {"strike": 100.0, "bid": 2.0, "ask": 2.2, "right": "put",
                "contract_terms": {"automatic_payoff_supported": True, "premium_multiplier": 100}}
    assert osx._leg(explicit, "buy", PUT_EXP, 35)["cost_per_contract"] == 210.0
    wrong_multiplier = {**explicit, "contract_terms": {"automatic_payoff_supported": True, "premium_multiplier": 10}}
    assert osx._leg(wrong_multiplier, "buy", PUT_EXP, 35) is None


@pytest.mark.parametrize("shares,coverable", [(99, 0), (100, 1), (250, 2), (None, 0)])
def test_covered_call_share_requirement_is_per_standard_deliverable(shares, coverable):
    assert osx._coverable_contracts(shares) == coverable


# ── the options-income engine (and its backtest, which calls the same selector) ────────────

def test_income_engine_offers_only_standard_contracts():
    from datetime import date as _d
    from types import SimpleNamespace
    from unittest.mock import patch
    from src.services import options_income_engine as eng

    today = _d(2026, 10, 22)
    exp = today + timedelta(days=30)

    def row(sym):
        return SimpleNamespace(option_symbol=sym, expiry=exp, strike=120.0, delta=0.25, nbbo_bid=1.5,
                               nbbo_ask=1.6, implied_volatility=0.4, open_interest=5000)

    class _Sess:
        def execute(self, *_a, **_k):
            return SimpleNamespace(all=lambda: [row("MU1261121C00120000"), row(None), row("MU261121C00120000")])

    with patch.object(eng, "_next_earnings_by_symbol", lambda *a: {}), \
         patch.object(eng, "_latest_chain_as_of", lambda *a: today), \
         patch.object(eng, "_chain_as_of_on_or_before", lambda *a: today):
        out = eng.rank_income_candidates(_Sess(), symbols=["MU"], strategies=["COVERED_CALL"],
                                         current_prices={"MU": 110.0}, point_in_time=today, all_contracts=True)
    assert [c["option_symbol"] for c in out] == ["MU261121C00120000"]


def test_a_mark_on_an_unsupported_contract_is_graded_as_such():
    from src.services import options_income_engine as eng
    # The engine relabels the quality of any mark on a contract with unsupported terms.
    assert eng.unsupported_contract_reason("AAA261120P00090000", "AAA") is None
    assert eng.unsupported_contract_reason("OPT-MARK", "AAA").startswith("unverified_100_assumed")



# ── review 2026-10-23: unresolved settlement is visible; unsupported marks are identifiable ──

def test_settlement_state_names_an_unresolved_expired_unsupported_position():
    from types import SimpleNamespace as NS
    from src.services import options_income_engine as eng
    today = date(2026, 10, 23)
    pos = lambda sym, exp, stage="open": NS(symbol="AMD", option_symbol=sym, expiry=exp, stage=stage)
    adj, std = "AMD1261016P00100000", "AMD261016P00100000"
    state, why = eng.settlement_state(pos(adj, date(2026, 10, 16)), today)
    assert state == "settlement_unresolved" and "nonstandard_root_deliverable_unknown" in why
    assert eng.settlement_state(pos(None, date(2026, 10, 16)), today)[0] == "settlement_unresolved"
    assert eng.settlement_state(pos(std, date(2026, 10, 16)), today)[0] == "settlement_pending"
    assert eng.settlement_state(pos(adj, date(2026, 11, 20)), today) == ("open", None)   # not expired yet
    assert eng.settlement_state(pos(std, date(2026, 10, 16), "closed"), today) == ("closed", None)


def test_an_unsupported_mark_is_identifiable_in_the_stored_equity_row():
    import importlib.util, pathlib
    spec = importlib.util.spec_from_file_location("t_r08", pathlib.Path(__file__).with_name("test_r08_mark_quality.py"))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    ev = mod._probe("snapshot_unsupported_contract")["mark_evidence"]
    assert ev["unsupported_contract_marks"] == 1
    assert ev["mark_quality"] == {"unsupported_contract_terms": 1}
    assert ev["approximate_marks"] == 1
    fresh = mod._probe("snapshot_fresh_quote")["mark_evidence"]
    assert fresh["unsupported_contract_marks"] == 0 and fresh["approximate_marks"] == 0
