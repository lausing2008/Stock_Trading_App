"""Real SQL probe for the options desk record (Part B). Optional --postgres-temp uses TEMP tables
and rolls back all DDL. No production rows are read.

Run by tests/test_options_desk_record.py in a subprocess, because the unit suite's conftest stubs
SQLAlchemy.
"""
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
from src.services import options_desk_record as desk

postgres = '--postgres-temp' in sys.argv
if postgres:
    from common.config import get_settings
    url = get_settings().database_url
else:
    url = 'sqlite://'
engine = create_engine(url)
TODAY = date(2026, 1, 2)


def rec(stop=95.0, shortlist_marker='a'):
    # The stored content is opaque to persist(); only the key and the insert rule matter here.
    inputs = {'stop_loss': stop}
    shortlist = {'marker': shortlist_marker}
    code = desk.code_fingerprint()['sha256']
    return {'record_date': TODAY, 'symbol': 'TESTAUDIT', 'record_version': desk.RECORD_VERSION,
            'code_fingerprint': code, 'input_hash': desk._digest(inputs), 'chain_session': TODAY.isoformat(),
            'inputs': inputs, 'shortlist': shortlist,
            'record_hash': desk._digest({'code': code, 'inputs': inputs, 'shortlist': shortlist})}


results = {}
with engine.connect() as connection:
    transaction = connection.begin()
    if postgres:
        connection = connection.execution_options(schema_translate_map={None: 'pg_temp'})
        connection.exec_driver_sql("SET LOCAL statement_timeout = '10s'")
    desk.metadata.create_all(connection, checkfirst=False)
    try:
        with Session(bind=connection, join_transaction_mode='create_savepoint') as session:
            count = lambda: session.execute(select(func.count()).select_from(desk.records)).scalar()
            assert desk.persist(session, rec()) is True
            first = session.execute(select(desk.records.c.recorded_at)).scalar()
            assert desk.persist(session, rec()) is False            # a repeat files nothing
            assert count() == 1
            assert session.execute(select(desk.records.c.recorded_at)).scalar() == first   # never rewritten
            assert desk.persist(session, rec(stop=90.0)) is True    # different input: beside
            assert desk.persist(session, rec(shortlist_marker='b')) is True   # different result: beside
            assert count() == 3
            results = {'rows': count(), 'repeat_filed_nothing': True, 'never_rewritten': True,
                       'database': 'postgresql-temp' if postgres else 'sqlite', 'outer_rollback': True}
    finally:
        transaction.rollback()
    if postgres:
        assert connection.exec_driver_sql("SELECT to_regclass('pg_temp.options_desk_records_v1')").scalar() is None
        connection.rollback()
engine.dispose()
print('DESK_RECORD_PROBE_JSON=' + json.dumps(results, sort_keys=True))
