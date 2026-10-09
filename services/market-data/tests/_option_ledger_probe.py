"""Run actual request handlers against real SQLite models, isolated from conftest stubs."""
import ast
import datetime as dt
import importlib.util
import pathlib
import sys
import types
from sqlalchemy import create_engine, select, func, desc
from sqlalchemy.orm import Session
from fastapi import HTTPException

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'shared'), str(ROOT / 'services/market-data')]
db = types.ModuleType('db'); db.__path__ = [str(ROOT / 'shared/db')]; sys.modules['db'] = db
spec = importlib.util.spec_from_file_location('db.models', ROOT / 'shared/db/models.py')
models = importlib.util.module_from_spec(spec); sys.modules['db.models'] = models; spec.loader.exec_module(models)
engine = create_engine('sqlite://')
models.Base.metadata.create_all(engine, tables=[
    models.OptionStrategyCapture.__table__, models.OptionStrategyResolution.__table__,
    models.Stock.__table__, models.OptionsFlowAlertOutcome.__table__, models.User.__table__,
    models.AlertPreference.__table__, models.NotificationOutbox.__table__,
])
source = ROOT / 'services/market-data/src/api/admin.py'
names = ['capture_option_strategy', 'resolve_option_strategy', 'get_option_strategy', 'options_flow_alert_performance']
ns = {'__package__': 'src.api', '_dt': dt, 'select': select, 'func': func, 'desc': desc,
      'HTTPException': HTTPException, 'OptionsFlowAlertOutcome': models.OptionsFlowAlertOutcome, 'Stock': models.Stock}
tree = ast.parse(source.read_text())
for node in tree.body:
    if isinstance(node, ast.FunctionDef) and node.name in names:
        node.decorator_list = []
        node.returns = None
        for arg in node.args.args: arg.annotation = None
        node.args.defaults = []
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), 'exec'), ns)

with Session(engine) as session:
    c = dict(strategy='long_call', origin='replay', symbol='TEST', contract_id='TEST-C100', quote_source='fixture',
             confirmation_rule='above 100', invalidation_rule='below 95', captured_at='2020-01-02T14:00:00Z',
             entry_at='2020-01-02T15:00:00Z', exit_at='2020-01-03T15:00:00Z', last_trade_at='2020-01-17T21:00:00Z',
             deliverable='standard_100_shares', multiplier=100, quantity=1, fee_per_contract_per_side=.65,
             slippage_per_share_per_side=.05, exit_policy='scheduled_close_before_expiry')
    cap = ns['capture_option_strategy'](c, None, session)
    assert cap['created']
    assert ns['capture_option_strategy'](c, None, session)['created'] is False
    e = dict(entry=dict(contract_id='TEST-C100', source='fixture', quoted_at=c['entry_at'], bid=4.8, ask=5),
             exit=dict(contract_id='TEST-C100', source='fixture', quoted_at=c['exit_at'], bid=3, ask=3.2), deliverable_unchanged_verified=True)
    unresolved = ns['resolve_option_strategy'](cap['id'], {}, None, session)
    resolved = ns['resolve_option_strategy'](cap['id'], e, None, session)
    assert resolved['result']['net_pnl'] == -211.3
    assert ns['resolve_option_strategy'](cap['id'], e, None, session)['created'] is False
    history = ns['get_option_strategy'](cap['id'], None, session)
    assert len(history['attempts']) == 2
    assert history['attempts'][0]['result']['net_pnl'] is None
    # Real SQL filtering/pagination, not a mocked .execute chain.
    stock = models.Stock(symbol='TEST', name='Test', exchange=models.Exchange.NASDAQ, market=models.Market.US)
    session.add(stock); session.flush()
    for i in range(3):
        session.add(models.OptionsFlowAlertOutcome(id=i+1, stock_id=stock.id, symbol='TEST', option_chain=f'TEST{i}', option_type='call',
            direction='bullish', fired_date=dt.date.today(), fired_at=dt.datetime.now(), expiry=dt.date.today(),
            alert_price=100, ask_side_dominant=True, total_premium=1000 + i, entry_date=dt.date.today(), return_10d=.01, is_correct_10d=True))
    session.commit()
    args = dict(days_back=30, limit=1, offset=1, symbol='TEST', direction='bullish', sweep_only=False,
                min_premium=0, sort='total_premium', _=None, session=session)
    result = ns['options_flow_alert_performance'](**args)
    assert result['matching_count'] == result['total_count'] == 3
    assert len(result['recent_alerts']) == 1
    assert result['recent_alerts'][0]['total_premium'] == 1001
    assert result['recent_alerts'][0]['calibration_eligibility'] == 'same_day_expiry'
    stats = result['by_direction'][0]['window_10d']
    assert stats['n'] == 0 and stats['excluded_total'] == 3 and stats['original_n'] == 3
    assert stats['horizon_unit'] == 'calendar_days'
    # New opportunity mail is explicit opt-in and queues through the durable outbox only.
    from src.services.option_opportunity_notifications import enqueue_if_eligible
    user = models.User(username='option-user', password_hash='x', email='option@example.com', is_active=True)
    session.add(user); session.commit()
    opportunity = dict(opportunity_id='opp-1', symbol='TEST', strategy='long_call',
        contract_id='TEST-C100', entry_deadline='2026-10-09T19:00:00+00:00',
        maximum_loss=250, quantity=1, confirmation_rule='above 100',
        invalidation_rule='below 95', measurement_status='prospective_unmeasured')
    gate = {'status': 'actionable', 'quantity': 1}
    assert enqueue_if_eligible(session, user=user, opportunity=opportunity, gate=gate)['status'] == 'not_opted_in'
    session.add(models.AlertPreference(user_id=user.id, alert_type='option_opportunity', enabled=True, source='settings'))
    session.commit()
    queued = enqueue_if_eligible(session, user=user, opportunity=opportunity, gate=gate)
    session.commit()
    assert queued['status'] == 'queued'
    assert enqueue_if_eligible(session, user=user, opportunity=opportunity, gate=gate)['status'] == 'already_queued'
    outbox = session.scalar(select(models.NotificationOutbox))
    assert outbox.alert_type == 'option_opportunity' and outbox.state == 'pending'
print('real-handler ledger and filtered-pagination probe passed')
