"""Exercise the REAL init_db/_ddl_begin/_startup_schema_lock against a recording engine.

Loaded by path in a subprocess because this suite's conftest stubs `db.session` and
`sqlalchemy` as MagicMocks, and a MagicMock accepts every assertion made of it. The point here
is to run the shipped code and watch what SQL it actually issues.
"""
import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

root = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(root / 'shared')]

# get_settings() is read at import time to build the engine. A sqlite URL keeps create_engine
# from needing a driver; the Postgres-specific branches are driven by the fake engine below.
cfg = types.ModuleType('common.config')
cfg.get_settings = lambda: types.SimpleNamespace(database_url='postgresql+psycopg2://u:p@h:5432/d', admin_password='')
common = types.ModuleType('common'); common.__path__ = [str(root / 'shared/common')]
sys.modules.setdefault('common', common)
sys.modules['common.config'] = cfg

db = types.ModuleType('db'); db.__path__ = [str(root / 'shared/db')]
sys.modules['db'] = db
for name in ('ddl_guard', 'models', 'session'):
    spec = importlib.util.spec_from_file_location(f'db.{name}', root / f'shared/db/{name}.py')
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f'db.{name}'] = mod
    spec.loader.exec_module(mod)
    setattr(db, name, mod)

ddl_guard = sys.modules['db.ddl_guard']
session = sys.modules['db.session']

failures = []
def check(label, cond, detail=''):
    if cond:
        print(f'  ok   {label}')
    else:
        failures.append(label)
        print(f'  FAIL {label} {detail}')


class LockTimeout(Exception):
    """Stands in for psycopg2's lock_not_available, carrying the SQLSTATE the real one does."""
    def __init__(self):
        super().__init__('canceling statement due to lock timeout')
        self.orig = types.SimpleNamespace(pgcode='55P03')


class FakeConn:
    def __init__(self, log, lock_results, params):
        self.log, self.lock_results, self.params = log, lock_results, params
    def execute(self, stmt, params=None):
        text = str(stmt)
        self.log.append(text)
        self.params.append(params)
        if 'pg_try_advisory_lock' in text:
            return MagicMock(scalar=lambda: self.lock_results.pop(0) if self.lock_results else True)
        return MagicMock(scalar=lambda: None, rowcount=1)
    def execution_options(self, **kw):
        self.log.append(f'EXECUTION_OPTIONS {sorted(kw.items())}')
        return self
    def close(self):
        self.log.append('CLOSE')
    def __enter__(self):
        return self
    def __exit__(self, *a):
        self.log.append('COMMIT')
        return False


class FakeEngine:
    def __init__(self, lock_results=None):
        self.log = []
        self.params = []
        self.lock_results = list(lock_results or [])
        self.dialect = types.SimpleNamespace(name='postgresql')
    def connect(self):
        return FakeConn(self.log, self.lock_results, self.params)
    def begin(self):
        return FakeConn(self.log, self.lock_results, self.params)


def with_engine(eng):
    session.engine = eng


# ---------------------------------------------------------------- 1. lock wraps the DDL
eng = FakeEngine(); with_engine(eng)
session._create_schema = lambda: eng.log.append('SCHEMA')
session.init_db()
locked = [i for i, s in enumerate(eng.log) if 'pg_try_advisory_lock' in s]
schema = [i for i, s in enumerate(eng.log) if s == 'SCHEMA']
unlock = [i for i, s in enumerate(eng.log) if 'pg_advisory_unlock' in s]
check('schema DDL runs between advisory lock and unlock',
      locked and schema and unlock and locked[0] < schema[0] < unlock[0], eng.log)
lock_params = [p for s_, p in zip(eng.log, eng.params) if 'advisory' in s_]
check('lock and unlock are bound to the one shared key, so all 12 services contend',
      lock_params and all(p and p.get('k') == ddl_guard.MIGRATION_LOCK_KEY for p in lock_params),
      lock_params)

# ---------------------------------------------------------------- 2. DDL txns set timeouts
eng = FakeEngine(); with_engine(eng)
with session._ddl_begin() as conn:
    conn.execute('ALTER TABLE t ADD COLUMN c INT')
issued = eng.log
check('every DDL transaction sets lock_timeout before its first statement',
      any('lock_timeout' in s for s in issued)
      and issued.index(next(s for s in issued if 'lock_timeout' in s))
      < issued.index('ALTER TABLE t ADD COLUMN c INT'), issued)
check('every DDL transaction sets idle_in_transaction_session_timeout',
      any('idle_in_transaction_session_timeout' in s for s in issued), issued)
check('the DDL timeouts are SET LOCAL, not session-wide',
      all(s.startswith('SET LOCAL') for s in issued if 'timeout' in s), issued)

# ---------------------------------------------------------------- 3. contention retries, then fails
eng = FakeEngine(); with_engine(eng)
attempts = []
def boom():
    attempts.append(1)
    raise LockTimeout()
session._create_schema = boom
session.time = types.SimpleNamespace(monotonic=lambda: 0.0, sleep=lambda s: None)
raised = None
try:
    session.init_db()
except Exception as exc:
    raised = exc
check('a contended migration is retried', len(attempts) > 1, attempts)
check('an unresolvable contention ends as a startup failure, not a silent return',
      isinstance(raised, LockTimeout), raised)

# ---------------------------------------------------------------- 4. real errors are not retried
eng = FakeEngine(); with_engine(eng)
attempts = []
def wrong():
    attempts.append(1)
    raise ValueError('column "nope" does not exist')
session._create_schema = wrong
raised = None
try:
    session.init_db()
except Exception as exc:
    raised = exc
check('a genuine schema error is raised immediately, not retried',
      isinstance(raised, ValueError) and len(attempts) == 1, (raised, attempts))

# ---------------------------------------------------------------- 5. lock wait does not fall through
eng = FakeEngine(lock_results=[False] * 50); with_engine(eng)
ran = []
session._create_schema = lambda: ran.append(1)
clock = {'t': 0.0}
def monotonic():
    clock['t'] += 400.0
    return clock['t']
session.time = types.SimpleNamespace(monotonic=monotonic, sleep=lambda s: None)
raised = None
try:
    session.init_db()
except Exception as exc:
    raised = exc
check('exhausting the lock wait raises rather than proceeding unserialized',
      isinstance(raised, TimeoutError), raised)
check('no DDL runs when the lock was never acquired', ran == [], ran)

# ---------------------------------------------------------------- 6. app connections get the timeout
opts = ddl_guard.connect_options('postgresql+psycopg2://u:p@h:5432/d')
check('every app connection carries an idle-in-transaction timeout',
      'idle_in_transaction_session_timeout' in opts.get('options', ''), opts)
check('a non-Postgres URL gets no Postgres-only connect options',
      ddl_guard.connect_options('sqlite:///x.db') == {}, ddl_guard.connect_options('sqlite:///x.db'))

print('FAILURES:', failures)
sys.exit(1 if failures else 0)
