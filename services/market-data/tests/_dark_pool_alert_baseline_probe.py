"""AUD-DP-TAB-BASELINE: the per-alert baseline table on real SQLAlchemy. Optional --postgres-temp
uses TEMP tables and rolls back all DDL. No production rows are read."""
import json
import sys
from datetime import date
from pathlib import Path

probe = Path(__file__).resolve()
if len(probe.parents) > 3:
    root = probe.parents[3]
    sys.path[:0] = [str(root / 'shared'), str(root / 'services/market-data')]
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from src.services import dark_pool_alert_baseline as bl

postgres = '--postgres-temp' in sys.argv
if postgres:
    from common.config import get_settings
    url = get_settings().database_url
else:
    url = 'sqlite://'
engine = create_engine(url)
D = date(2026, 11, 10)
RELATIVE = {"baseline_status": "ok", "baseline_median": 410000.0, "baseline_prints": 640,
            "baseline_window_days": 14, "vs_baseline": 12.3}
FLOOR = {"baseline_status": "query_failed", "baseline_median": None, "baseline_prints": None,
         "baseline_window_days": 14, "vs_baseline": None}

results = {}
with engine.connect() as connection:
    transaction = connection.begin()
    if postgres:
        connection = connection.execution_options(schema_translate_map={None: 'pg_temp'})
        connection.exec_driver_sql("SET LOCAL statement_timeout = '10s'")
    with Session(bind=connection, join_transaction_mode='create_savepoint') as session:
        # Before the table exists: an empty answer, not an error.
        assert bl.lookup(session, [(1, D)]) == {}
    bl.metadata.create_all(connection, checkfirst=False)
    try:
        with Session(bind=connection, join_transaction_mode='create_savepoint') as session:
            assert bl.record(session, 1, D, RELATIVE) is True
            assert bl.record(session, 1, D, FLOOR) is False            # first fire of the day stands
            assert bl.record(session, 2, D, FLOOR) is True
            got = bl.lookup(session, [(1, D), (2, D), (3, D)])
            assert set(got) == {(1, D), (2, D)}
            assert got[(1, D)]["qualified_by"] == "floor_and_relative" and got[(1, D)]["vs_baseline"] == 12.3
            assert got[(2, D)]["qualified_by"] == "floor_only" and got[(2, D)]["baseline_status"] == "query_failed"
            n = session.execute(select(func.count()).select_from(bl.baselines)).scalar()
            results = {"rows": n, "database": "postgresql-temp" if postgres else "sqlite"}
    finally:
        transaction.rollback()
engine.dispose()
print('DP_BASELINE_PROBE_JSON=' + json.dumps(results, sort_keys=True))
