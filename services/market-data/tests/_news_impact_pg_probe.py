"""The product-impact producer on PostgreSQL, using the REAL audience gate.

WHY THIS EXISTS SEPARATELY FROM THE SQLITE PROBE. Two reasons, each of which hid a shipped
defect that every green suite missed:

  * SQLite DOES NOT ENFORCE A DECLARED VARCHAR LENGTH. The producer's only `evidence_status`
    value is 38 characters and the column was declared VARCHAR(32), so every recognized event
    failed to persist on PostgreSQL and passed on SQLite. A length constraint that the test
    engine ignores is not tested by that engine at all.
  * The SQLite probe INJECTS a `may_send` lambda. That lambda checked one preference row and
    nothing else — not the registry default, not the email address, not account activity — so
    it agreed with itself rather than with the shipped gate. This probe injects nothing and
    calls the real `alert_audience` functions.

Needs a disposable PostgreSQL at $NEWS_IMPACT_PG_URL. Skips (exit 0) when absent so the suite
stays runnable without Docker. DROPS AND RECREATES the public schema — throwaway databases only.
"""
import importlib.util
import json
import os
import pathlib
import sys
import types
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

_URL = os.environ.get("NEWS_IMPACT_PG_URL")
if not _URL:
    print(json.dumps({"skipped": True, "reason": "NEWS_IMPACT_PG_URL not set"}))
    sys.exit(0)

root = pathlib.Path(__file__).resolve().parents[3]
sys.path[:0] = [str(root / 'shared'), str(root / 'services/market-data')]

from sqlalchemy import create_engine, select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

common = types.ModuleType('common'); common.__path__ = [str(root / 'shared/common')]
sys.modules.setdefault('common', common)
cfg = types.ModuleType('common.config')
cfg.get_settings = lambda: types.SimpleNamespace(database_url=_URL, admin_password='')
sys.modules['common.config'] = cfg
_log = types.ModuleType('common.logging'); _log.get_logger = lambda *a, **k: MagicMock()
sys.modules['common.logging'] = _log

db = types.ModuleType('db'); db.__path__ = [str(root / 'shared/db')]
sys.modules['db'] = db
spec = importlib.util.spec_from_file_location('db.models', root / 'shared/db/models.py')
models = importlib.util.module_from_spec(spec); sys.modules['db.models'] = models
spec.loader.exec_module(models)
for name in ('AlertPreference', 'AppNotification', 'NewsImpactEvent', 'NewsImpactReceipt',
             'NotificationOutbox', 'RealtimeNewsItem', 'Stock', 'User', 'Watchlist',
             'WatchlistItem', 'OptionsFlowRecipientDecision', 'Market', 'Exchange'):
    setattr(db, name, getattr(models, name))

from src.services.news_impact_watch import process_recent  # noqa: E402
import src.services.alert_audience as audience  # noqa: E402

engine = create_engine(_URL)
with engine.begin() as c:
    c.execute(text("DROP SCHEMA public CASCADE")); c.execute(text("CREATE SCHEMA public"))
models.Base.metadata.create_all(engine, tables=[
    models.User.__table__, models.Stock.__table__, models.Watchlist.__table__,
    models.WatchlistItem.__table__, models.RealtimeNewsItem.__table__,
    models.NewsImpactEvent.__table__, models.NewsImpactReceipt.__table__,
    models.AppNotification.__table__, models.NotificationOutbox.__table__,
    models.AlertPreference.__table__,
])

failures = []
def check(label, cond, detail=''):
    print(('  ok   ' if cond else '  FAIL ') + label + ('' if cond else f'  {detail}'))
    if not cond:
        failures.append(label)

t0 = datetime(2026, 10, 7, 17, 49, 33)
NOW = t0.replace(tzinfo=timezone.utc) + timedelta(minutes=1)

with Session(engine) as s:
    for sym in ('MSFT', 'NVDA', 'INTC'):
        s.add(models.Stock(symbol=sym, name=sym, market=models.Market.US,
                           exchange=models.Exchange.NASDAQ, active=True))
    s.add(models.RealtimeNewsItem(
        id=1, symbol='MSFT', source='alpaca',
        headline='Microsoft Exec Says RTX Spark-Based Machines With NVIDIA Chips Will Begin '
                 'Shipping Next Week',
        url='https://www.benzinga.com/news/2026/10/launch', published_at=t0))
    s.flush()
    # 1: explicit opt-in, has email.      2: NO preference row, has email.
    # 3: explicit opt-in, NO email.       4: explicit opt-out, has email.
    for uid, email in ((1, 'u1@example.com'), (2, 'u2@example.com'),
                       (3, None), (4, 'u4@example.com')):
        s.add(models.User(id=uid, username=f'u{uid}', password_hash='x', email=email,
                          is_active=True))
        s.flush()
        stock = s.scalar(select(models.Stock).where(models.Stock.symbol == 'MSFT'))
        s.add(models.WatchlistItem(user_id=uid, stock_id=stock.id))
    s.add(models.AlertPreference(user_id=1, alert_type='product_impact', enabled=True))
    s.add(models.AlertPreference(user_id=3, alert_type='product_impact', enabled=True))
    s.add(models.AlertPreference(user_id=4, alert_type='product_impact', enabled=False))
    s.commit()

    result = process_recent(s, now=NOW)
    s.commit()

    # The event persists at all. This is the whole of defect 1: on PostgreSQL the 38-character
    # evidence_status raised StringDataRightTruncation on flush, before any receipt existed.
    events = list(s.scalars(select(models.NewsImpactEvent)))
    check('the recognized event persists on PostgreSQL', len(events) == 1, result)
    if events:
        check('the evidence status survives round-trip unchanged',
              events[0].evidence_status == 'official_spec_and_provider_observation',
              events[0].evidence_status)

    receipts = {r.user_id: (r.state, r.reason) for r in s.scalars(select(models.NewsImpactReceipt))}
    check('an explicit opt-in with an email is emailed',
          receipts.get(1, ('', ''))[0] == 'email_queued', receipts.get(1))
    # Defect 2: no row means NOT subscribed for an opt-in channel, in the SENDER and not only
    # on the settings screen.
    check('an account with NO preference row is not enrolled',
          receipts.get(2, ('', ''))[0] == 'preference_disabled', receipts.get(2))
    # Defect 3: opted in, no email -> in-app notice, and a reason that names the real cause.
    check('an opted-in account without an email gets the in-app notice',
          receipts.get(3, ('', ''))[0] == 'in_app_only', receipts.get(3))
    check('...and its reason names the channel, not a preference it did not set',
          'email' in (receipts.get(3, ('', '')) [1] or ''), receipts.get(3))
    check('an explicit opt-out is not enrolled',
          receipts.get(4, ('', ''))[0] == 'preference_disabled', receipts.get(4))

    notified = {n.user_id for n in s.scalars(select(models.AppNotification))}
    check('in-app notices go to the opted-in users only', notified == {1, 3}, notified)
    queued = {o.user_id for o in s.scalars(select(models.NotificationOutbox))}
    check('only the opted-in account with an email is queued for mail', queued == {1}, queued)

    # The real gate, directly, including the failure path the injected lambdas never had.
    u2 = s.get(models.User, 2)
    check('may_send is False for an account with no preference row',
          audience.may_send(s, u2, 'product_impact') is False)
    check('may_notify is False for the same account',
          audience.may_notify(s, u2, 'product_impact') is False)
    check('a long-standing type still defaults to subscribed',
          audience.may_send(s, u2, 'signal') is True)

class _Boom:
    def execute(self, *a, **k):
        raise RuntimeError('preference table unavailable')
boom = _Boom()
u = types.SimpleNamespace(id=9, email='x@example.com', is_active=True)
check('a lookup failure fails CLOSED for an opt-in type',
      audience.may_send(boom, u, 'product_impact') is False)
check('a lookup failure still fails OPEN for a long-standing type',
      audience.may_send(boom, u, 'signal') is True)
check('a bulk filter fails CLOSED for an opt-in type',
      audience.filter_recipients(boom, {9: u}, 'product_impact') == {})
check('a bulk filter still fails OPEN for a long-standing type',
      audience.filter_recipients(boom, {9: u}, 'signal') == {9: u})

print('FAILURES:', failures)
sys.exit(1 if failures else 0)
