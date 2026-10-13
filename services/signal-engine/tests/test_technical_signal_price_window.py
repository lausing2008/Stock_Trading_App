"""Exercise both services' actual price-window code against SQLite.

Extract only the query and row preparation to avoid importing service startup,
schedulers, and external integrations into this database regression test.
"""
import ast
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import Column, Float, Integer, MetaData, String, Table, create_engine, select
from sqlalchemy.orm import Session


ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("path,function", [
    ("services/signal-engine/src/api/routes.py", "detect_patterns"),
    ("services/market-data/src/services/scheduler.py", "check_technical_alerts"),
])
@pytest.mark.parametrize("history_size", [0, 30, 260, 400])
def test_latest_daily_window_is_chronological(path, function, history_size):
    tree = ast.parse((ROOT / path).read_text())
    function_node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == function)
    # Locate the real price fetch and all row preparation before the length guard.
    blocks = [getattr(n, "body", []) for n in ast.walk(function_node)]
    statements = None
    for block in blocks:
        for index, node in enumerate(block):
            if (isinstance(node, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == "rows" for t in node.targets)
                    and "session.execute" in ast.unparse(node.value)):
                end = next(i for i in range(index + 1, len(block)) if isinstance(block[i], ast.If))
                statements = block[index:end]
                break
        if statements is not None:
            break
    assert statements is not None, "Price-window query not found"

    metadata = MetaData()
    prices = Table("prices", metadata,
        Column("stock_id", Integer), Column("timeframe", String),
        Column("ts", Integer), Column("close", Float),
        Column("high", Float), Column("low", Float), Column("volume", Float))
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    records = [dict(stock_id=1, timeframe="D1", ts=i, close=float(i),
                    high=i + 1.0, low=i - 1.0, volume=i * 10.0)
               for i in range(history_size)]
    # Newer unrelated bars must not displace this stock's daily history.
    records.extend([
        dict(stock_id=2, timeframe="D1", ts=9999, close=9999, volume=1),
        dict(stock_id=1, timeframe="H1", ts=9999, close=9999, volume=1),
    ])
    with Session(engine) as session:
        for record in reversed(records):
            session.execute(prices.insert().values(**record))
        namespace = dict(session=session, select=select, Price=prices.c,
                         stock=SimpleNamespace(id=1), TimeFrame=SimpleNamespace(D1="D1"))
        exec(compile(ast.Module(body=statements, type_ignores=[]), path, "exec"), namespace)
        rows = namespace["rows"]
        expected = list(range(max(0, history_size - 260), history_size))
        assert [r.ts for r in rows] == expected
        assert [r.close for r in rows] == expected
        assert [r.volume for r in rows] == [i * 10 for i in expected]
    engine.dispose()
