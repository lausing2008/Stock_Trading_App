"""Delivery metadata is written in signal-engine's row-lock order (2026-10-27 deadlock review).

This suite stubs SQLAlchemy, so the database behaviour (SQLite write test and the PostgreSQL
deadlock regression) lives in services/signal-engine/tests/test_signal_delivery_lock_order_db.py.
These pin the order, its agreement with the generator, and that the real sender uses the helper.
"""
import ast
import enum
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "services/market-data/src"))

from services.ai_signal_delivery_write import (  # noqa: E402
    GENERATION_HORIZON_ORDER, canonical_write_order,
)


class _Horizon(str, enum.Enum):          # rows carry the ORM enum, not a plain string
    GROWTH = "GROWTH"


def test_the_order_is_the_generators_own_order():
    """If signal-engine ever persists horizons in another order (or adds INTRADAY), the cycle
    is back. Read from the literal generate_all_signals() returns, not from a copy."""
    src = (ROOT / "services/signal-engine/src/generators/signals.py").read_text()
    fn = next(n for n in ast.parse(src).body
              if isinstance(n, ast.FunctionDef) and n.name == "generate_all_signals")
    ret = fn.body[-1]
    assert isinstance(ret, ast.Return) and isinstance(ret.value, ast.DictComp)
    assert tuple(ast.literal_eval(ret.value.generators[0].iter)) == GENERATION_HORIZON_ORDER


def test_stock_then_generation_rank_regardless_of_id_spelling_or_input_order():
    # ids deliberately anti-correlated with horizon rank, as on old/migrated rows
    rows = [(10, 2, "SHORT"), (1, 1, "GROWTH"), (2, 1, "LONG"), (3, 1, "SWING"), (4, 1, "SHORT"),
            (9, 2, _Horizon.GROWTH)]
    assert canonical_write_order(rows) == [4, 3, 2, 1, 10, 9]
    assert canonical_write_order(list(reversed(rows))) == [4, 3, 2, 1, 10, 9]


def test_an_unknown_horizon_sorts_last_within_its_stock_instead_of_dropping_the_run():
    assert canonical_write_order([(1, 1, "INTRADAY"), (2, 1, "GROWTH"), (3, 1, "SHORT")]) == [3, 2, 1]


def test_the_real_sender_writes_through_the_ordered_helper_only():
    src = (ROOT / "services/market-data/src/services/scheduler.py").read_text()
    fn = next(n for n in ast.parse(src).body
              if isinstance(n, ast.FunctionDef) and n.name == "check_signal_alerts")
    body = ast.unparse(fn)
    assert "_persist_ai_delivery(session, Signal, _ai_delivery" in body
    assert "_ai_delivery.items()" not in body, "an insertion-order write loop is the deadlock"
    assert "alert_delivery_state=" not in body
