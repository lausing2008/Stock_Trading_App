"""The real detector, the real audience gate, real models — end to end on SQLite.

Nothing is injected: `detect()` resolves prices, applies the adjustment contract, writes events
and receipts, and consults the shipped `alert_audience` functions.
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
for name in ('outcome_price_basis', 'alert_prefs'):
    spec = importlib.util.spec_from_file_location(f'common.{name}', root / f'shared/common/{name}.py')
    m = importlib.util.module_from_spec(spec); sys.modules[f'common.{name}'] = m
    spec.loader.exec_module(m); setattr(common, name, m)

db = types.ModuleType('db'); db.__path__ = [str(root / 'shared/db')]
sys.modules['db'] = db
spec = importlib.util.spec_from_file_location('db.models', root / 'shared/db/models.py')
models = importlib.util.module_from_spec(spec); sys.modules['db.models'] = models
spec.loader.exec_module(models)
for n in ('AlertPreference', 'AppNotification', 'CorporateAction', 'CorporateActionCoverage',
          'NotificationOutbox', 'Price', 'RiskEvent', 'RiskEventReceipt', 'Stock', 'TimeFrame',
          'User', 'Watchlist', 'WatchlistItem', 'Market', 'Exchange', 'Base'):
    setattr(db, n, getattr(models, n))

from src.services.price_risk_watch import detect, ALERT_TYPE_EMAIL, ALERT_TYPE_IN_APP  # noqa: E402
import src.services.price_risk_watch as prw  # noqa: E402

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

engine = create_engine('sqlite://')
models.Base.metadata.create_all(engine, tables=[
    models.User.__table__, models.Stock.__table__, models.Watchlist.__table__,
    models.WatchlistItem.__table__, models.Price.__table__, models.RiskEvent.__table__,
    models.RiskEventReceipt.__table__, models.AlertPreference.__table__,
    models.CorporateAction.__table__, models.CorporateActionCoverage.__table__,
    models.NotificationOutbox.__table__, models.AppNotification.__table__,
])

failures = []
def check(label, cond, detail=''):
    print(('  ok   ' if cond else '  FAIL ') + label + ('' if cond else f'  {detail}'))
    if not cond:
        failures.append(label)


TODAY = date(2026, 10, 12)
NOW = datetime(2026, 10, 12, 21, 0, tzinfo=timezone.utc)


class Redis:
    def __init__(self, live=False):
        self.live = live
    def get(self, key):
        return b"1" if self.live else None


_ids = {'cov': 0, 'act': 0, 'pref': 0}


def build(closes_by_symbol, *, split_for=None, coverage=True, opt_in=()):
    with Session(engine) as s:
        for t in (models.RiskEventReceipt, models.RiskEvent, models.AlertPreference,
                  models.CorporateActionCoverage, models.CorporateAction,
                  models.WatchlistItem, models.Price, models.Stock, models.User,
                  models.NotificationOutbox, models.AppNotification):
            s.query(t).delete()
        s.commit()
        u = models.User(id=1, username='u1', password_hash='x', email='u1@example.com',
                        is_active=True)
        s.add(u); s.flush()
        pid = 0
        for sid, (symbol, closes) in enumerate(closes_by_symbol.items(), start=1):
            s.add(models.Stock(id=sid, symbol=symbol, name=symbol, market=models.Market.US,
                               exchange=models.Exchange.NASDAQ, active=True))
            s.flush()
            s.add(models.WatchlistItem(id=sid, user_id=1, stock_id=sid))
            start = TODAY - timedelta(days=len(closes) - 1)
            for i, c in enumerate(closes):
                pid += 1
                d = start + timedelta(days=i)
                s.add(models.Price(id=pid, stock_id=sid, ts=datetime(d.year, d.month, d.day),
                                   timeframe=models.TimeFrame.D1, open=float(c), high=float(c),
                                   low=float(c), close=float(c), volume=1_000_000))
            if coverage:
                _ids['cov'] += 1
                s.add(models.CorporateActionCoverage(
                    id=_ids['cov'], symbol=symbol, requested_from=start, requested_to=TODAY,
                    evidenced_from=start, evidenced_to=TODAY,
                    completeness_basis='source_guarantee', source='test',
                    method='cumulative_split_factor_v1', retrieved_at=datetime(2026, 10, 12)))
            if split_for == symbol:
                _ids['act'] += 1
                s.add(models.CorporateAction(id=_ids['act'], symbol=symbol, ex_date=TODAY, action_type='split',
                                             split_ratio=None, source='test',
                                             retrieved_at=datetime(2026, 10, 12)))
        for t in opt_in:
            _ids['pref'] += 1
            s.add(models.AlertPreference(id=_ids['pref'], user_id=1, alert_type=t, enabled=True))
        s.commit()


def run(live=False):
    with Session(engine) as s:
        result = detect(s, now=NOW, redis_client=Redis(live))
        s.commit()
        events = list(s.scalars(select(models.RiskEvent)))
        receipts = list(s.scalars(select(models.RiskEventReceipt)))
        outbox = list(s.scalars(select(models.NotificationOutbox)))
        return result, events, receipts, outbox


# 1. A qualifying decline becomes an event, in shadow, with a receipt that says so.
build({'NVDA': [100, 100, 100, 100, 100, 94]})
result, events, receipts, outbox = run()
check('a qualifying decline is detected', any(e.rule == 'session_decline' for e in events),
      [(e.rule, e.change_pct) for e in events])
check('the event records that delivery was withheld, not that nobody was eligible',
      all(e.delivery_mode == 'shadow' for e in events), [e.delivery_mode for e in events])
check('a receipt is written for the watching user',
      [r.state for r in receipts] and all(r.state == 'shadow_suppressed' for r in receipts),
      [(r.state, r.reason) for r in receipts])
check('SHADOW SENDS NOTHING', outbox == [], outbox)

# 2. Re-running the same session does not duplicate.
before = len(events)
with Session(engine) as s:
    detect(s, now=NOW, redis_client=Redis()); s.commit()
    after = len(list(s.scalars(select(models.RiskEvent))))
check('a second run in the same session creates no duplicate', after == before, (before, after))

# 3. A split inside the window produces an unmeasurable RECORD, never a magnitude.
build({'SPLT': [100, 100, 100, 100, 100, 50]}, split_for='SPLT')
result, events, receipts, outbox = run()
unmeasurable = [e for e in events if e.rule == 'unmeasurable']
check('an unapplied split yields an unmeasurable record', len(unmeasurable) == 1,
      [(e.rule, e.change_pct) for e in events])
check('...and no event claims the 50% crash the split invented',
      all(e.change_pct is None or abs(e.change_pct) < 40 for e in events),
      [(e.rule, e.change_pct) for e in events])
check('...and nothing was sent for it', outbox == [], outbox)

# 4. Live mode, opted in: eligible — and STILL nothing is sent, because this job never sends.
build({'NVDA': [100, 100, 100, 100, 100, 94]}, opt_in=(ALERT_TYPE_EMAIL, ALERT_TYPE_IN_APP))
result, events, receipts, outbox = run(live=True)
check('with delivery enabled and opted in, the receipt is eligible',
      any(r.state == 'eligible_for_email' for r in receipts),
      [(r.state, r.reason) for r in receipts])
check('THE DETECTION JOB NEVER SENDS, even in live mode', outbox == [], outbox)

# 5. Live mode, NOT opted in: the opt-in default governs delivery here too.
build({'NVDA': [100, 100, 100, 100, 100, 94]})
result, events, receipts, outbox = run(live=True)
check('live mode still refuses an account that never opted in',
      all(r.state == 'preference_disabled' for r in receipts),
      [(r.state, r.reason) for r in receipts])

# 6. An unreadable delivery flag means shadow, never live.
class Broken:
    def get(self, key):
        raise RuntimeError('redis unavailable')
check('an unreadable delivery flag means shadow', prw.delivery_enabled(Broken()) is False)
check('a missing redis client means shadow', prw.delivery_enabled(None) is False)

# 7. Theme breadth: a basket that falls together is reported with its coverage.
build({s: [100, 100, 100, 100, 100, 96] for s in ('NVDA', 'AMD', 'AVGO', 'TSM')})
result, events, receipts, outbox = run()
breadth = [e for e in events if e.rule == 'basket_breadth']
check('a basket falling together is reported', len(breadth) >= 1,
      [(e.rule, e.subject) for e in events])
if breadth:
    ev = breadth[0].evidence.get('breadth', {})
    check('...carrying coverage and sample size, not just a fraction',
          'coverage' in ev and 'observed_count' in ev and 'declined_symbols' in ev, ev)

print('FAILURES:', failures)
sys.exit(1 if failures else 0)
