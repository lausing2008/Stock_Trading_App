"""Does the options-chain fetch hold a database transaction across the provider call?

Checked with a real SQLite session and `session.in_transaction()`, because that is the thing
production reported: connections sitting `idle in transaction` for 30-35 seconds per symbol
while the process waited on Unusual Whales.
"""
import importlib.util
import sys
import types
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

root = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(root / 'shared'), str(root / 'services/market-data')]

common = types.ModuleType('common'); common.__path__ = [str(root / 'shared/common')]
sys.modules.setdefault('common', common)
cfg = types.ModuleType('common.config')
cfg.get_settings = lambda: types.SimpleNamespace(database_url='sqlite://', admin_password='')
sys.modules['common.config'] = cfg
_log = types.ModuleType('common.logging'); _log.get_logger = lambda *a, **k: MagicMock()
sys.modules['common.logging'] = _log

db = types.ModuleType('db'); db.__path__ = [str(root / 'shared/db')]
sys.modules['db'] = db
spec = importlib.util.spec_from_file_location('db.models', root / 'shared/db/models.py')
models = importlib.util.module_from_spec(spec); sys.modules['db.models'] = models
spec.loader.exec_module(models)
for n in ('OptionChainHistory', 'OptionChainCoverage', 'Stock', 'User', 'Market', 'Exchange',
          'Base'):
    setattr(db, n, getattr(models, n))

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

import src.services.uw_option_chain as uw  # noqa: E402

engine = create_engine('sqlite://')
models.Base.metadata.create_all(engine, tables=[
    models.OptionChainHistory.__table__, models.OptionChainCoverage.__table__,
    models.User.__table__,
])

failures = []
def check(label, cond, detail=''):
    print(('  ok   ' if cond else '  FAIL ') + label + ('' if cond else f'  {detail}'))
    if not cond:
        failures.append(label)


# A stub scheduler: the provider call. It records whether a transaction was open when it ran.
observed = {}
sched = types.ModuleType('src.services.scheduler')
def _capture(symbols, start, end, skip_existing=True):
    observed['in_transaction_during_provider_call'] = SESSION.in_transaction()
    return {"captured": 0, "skipped": 0}
sched.capture_option_chain_history = _capture
sys.modules['src.services.scheduler'] = sched

SESSION = Session(engine)
# A SELECT opens a transaction and leaves it open — the premise of the whole finding.
SESSION.execute(text("SELECT 1")).all()
check('a plain SELECT leaves a transaction open', SESSION.in_transaction() is True)

# get_chain finds nothing archived, so it takes the fetch path.
uw.get_chain(SESSION, 'NVDA', allow_fetch=True)
check('the provider call ran', 'in_transaction_during_provider_call' in observed, observed)
check('NO TRANSACTION IS HELD ACROSS THE PROVIDER CALL',
      observed.get('in_transaction_during_provider_call') is False, observed)
SESSION.close()

# A caller with pending work keeps its transaction: tidying up a read must never discard
# somebody else's uncommitted changes.
s2 = Session(engine)
s2.add(models.User(id=1, username='pending', password_hash='x', email='p@example.com',
                   is_active=True))
released = uw._release_read_transaction(s2, symbol='NVDA')
check('a session with pending changes is NOT rolled back', released is False)
check('...and its pending object survives', any(o.username == 'pending' for o in s2.new))
s2.close()

# A clean read-only session is released.
s3 = Session(engine)
s3.execute(text("SELECT 1")).all()
check('a clean read-only session is released',
      uw._release_read_transaction(s3, symbol='NVDA') is True and s3.in_transaction() is False)
s3.close()

print('FAILURES:', failures)
sys.exit(1 if failures else 0)
