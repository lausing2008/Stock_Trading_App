"""Intraday Stage C daily isolation, research-engine side (design §10).

code presence (from source): this suite stubs `db`, so the statement cannot be compiled here.
The report's "platform signal" is the newest DAILY signal; without the horizon predicate a
newer row of another horizon would become "the platform's signal" in every report."""
import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "intel_reports" / "adapters.py"


def test_latest_signal_reads_daily_horizons_only():
    fn = next(n for n in ast.walk(ast.parse(SRC.read_text()))
              if isinstance(n, ast.FunctionDef) and n.name == "latest_signal")
    selects = [n for n in ast.walk(fn) if isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "where"
               and "select(Signal)" in ast.unparse(n)]
    assert len(selects) == 1
    assert "Signal.horizon.in_([SignalHorizon(h) for h in DAILY_HORIZONS])" in ast.unparse(selects[0])
