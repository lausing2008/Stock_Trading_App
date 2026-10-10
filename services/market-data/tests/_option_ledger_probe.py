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
    class Clock(dt.datetime):
        current = dt.datetime(2020, 1, 2, 14, tzinfo=dt.timezone.utc)
        @classmethod
        def now(cls, tz=None):
            return cls.current
    ns['_dt'] = types.SimpleNamespace(datetime=Clock, timezone=dt.timezone)
    prospective = {**c, 'origin': 'prospective'}
    first = ns['capture_option_strategy'](prospective, None, session)
    Clock.current = dt.datetime(2020, 1, 2, 16, tzinfo=dt.timezone.utc)
    retry = ns['capture_option_strategy'](prospective, None, session)
    assert retry['id'] == first['id'] and retry['created'] is False
    assert retry['inputs']['captured_at'] == first['inputs']['captured_at']
    try:
        ns['capture_option_strategy']({**prospective, 'contract_id': 'NEW'}, None, session)
    except HTTPException as exc:
        assert exc.status_code == 422
    else:
        raise AssertionError('A new late prospective capture must fail')
    ns['_dt'] = dt
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
    same_day_expiry = dt.date(2026, 10, 9)
    for i in range(3):
        session.add(models.OptionsFlowAlertOutcome(id=i+1, stock_id=stock.id, symbol='TEST', option_chain=f'TEST{i}', option_type='call',
            direction='bullish', fired_date=same_day_expiry, fired_at=dt.datetime(2026, 10, 9, 14), expiry=same_day_expiry,
            alert_price=100, ask_side_dominant=True, total_premium=1000 + i, entry_date=same_day_expiry, return_10d=.01, is_correct_10d=True))
    session.commit()
    args = dict(days_back=30, limit=1, offset=1, symbol='TEST', direction='bullish', sweep_only=False,
                min_premium=0, sort='total_premium', _=None, session=session)
    result = ns['options_flow_alert_performance'](**args)
    assert result['matching_count'] == result['total_count'] == 3
    assert len(result['recent_alerts']) == 1
    assert result['recent_alerts'][0]['total_premium'] == 1001
    assert result['recent_alerts'][0]['calibration_eligibility'] == 'same_day_expiry'
    assert len(result['stock_summaries']) == 1
    assert result['stock_summaries'][0]['alert_count'] == 3  # all pages, not the single returned row
    assert result['stock_summaries'][0]['bullish'] == 3
    assert result['stock_summaries'][0]['bearish'] == 0
    stats = result['by_direction'][0]['window_10d']
    assert stats['n'] == 0 and stats['excluded_total'] == 3 and stats['original_n'] == 3
    assert stats['status'] == 'insufficient_eligible_history' and stats['win_rate'] is None
    assert stats['horizon_unit'] == 'calendar_days'
    # New opportunity mail is explicit opt-in and queues through the durable outbox only.
    from src.services.option_opportunity_notifications import enqueue_if_eligible
    from src.services.option_strategy_ledger import actionable_gate
    user = models.User(username='option-user', password_hash='x', email='option@example.com', is_active=True)
    session.add(user); session.commit()
    gates = {key: True for key in (
        'direction_compatible', 'identity_verified', 'deliverable_verified',
        'market_open', 'entry_before_last_trade', 'event_coverage_verified',
        'account_permissions_verified',
    )}
    decision_facts = {**gates, 'opportunity_id': 'opp-1',
        'symbol': 'TEST', 'strategy': 'long_call',
        'contract_id': 'TEST-C100', 'quote_source': 'fixture',
        'confirmation_rule': 'above 100', 'invalidation_rule': 'below 95',
        'measurement_status': 'prospective_unmeasured',
        'decision_at': '2099-10-09T18:00:30+00:00',
        'quote_as_of': '2099-10-09T18:00:00+00:00',
        'event_at': '2099-10-09T17:55:00+00:00',
        'entry_deadline': '2099-10-09T19:00:00+00:00',
        'bid': 4.8, 'ask': 5.0, 'ask_size_contracts': 1,
        'fee_per_contract_per_side': .65, 'slippage_per_share_per_side': .05,
        'account_value': 250_000, 'open_option_risk': 0,
        'available_options_buying_power': 10_000}
    gate = actionable_gate(decision_facts)
    bound = gate['bound_decision']
    assert gate['status'] == 'actionable' and bound['quantity'] == 1
    opportunity = dict(opportunity_id='opp-1', symbol='TEST', strategy='long_call',
        contract_id='TEST-C100', entry_deadline=bound['entry_deadline'],
        quote_source=bound['quote_source'], quote_as_of=bound['quote_as_of'],
        maximum_loss=bound['maximum_loss'], quantity=bound['quantity'],
        decision_fingerprint=bound['decision_fingerprint'], confirmation_rule='above 100',
        invalidation_rule='below 95', measurement_status='prospective_unmeasured')
    assert enqueue_if_eligible(session, user=user, opportunity=opportunity, decision_facts=decision_facts)['status'] == 'not_opted_in'
    session.add(models.AlertPreference(user_id=user.id, alert_type='option_opportunity', enabled=True, source='settings'))
    session.commit()
    queued = enqueue_if_eligible(session, user=user, opportunity=opportunity, decision_facts=decision_facts)
    session.commit()
    assert queued['status'] == 'queued'
    assert enqueue_if_eligible(session, user=user, opportunity=opportunity, decision_facts=decision_facts)['status'] == 'already_queued'
    outbox = session.scalar(select(models.NotificationOutbox))
    assert outbox.alert_type == 'option_opportunity' and outbox.state == 'pending'
print('real-handler ledger and filtered-pagination probe passed')
