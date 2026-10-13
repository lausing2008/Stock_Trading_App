"""The real detector, the real audience gate, real models — end to end on SQLite.

Nothing is injected: `detect()` resolves prices, applies the adjustment contract, writes events
and receipts, and consults the shipped `alert_audience` functions.
"""
import importlib.util
import json
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
    """Key-aware: the detector reads the delivery flag, the config and the live-price cache."""
    def __init__(self, live=False, quotes=None, config=None):
        self.live, self.quotes, self.config = live, quotes, config
    def get(self, key):
        if key == prw.DELIVERY_FLAG:
            return b"1" if self.live else None
        if key == prw.LIVE_PRICES_KEY and self.quotes is not None:
            return json.dumps(self.quotes).encode()
        if key == prw.CONFIG_KEY and self.config is not None:
            return self.config if isinstance(self.config, bytes) else json.dumps(self.config).encode()
        return None


_ids = {'cov': 0, 'act': 0, 'pref': 0}


def build(closes_by_symbol, *, split_for=None, coverage=True, opt_in=(),
          completeness='response_only', dividends=None, end_for=None, reset=True):
    if not reset:
        return
    with Session(engine, autoflush=False) as s:
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
            end = (end_for or {}).get(symbol, TODAY)
            start = end - timedelta(days=len(closes) - 1)
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
                    completeness_basis=completeness, source='test',
                    method='cumulative_split_factor_v1', retrieved_at=datetime(2026, 10, 12)))
            for ex, amount in (dividends or {}).get(symbol, []):
                _ids['act'] += 1
                s.add(models.CorporateAction(id=_ids['act'], symbol=symbol, ex_date=ex,
                                             action_type='cash_dividend', cash_amount=amount,
                                             source='test', retrieved_at=datetime(2026, 10, 12)))
            if split_for == symbol:
                _ids['act'] += 1
                s.add(models.CorporateAction(id=_ids['act'], symbol=symbol, ex_date=TODAY, action_type='split',
                                             split_ratio=None, source='test',
                                             retrieved_at=datetime(2026, 10, 12)))
        for t in opt_in:
            _ids['pref'] += 1
            s.add(models.AlertPreference(id=_ids['pref'], user_id=1, alert_type=t, enabled=True))
        s.commit()


def run(live=False, now=NOW, quotes=None, config=None):
    with Session(engine, autoflush=False) as s:
        result = detect(s, now=now, redis_client=Redis(live, quotes, config))
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
with Session(engine, autoflush=False) as s:
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

# 4. Live mode, opted in: ONE digest is QUEUED — and no provider is called, because detection
#    never sends. The drain job, on the alerting side, is the only sender.
build({'NVDA': [100, 100, 100, 100, 100, 94]}, opt_in=(ALERT_TYPE_EMAIL, ALERT_TYPE_IN_APP))
result, events, receipts, outbox = run(live=True)
check('with delivery enabled and opted in, the receipts are queued',
      receipts and all(r.state == 'email_queued' for r in receipts),
      [(r.state, r.reason) for r in receipts])
check('...as ONE digest for several events, not one email each',
      len(outbox) == 1 and len(events) >= 2, (len(outbox), [(e.rule, e.step) for e in events]))
check('THE DETECTION JOB NEVER CALLS A PROVIDER: the row is pending',
      all(o.state == 'pending' for o in outbox), [o.state for o in outbox])
check('the receipt points at the outbox row it was queued in',
      all(r.outbox_event_id == outbox[0].event_id for r in receipts))
check('the digest says this is a measured move, not a signal, and claims no cause',
      outbox and 'not trade signals' in outbox[0].body_text
      and 'does not establish WHY' in outbox[0].body_text, outbox and outbox[0].body_text)
before = len(outbox)
result, events, receipts, outbox = run(live=True)
check('a re-run queues no duplicate digest', len(outbox) == before, (before, len(outbox)))

# 5. Live mode, NOT opted in: the opt-in default governs delivery here too.
build({'NVDA': [100, 100, 100, 100, 100, 94]})
result, events, receipts, outbox = run(live=True)
check('live mode still refuses an account that never opted in',
      receipts and all(r.state == 'preference_disabled' for r in receipts) and outbox == [],
      [(r.state, r.reason) for r in receipts])

# 5b. In-app only: email off, in-app on -> a notice, no email, and the receipt says why.
build({'NVDA': [100, 100, 100, 100, 100, 94]}, opt_in=(ALERT_TYPE_IN_APP,))
result, events, receipts, outbox = run(live=True)
with Session(engine, autoflush=False) as s:
    notices = list(s.scalars(select(models.AppNotification)))
check('in-app only: one notice, no email', len(notices) == 1 and outbox == [],
      (len(notices), len(outbox)))
check('...recorded as in_app_only, not as preference_disabled',
      receipts and all(r.state == 'in_app_only' for r in receipts),
      [(r.state, r.reason) for r in receipts])

# 5c. A shadow detection does not count as having told anybody.
build({'NVDA': [100, 100, 100, 100, 100, 94]}, opt_in=(ALERT_TYPE_EMAIL,))
run(live=False)
result, events, receipts, outbox = run(live=True)
check('an episode recorded only in shadow is still reported once live', len(outbox) == 1,
      [(e.delivery_mode, e.rule) for e in events])

# 5d. The per-user daily ceiling.
build({'NVDA': [100, 100, 100, 100, 100, 94]}, opt_in=(ALERT_TYPE_EMAIL,))
with Session(engine, autoflush=False) as s:
    for i in range(prw.MAX_EMAILS_PER_USER_PER_DAY):
        s.add(models.NotificationOutbox(event_id=f'price-risk:1:prior{i}', recipient='u1@example.com',
              subject='x', body_html='x', body_text='x', alert_type=ALERT_TYPE_EMAIL, user_id=1,
              state='accepted', created_at=NOW.replace(tzinfo=None) - timedelta(hours=1)))
    s.commit()
result, events, receipts, outbox = run(live=True)
check('the daily ceiling stops a further email', len(outbox) == prw.MAX_EMAILS_PER_USER_PER_DAY,
      len(outbox))
check('...and the receipt records the cap rather than a silent drop',
      receipts and all(r.state == 'email_capped' for r in receipts),
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
check('...with ONE receipt per user, not one per member they watch (prod runs autoflush=False)',
      breadth and len([r for r in receipts if r.event_id == breadth[0].id]) == 1,
      [(r.event_id, r.matched_symbols) for r in receipts])
if breadth:
    ev = breadth[0].evidence.get('breadth', {})
    check('...carrying coverage and sample size, not just a fraction',
          'coverage' in ev and 'observed_count' in ev and 'declined_symbols' in ev, ev)

# 8. EVERY excluded subject must carry an actionable reason — a delivery precondition.
build({'SPLT': [100, 100, 100, 100, 100, 50]}, split_for='SPLT')
result, events, receipts, outbox = run()
excl = [e for e in events if e.rule == 'unmeasurable']
check('every exclusion names its limiting factor',
      excl and all((e.evidence or {}).get('limiting_factor') for e in excl),
      [(e.subject, (e.evidence or {}).get('limiting_factor')) for e in excl])
check('every exclusion states the best status this source could ever reach',
      all((e.evidence or {}).get('best_achievable') for e in excl),
      [(e.subject, (e.evidence or {}).get('best_achievable')) for e in excl])
check('a source with no completeness guarantee can never reach verified',
      all((e.evidence or {}).get('best_achievable') != 'verified' for e in excl
          if (e.evidence or {}).get('best_achievable')),
      [(e.subject, (e.evidence or {}).get('best_achievable')) for e in excl])

# A stale span with NOTHING recorded in it is still MEASURABLE — just not verifiable. Keeping
# those usable is the difference between a conservative rule and one that discards most of the
# sample; what must not happen is calling the result verified.
build({'STALE': [100, 100, 100, 100, 100, 94]}, coverage=False)
result, events, receipts, outbox = run()
measured = [e for e in events if e.rule == 'session_decline']
check('a stale span with no recorded action is still measured', len(measured) == 1,
      [(e.rule, e.change_pct) for e in events])
check('...but is never presented as verified',
      all(e.evidence_status != 'verified' for e in events),
      [(e.rule, e.evidence_status) for e in events])

# A source that DOES document completeness can reach verified; the ceiling is a property of the
# source, not a blanket refusal.
build({'GUAR': [100, 100, 100, 100, 100, 94]}, completeness='source_guarantee')
result, events, receipts, outbox = run()
check('a source documenting completeness reaches verified',
      any(e.evidence_status == 'verified' for e in events),
      [(e.rule, e.evidence_status) for e in events])

# ── 2026-10-13 fixes ─────────────────────────────────────────────────────────

def rules(events, subject=None):
    return sorted((e.rule, e.step) for e in events if subject in (None, e.subject))

# 9. CASH DIVIDEND with a stale coverage span — the AVGO case. It used to be "unmeasurable".
ex = TODAY - timedelta(days=2)
build({'AVGO': [376, 376, 376, 376, 360, 355]}, coverage=False,
      dividends={'AVGO': [(ex, 0.65)]})
result, events, receipts, outbox = run()
check('a cash dividend no longer makes a window unmeasurable',
      not [e for e in events if e.rule == 'unmeasurable'], rules(events))
check('...the decline through it is measured (slow decline: -5.6% is under the pullback rung)',
      ('slow_decline', 1) in rules(events), rules(events))
check('...and the event records the dividend it added back',
      all((e.evidence or {}).get('cash_dividends_added_back') for e in events if e.scope == 'symbol'),
      [(e.rule, (e.evidence or {}).get('cash_dividends_added_back')) for e in events])

# 9b. An ex-dividend drop that IS the whole move is not a decline.
build({'DIVY': [100, 100, 100, 100, 100, 96]}, dividends={'DIVY': [(TODAY, 4.0)]})
result, events, receipts, outbox = run()
check('an ex-dividend drop alone produces no event', events == [], rules(events))

# 9c. A dividend whose amount was never recorded is refused, with the reason named.
build({'DIVN': [100, 100, 100, 100, 100, 96]}, dividends={'DIVN': [(TODAY, None)]})
result, events, receipts, outbox = run()
check('an unknown dividend amount is unmeasurable, never a measured decline',
      rules(events) == [('unmeasurable', 0)], rules(events))
check('...naming dividend_amount_unknown as the limit',
      events and events[0].evidence.get('limiting_factor') == 'dividend_amount_unknown',
      events and events[0].evidence)

# 10. SLOW DECLINE: the NVDA shape, no day worse than -3%.
build({'NVDA': [239.24, 237.47, 230.48, 229.28, 226.32, 225.33]})
result, events, receipts, outbox = run()
check('a slow bleed is detected', ('slow_decline', 1) in rules(events), rules(events))
check('...without inventing a session decline', not any(e.rule == 'session_decline' for e in events))

# 10b. Configurable, and a bad override is named rather than half-applied.
build({'NVDA': [239.24, 237.47, 230.48, 229.28, 226.32, 225.33]})
result, events, receipts, outbox = run(config={'slow_decline_steps': [7.0]})
check('a raised threshold is honoured', not any(e.rule == 'slow_decline' for e in events), rules(events))
build({'NVDA': [239.24, 237.47, 230.48, 229.28, 226.32, 225.33]})
result, events, receipts, outbox = run(config={'slow_decline_steps': [8, 5], 'slow_decline_sessions': 99})
check('an invalid override falls back to defaults and says so',
      ('slow_decline', 1) in rules(events) and len(result['config_errors']) == 2, result)
result, *_ = run(config=b'[1, 2]')
check('a non-object override is named', result['config_errors'] == ['config must be a JSON object'], result)

# 11. RECOVERY IS PERSISTED and a second decline alerts again. Three sessions run in order.
def run_on(day_closes, day):
    """Rewrite NVDA's bars to end on `day`, then run detection as of that evening."""
    with Session(engine, autoflush=False) as s:
        s.query(models.Price).delete(); s.commit()
        start = day - timedelta(days=len(day_closes) - 1)
        for i, c in enumerate(day_closes):
            d = start + timedelta(days=i)
            s.add(models.Price(id=i + 1, stock_id=1, ts=datetime(d.year, d.month, d.day),
                               timeframe=models.TimeFrame.D1, open=c, high=c, low=c, close=c,
                               volume=1)); s.commit()
    return run(now=datetime(day.year, day.month, day.day, 23, 0, tzinfo=timezone.utc))

build({'NVDA': [100, 100]})
d1 = TODAY
_, ev1, *_ = run_on([100, 100, 100, 100, 100, 91], d1)
first = [e for e in ev1 if e.rule == 'pullback']
check('first decline reported', len(first) == 1, rules(ev1))
_, ev2, *_ = run_on([100, 100, 100, 100, 100, 91, 99], d1 + timedelta(days=1))
with Session(engine, autoflush=False) as s:
    stored = s.get(models.RiskEvent, first[0].id)
    closed_flag = bool((stored.evidence or {}).get('episode_closed'))
    closed_by = (stored.evidence or {}).get('episode_closed_by')
check('recovery is WRITTEN to the event that opened the episode', closed_flag and closed_by == 'price_recovery',
      stored.evidence)
_, ev3, *_ = run_on([100, 100, 100, 100, 91, 99, 100, 100, 91], d1 + timedelta(days=3))
second = [e for e in ev3 if e.rule == 'pullback' and e.observed_to == d1 + timedelta(days=3)]
check('a second decline after recovery alerts again', len(second) == 1, rules(ev3))
check('...and says it is a new episode',
      second and second[0].evidence.get('emit_reason') == 'new episode after recovery',
      second and second[0].evidence.get('emit_reason'))

# 12. BASKET EVIDENCE: an unmeasurable member is unobserved, not a raw return.
build({s: [100, 100, 100, 100, 100, 96] for s in ('NVDA', 'AMD', 'TSM')} |
      {'AVGO': [100, 100, 100, 100, 100, 50]}, split_for='AVGO')
result, events, receipts, outbox = run()
b = [e for e in events if e.scope == 'theme' and e.subject.startswith('AI / GPU')]
check('a basket with an unmeasurable member lacks coverage and does not fire', b == [],
      [(e.rule, e.evidence.get('member_moves')) for e in b])

# 12b. A STALE member (last bar a session behind) is unobserved for this session.
build({s: [100, 100, 100, 100, 100, 96] for s in ('NVDA', 'AMD', 'TSM')} |
      {'AVGO': [100, 100, 100, 100, 100, 96]}, end_for={'AVGO': TODAY - timedelta(days=1)})
result, events, receipts, outbox = run()
b = [e for e in events if e.scope == 'theme' and e.subject.startswith('AI / GPU')]
check('a stale member is not counted for the newer session', b == [],
      [(e.rule, e.evidence.get('member_moves')) for e in b])

# 12c. Multi-day basket breadth: a sector grinding lower with no 2% day.
build({s: [100, 99, 98, 97, 96, 95.5] for s in ('NVDA', 'AMD', 'AVGO', 'TSM')})
result, events, receipts, outbox = run()
kinds = sorted(e.rule for e in events if e.scope == 'theme' and e.subject.startswith('AI / GPU'))
check('a basket grinding lower is caught by multi-day breadth, not the one-day rule',
      kinds == ['basket_slow_decline'], kinds)

# 13. INTRADAY: a live quote is today's price only when its previous close matches.
MORNING = datetime(2026, 10, 12, 15, 0, tzinfo=timezone.utc)   # 11:00 ET, regular session
build({'NVDA': [100, 100, 100, 100, 100, 100]})
result, events, receipts, outbox = run(now=MORNING, quotes=[{'symbol': 'NVDA', 'price': 96.0,
                                                             'prev_close': 100.0}])
check('an intraday quote produces a same-session decline',
      ('session_decline', 1) in rules(events) and result['intraday_quotes'] == 1, (rules(events), result))
check('...labelled as an intraday quote',
      all(e.evidence.get('price_source') == 'live_quote' for e in events))
result, ev2, *_ = run(now=MORNING + timedelta(hours=3),
                      quotes=[{'symbol': 'NVDA', 'price': 93.0, 'prev_close': 100.0}])
check('a worse reading later the same session is an escalation, not a duplicate',
      ('session_decline', 2) in rules(ev2), rules(ev2))
result, ev3, *_ = run(now=MORNING + timedelta(hours=4),
                      quotes=[{'symbol': 'NVDA', 'price': 93.5, 'prev_close': 100.0}])
check('the same step again is not repeated', len(ev3) == len(ev2), (rules(ev2), rules(ev3)))

build({'NVDA': [100, 100, 100, 100, 100, 100]})
result, events, *_ = run(now=MORNING, quotes=[{'symbol': 'NVDA', 'price': 80.0, 'prev_close': 120.0}])
check('a quote whose previous close does not match the bars is not used',
      events == [] and result['intraday_quotes'] == 0, (rules(events), result))
build({'NVDA': [100, 100, 100, 100, 100, 100]})
result, events, *_ = run(quotes=[{'symbol': 'NVDA', 'price': 90.0, 'prev_close': 100.0}])
check('outside the regular session the cache is ignored', events == [], rules(events))

print('FAILURES:', failures)
sys.exit(1 if failures else 0)
