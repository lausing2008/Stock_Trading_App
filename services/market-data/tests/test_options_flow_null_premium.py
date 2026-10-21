"""Options review F1 (2026-10-21): two+ unusual rows with unknown premium made the route fail.

Runs the REAL get_options_flow body (decorators stripped) on synthetic settled-chain frames,
as the review's offline probe did. Before the fix: 2 unusual rows -> available False,
reason fetch_error, "'<' not supported between instances of 'NoneType' and 'NoneType'".
"""
import ast
import json
import sys
from datetime import date
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pandas as pd

SRC = Path(__file__).resolve().parents[1] / "src/api/routes.py"
TREE = ast.parse(SRC.read_text())
NAMES = {"get_options_flow", "unusual_premium_fields", "whale_coverage", "compute_options_pressure_score", "rank_unusual"}


class _Cache:
    def get(self, key): return None
    def setex(self, *a): pass


def _frame(total, unusual_rows):
    rows = [{"strike": 100 + i, "volume": 100 + (unusual_rows - i), "openInterest": 100, "lastPrice": 0.0,
             "last_price_available": False, "bid": 1.0, "ask": 1.2, "impliedVolatility": 0.3, "inTheMoney": False}
            for i in range(unusual_rows)]
    used = sum(r["volume"] for r in rows)
    rows.append({"strike": 150, "volume": max(total - used, 0), "openInterest": 1_000_000, "lastPrice": 0.0,
                 "last_price_available": False, "bid": 1.0, "ask": 1.2, "impliedVolatility": 0.3, "inTheMoney": False})
    return pd.DataFrame(rows)


def _run(calls, puts, call_unusual, put_unusual=0):
    warnings = []
    ns = {"__package__": "t.api", "Session": object, "Depends": lambda v: None, "get_session": lambda: None,
          "json": json, "_OPTIONS_TTL": 900, "_get_redis": _Cache, "_uw_expiries": lambda *_: ["2099-01-16"],
          "_options_flow_gex_component": lambda *_: None,
          "log": SimpleNamespace(warning=lambda *a, **k: warnings.append(k)),
          "_uw_option_chain": lambda *_: SimpleNamespace(calls=_frame(calls, call_unusual), puts=_frame(puts, put_unusual))}
    for node in TREE.body:
        if isinstance(node, ast.FunctionDef) and node.name in NAMES:
            node.decorator_list = []
            exec(compile(ast.Module(body=[node], type_ignores=[]), str(SRC), "exec"), ns)
    saved = {n: sys.modules.get(n) for n in ("t.services.uw_option_chain",)}
    mod = ModuleType("t.services.uw_option_chain"); mod.latest_as_of = lambda *_: date(2026, 10, 20)
    sys.modules[mod.__name__] = mod
    try:
        return ns["get_options_flow"]("SYNTH", session=None), warnings
    finally:
        for n, v in saved.items():
            if v is None: sys.modules.pop(n, None)
            else: sys.modules[n] = v


def test_several_unknown_premiums_no_longer_fail_the_route():
    result, warnings = _run(3000, 1000, call_unusual=3, put_unusual=2)
    assert warnings == [] and result["available"] is True
    assert result["call_volume"] == 3000 and result["put_volume"] == 1000
    assert all(u["premium"] is None for u in result["unusual"])          # unknown stays unknown
    vols = [u["volume"] for u in result["unusual"]]
    assert vols == sorted(vols, reverse=True)                             # ranked by volume
    assert result["unusual_ranking"].startswith("contract volume")


def test_ranking_is_deterministic_on_ties():
    from importlib import util
    ns = {}
    node = next(n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name == "rank_unusual")
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(SRC), "exec"), ns)
    rows = [dict(expiry="2099-01-16", side="put", strike=95.0, volume=500, vol_oi=1.0, premium=None),
            dict(expiry="2099-01-16", side="call", strike=105.0, volume=500, vol_oi=1.0, premium=None),
            dict(expiry="2099-01-09", side="call", strike=110.0, volume=500, vol_oi=1.0, premium=None),
            dict(expiry="2099-01-16", side="call", strike=100.0, volume=900, vol_oi=0.5, premium=None)]
    out = ns["rank_unusual"](rows)
    assert [(r["expiry"], r["side"], r["strike"]) for r in out] == [
        ("2099-01-16", "call", 100.0), ("2099-01-09", "call", 110.0), ("2099-01-16", "call", 105.0), ("2099-01-16", "put", 95.0)]
    assert ns["rank_unusual"](list(reversed(rows))) == out
