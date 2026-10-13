"""Exercise actual producer, models, constraints and outbox against SQLite."""
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import sys
import types
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

root = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(root / 'shared'), str(root / 'services/market-data')]
db = types.ModuleType('db')
db.__path__ = [str(root / 'shared/db')]
sys.modules['db'] = db
spec = importlib.util.spec_from_file_location('db.models', root / 'shared/db/models.py')
models = importlib.util.module_from_spec(spec)
sys.modules['db.models'] = models
spec.loader.exec_module(models)
for name in ('AppNotification', 'NewsImpactEvent', 'NewsImpactReceipt', 'RealtimeNewsItem',
             'Stock', 'User', 'Watchlist', 'WatchlistItem', 'OptionsFlowRecipientDecision'):
    setattr(db, name, getattr(models, name))
from src.services.news_impact_watch import process_recent, recognize  # noqa: E402
from src.services.flow_recipient_funnel import record as record_flow  # noqa: E402
from common.outbox import claim, enqueue  # noqa: E402
import common.outbox as outbox  # noqa: E402

engine = create_engine('sqlite://')
models.Base.metadata.create_all(engine, tables=[
    models.User.__table__, models.Stock.__table__, models.Watchlist.__table__,
    models.WatchlistItem.__table__, models.RealtimeNewsItem.__table__,
    models.NewsImpactEvent.__table__, models.NewsImpactReceipt.__table__,
    models.AppNotification.__table__, models.NotificationOutbox.__table__,
    models.AlertPreference.__table__, models.OptionsFlowRecipientDecision.__table__,
])
t0 = datetime(2026, 10, 7, 17, 49, 33)
outbox.utcnow = lambda: t0 + timedelta(minutes=1)
with Session(engine) as session:
    for sym in ('MSFT', 'NVDA', 'INTC'):
        session.add(models.Stock(symbol=sym, name=sym, market=models.Market.US,
                                 exchange=models.Exchange.NASDAQ, active=True))
    session.add(models.RealtimeNewsItem(id=1, symbol='MSFT', source='alpaca',
        headline='Microsoft Exec Says RTX Spark-Based Machines With NVIDIA Chips Will Begin Shipping Next Week',
        url='https://www.benzinga.com/news/2026/10/launch', published_at=t0))
    session.add(models.RealtimeNewsItem(id=2, symbol='RTX', source='alpaca',
        headline='Dell Technologies Rolls Out Nvidia RTX Spark-Powered XPS 16',
        url='https://www.benzinga.com/news/2026/10/dell', published_at=t0))
    for i, sym in enumerate(('NVDA', 'INTC', 'MSFT'), 1):
        session.add(models.User(id=i, username=f'user{i}', password_hash='x',
            email=f'user{i}@example.com' if i < 3 else None, is_active=True))
        session.flush()
        stock = session.scalar(select(models.Stock).where(models.Stock.symbol == sym))
        session.add(models.WatchlistItem(user_id=i, stock_id=stock.id))
    session.add(models.AlertPreference(user_id=2, alert_type='product_impact', enabled=False))
    session.commit()
    # AQ-REVIEW-2/3 (2026-10-12): THIS USED TO INJECT A LAMBDA, and the lambda implemented a
    # different rule from the shipped gate — it checked one preference row and ignored the
    # registry default, the email address and account activity. The suite was therefore green
    # on behaviour the live caller could not produce, and two defects shipped behind it. The
    # real gate is used instead; it lives in `alert_audience` precisely so this is possible
    # without standing the scheduler up.
    #
    # product_impact is opt-in, so the users who should receive it need an explicit row.
    for uid in (1, 3):
        session.add(models.AlertPreference(user_id=uid, alert_type='product_impact', enabled=True))
    session.commit()
    assert not recognize(session.get(models.RealtimeNewsItem, 2))
    result = process_recent(session, now=t0.replace(tzinfo=timezone.utc) + timedelta(minutes=1))
    session.commit()
    assert result['new_alerts'] == 2
    receipts = {r.user_id: r.state for r in session.scalars(select(models.NewsImpactReceipt))}
    assert receipts == {1: 'email_queued', 2: 'preference_disabled', 3: 'in_app_only'}
    assert len(list(session.scalars(select(models.NotificationOutbox)))) == 1
    assert len(list(session.scalars(select(models.AppNotification)))) == 2
    process_recent(session, now=t0.replace(tzinfo=timezone.utc) + timedelta(minutes=2))
    session.commit()
    assert len(list(session.scalars(select(models.NewsImpactReceipt)))) == 3
    claimed = claim(session, owner='test', now=t0 + timedelta(minutes=2), alert_types=('product_impact',))
    assert len(claimed) == 1
    session.add(models.User(id=4, username='late-user', password_hash='x',
                            email='late@example.com', is_active=True))
    stock = session.scalar(select(models.Stock).where(models.Stock.symbol == 'INTC'))
    session.add(models.WatchlistItem(user_id=4, stock_id=stock.id))
    session.commit()
    process_recent(session, now=t0.replace(tzinfo=timezone.utc) + timedelta(days=5))
    session.commit()
    late = session.scalar(select(models.NewsImpactReceipt).where(models.NewsImpactReceipt.user_id == 4))
    assert late.state == 'historical_no_alert'
    assert len(list(session.scalars(select(models.NotificationOutbox)))) == 1
    candidate = {'NVDA261120C00100000': {'symbol': 'NVDA'}}
    record_flow(session, user_id=1, candidates=candidate, fired_date=t0.date(),
        accepted=set(), queued=list(candidate), omitted=[], deferred=[],
        previously_seen=set(), send_attempted=True, send_ok=False)
    session.commit()
    decision = session.scalar(select(models.OptionsFlowRecipientDecision))
    assert decision.state == 'sender_failed'
    record_flow(session, user_id=1, candidates=candidate, fired_date=t0.date(),
        accepted=set(candidate), queued=list(candidate), omitted=[], deferred=[],
        previously_seen=set(), send_attempted=True, send_ok=True)
    session.commit()
    assert decision.state == 'sender_accepted'
    record_flow(session, user_id=1, candidates=candidate, fired_date=t0.date(),
        accepted=set(), queued=[], omitted=[], deferred=list(candidate),
        previously_seen=set(), send_attempted=False, send_ok=True)
    session.commit()
    assert decision.state == 'sender_accepted', 'a later cooldown cannot erase acceptance'
    for alert_type in ('product_impact', 'early_earnings_news'):
        enqueue(session, event_id=f'isolation:{alert_type}', user_id=1,
                recipient='user1@example.com', alert_type=alert_type,
                subject='channel isolation', body_text='test', body_html='test',
                available_at=t0, expires_at=t0 + timedelta(days=1))
    session.commit()
    claimed = claim(session, owner='product-worker', now=t0 + timedelta(minutes=2),
                    alert_types=('product_impact',))
    assert [row.event_id for row in claimed] == ['isolation:product_impact']
print('news impact and flow recipient persistence passed')
