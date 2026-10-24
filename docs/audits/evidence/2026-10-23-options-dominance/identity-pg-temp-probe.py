from pathlib import Path
root=Path(__file__).resolve().parents[4]
diagnostic={'__name__':'identity_report'}
exec(compile((root/'scripts/dark_pool_identity_report.py').read_text(),'identity_report','exec'),diagnostic)
import json
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
from sqlalchemy import text
from sqlalchemy.orm import Session
from db import SessionLocal
from src.services import dark_pool_ledger as ledger

def probe():
    now=datetime.now(timezone.utc)
    def row(identity,price=100,bid=99):
        return SimpleNamespace(tracking_id=identity,observed_at=now.isoformat(),
            executed_at=(now-timedelta(minutes=1)).isoformat(),price=price,size=100,premium=price*100,
            canceled=False,nbbo_bid=bid,nbbo_ask=101,venue='T')
    with SessionLocal() as base:
        engine=base.get_bind()
    with engine.connect() as conn:
        tx=conn.begin()
        conn.exec_driver_sql("SET LOCAL statement_timeout = '10s'")
        conn=conn.execution_options(schema_translate_map={None:'pg_temp'})
        try:
            ledger.metadata.create_all(conn,checkfirst=False)
            conn.exec_driver_sql('SET LOCAL search_path TO pg_temp')
            assert conn.exec_driver_sql("SELECT relpersistence FROM pg_class WHERE oid=to_regclass('dark_pool_events_v2')").scalar()=='t'
            with Session(bind=conn,join_transaction_mode='create_savepoint') as session:
                ledger.persist(session,'TESTIDENTITY',[row('quote'),row('quote',bid=98),row('price'),row('price',price=101),row('ordinary')])
                result=diagnostic['report'](session,days=1,now=now)
                c=result['counts']
                assert c['ambiguous_ids_in_window']==c['ids_reviewed']==2
                assert c['quote_only_variant_ids']==c['trade_field_variant_ids']==1
                assert c['price_variant_ids']==1 and c['retained_versions_reviewed']==4
                limited=diagnostic['report'](session,days=1,limit=1,now=now)
                assert limited['limited'] and limited['counts']['ids_reviewed']==1
                empty=diagnostic['report'](session,symbol='NONE',days=1,now=now)
                assert empty['counts']['ids_reviewed']==0
        finally:
            tx.rollback()
        assert conn.exec_driver_sql("SELECT to_regclass('pg_temp.dark_pool_events_v2')").scalar() is None
        conn.rollback()
    print(json.dumps({'postgres_temp_probe':'passed','counts':c,'limit_and_empty_filter':'passed','temporary_tables_only':True,'outer_rollback':True},sort_keys=True))
try:
    probe()
except Exception as exc:
    print(json.dumps({'postgres_temp_probe':'failed','error_type':type(exc).__name__}))
    raise SystemExit(1)
