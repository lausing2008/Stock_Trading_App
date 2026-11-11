"""Options candidate study Part B: the append-only record of what the desk served.

Pins: the record is built from the real matrix the route returned, the lead is the golden-pinned
port (or an explicit reason it was not recorded), rows are never rewritten, a different input or
code fingerprint files beside rather than over, and recording can never break the response.
"""
import json
import subprocess
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.services import options_desk_record as desk
from src.services.option_research_lead import research_lead
from src.services.options_strategies import build_strategy_matrix

TODAY = date(2026, 1, 2)
EXP = "2026-02-06"
TERMS = {"automatic_payoff_supported": True, "premium_multiplier": 100}


def row(strike, bid=2.0, ask=2.2, right="call", oi=100, **kw):
    return {"strike": strike, "bid": bid, "ask": ask, "last_price": 0, "right": right,
            "option_symbol": f"EX-{right[0].upper()}{strike}", "contract_terms": TERMS, "oi": oi,
            "delta": None, "iv": 0.3, **kw}


def plan():
    matrix = build_strategy_matrix(
        current_price=100, stop_loss=95, take_profit=120, signal=None,
        call_rows=[row(k, ask=2.1 + 0.05 * i, oi=100 + 10 * i) for i, k in enumerate((100, 105, 110, 115))],
        call_expiry=EXP,
        put_rows=[row(k, right="put", oi=100 + 10 * i) for i, k in enumerate((90, 95, 100, 105))],
        put_expiry=EXP, today=TODAY)
    return {"strategy_matrix": matrix, "current_price": 100, "chain_as_of": TODAY.isoformat(),
            "contract_terms_refused": {"puts": 0, "calls": 0}}


def build(p=None, **kw):
    args = dict(symbol="EX", plan=p or plan(), today=TODAY, stop_loss=95, take_profit=120, shares=None,
                holding_days=14, preferences={}, chain_session=TODAY.isoformat())
    args.update(kw)
    return desk.build_record(**args)


def test_the_record_carries_every_strategy_from_the_matrix_actually_built():
    p = plan()
    rec = build(p)
    reviews = p["strategy_matrix"]["candidate_review"]
    assert set(rec["shortlist"]) == set(desk.STRATEGIES)
    for key in desk.STRATEGIES:
        entry, review = rec["shortlist"][key], reviews[key]
        assert entry["method_version"] == review["method_version"]
        assert entry["rejected_counts"] == review["rejected_counts"]
        assert [a["option_symbol"] for a in entry["alternatives"]] == \
            [s["legs"][0]["option_symbol"] for s in review["alternatives"]]
        assert entry["preset"]["option_symbol"] == p["strategy_matrix"]["singles"][key]["legs"][0]["option_symbol"]


def test_the_lead_is_the_golden_pinned_port_with_the_requested_holding_period():
    p = plan()
    rec = build(p, holding_days=14)
    matrix = p["strategy_matrix"]
    for key in desk.STRATEGIES:
        expected = research_lead(key, matrix, matrix["candidate_review"][key], TODAY, 14)
        lead = rec["shortlist"][key]["lead"]
        assert lead["status"] == "recomputed"
        assert lead["index"] == expected[1]
        assert lead["contract"]["option_symbol"] == expected[0]["legs"][0]["option_symbol"]


def test_the_lead_is_not_guessed_when_the_desk_would_have_used_inputs_the_port_lacks():
    with_prefs = build(preferences={"min_dte": 20, "max_dte": None, "min_abs_delta": None, "max_abs_delta": None})
    no_hold = build(holding_days=None)
    assert {e["lead"]["status"] for e in with_prefs["shortlist"].values()} == {"not_recorded_with_preferences"}
    assert {e["lead"]["status"] for e in no_hold["shortlist"].values()} == {"not_recorded_without_holding_period"}


def test_real_database_rows_are_append_only_and_file_beside():
    """persist() on real SQLAlchemy (this suite stubs it): see _options_desk_record_probe.py."""
    probe = Path(__file__).with_name("_options_desk_record_probe.py")
    process = subprocess.run([sys.executable, str(probe)], capture_output=True, text=True, timeout=60)
    assert process.returncode == 0, process.stdout + process.stderr
    data = json.loads(process.stdout.split("DESK_RECORD_PROBE_JSON=")[-1])
    assert data == {"rows": 3, "repeat_filed_nothing": True, "never_rewritten": True,
                    "database": "sqlite", "outer_rollback": True}


def test_the_input_hash_ignores_the_shortlist_and_the_record_hash_does_not():
    a, b = build(), build()
    p = plan()
    p["strategy_matrix"]["candidate_review"]["long_call"]["alternatives"] = []
    c = build(p)
    assert a["record_hash"] == b["record_hash"] and a["input_hash"] == c["input_hash"]
    assert a["record_hash"] != c["record_hash"]


def test_the_fingerprint_covers_the_candidate_code_and_the_route_functions():
    parts = desk.code_fingerprint()["parts"]
    assert {"options_strategies", "option_research_lead", "routes.get_options_game_plan",
            "routes._nearest_expiry_in_dte_window"} <= set(parts)


def test_recording_never_raises_and_rolls_back():
    calls = []
    broken = SimpleNamespace(get_bind=lambda: (_ for _ in ()).throw(RuntimeError("db down")),
                             rollback=lambda: calls.append("rollback"))
    desk.record(broken, symbol="EX", plan=plan(), today=TODAY, stop_loss=95, take_profit=120, shares=None,
                holding_days=14, preferences={}, chain_session=TODAY.isoformat())
    assert calls == ["rollback"]


def test_a_plan_without_reviews_files_nothing():
    touched = []
    spy = SimpleNamespace(get_bind=lambda: touched.append("bind"), rollback=lambda: touched.append("rollback"))
    desk.record(spy, symbol="EX", plan={"strategy_matrix": None}, today=TODAY, stop_loss=None,
                take_profit=None, shares=None, holding_days=None, preferences={}, chain_session=None)
    assert touched == []


def test_the_real_route_records_what_it_served_with_its_own_session():
    from src.api import routes
    import src.services.options_scope as scope
    chain = SimpleNamespace(calls=[row(k) for k in (100, 105, 110)],
                            puts=[row(k, right="put") for k in (90, 95, 100)], as_of=TODAY.isoformat())
    calls, route_session = [], object()
    with patch.object(routes, "_uw_session_expiries", lambda *_: (TODAY, [EXP])), \
         patch.object(scope, "current_expiries", lambda expiries, *a, **k: expiries), \
         patch.object(routes, "_goal_current_price", lambda *_: 100.0), \
         patch.object(routes, "_uw_option_chain", lambda *a, **k: chain), \
         patch.object(routes, "_options_chain_rows", lambda values, symbol: values), \
         patch.object(routes, "_today_et", lambda: TODAY), \
         patch.object(desk, "record", lambda s, **kw: calls.append((s, kw))):
        served = routes.get_options_game_plan(
            "EX", stop_loss=95, take_profit=120, shares=None, session=route_session, _user=None, holding_days=14,
            min_dte=None, max_dte=None, min_abs_delta=None, max_abs_delta=None)
    assert served["available"]
    assert len(calls) == 1 and calls[0][0] is route_session          # no second connection
    kw = calls[0][1]
    assert kw["plan"] is served and kw["symbol"] == "EX" and kw["holding_days"] == 14
    assert kw["stop_loss"] == 95 and kw["chain_session"] == TODAY.isoformat() and kw["today"] == TODAY
    # The record built from exactly that response covers all four strategies.
    built = desk.build_record(**kw)
    assert set(built["shortlist"]) == set(desk.STRATEGIES)


def test_a_recording_failure_leaves_the_served_plan_unchanged():
    from src.api import routes
    import src.services.options_scope as scope
    chain = SimpleNamespace(calls=[row(k) for k in (100, 105, 110)],
                            puts=[row(k, right="put") for k in (90, 95, 100)], as_of=TODAY.isoformat())
    broken = SimpleNamespace(get_bind=lambda: (_ for _ in ()).throw(RuntimeError("db down")), rollback=lambda: None)
    with patch.object(routes, "_uw_session_expiries", lambda *_: (TODAY, [EXP])), \
         patch.object(scope, "current_expiries", lambda expiries, *a, **k: expiries), \
         patch.object(routes, "_goal_current_price", lambda *_: 100.0), \
         patch.object(routes, "_uw_option_chain", lambda *a, **k: chain), \
         patch.object(routes, "_options_chain_rows", lambda values, symbol: values), \
         patch.object(routes, "_today_et", lambda: TODAY):
        call = lambda s: routes.get_options_game_plan(
            "EX", stop_loss=95, take_profit=120, shares=None, session=s, _user=None, holding_days=14,
            min_dte=None, max_dte=None, min_abs_delta=None, max_abs_delta=None)
        with patch.object(desk, "record", lambda *a, **k: None):
            expected = call(None)
        assert call(broken) == expected and expected["available"]
