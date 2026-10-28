"""Disposable local PostgreSQL witness. Never connects to production or edits application code.

Uses real Stock/Signal models, the shipped bulk-upsert SQL literal and the shipped delivery
UPDATE AST. Minimal two-writer transaction schedules exclude F03 hooks/network calls to isolate
the existing row-lock cycle. Ordered control changes only the fixture delivery-ID order.
"""
import ast
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
from sqlalchemy import create_engine, text, update

ROOT = Path(__file__).resolve().parents[4]
CONTAINER = 'stockai-deadlock-audit-20261027'
inspect = json.loads(subprocess.check_output(['docker', 'inspect', CONTAINER], text=True))[0]
assert inspect['Config']['Labels'].get('stockai.audit') == 'signals-deadlock'
port = inspect['NetworkSettings']['Ports']['5432/tcp'][0]
assert port['HostIp'] == '127.0.0.1'
engine = create_engine(f"postgresql+psycopg2://postgres@127.0.0.1:{port['HostPort']}/postgres", pool_size=5)
for attempt in range(60):
    try:
        with engine.connect() as c:
            version = c.scalar(text('SHOW server_version'))
            assert version.startswith('16.')
            assert c.scalar(text("SELECT count(*) FROM pg_tables WHERE schemaname='public'")) == 0
        break
    except AssertionError:
        raise
    except Exception:
        if attempt == 59:
            raise
        time.sleep(.2)

spec = importlib.util.spec_from_file_location('deadlock_audit_models', ROOT/'shared/db/models.py')
models = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = models
spec.loader.exec_module(models)
models.Base.metadata.create_all(engine, tables=[models.Stock.__table__, models.Signal.__table__])
with engine.begin() as c:
    c.execute(text("CREATE UNIQUE INDEX uq_signals_stock_horizon_day ON signals (stock_id,horizon,date_trunc('day',ts))"))
    c.execute(models.Stock.__table__.insert().values(id=1, symbol='DEADLOCK_FIXTURE', name='Disposable fixture',
                                                    market=models.Market.US, exchange=models.Exchange.NASDAQ))

routes = ROOT/'services/signal-engine/src/api/routes.py'
scheduler = ROOT/'services/market-data/src/services/scheduler.py'
bulk = next(n for n in ast.parse(routes.read_text()).body if isinstance(n, ast.FunctionDef) and n.name == '_bulk_persist')
upsert = next(n.value for n in ast.walk(bulk) if isinstance(n, ast.Constant) and isinstance(n.value, str)
              and 'INSERT INTO signals' in n.value and 'ON CONFLICT' in n.value)
alerts = next(n for n in ast.parse(scheduler.read_text()).body if isinstance(n, ast.FunctionDef) and n.name == 'check_signal_alerts')
loop = next(n for n in ast.walk(alerts) if isinstance(n, ast.For) and ast.unparse(n.iter) == '_ai_delivery.items()')
code = compile(ast.Module(body=[loop], type_ignores=[]), str(scheduler), 'exec')
ORDER = ('SHORT', 'SWING', 'LONG', 'GROWTH')


def persist(c, horizon, confidence):
    return c.execute(text(upsert), dict(sid=1, sig='BUY' if confidence == 99 else 'HOLD', hor=horizon,
                                      conf=confidence, bp=.8 if confidence == 99 else .5, rsns='{}', src='audit')).scalar()


def deliver(c, row_id):
    # The exact loop/UPDATE builder, with only its normally external inputs supplied.
    ns = {'session': c, 'Signal': models.Signal, 'update': update,
          '_ai_delivery': {row_id: {'state': 'candidate', 'recipients': 0}},
          '_delivery_now': datetime.now(timezone.utc), '_AI_ALERT_CONTRACT_VERSION': 'ai-alert-1'}
    exec(code, ns)


def trial(inverted):
    with engine.begin() as c:
        c.execute(text('TRUNCATE signals RESTART IDENTITY'))
        ids = {h: persist(c, h, 40) for h in ORDER}
    ready_a, ready_b, release_a = threading.Event(), threading.Event(), threading.Event()
    pids, result = {}, {}
    trace = []

    def writer_a():
        with engine.connect() as c:
            try:
                c.execute(text("SET LOCAL deadlock_timeout='100ms'"))
                c.execute(text("SET LOCAL lock_timeout='6s'"))
                c.execute(text("SET LOCAL statement_timeout='8s'"))
                pids['upsert'] = c.scalar(text('SELECT pg_backend_pid()'))
                persist(c, 'SHORT', 99)
                trace.append('upsert holds SHORT')
                ready_a.set()
                assert release_a.wait(8), 'Monitor did not establish the competing lock wait'
                for h in ORDER[1:]:
                    persist(c, h, 99)
                c.commit()
                result['upsert'] = {'status': 'committed'}
            except Exception as exc:
                c.rollback()
                result['upsert'] = {'status': 'rolled_back', 'sqlstate': getattr(getattr(exc, 'orig', None), 'pgcode', None),
                                    'error_type': type(exc).__name__}

    def writer_b():
        assert ready_a.wait(8)
        with engine.connect() as c:
            try:
                c.execute(text("SET LOCAL deadlock_timeout='5s'"))
                c.execute(text("SET LOCAL lock_timeout='6s'"))
                c.execute(text("SET LOCAL statement_timeout='8s'"))
                pids['delivery'] = c.scalar(text('SELECT pg_backend_pid()'))
                if inverted:
                    deliver(c, ids['GROWTH'])
                    trace.append('delivery holds GROWTH')
                ready_b.set()
                deliver(c, ids['SHORT'])
                if not inverted:
                    deliver(c, ids['GROWTH'])
                c.commit()
                result['delivery'] = {'status': 'committed'}
            except Exception as exc:
                c.rollback()
                result['delivery'] = {'status': 'rolled_back', 'sqlstate': getattr(getattr(exc, 'orig', None), 'pgcode', None),
                                      'error_type': type(exc).__name__}

    threads = [threading.Thread(target=writer_a, daemon=True), threading.Thread(target=writer_b, daemon=True)]
    for t in threads:
        t.start()
    assert ready_b.wait(8), 'Delivery writer did not start'
    blocking = []
    with engine.connect().execution_options(isolation_level='AUTOCOMMIT') as monitor:
        until = time.monotonic()+5
        while time.monotonic() < until:
            blocking = monitor.scalar(text('SELECT pg_blocking_pids(:pid)'), {'pid': pids['delivery']})
            if pids['upsert'] in blocking:
                break
            time.sleep(.01)
    try:
        assert pids['upsert'] in blocking, 'Threads serialized: this is not a valid race witness'
        trace.append('server confirms delivery waits for upsert SHORT lock')
    finally:
        release_a.set()
    for t in threads:
        t.join(10)
        assert not t.is_alive(), 'Writer hung'
    with engine.connect() as c:
        rows = [dict(r) for r in c.execute(text('SELECT horizon::text,confidence,alert_delivery_state FROM signals ORDER BY horizon')).mappings()]
    deadlocks = [role for role, r in result.items() if r.get('sqlstate') == '40P01']
    if inverted:
        # Unequal detector timeouts make upsert the expected victim in this controlled test.
        # Production victim selection is not guaranteed by PostgreSQL.
        assert deadlocks == ['upsert'], result
        assert result['delivery']['status'] == 'committed'
        assert all(r['confidence'] == 40 for r in rows), rows
    else:
        assert not deadlocks and all(r['status'] == 'committed' for r in result.values()), result
        assert all(r['confidence'] == 99 for r in rows), rows
    return {'delivery_order': ['GROWTH','SHORT'] if inverted else ['SHORT','GROWTH'],
            'overlap_verified_by_server': True, 'trace': trace, 'writers': result,
            'all_four_horizon_confidences': {r['horizon']:r['confidence'] for r in rows},
            'upsert_batch_lost': all(r['confidence'] == 40 for r in rows)}


report = {'postgres_version': version, 'container': CONTAINER,
          'source_sha256': {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in
                            (routes,scheduler,ROOT/'shared/db/models.py')},
          'method': 'Real models and unique index, actual production upsert SQL and alert UPDATE AST; two local transactions; pg_blocking_pids must prove overlap. F03 hooks and external side effects excluded.',
          'inverted_order': [trial(True) for _ in range(3)],
          'matching_order_control': [trial(False) for _ in range(3)],
          'limitations': 'Minimal mechanism witness, not an end-to-end scheduler test or proof of a production fix. No application changes applied. Production can select either transaction as deadlock victim.'}
engine.dispose()
print(json.dumps(report, indent=2))
