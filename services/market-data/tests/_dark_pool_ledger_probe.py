"""Real SQL ledger probe. Optional --postgres-temp uses TEMP tables and rolls back all DDL.

No production rows are read. The real application's database URL stays inside the process.
"""
import json
import sys
from pathlib import Path
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace as NS
probe=Path(__file__).resolve()
if len(probe.parents)>3:
    root=probe.parents[3]
    sys.path[:0]=[str(root/'shared'),str(root/'services/market-data')]
# Container images already set PYTHONPATH=/app/shared:/app.
from sqlalchemy import create_engine,select,func
from sqlalchemy.orm import Session
from src.services import dark_pool_ledger as ledger

postgres='--postgres-temp' in sys.argv
if postgres:
    from common.config import get_settings
    url=get_settings().database_url
else:
    url='sqlite://'
engine=create_engine(url)
results={}
clock=datetime(2026,10,23,14,tzinfo=timezone.utc)
def row(identity,step=0,price=100,canceled=False,**kwargs):
    return NS(tracking_id=identity,observed_at=(clock+timedelta(seconds=step)).isoformat(),
              executed_at='2026-10-23T13:45:00Z',price=price,size=100,premium=None,
              canceled=canceled,nbbo_bid=99,nbbo_ask=101,venue='L',**kwargs)
with engine.connect() as connection:
    transaction=connection.begin()
    if postgres:
        # Each table is session-temporary, and outer rollback also removes the DDL.
        connection=connection.execution_options(schema_translate_map={None:'pg_temp'})
    if postgres:
        connection.exec_driver_sql("SET LOCAL statement_timeout = '10s'")
    ledger.metadata.create_all(connection,checkfirst=False)
    try:
        with Session(bind=connection,join_transaction_mode='create_savepoint') as session:
            write=lambda rows: ledger.persist(session,'TESTAUDIT',rows)
            current=lambda identity:session.execute(select(ledger.events).where(ledger.events.c.tracking_id==identity)).mappings().one()
            count=lambda table:session.execute(select(func.count()).select_from(table)).scalar()
            write([row('a'),row('b')])
            assert count(ledger.events)==2 # identical economic tuples, distinct provider IDs
            write([row('a'),row('b')])
            assert count(ledger.versions)==2
            write([row('a',step=10,price=101)])
            assert count(ledger.events)==2 and count(ledger.versions)==3 and current('a')['price']==101
            write([row('a',step=5,price=98)])
            assert current('a')['price']==101
            # Identical later state still advances the capture watermark.
            write([row('a',step=30,price=101)])
            write([row('a',step=20,price=99)])
            assert current('a')['price']==101
            write([row('a',step=40,price=0,canceled=True)])
            assert current('a')['canceled'] and current('a')['price'] is None
            write([row('a',step=35,price=102)])
            assert current('a')['canceled']
            write([row('b',step=10,price=102),row('b',step=10,price=103)])
            assert current('b')['ambiguous']
            group=lambda identity:session.execute(select(ledger.id_groups).where(ledger.id_groups.c.tracking_id==identity)).mappings().all()
            # Same response, same ID, same execution time: classified, still quarantined.
            assert [(g['state'],g['row_count'],g['distinct_payloads']) for g in group('b')]==[('multi_row_same_id',2,2)]
            write([row('b',step=12,price=102),row('b',step=12,price=103)])
            assert len(group('b'))==1 and group('b')[0]['last_observed_at']>group('b')[0]['first_observed_at']
            assert current('b')['ambiguous']
            write([row('c',step=10),row('c',step=10,price=0,canceled=True)])
            assert [g['state'] for g in group('c')]==['conflicting_rows'] and current('c')['ambiguous']
            write([row('d',step=10),row('d',step=10)])
            assert group('d')==[] and not current('d')['ambiguous']
            write([row('b',step=50,price=104)])
            assert not current('b')['ambiguous'] and current('b')['price']==104
            assert write([row(None)])==0
            assert write([row('unknown',canceled=None)])==0
            active=session.execute(select(func.count()).select_from(ledger.events).where(ledger.events.c.canceled.is_(False),ledger.events.c.ambiguous.is_(False))).scalar()
            assert active==2
            results={'multi_row_groups':count(ledger.id_groups),'distinct_events':count(ledger.events),'retained_versions':count(ledger.versions),
                     'active_unambiguous':active,'corrections_and_cancellations':True,
                     'stale_capture_cannot_revert':True,'database':'postgresql-temp' if postgres else 'sqlite',
                     'temporary_tables_only':postgres,'outer_rollback':True}
    finally:
        transaction.rollback()
    if postgres:
        assert connection.exec_driver_sql("SELECT to_regclass('pg_temp.dark_pool_events_v2')").scalar() is None
        connection.rollback()
engine.dispose()
print('LEDGER_PROBE_JSON='+json.dumps(results,sort_keys=True))
