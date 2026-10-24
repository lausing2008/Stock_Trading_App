import json
from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest

from src.services.options_dominance import DominanceAccumulator

SESSION = date(2026, 10, 22)
EXP = '2026-11-20'


def frame(volume, oi=None):
    return pd.DataFrame({'volume': volume, 'openInterest': volume if oi is None else oi})


def summary(calls, puts, requested=None, coverage='complete'):
    acc = DominanceAccumulator('ANY', SESSION, requested or [EXP])
    acc.add(EXP, calls, puts, source_session=SESSION, coverage_status=coverage)
    return acc.summary()


def test_volume_and_oi_remain_separate_when_they_favor_different_sides():
    result = summary(frame([3000], [100]), frame([1000], [900]))
    volume, oi, premium, aggressor = result['rows']
    assert volume['composition'] == 'call_heavy' and volume['call_share'] == .75
    assert oi['composition'] == 'more_puts' and oi['put_share'] == .9
    assert result['volume_oi_disagree']
    assert result['directional_intent'] == 'not_established' and result['actionability'] == 'research_only'
    assert premium['status'] == aggressor['status'] == 'unavailable'
    assert oi['snapshot_session'] == '2026-10-22' and oi['reported_as_of'] is None
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize('calls,puts,label', [(1296, 1000, 'mixed'), (1300, 1000, 'call_heavy'),
    (800, 1000, 'put_heavy'), (801, 1000, 'mixed'), (1000, 0, 'call_heavy'), (0, 1000, 'put_heavy')])
def test_exact_volume_cut_points_and_measured_zero_side(calls, puts, label):
    row = summary(frame([calls]), frame([puts]))['rows'][0]
    assert row['composition'] == label
    assert row['call_share'] == calls / (calls + puts)


@pytest.mark.parametrize('bad', [None, float('nan'), float('inf'), -1, .5, True])
def test_unknown_or_invalid_measurement_does_not_become_a_zero_or_share(bad):
    row = summary(frame([100, bad]), frame([50]))['rows'][0]
    assert row['status'] == 'partial'
    assert row['sides']['call']['value'] is None
    assert row['sides']['call']['observed_subtotal'] == 100
    assert row['sides']['call']['known'] == 1 and row['sides']['call']['total'] == 2
    assert row['call_share'] is None and row['put_share'] is None


def test_unknown_entire_side_and_empty_side_are_not_all_calls():
    for puts in (frame([None]), frame([])):
        row = summary(frame([100]), puts)['rows'][0]
        assert row['put_share'] is None and row['composition'] == 'unmeasured'
        assert row['sides']['put']['value'] is None


def test_zero_and_missing_are_distinct_and_oi_survives_zero_volume():
    result = summary(frame([0], [300]), frame([0], [100]))
    assert result['rows'][0]['status'] == 'zero' and result['rows'][0]['call_share'] is None
    assert result['rows'][1]['composition'] == 'more_calls'
    result = summary(frame([None], [300]), frame([None], [100]))
    assert result['rows'][0]['status'] == 'unavailable'
    assert result['rows'][1]['status'] == 'measured'


def test_partial_and_unverified_capture_scope_stays_visible():
    for coverage in ('incomplete', 'partial', 'unverified_legacy'):
        result = summary(frame([100]), frame([50]), requested=[EXP, '2026-12-18'], coverage=coverage)
        assert result['scope']['missing'] == ['2026-12-18']
        assert result['scope']['coverage_status'] == 'partial'
        assert result['scope']['slices'][0]['coverage_status'] == coverage
    assert summary(frame([100]), frame([50]), coverage='incomplete')['scope']['coverage_status'] == 'partial'
    assert summary(frame([100]), frame([50]), coverage='unverified_legacy')['scope']['coverage_status'] == 'unverified'


def test_mixed_session_unrequested_and_duplicate_slices_are_refused():
    acc = DominanceAccumulator('ANY', SESSION, [EXP])
    for session, expiry in [('2026-10-21', EXP), (SESSION, '2026-12-18')]:
        with pytest.raises(ValueError):
            acc.add(expiry, frame([1]), frame([1]), source_session=session)
    acc.add(EXP, frame([1]), frame([1]), source_session=SESSION)
    with pytest.raises(ValueError):
        acc.add(EXP, frame([1]), frame([1]), source_session=SESSION)


@pytest.fixture
def route_inputs(monkeypatch):
    from src.api import routes
    cache = {}
    reads = []
    monkeypatch.setattr(routes, '_get_redis', lambda: SimpleNamespace(get=cache.get, setex=lambda k, ttl, v: cache.update({k: v})))
    monkeypatch.setattr(routes, '_uw_session_expiries', lambda *_: (SESSION, [EXP]))
    def chain(_session, symbol, expiry, **kwargs):
        reads.append((symbol, expiry, kwargs['as_of']))
        def full(vol, oi):
            return pd.DataFrame([{'strike': 100, 'volume': vol, 'openInterest': oi,
                'lastPrice': 0., 'last_price_available': False, 'bid': 1., 'ask': 2.,
                'impliedVolatility': .3, 'inTheMoney': None, 'option_symbol': None}])
        return SimpleNamespace(calls=full(3000, 100), puts=full(1000, 900), as_of=SESSION,
                               coverage_status='complete', coverage_reason=None)
    monkeypatch.setattr(routes, '_uw_option_chain', chain)
    return routes, reads, chain


def test_actual_route_and_dedicated_endpoint_share_one_cached_chain_read(route_inputs):
    routes, reads, _ = route_inputs
    result = routes.get_options_flow('any', session=None)
    assert result['available'] and result['dominance']['symbol'] == 'ANY'
    assert result['dominance']['computed_at'] == result['computed_at']
    assert result['dominance']['scope']['included'] == result['expiries_used']
    from src.services.options_dominance import flow_cache_key
    assert json.loads(routes._get_redis().get(flow_cache_key('any')))['dominance'] == result['dominance']
    assert routes.get_options_dominance('any', session=None) == result['dominance']
    assert reads == [('ANY', EXP, SESSION)]


def test_route_does_not_pool_a_different_session(route_inputs, monkeypatch):
    routes, _, chain = route_inputs
    def wrong(*a, **kw):
        result = chain(*a, **kw)
        result.as_of = '2026-10-21'
        return result
    monkeypatch.setattr(routes, '_uw_option_chain', wrong)
    result = routes.get_options_flow('any', session=None)
    assert result['reason'] == 'chain_read_failed'
    assert result['dominance']['available'] is False
    assert result['dominance']['scope']['included'] == []


@pytest.mark.parametrize('volume,reason', [(0, 'no_volume'), (None, 'volume_unmeasured')])
def test_route_exposes_oi_when_volume_is_zero_or_unknown(route_inputs, monkeypatch, volume, reason):
    routes, _, chain = route_inputs
    def no_volume(*a, **kw):
        result = chain(*a, **kw)
        result.calls['volume'] = volume
        result.puts['volume'] = volume
        return result
    monkeypatch.setattr(routes, '_uw_option_chain', no_volume)
    result = routes.get_options_flow('any', session=None)
    assert result['available'] is False and result['reason'] == reason
    assert result['dominance']['available'] is True
    assert result['dominance']['rows'][1]['composition'] == 'more_puts'


def test_dedicated_endpoint_preserves_provider_failure_reason(monkeypatch):
    from src.api import routes
    monkeypatch.setattr(routes, 'get_options_flow', lambda *a, **kw: {'available': False, 'reason': 'fetch_error'})
    result = routes.get_options_dominance('any', session=None)
    assert not result['available'] and result['reason'] == 'fetch_error'
