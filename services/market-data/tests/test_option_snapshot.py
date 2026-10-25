"""Current-chain captures must be atomic, bounded and separate from settled archives."""
import ast
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from src.services import option_snapshot as ss
from test_options_game_plan import compute_options_game_plan, _nearest_expiry_in_dte_window

NOW = datetime(2026, 10, 24, 15, tzinfo=timezone.utc)


class Redis:
    def __init__(self): self.data, self.ttls = {}, {}
    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.data: return False
        self.data[key], self.ttls[key] = value, ex
        return True
    def get(self, key): return self.data.get(key)
    def setex(self, key, ttl, value): self.data[key], self.ttls[key] = value, ttl
    def ttl(self, key): return self.ttls.get(key, -2)
    def eval(self, script, count, key, token):
        if self.data.get(key) == token:
            self.ttls[key] = int(re.search(r"KEYS\[1\],(\d+)", script).group(1))
            return 1
        return 0


def row(strike=880, side='C', expiry='261120', **extra):
    return {'option_symbol': f'MU{expiry}{side}{int(strike*1000):08d}',
        'nbbo_bid': '10', 'nbbo_ask': '10.5', 'open_interest': 100,
        'delta': '.5' if side=='C' else '-.5', 'gamma': '.02',
        'volume': 10, 'implied_volatility': '.3', 'last_tape_time': '2026-10-23T20:00:00Z', **extra}


STOCK = {'close': '877.55', 'tape_time': '2026-10-23T20:00:00Z', 'market_time': 'regular'}


def capture(monkeypatch, rows=None, stock=STOCK, rdb=None):
    monkeypatch.setattr(ss, 'PAGE_SIZE', 2)
    rows = [row(875), row(880), row(885), row(850, 'P')] if rows is None else rows
    calls = []
    def fetch(path, params=None, **kw):
        calls.append((path, params))
        return stock if path.endswith('stock-state') else rows[params['page']*2:params['page']*2+2]
    rdb = rdb or Redis()
    meta = ss.capture('MU', rdb, fetch, clock=lambda: NOW, timer=lambda: 0)
    return meta, rdb, calls


def test_complete_pagination_and_stock_reference_form_one_immutable_capture(monkeypatch):
    meta, rdb, calls = capture(monkeypatch)
    assert [c[1]['page'] for c in calls[:-1]] == [0,1,2]
    assert meta['pages']==3 and meta['contracts']==4 and meta['stock_reference']==877.55
    data = ss.load('MU', meta['id'], rdb, now=NOW)
    assert len(data['rows'])==4 and data['metadata']==meta
    assert meta['source_session']=='2026-10-23' and meta['quote_observation_time_available'] is False
    assert meta['captured_at']!=meta['stock_observed_at']
    assert rdb.ttl('options_snapshot_capture:v1:MU')==60
    assert all('false'==c[1]['exclude_zero_vol_chains'] for c in calls[:-1])


def test_refreshed_table_and_plan_use_same_rows_and_stock_even_if_forecast_is_far(monkeypatch):
    meta, rdb, _ = capture(monkeypatch)
    data = ss.load('MU', meta['id'], rdb, now=NOW)
    table = ss.chain_view(data, '2026-11-20', now=NOW)
    plan = ss.calculate_plan(data, compute_options_game_plan, _nearest_expiry_in_dte_window,
        today=NOW.date(), now=NOW, take_profit=1561.91, stop_loss=850, holding_days=14)
    leg = plan['strategy_matrix']['singles']['covered_call']['legs'][0]
    assert leg['strike']==880 and leg['price_per_share']==10.25
    assert plan['current_price']==877.55 and plan['option_snapshot']['id']==table['option_snapshot']['id']==meta['id']
    assert table['calls'][1]['bid']==10
    review=plan['strategy_matrix']['candidate_review']['covered_call']
    assert review['snapshot_id']==meta['id'] and review['listing_verification']=='provider_current_contracts'
    assert all(s['legs'][0]['expiry']=='2026-11-20' for s in review['alternatives'])
    # Search is entirely in the stored capture: no fetch callback exists here.
    assert review['failed_expiries']==[]


@pytest.mark.parametrize('field,value', [('nbbo_bid',None),('open_interest',None),('delta',None),('delta',True),('delta','nan')])
def test_missing_measurements_do_not_become_known_zeros(field,value):
    r=ss.normalize_row(row(**{field:value}), 'MU', NOW)
    key={'nbbo_bid':'bid','open_interest':'oi'}.get(field,field)
    assert r[key] is None or (field=='nbbo_bid' and r[key]==0)
    assert r['quote_observed_at'] is None


@pytest.mark.parametrize('raw', [row(option_symbol='MU1261120C00880000'),{**row(), 'strike': 900},row(option_type='put'),row(expires='2026-11-21'),{'option_symbol':'bad'},None])
def test_inconsistent_and_unsupported_identity_is_excluded(raw):
    assert ss.normalize_row(raw,'MU',NOW) is None


def test_real_zero_delta_and_missing_trade_time_are_preserved_without_fake_quote_time():
    r=ss.normalize_row(row(delta='0',last_tape_time=None),'MU',NOW)
    assert r['delta']==0 and r['last_tape_time'] is None and r['quote_observed_at'] is None


@pytest.mark.parametrize('value',['2026-10-24T20:00:00Z','2026-10-23T20:00:00','bad',None])
def test_unknown_naive_or_future_timestamps_do_not_claim_freshness(value):
    assert ss.instant(value,NOW) is None


@pytest.mark.parametrize('stock',[None,{},dict(STOCK,close='nan'),dict(STOCK,tape_time=None),dict(STOCK,tape_time='2026-10-24T20:00:00Z')])
def test_invalid_stock_reference_never_falls_back_to_archive(monkeypatch,stock):
    rdb=Redis()
    with pytest.raises(ss.SnapshotError,match='timed stock reference'):
        capture(monkeypatch,stock=stock,rdb=rdb)
    assert not any(k.startswith('options_snapshot:v1:') for k in rdb.data)


def test_repeated_provider_pages_fail_without_publishing(monkeypatch):
    monkeypatch.setattr(ss,'PAGE_SIZE',1)
    rdb=Redis()
    with pytest.raises(ss.SnapshotError) as exc:
        ss.capture('MU',rdb,lambda *a,**k:[row()],clock=lambda:NOW,timer=lambda:0)
    assert exc.value.code=='pagination_changed'
    assert not any(k.startswith('options_snapshot:v1:') for k in rdb.data)


def test_incomplete_page_cap_is_not_published(monkeypatch):
    monkeypatch.setattr(ss,'PAGE_SIZE',1);monkeypatch.setattr(ss,'MAX_PAGES',1)
    with pytest.raises(ss.SnapshotError) as exc:
        ss.capture('MU',Redis(),lambda *a,**k:[row()],clock=lambda:NOW,timer=lambda:0)
    assert exc.value.code=='page_limit'


def test_second_click_is_coalesced_by_capture_cooldown_without_provider_calls(monkeypatch):
    meta,rdb,_=capture(monkeypatch)
    def no_fetch(*a,**k): pytest.fail('provider called during cooldown')
    with pytest.raises(ss.SnapshotError) as exc: ss.capture('MU',rdb,no_fetch)
    assert exc.value.code=='busy' and exc.value.retry_after==60
    assert ss.load('MU',meta['id'],rdb,now=NOW)['metadata']==meta


@pytest.mark.parametrize('error_code',['provider','cache'])
def test_failure_does_not_relabel_or_replace_a_previous_snapshot(monkeypatch,error_code):
    meta,rdb,_=capture(monkeypatch)
    rdb.data.pop('options_snapshot_capture:v1:MU')
    original=dict(rdb.data)
    def fail(*a,**k): raise RuntimeError('provider unavailable')
    if error_code=='cache':
        rdb.setex=fail
        fetch=lambda path,**kw: STOCK if path.endswith('stock-state') else [row()]
    else: fetch=fail
    with pytest.raises(ss.SnapshotError):ss.capture('MU',rdb,fetch,clock=lambda:NOW)
    assert rdb.data[f'options_snapshot:v1:MU:{meta["id"]}']==original[f'options_snapshot:v1:MU:{meta["id"]}']


def test_expired_evicted_or_wrong_stock_snapshot_fails_closed(monkeypatch):
    meta,rdb,_=capture(monkeypatch)
    for sym,at in [('MU',NOW+timedelta(seconds=ss.TTL+1)),('AAPL',NOW)]:
        with pytest.raises(ss.SnapshotError):ss.load(sym,meta['id'],rdb,now=at)
    rdb.data.clear()
    with pytest.raises(ss.SnapshotError):ss.load('MU',meta['id'],rdb,now=NOW)


def test_corrupt_cache_is_not_a_partial_chain():
    rdb=Redis();rdb.data['options_snapshot:v1:MU:'+'a'*32]='bad'
    with pytest.raises(ss.SnapshotError):ss.load('MU','a'*32,rdb,now=NOW)


def test_unavailable_expiry_is_not_silently_replaced(monkeypatch):
    meta,rdb,_=capture(monkeypatch)
    view=ss.chain_view(ss.load('MU',meta['id'],rdb,now=NOW),'2027-01-15',now=NOW)
    assert view['available'] is False and view['reason']=='expiry_not_in_snapshot'


def test_all_three_snapshot_routes_require_advanced_access_and_capture_releases_auth_transaction():
    source=(Path(__file__).parents[1]/'src/api/routes.py').read_text()
    tree=ast.parse(source)
    for name in ('refresh_options_snapshot','get_snapshot_chain','get_snapshot_game_plan'):
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==name)
        assert 'Depends(get_advanced_user)' in ast.get_source_segment(source,fn)
    fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='refresh_options_snapshot')
    body=ast.get_source_segment(source,fn)
    assert body.index('_release_read_transaction(session')<body.index('snapshots.capture(')


def test_rate_limit_stops_capture_and_keeps_a_backoff(monkeypatch):
    error=type('UnusualWhalesRateLimitError',(Exception,),{})
    rdb=Redis();calls=[]
    def fetch(*a,**kw):calls.append(a);raise error()
    with pytest.raises(ss.SnapshotError) as exc:ss.capture('MU',rdb,fetch,clock=lambda:NOW)
    assert exc.value.code=='rate_limited' and exc.value.retry_after==60
    assert len(calls)==1 and rdb.ttl('options_snapshot_capture:v1:MU')==60


def test_cache_outage_prevents_provider_capture():
    class Broken(Redis):
        def set(self,*a,**k):raise OSError()
    with pytest.raises(ss.SnapshotError) as exc:
        ss.capture('MU',Broken(),lambda *a,**k:pytest.fail('provider called without storage'))
    assert exc.value.code=='cache_unavailable'


def test_second_capture_changes_calculation_prices_without_mutating_the_first(monkeypatch):
    first,rdb,_=capture(monkeypatch,[row(880,nbbo_bid='10',nbbo_ask='10.5')])
    before=ss.load('MU',first['id'],rdb,now=NOW)
    rdb.data.pop('options_snapshot_capture:v1:MU')
    second,rdb,_=capture(monkeypatch,[row(880,nbbo_bid='20',nbbo_ask='20.5')],rdb=rdb)
    def plan(data):
        return ss.calculate_plan(data,compute_options_game_plan,_nearest_expiry_in_dte_window,
            today=NOW.date(),now=NOW)['strategy_matrix']['singles']['covered_call']
    a,b=plan(ss.load('MU',first['id'],rdb,now=NOW)),plan(ss.load('MU',second['id'],rdb,now=NOW))
    assert a['net_per_contract']==1025 and b['net_per_contract']==2025
    assert a['breakeven']==867.3 and b['breakeven']==857.3
    assert ss.load('MU',first['id'],rdb,now=NOW)==before
    assert first['id']!=second['id']


def test_current_route_uses_its_snapshot_and_does_not_query_archive(monkeypatch):
    from src.api import routes
    from src.services import options_scope
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None): return NOW
    monkeypatch.setattr(options_scope, 'datetime', Clock)
    meta,rdb,_=capture(monkeypatch)
    data=ss.load('MU',meta['id'],rdb,now=NOW)
    monkeypatch.setattr(routes,'_read_option_snapshot',lambda sym,token: data)
    monkeypatch.setattr(routes,'_today_et',lambda:NOW.date())
    monkeypatch.setattr(routes,'_uw_option_chain',lambda *a,**k:pytest.fail('archive chain read'))
    # Freeze the expiry scope as well as the plan date; this must keep passing after November.
    response=routes.get_snapshot_game_plan('MU',meta['id'],stop_loss=850,take_profit=1561.91,
        shares=100,holding_days=14,min_dte=14,max_dte=60,min_abs_delta=None,max_abs_delta=None,_user=object())
    assert response['option_snapshot']['id']==meta['id']
    assert response['strategy_matrix']['singles']['covered_call']['legs'][0]['strike']==880
    assert routes.get_snapshot_chain('MU',meta['id'],'2026-11-20',_user=object())['calls'][1]['bid']==10


@pytest.mark.parametrize('bound',['MAX_RAW','MAX_COMPRESSED'])
def test_oversized_snapshot_never_publishes_a_partial_result(monkeypatch,bound):
    monkeypatch.setattr(ss,bound,10)
    rdb=Redis()
    with pytest.raises(ss.SnapshotError) as exc:capture(monkeypatch,rdb=rdb)
    assert exc.value.code=='snapshot_too_large'
    assert not any(k.startswith('options_snapshot:v1:') for k in rdb.data)


def test_capture_deadline_stops_before_another_provider_page():
    times=iter([0,46])
    with pytest.raises(ss.SnapshotError) as exc:
        ss.capture('MU',Redis(),lambda *a,**k:pytest.fail('called after deadline'),clock=lambda:NOW,timer=lambda:next(times))
    assert exc.value.code=='capture_timeout'


def test_single_expiration_scope_is_explicit_and_cannot_include_other_dates(monkeypatch):
    monkeypatch.setattr(ss,'PAGE_SIZE',2)
    requests=[]
    def fetch(path,params=None,**kw):
        requests.append((path,params))
        return STOCK if path.endswith('stock-state') else [row()]
    rdb=Redis();meta=ss.capture('MU',rdb,fetch,expiry='2026-11-20',clock=lambda:NOW,timer=lambda:0)
    assert meta['capture_expiry']=='2026-11-20' and meta['expiries']==['2026-11-20']
    assert requests[0][1]['expiry']=='2026-11-20'
    assert ss.load('MU',meta['id'],rdb,now=NOW)['metadata']['capture_expiry']=='2026-11-20'
    rdb=Redis()
    with pytest.raises(ss.SnapshotError) as exc:
        ss.capture('MU',rdb,lambda *a,**kw:[row(expiry='261218')],expiry='2026-11-20',clock=lambda:NOW,timer=lambda:0)
    assert exc.value.code=='provider_scope_mismatch'


@pytest.mark.parametrize('expiry',['bad','2026-01-01','20261120'])
def test_bad_refresh_scope_never_calls_provider(expiry):
    with pytest.raises(ss.SnapshotError) as exc:
        ss.capture('MU',Redis(),lambda *a,**k:pytest.fail('bad scope called provider'),expiry=expiry,clock=lambda:NOW)
    assert exc.value.code=='invalid_expiry'


def test_closed_expiry_scope_does_not_fetch_even_when_date_is_today():
    friday_after_close=datetime(2026,10,23,21,tzinfo=timezone.utc)
    with pytest.raises(ss.SnapshotError) as exc:
        ss.capture('MU',Redis(),lambda *a,**k:pytest.fail('closed expiry fetched'),expiry='2026-10-23',clock=lambda:friday_after_close)
    assert exc.value.code=='expiry_closed'
