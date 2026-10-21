"""Execute the current options-flow route with controlled, offline chain fixtures.

No database, provider, credentials, orders or delivery. AST extraction preserves the route
body while removing framework decorators/default arguments. Fixtures are synthetic and
are NOT market observations. Run from anywhere with Python 3 and pandas installed.
"""
import ast
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pandas as pd

ROOT = Path(__file__).resolve().parents[4]
SOURCE = ROOT / "services/market-data/src/api/routes.py"
TREE = ast.parse(SOURCE.read_text())
FUNCTIONS = {
    "get_options_flow", "unusual_premium_fields", "whale_coverage",
    "compute_options_pressure_score",
}


class Cache:
    def get(self, key):
        return None

    def setex(self, *args):
        pass


def run_case(calls, puts, unusual_rows):
    warnings = []
    namespace = {
        "__package__": "audit.api",
        "Session": object,
        "Depends": lambda value: None,
        "get_session": lambda: None,
        "json": json,
        "_OPTIONS_TTL": 900,
        "_get_redis": Cache,
        "_uw_expiries": lambda *_: ["2099-01-16"],
        "_options_flow_gex_component": lambda *_: None,
        "log": SimpleNamespace(warning=lambda *args, **kwargs: warnings.append(kwargs)),
    }

    def frame(total_volume, count):
        rows = []
        for i in range(count):
            rows.append({
                "strike": 100 + i, "volume": 100, "openInterest": 100,
                "lastPrice": 0., "last_price_available": False,
                "bid": 1., "ask": 1.2, "impliedVolatility": .3,
                "inTheMoney": False,
            })
        rows.append({
            "strike": 150, "volume": total_volume - count * 100,
            "openInterest": 1_000_000, "lastPrice": 0.,
            "last_price_available": False, "bid": 1., "ask": 1.2,
            "impliedVolatility": .3, "inTheMoney": False,
        })
        return pd.DataFrame(rows)

    namespace["_uw_option_chain"] = lambda *_: SimpleNamespace(
        calls=frame(calls, unusual_rows), puts=frame(puts, 0))
    for node in TREE.body:
        if isinstance(node, ast.FunctionDef) and node.name in FUNCTIONS:
            node.decorator_list = []
            exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), "exec"), namespace)
    result = namespace["get_options_flow"]("SYNTHETIC", session=None)
    return {
        "fixture": {"calls": calls, "puts": puts, "unusual_call_rows": unusual_rows},
        "result": {key: result.get(key) for key in (
            "available", "reason", "call_volume", "put_volume", "cp_ratio", "sentiment",
            "activity_composition", "directional_intent", "chain_as_of")},
        "warning_errors": [w.get("error") for w in warnings],
    }


def main():
    saved = {name: sys.modules.get(name) for name in ("yfinance", "audit.services.uw_option_chain")}
    sys.modules["yfinance"] = ModuleType("yfinance")  # imported but unused by the route
    module = ModuleType("audit.services.uw_option_chain")
    module.latest_as_of = lambda *_: date(2026, 10, 20)
    sys.modules[module.__name__] = module
    try:
        results = [run_case(*case) for case in (
            (3000, 1000, 2), (3000, 1000, 1), (1000, 0, 0),
            (10, 100, 0), (1296, 1000, 0),
        )]
    finally:
        for name, value in saved.items():
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value
    assert results[0]["result"]["reason"] == "fetch_error"
    assert "NoneType" in results[0]["warning_errors"][0]
    assert results[1]["result"]["available"] is True
    assert results[2]["result"]["sentiment"] == "neutral"
    assert results[2]["result"]["activity_composition"] == "call_heavy"
    assert results[3]["result"]["sentiment"] == "bearish"
    assert results[4]["result"]["activity_composition"] == "call_heavy"
    scheduler = ROOT / "services/market-data/src/services/scheduler.py"
    node = next(n for n in ast.parse(scheduler.read_text()).body
                if isinstance(n, ast.FunctionDef) and n.name == "_dark_pool_print_is_recent")
    namespace = {"datetime": datetime, "timezone": timezone}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(scheduler), "exec"), namespace)
    now = datetime(2026, 10, 21, 16, tzinfo=timezone.utc)
    future_accepted = namespace[node.name](
        SimpleNamespace(executed_at="2026-10-22T16:00:00Z"), now - timedelta(minutes=90))
    assert future_accepted is True
    print(json.dumps({"evidence_kind": "synthetic_offline_route_execution", "cases": results,
                      "dark_pool_future_timestamp_accepted": future_accepted}, indent=2))


if __name__ == "__main__":
    main()
