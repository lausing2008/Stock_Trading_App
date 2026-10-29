"""Intraday Stage C daily isolation, event-intelligence side (design §10).

code presence (from source): scheduler.py cannot be imported locally (apscheduler), matching the
other scheduler-wiring tests here. The catalyst recompute's newest-signal read must restrict to
daily horizons and bind them; on PostgreSQL this is defence in depth behind the enum."""
import ast
from pathlib import Path

SRC = Path(__file__).parent.parent / "src" / "scheduler.py"


def _function(name):
    tree = ast.parse(SRC.read_text())
    return next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef | ast.FunctionDef) and n.name == name)


def test_catalyst_recompute_reads_daily_signals_only():
    fn = _function("job_recompute_catalyst")
    strings = ["".join(p.value for p in ast.walk(n) if isinstance(p, ast.Constant) and isinstance(p.value, str))
               for n in ast.walk(fn) if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "text"]
    sql = next(s for s in strings if "FROM signals" in s)
    assert "CAST(horizon AS text) = ANY(:daily_horizons)" in sql
    binds = [v for n in ast.walk(fn) if isinstance(n, ast.Dict)
             for k, v in zip(n.keys, n.values) if getattr(k, "value", None) == "daily_horizons"]
    assert len(binds) == 1 and ast.unparse(binds[0]) == "list(DAILY_HORIZONS)"
