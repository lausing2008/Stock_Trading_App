"""Audit R07 (2026-10-22): the unusual-contract shortlist must not depend on provider row order.

Before the fix each side/expiry was cut with `sort_values("volume").head(3)`, and only then did
rank_unusual() apply a deterministic order — too late to recover a tied row already discarded.
The probe's case: four equally active strikes gave 100/101/102 in one input order and
101/102/103 in the reverse order.

Runs the REAL get_options_flow body (decorators stripped) on synthetic settled-chain frames.
"""
import ast
import json
import random
import sys
from datetime import date
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pandas as pd

SRC = Path(__file__).resolve().parents[1] / "src/api/routes.py"
TREE = ast.parse(SRC.read_text())
NAMES = {"get_options_flow", "_with_current_scope", "unusual_premium_fields", "whale_coverage",
         "compute_options_pressure_score", "rank_unusual", "unusual_order_key"}


class _Cache:
    def get(self, key): return None
    def setex(self, *a): pass


def _row(strike, volume, oi=100, symbol=None):
    return {"strike": float(strike), "volume": volume, "openInterest": oi, "lastPrice": 0.0,
            "last_price_available": False, "bid": 1.0, "ask": 1.2, "impliedVolatility": 0.3,
            "inTheMoney": False, "option_symbol": symbol}


def _run(calls_by_expiry: dict, puts_by_expiry: dict):
    expiries = sorted(set(calls_by_expiry) | set(puts_by_expiry))

    def chain(_session, _sym, exp, **_):
        return SimpleNamespace(calls=pd.DataFrame(calls_by_expiry.get(exp, [_row(500, 0, 10)])),
                               puts=pd.DataFrame(puts_by_expiry.get(exp, [_row(500, 0, 10)])),
                               as_of="2026-10-20", coverage_status=None, coverage_reason=None)

    ns = {"__package__": "t.api", "Session": object, "Depends": lambda v: None, "get_session": lambda: None,
          "json": json, "_OPTIONS_TTL": 900, "_get_redis": _Cache,
          "_uw_session_expiries": lambda *_: (date(2026, 10, 20), expiries),
          "_options_flow_gex_component": lambda *_: None,
          "log": SimpleNamespace(warning=lambda *a, **k: None), "_uw_option_chain": chain}
    for node in TREE.body:
        if isinstance(node, ast.FunctionDef) and node.name in NAMES:
            node.decorator_list = []
            exec(compile(ast.Module(body=[node], type_ignores=[]), str(SRC), "exec"), ns)
    from src.api import routes as _real
    ns.update({k: getattr(_real, k) for k in ("_UNUSUAL_MIN_OI", "_UNUSUAL_VOL_OI", "_LOW_OI_MIN_VOLUME",
                                             "contract_multiplier_status")})
    saved = {n: sys.modules.get(n) for n in ("t.services.uw_option_chain", "t.services.options_scope")}
    from src.services import options_scope as _scope
    sys.modules["t.services.options_scope"] = _scope
    mod = ModuleType("t.services.uw_option_chain"); mod.latest_as_of = lambda *_: date(2026, 10, 20)
    sys.modules[mod.__name__] = mod
    try:
        return ns["get_options_flow"]("SYNTH", session=None)
    finally:
        for n, v in saved.items():
            if v is None: sys.modules.pop(n, None)
            else: sys.modules[n] = v


def _ids(result):
    return [(u["expiry"], u["side"], u["strike"]) for u in result["unusual"]]


EXP = "2099-01-16"


def test_tied_strikes_survive_the_per_side_cut_independent_of_input_order():
    # The probe's witness: four equally active strikes, forward and reversed.
    rows = [_row(s, 200) for s in (100, 101, 102, 103)]
    fwd = _run({EXP: rows}, {EXP: [_row(90, 150)]})
    rev = _run({EXP: list(reversed(rows))}, {EXP: [_row(90, 150)]})
    assert _ids(fwd) == _ids(rev)
    assert [s for _, side, s in _ids(fwd) if side == "call"] == [100.0, 101.0, 102.0]


def test_shuffled_inputs_select_identical_contracts():
    rng = random.Random(20261022)
    exps = ["2099-01-09", "2099-01-16", "2099-01-23", "2099-01-30"]
    # Heavy ties on volume, several ties on exact vol/OI, across all four expiries and both sides.
    calls = {e: [_row(100 + i, 200 + (i % 2) * 50, 100 + (i % 3) * 50, f"SYNTH{e[2:4]}{e[5:7]}{e[8:]}C{100 + i:05d}000")
                 for i in range(8)] for e in exps}
    puts = {e: [_row(80 + i, 200, 100, f"SYNTH{e[2:4]}{e[5:7]}{e[8:]}P{80 + i:05d}000") for i in range(6)]
            for e in exps}
    base = _run(calls, puts)
    assert len(base["unusual"]) == 10                     # the overall cut also binds
    for _ in range(25):
        shuffled_c = {e: rng.sample(v, len(v)) for e, v in calls.items()}
        shuffled_p = {e: rng.sample(v, len(v)) for e, v in puts.items()}
        out = _run(shuffled_c, shuffled_p)
        assert _ids(out) == _ids(base)
        assert out["unusual_selection"]["kept"] == base["unusual_selection"]["kept"]
        assert out["unusual_selection"]["displayed"] == base["unusual_selection"]["displayed"]


def test_exact_ratio_breaks_a_tie_the_rounded_ratio_cannot():
    # Same volume; OI 6000..6003 gives 0.33333..0.33319 — all displayed as vol_oi 0.33. Ranking on
    # the rounded value would fall through to strike order; the exact ratio puts lowest OI first.
    rows = [_row(110, 2000, 6002), _row(111, 2000, 6000), _row(112, 2000, 6003), _row(113, 2000, 6001)]
    out = _run({EXP: rows}, {EXP: [_row(90, 150)]})
    calls = [u for u in out["unusual"] if u["side"] == "call"]
    assert {u["vol_oi"] for u in calls} == {0.33}          # displayed ratios tie...
    assert [u["strike"] for u in calls] == [111.0, 113.0, 110.0]   # ...exact ratios do not


def test_contract_symbol_is_the_final_tie_break_and_is_returned():
    a = _row(100, 200, 100, "SYNTH990116C00100000")
    b = _row(100, 200, 100, "SYNTH1990116C00100000")       # adjusted root, same strike
    out1 = _run({EXP: [a, b]}, {EXP: [_row(90, 150)]})
    out2 = _run({EXP: [b, a]}, {EXP: [_row(90, 150)]})
    syms = lambda r: [u["option_symbol"] for u in r["unusual"] if u["side"] == "call"]
    assert syms(out1) == syms(out2) == sorted([a["option_symbol"], b["option_symbol"]])
