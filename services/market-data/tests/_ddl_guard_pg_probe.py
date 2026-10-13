"""Startup DDL under a REAL lock, on PostgreSQL. Covers what the fake-engine probe cannot.

AQ-REVIEW-4 (2026-10-12). The fake-engine probe records the statements `_ddl_begin()` issues,
which establishes that the timeout is SET — and says nothing about whether the statement that
actually blocks runs on that connection. `create_all()` used to run on its own connection, so
the one call most likely to wait on another client's lock had `lock_timeout = 0`. Only a real
server, with a real conflicting lock, distinguishes those two situations.

Needs a disposable PostgreSQL at $DDL_GUARD_PG_URL. Skips (exit 0) when absent.
This probe DROPS AND RECREATES the public schema — throwaway databases only.
"""
import importlib.util
import json
import os
import pathlib
import sys
import threading
import time
import types
from unittest.mock import MagicMock

_URL = os.environ.get("DDL_GUARD_PG_URL")
if not _URL:
    print(json.dumps({"skipped": True, "reason": "DDL_GUARD_PG_URL not set"}))
    sys.exit(0)

root = pathlib.Path(__file__).resolve().parents[3]
sys.path[:0] = [str(root / 'shared')]

from sqlalchemy import create_engine, text  # noqa: E402

common = types.ModuleType('common'); common.__path__ = [str(root / 'shared/common')]
sys.modules.setdefault('common', common)
cfg = types.ModuleType('common.config')
cfg.get_settings = lambda: types.SimpleNamespace(database_url=_URL, admin_password='')
sys.modules['common.config'] = cfg
_log = types.ModuleType('common.logging'); _log.get_logger = lambda *a, **k: MagicMock()
sys.modules['common.logging'] = _log

db = types.ModuleType('db'); db.__path__ = [str(root / 'shared/db')]
sys.modules['db'] = db
for name in ('ddl_guard', 'models', 'session'):
    spec = importlib.util.spec_from_file_location(f'db.{name}', root / f'shared/db/{name}.py')
    mod = importlib.util.module_from_spec(spec); sys.modules[f'db.{name}'] = mod
    spec.loader.exec_module(mod); setattr(db, name, mod)
ddl_guard = sys.modules['db.ddl_guard']
session_mod = sys.modules['db.session']
models = sys.modules['db.models']

failures = []
def check(label, cond, detail=''):
    print(('  ok   ' if cond else '  FAIL ') + label + ('' if cond else f'  {detail}'))
    if not cond:
        failures.append(label)

admin = create_engine(_URL)
with admin.begin() as c:
    c.execute(text("DROP SCHEMA public CASCADE")); c.execute(text("CREATE SCHEMA public"))
with admin.begin() as c:
    c.execute(text("CREATE TABLE lock_victim (id integer primary key)"))

# 1. The DDL transaction really carries the timeout, as the server reports it.
with session_mod._ddl_begin() as conn:
    got = conn.execute(text("SHOW lock_timeout")).scalar()
check('a DDL transaction carries lock_timeout on the server', got == '15s', got)

# 2. THE ONE THAT MATTERS: call the REAL `_create_schema()` and make its create_all block.
#
#    An earlier version of this probe called `create_all` itself and asserted on that. It
#    passed with the defect reinstated, because reverting the shipped function did not change
#    what the probe called — the trap of a test that re-implements the thing it is testing. The
#    "contention" it observed was the ALTER on the next line, which was already inside
#    `_ddl_begin()` and was never the question.
#
#    The parent table exists; the children do not. Locking the parent ACCESS EXCLUSIVE means
#    create_all's CREATE TABLE ... REFERENCES users must take a lock on it and will wait. With
#    create_all bound to the timed connection it gives up; unbound, it waits indefinitely while
#    holding the startup advisory lock, which is the convoy one layer down.
with session_mod._ddl_begin() as conn:
    models.Base.metadata.create_all(bind=conn, tables=[models.User.__table__])
with admin.connect() as c:
    parent_only = c.execute(text("SELECT to_regclass('public.alert_preferences')")).scalar()
check('the child tables are absent, so create_all has real work to do', parent_only is None,
      parent_only)

holder_ready = threading.Event()
release = threading.Event()

def hold_lock():
    with admin.begin() as c:
        c.execute(text("LOCK TABLE users IN ACCESS EXCLUSIVE MODE"))
        holder_ready.set()
        release.wait(120)

t = threading.Thread(target=hold_lock, daemon=True); t.start()
holder_ready.wait(20)

BUDGET = (ddl_guard.LOCK_TIMEOUT_MS / 1000.0) + 20
result = {}

def run_create_schema():
    started = time.monotonic()
    try:
        session_mod._create_schema()
        result['outcome'], result['err'] = 'completed', None
    except Exception as exc:  # noqa: BLE001
        result['outcome'], result['err'] = 'raised', exc
    result['elapsed'] = time.monotonic() - started

worker = threading.Thread(target=run_create_schema, daemon=True)
worker.start()
worker.join(BUDGET)
hung = worker.is_alive()
release.set()
if hung:
    worker.join(30)
t.join(5)

check('contended startup DDL stops instead of waiting indefinitely',
      not hung and result.get('outcome') == 'raised',
      'still running when the budget expired' if hung else result.get('outcome'))
check('...and stops because of OUR lock_timeout, not by chance',
      result.get('err') is not None and ddl_guard.is_lock_contention(result['err']),
      repr(result.get('err'))[:160])
check('...within the configured bound',
      result.get('elapsed', 1e9) < BUDGET, f"{result.get('elapsed', -1):.1f}s")

# 3. Once the holder is gone the same call succeeds — the timeout bounds waiting, not progress.
session_mod._create_schema()
with admin.connect() as c:
    present = c.execute(text("SELECT to_regclass('public.alert_preferences')")).scalar()
check('the same DDL succeeds once the lock is released', present == 'alert_preferences', present)

print('FAILURES:', failures)
sys.exit(1 if failures else 0)
