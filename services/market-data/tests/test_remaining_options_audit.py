from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock
import pytest
from src.services.options_scope import expiry_scope
from src.services.options_contract import contract_terms
from src.services.dark_pool_sampling import record, summarize, coverage


@pytest.mark.parametrize('symbol', ['SPX', 'SPXW', '^SPX', 'VIX', 'NDX', 'SPXO'])
def test_index_history_is_preserved_but_cannot_become_current(symbol):
    scope = expiry_scope(['2026-11-20'], date(2026,10,23), datetime(2026,11,20,14,tzinfo=timezone.utc), symbol=symbol)
    assert scope['historical_expiries'] == ['2026-11-20']
    assert scope['current_expiries'] == []
    assert scope['excluded_from_current'][0]['reason'] == 'unsupported_product_settlement'
    assert not contract_terms('SPXW261120C05000000', symbol)['automatic_payoff_supported']


def test_ordinary_equity_remains_current():
    assert expiry_scope(['2026-11-20'], date(2026,10,23), datetime(2026,10,23,tzinfo=timezone.utc), symbol='MU')['current_expiries'] == ['2026-11-20']


def test_missing_telemetry_is_never_complete():
    assert summarize([])['status'] == 'unobserved'
    redis = MagicMock()
    redis.zrangebyscore.side_effect = RuntimeError('redis unavailable')
    assert coverage(redis,'MU')['status'] == 'query_failed'


def test_busy_bursts_do_not_hide_poll_gaps_or_limit_saturation():
    start = datetime(2026,10,23,13,30,tzinfo=timezone.utc)
    rows = [{'at':start.isoformat(),'status':'ok','count':50,'saturated':True},
            {'at':(start+timedelta(hours=3)).isoformat(),'status':'timeout','count':0,'saturated':False}]
    result = summarize(rows,now=start+timedelta(hours=4))
    assert result['status'] == 'degraded'
    assert result['failed_polls'] == result['saturated_windows'] == 1
    assert result['missing_session_slots'] == 15
    assert result['identity_status'] == 'legacy_tuple_identity_unverified'


def test_closed_market_does_not_create_missing_session_slots():
    saturday = datetime(2026,10,24,13,30,tzinfo=timezone.utc)
    assert summarize([{'at':saturday.isoformat(),'status':'empty','saturated':False}],now=saturday+timedelta(hours=5))['missing_session_slots'] == 0


def test_telemetry_is_bounded_and_failure_does_not_break_feed():
    redis = MagicMock()
    record(redis,'MU','ok',50)
    assert redis.pipeline.return_value.zremrangebyrank.call_args.args == ('stockai:darkpool:polls:v1:MU',0,-4097)
    redis.pipeline.side_effect = RuntimeError('down')
    record(redis,'MU','timeout')
