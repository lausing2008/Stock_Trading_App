"""Behavioural tests of the shipped pure engine, not restated signal lambdas."""
import copy
import json
import math
from datetime import datetime,timedelta,timezone
from dataclasses import replace
import pytest
from src.services.little_rzy import Engine,Config,replay
from src.services.rzy_backtest import compare


def candle(i,close=None,**over):
    c=100+.12*i+5*math.sin(i*.22) if close is None else close
    t=datetime(2026,1,1,tzinfo=timezone.utc)+timedelta(minutes=5*i)
    return dict(time=t.isoformat(),closed_at=(t+timedelta(minutes=5)).isoformat(),complete=True,
        open=c-.2,high=c+.6,low=c-.8,close=c,volume=10000,provider='synthetic_fixture',delay='none',session='REGULAR',
        price_basis='consistent_adjusted',freshness='fixture',execution_eligible=True,shortable=True,borrow_rate_annual=.03,**over)


def armed(config=None):
    engine=Engine('TEST','5m',config); bars=[]
    for i in range(150):
        b=candle(i);bars.append(b);engine.step(b)
        if engine.s['active']: return engine,bars,engine.s['structures'][engine.s['active']]
    raise AssertionError('The real default thresholds cannot arm this qualified structure')


def triggered(config=None,fill=True,trigger_volume=10000):
    e,bars,st=armed(config)
    for off in (1,2):
        i=len(bars);line=e.line(st,i)
        b=candle(i); b.update(open=line+.2,close=line+.1,low=line-.1,high=line+.4) if off==1 else b.update(open=line+.3,close=line+.7,low=line-.1,high=line+.8)
        if off==2: b['volume']=trigger_volume
        bars.append(b);events=e.step(b)
    assert any(x['event_type']=='TRIGGERED' for x in events)
    if fill:
        i=len(bars);line=e.line(st,i);b=candle(i);b.update(open=line+.8,close=line+1,low=line+.3,high=line+1.2)
        bars.append(b);events=e.step(b)
        assert any(x['event_type']=='PAPER_FILL' for x in events)
    return e,bars,st


def test_pivots_are_unknown_until_right_hand_candles_complete():
    e=Engine('T','5m',Config(pivot_left=2,pivot_right=2))
    bars=[candle(i,c) for i,c in enumerate([100,101,105,101,100])]
    for b in bars[:4]: assert not any(x.get('pivot',{}).get('index')==2 for x in e.step(b))
    events=e.step(bars[4]);pivot=next(x['pivot'] for x in events if x.get('pivot',{}).get('index')==2)
    assert pivot['pivot_time']==bars[2]['time'];assert pivot['confirmed_at']==bars[4]['closed_at']


def test_population_bands_and_wilder_atr_seed():
    e=Engine('T','5m',Config(bb_length=3,atr_length=3,pivot_left=2,pivot_right=2))
    for i,c in enumerate([100,101,102,104]): e.step(candle(i,c))
    first=e.s['indicators'][2]; assert first['middle_band']==101
    assert first['upper_band']==pytest.approx(101+2*math.sqrt(2/3))
    assert first['atr']==pytest.approx(sum(x['tr'] for x in e.s['indicators'][:3])/3)
    assert e.s['indicators'][3]['atr']==pytest.approx((first['atr']*2+e.s['indicators'][3]['tr'])/3)


def test_full_stream_replay_and_checkpoint_resume_are_identical():
    _,bars,_=triggered();bars.extend(candle(i) for i in range(len(bars),180))
    full,expected=replay('TEST','5m',bars)
    first,events=replay('TEST','5m',bars[:55]);resumed=Engine('TEST','5m',checkpoint=json.loads(json.dumps(first.s)))
    for b in bars[55:]: events.extend(resumed.step(b))
    assert events==expected; assert resumed.s==full.s


def test_target_geometry_uses_extreme_index_and_stays_frozen():
    e,bars,st=armed();before=copy.deepcopy(st)
    expected=st['extreme']['price']-e.line(st,st['extreme']['index'])
    assert st['D']==pytest.approx(expected);assert st['target']==pytest.approx(st['extreme']['price']+expected)
    for i in range(len(bars),len(bars)+50):e.step(candle(i))
    assert st['anchors']==before['anchors']; assert st['D']==before['D']; assert st['target']==before['target']
    assert st['sequence_confidence']=='unknown'  # no invented first-in-trend claim


def test_entries_wait_for_the_bar_after_trigger_and_charge_costs():
    e,bars,st=triggered(fill=False);pos=e.s['positions'][st['id']]
    assert pos['state']=='PENDING_ENTRY';assert 'fill' not in pos
    b=candle(len(bars));line=e.line(st,len(bars));b.update(open=line+.8,close=line+1,low=line+.3,high=line+1.2)
    events=e.step(b);fill=next(x for x in events if x['event_type']=='PAPER_FILL')
    assert fill['actual_paper_fill']>b['open'];assert fill['paper_fill_time']==b['time']
    assert pos['stop']==st['stop'];assert pos['fees']>0


@pytest.mark.parametrize('field,value,reason',[('execution_eligible',False,'data_basis'),('volume',0,'liquidity')])
def test_actual_fill_requires_data_and_liquidity(field,value,reason):
    e,bars,st=triggered(fill=False,trigger_volume=value if field=='volume' else 10000);b=candle(len(bars))
    if field!='volume': b[field]=value
    events=e.step(b);cancel=next(x for x in events if x['event_type']=='ENTRY_CANCELLED')
    assert reason in cancel['rejection_reason'];assert e.s['positions'][st['id']]['state']=='CLOSED';assert st['state']=='REJECTED'


def test_gap_past_target_cancels_entry_rather_than_scoring_a_win():
    e,bars,st=triggered(fill=False);b=candle(len(bars),st['target']+2)
    events=e.step(b);assert any(x['event_type']=='ENTRY_CANCELLED' for x in events)
    assert not any(x['event_type']=='PAPER_EXIT' for x in events)


def test_gap_through_stop_fills_at_worse_open_and_both_touch_assumes_stop_first():
    for gap in (False,True):
        e,bars,st=triggered();p=e.s['positions'][st['id']];stop=p['stop'];b=candle(len(bars))
        b.update(open=stop-2 if gap else p['fill'],close=stop-.5,low=stop-3,high=st['target']+1)
        events=e.step(b);exit=next(x for x in events if x['event_type']=='PAPER_EXIT')
        assert exit['exit_price']<stop if gap else exit['exit_price']<=stop
        assert exit['net_pnl_per_unit']<0; assert p['remaining']==0


def test_partial_runner_never_assumes_favourable_intrabar_order_and_recovers_after_restart():
    cfg=Config(partial_runner=True);e,bars,st=triggered(cfg);p=e.s['positions'][st['id']]
    b=candle(len(bars));b.update(open=p['fill']+1,low=p['fill']+.05,high=st['runner']+1,close=st['target'])
    state=json.loads(json.dumps(e.s));clone=Engine('TEST','5m',cfg,state)
    expected=e.step(b);assert clone.step(b)==expected
    exits=[x for x in expected if x['event_type']=='PAPER_EXIT'];assert exits[0]['exit_quantity']==.5
    assert exits[-1]['reason']=='partial_stop_conservative_order';assert p['state']=='CLOSED'


def test_structural_invalidation_exits_next_open_before_later_intrabar_extremes():
    e,bars,st=triggered();p=e.s['positions'][st['id']];p['exit_pending']=True
    b=candle(len(bars));b.update(open=p['fill']+1,close=p['stop'],low=p['stop']-5,high=p['fill']+2)
    events=e.step(b);exit=next(x for x in events if x['event_type']=='PAPER_EXIT')
    assert exit['reason']=='structural_invalidation_next_open';assert exit['exit_price']>p['stop']


def test_duplicate_bar_is_no_op_and_correction_is_not_silently_applied():
    e,bars,st=armed();assert e.step(copy.deepcopy(bars[-1]))==[]
    corrected=dict(bars[-1],volume=bars[-1]['volume']+1)
    with pytest.raises(ValueError,match='revised'):e.step(corrected)


@pytest.mark.parametrize('bad',[{'complete':False},{'close':float('nan')},{'low':-1},{'high':1}])
def test_invalid_or_forming_candles_fail_explicitly(bad):
    b=candle(0);b.update(bad)
    with pytest.raises(ValueError):Engine('T','5m').step(b)


def test_unknown_or_invalid_configuration_is_refused():
    for cfg in ({'pivot_right':0},{'pivot_right':1.5},{'bb_std':float('nan')},{'no_chase_filter':'false'},{'retracement_min':.9}):
        with pytest.raises(ValueError):Config(**cfg)


def test_backtest_partitions_and_required_variants_are_explicit():
    _,bars,_=triggered();bars.extend(candle(i) for i in range(len(bars),200))
    report=compare('TEST','5m',bars)
    assert set(report['variants'])=={'baseline','no_bollinger_context','no_chase_filter','unrestricted_sequence','partial_runner','line_stop'}
    assert 'no positions' in report['partition_policy']
    for v in report['variants'].values():
        assert len(v['walk_forward'])==2
        assert 'unresolved_or_boundary_censored' in v['held_out']
        assert v['held_out']['conclusion']=='insufficient evidence'


def test_equal_extrema_choose_earliest_and_ambiguous_outside_candle_is_not_two_pivots():
    e=Engine('T','5m',Config(pivot_left=1,pivot_right=1))
    bars=[candle(i,c) for i,c in enumerate([100,105,105,100])]
    events=[]
    for b in bars:events.extend(e.step(b))
    highs=[x['pivot'] for x in events if x.get('pivot',{}).get('kind')=='high']
    assert len(highs)==1;assert highs[0]['index']==1
    e=Engine('T','5m',Config(pivot_left=1,pivot_right=1));bars=[candle(i,100) for i in range(3)]
    bars[1].update(high=110,low=90)
    for b in bars:events=e.step(b)
    assert not any(x['event_type']=='PIVOT_CONFIRMED' for x in events)


@pytest.mark.parametrize('failure',['invalidated','target'])
def test_confirmation_delay_replays_intervening_bars_before_arming(failure):
    e,bars,st=armed();e.s['structures']={};e.s['active']=None;e.s['last_structure']=None
    a,b,c=copy.deepcopy(st['anchors']);j=c['index']+1
    if failure=='invalidated':
        e.s['bars'][j]['close']=e.line(st,j)-1;e.s['bars'][j]['low']=min(e.s['bars'][j]['low'],e.s['bars'][j]['close']-.1)
    else:e.s['bars'][j]['high']=st['target']+1
    e._detect(a,b,c,len(bars)-1)
    new=e.s['structures'][st['id']]
    assert new['state']==('INVALIDATED' if failure=='invalidated' else 'COMPLETED')
    assert e.s['active'] is None;assert not e.s['positions']


def test_known_sequence_three_is_counted_but_not_armed_and_duplicate_does_not_increment():
    e,bars,st=armed();e.s['structures']={};e.s['active']=None;e.s['last_structure']=None;e.s['sequence']=2;e.s['epoch_known']=True
    e._detect(*copy.deepcopy(st['anchors']),len(bars)-1)
    assert e.s['sequence']==3; assert e.s['structures'][st['id']]['reason']=='sequence_filter'
    e._detect(*copy.deepcopy(st['anchors']),len(bars)-1)
    assert e.s['sequence']==3


def test_lifetime_and_zero_bandwidth_do_not_create_spurious_entries():
    e,bars,st=armed(Config(max_armed_bars=1))
    for off in (1,2):
        i=len(bars);line=e.line(st,i);b=candle(i);b.update(open=line+.2,close=line+.1,low=line+.05,high=line+.3);bars.append(b);e.step(b)
    assert st['state']=='EXPIRED';assert st['reason']=='armed_lifetime'
    e=Engine('T','5m')
    for i in range(30):e.step(dict(candle(i,100),open=100,high=100,low=100))
    assert e.s['indicators'][-1]['percent_b'] is None;assert not e.s['positions']


def test_mirrored_stream_opens_short_only_with_verified_borrow_inputs():
    _,bars,_=triggered()
    def mirror(b):return dict(b,open=300-b['open'],close=300-b['close'],high=300-b['low'],low=300-b['high'])
    short=[mirror(b) for b in bars]
    e,events=replay('TEST','5m',short)
    assert any(x['event_type']=='TRIGGERED' and x['position_action']=='OPEN_SHORT' for x in events)
    assert any(x['event_type']=='PAPER_FILL' and x['position_action']=='OPEN_SHORT' for x in events)
    short[-1]['shortable']=None
    _,events=replay('TEST','5m',short)
    assert any(x['event_type']=='ENTRY_CANCELLED' and 'shortability' in x['rejection_reason'] for x in events)


def test_no_chase_is_a_separate_block_not_a_midline_cross_requirement():
    e,bars,st=armed()
    i=len(bars);line=e.line(st,i);b=candle(i);b.update(open=line+.2,close=line+.1,low=line-.1,high=line+.4);bars.append(b);e.step(b)
    i=len(bars);line=e.line(st,i);b=candle(i);b.update(open=line+.3,close=st['target']-.1,high=st['target']-.01,low=line-.1)
    events=e.step(b)
    assert any('NO_CHASE_FILTER' in x['failed_rules'] for x in events)
    assert st['state']=='ARMED'


def test_completed_structure_can_later_produce_watch_without_automatic_reversal():
    e,bars,st=triggered();p=e.s['positions'][st['id']]
    def add(close,low=None,high=None):
        i=len(bars);b=candle(i,close);b.update(low=low if low is not None else close-.8,high=high if high is not None else close+.6)
        bars.append(b);return e.step(b)
    add(st['target'],low=p['fill']+.5,high=st['target']+1)
    assert p['state']=='CLOSED'
    add(e.line(st,len(bars))-.2)
    add(e.s['indicators'][-1]['middle_band']+5)
    events=add(e.s['indicators'][-1]['middle_band']-2)
    watch=next(x for x in events if x['event_type']=='REVERSAL_WATCH')
    assert watch['direction']=='short';assert watch['position_action']=='WATCH_ONLY'
    assert not any(x['event_type']=='TRIGGERED' for x in events)


def test_replay_orders_actual_instants_and_refuses_boolean_prices():
    engine=Engine('T','5m')
    first=candle(0);engine.step(first)
    other=dict(first,time='2025-12-31T19:00:00-05:00',closed_at='2025-12-31T19:05:00-05:00')
    assert engine.step(other)==[]
    earlier=candle(1);earlier.update(time='2026-01-01T01:00:00+02:00',closed_at='2026-01-01T01:05:00+02:00')
    with pytest.raises(ValueError,match='Out-of-order'): engine.step(earlier)
    invalid=candle(1);invalid['volume']=True
    with pytest.raises(ValueError,match='Invalid volume'): engine.step(invalid)
    naive=candle(1);naive['time']='2026-01-01T00:05:00'
    with pytest.raises(ValueError,match='explicit timezone'): engine.step(naive)


def test_next_open_liquidity_uses_only_already_completed_trigger_volume():
    cfg=Config(min_bar_volume=5000)
    engine,bars,st=triggered(cfg,fill=False)
    checkpoint=copy.deepcopy(engine.s);fills=[]
    for final_entry_volume in (1,1000000):
        replayed=Engine('TEST','5m',cfg,copy.deepcopy(checkpoint))
        i=len(bars);line=replayed.line(st,i);bar=candle(i)
        bar.update(open=line+.8,close=line+1,low=line+.3,high=line+1.2,volume=final_entry_volume)
        events=replayed.step(bar)
        fills.append(next(x['actual_paper_fill'] for x in events if x['event_type']=='PAPER_FILL'))
    assert fills[0]==fills[1]


def test_runner_stopped_after_banked_primary_target_is_completed_not_invalidated():
    cfg=Config(partial_runner=True);e,bars,st=triggered(cfg);p=e.s['positions'][st['id']]
    b=candle(len(bars));b.update(open=p['fill']+1,low=p['fill']+.05,high=st['runner']+1,close=st['target'])
    e.step(b)
    assert p['state']=='CLOSED' and p.get('partial_done');assert st['state']=='COMPLETED'


def _random_stream(seed,n=3000):
    import random
    rng=random.Random(seed);p=100.;t=datetime(2026,1,5,14,30,tzinfo=timezone.utc);out=[]
    for k in range(n):
        o=p*(1+rng.gauss(0,.002)) if rng.random()<.05 else p
        c=max(1,o+rng.gauss(.02*((k//300)%2*2-1),.5));h=max(o,c)+abs(rng.gauss(0,.25));l=min(o,c)-abs(rng.gauss(0,.25));p=c
        out.append(dict(time=t.isoformat(),closed_at=(t+timedelta(minutes=5)).isoformat(),complete=True,open=o,high=h,low=l,close=c,
            volume=1000.,execution_eligible=True,shortable=True,borrow_rate_annual=.02));t+=timedelta(minutes=5)
    return out


@pytest.mark.parametrize('cfg',[Config(),Config(partial_runner=True),Config(sequence_filter=False,no_chase_filter=False,bollinger_context=False)])
def test_bounded_checkpoint_is_event_identical_to_unbounded_history(cfg):
    class Small(Engine): RETAIN=60;TRIM_BATCH=7;KEEP_PIVOTS=6;KEEP_STRUCTURES=5;KEEP_CLOSED_POSITIONS=3
    class Big(Engine): RETAIN=10**9;KEEP_PIVOTS=10**9;KEEP_STRUCTURES=10**9;KEEP_CLOSED_POSITIONS=10**9
    for seed in range(3):
        bars=_random_stream(seed);a=Big('X','5m',cfg);b=Small('X','5m',cfg);ea=[];eb=[]
        for k,x in enumerate(bars):
            ea+=a.step(x)
            if k and k%997==0: b=Small('X','5m',cfg,json.loads(json.dumps(b.s)))
            eb+=b.step(x)
        assert ea==eb and any(e['event_type']=='PAPER_EXIT' for e in ea)
        assert len(b.s['bars'])<=Small.RETAIN+Small.TRIM_BATCH+3*cfg.pivot_left+cfg.pivot_right
        assert len(json.dumps(b.s))<len(json.dumps(a.s))/10


def test_paper_trades_obey_entry_fill_and_exit_invariants_on_random_streams():
    for cfg in (Config(),Config(partial_runner=True),Config(structural_stop=False)):
        fr=(cfg.spread_bps/2+cfg.slippage_bps)/1e4;trades=0
        for seed in range(8):
            e=Engine('X','5m',cfg);trig={}
            for i,b in enumerate(_random_stream(seed,2500)):
                busy=any(p['state'] in ('OPEN','PARTIAL','PENDING_ENTRY') for p in e.s['positions'].values())
                for ev in e.step(b):
                    t=ev['event_type'];sid=ev['structure_id'];s=1 if ev['direction']=='long' else -1
                    if t=='TRIGGERED':
                        assert not busy and ev['reward_risk']>=cfg.min_rr
                        assert s*(ev['planned_entry']-ev['stop'])>0 and s*(ev['primary_target']-ev['planned_entry'])>0
                        line,tol=ev['trendline_value'],cfg.touch_atr*ev['atr']
                        assert b['low']<=line+tol and b['high']>=line-tol and s*(b['close']-line)>0 and s*(b['close']-b['open'])>0
                        trig[sid]=i
                    if t in ('PAPER_FILL','ENTRY_CANCELLED'): assert trig[sid]==i-1
                    if t=='PAPER_FILL':
                        trades+=1;assert ev['actual_paper_fill']==pytest.approx(b['open']*(1+s*fr))
                        assert s*(ev['actual_paper_fill']-ev['stop'])>0 and s*(ev['primary_target']-ev['actual_paper_fill'])>0
                    if t=='PAPER_EXIT':
                        raw=ev['exit_price']/(1-s*fr);assert b['low']-1e-9<=raw<=b['high']+1e-9
                        if 'stop' in ev['reason']: assert s*(raw-e.s['positions'][sid]['stop'])<=1e-9
                    if t=='EXHAUSTION_WATCH': assert ev['trend_epoch_id']==e.s['epoch']
        assert trades>5


@pytest.mark.parametrize('value',['false','true',1,None,'missing'])
def test_rza05_only_boolean_true_is_execution_evidence(value):
    e,bars,st=triggered(fill=False);b=candle(len(bars))
    if value=='missing': b.pop('execution_eligible')
    else: b['execution_eligible']=value
    events=e.step(b)
    assert not any(x['event_type']=='PAPER_FILL' for x in events)
    cancel=next(x for x in events if x['event_type']=='ENTRY_CANCELLED')
    assert 'data_basis_or_freshness_unverified' in cancel['failed_rules']


def _short_pending():
    bars=[];e=Engine('TEST','5m')
    for i in range(150):
        b=candle(i,close=200-(100+.12*i+5*math.sin(i*.22))+100)
        b.update(open=b['close']+.2,high=b['close']+.8,low=b['close']-.6);bars.append(b);e.step(b)
        if e.s['active'] and e.s['structures'][e.s['active']]['direction']=='short': break
    st=e.s['structures'][e.s['active']]
    for off in (1,2):
        i=len(bars);line=e.line(st,i);b=candle(i)
        b.update(open=line-.2,close=line-.1,low=line-.4,high=line+.1) if off==1 else b.update(open=line-.3,close=line-.7,low=line-.8,high=line+.1)
        bars.append(b);events=e.step(b)
    assert any(x['event_type']=='TRIGGERED' for x in events)
    return e,bars


@pytest.mark.parametrize('borrow,invalid',[(None,False),('x',True),(True,True),(-.1,True),(.03,False)])
def test_rza06_missing_borrow_is_standing_but_malformed_borrow_is_invalid(borrow,invalid):
    e,bars=_short_pending();b=candle(len(bars));b.update(execution_eligible=False,shortable=None,borrow_rate_annual=borrow)
    cancel=next(x for x in e.step(b) if x['event_type']=='ENTRY_CANCELLED')
    assert ('borrow_cost_invalid' in cancel['failed_rules'])==invalid


def test_rza03_backtest_ledger_survives_live_retention_caps(monkeypatch):
    from src.services import little_rzy
    from src.services.rzy_backtest import run
    import random
    rng=random.Random(0);p=100.;t=datetime(2026,1,5,14,30,tzinfo=timezone.utc);bars=[]
    for k in range(6000):
        o=p;c=max(1,o+rng.gauss(.02*((k//300)%2*2-1),.5));h=max(o,c)+abs(rng.gauss(0,.25));l=min(o,c)-abs(rng.gauss(0,.25));p=c
        bars.append(dict(time=t.isoformat(),closed_at=(t+timedelta(minutes=5)).isoformat(),complete=True,open=o,high=h,low=l,close=c,volume=1000.,
                         execution_eligible=True,shortable=True,borrow_rate_annual=.02));t+=timedelta(minutes=5)
    cfg=Config(sequence_filter=False,no_chase_filter=False,bollinger_context=False)
    monkeypatch.setattr(little_rzy.Engine,'KEEP_CLOSED_POSITIONS',3)
    monkeypatch.setattr(little_rzy.Engine,'KEEP_STRUCTURES',3)
    rep=run('X','5m',bars,cfg)
    closed=[x for x in rep['events'] if x['event_type']=='PAPER_EXIT' and x['paper_state']=='CLOSED']
    assert len(closed)>3 and rep['metrics']['trade_count']==len(rep['trades'])==len(closed)


def test_rza04_replay_never_fills_across_a_missing_intraday_bar():
    from src.services.rzy_backtest import replay_segments
    e,bars,st=triggered(fill=False)
    # The next supplied bar opens 45 minutes after the trigger closed: entry bars are missing.
    nxt=candle(len(bars)+9);nxt.update(execution_eligible=True)
    out=replay_segments('TEST','5m',[dict(b) for b in bars]+[nxt])
    assert len(out)==2
    assert not any(x['event_type']=='PAPER_FILL' for seg in out for x in seg['events'])
    assert any(p['state']=='PENDING_ENTRY' for p in out[0]['positions'].values())


def test_backtest_cli_runs_as_shipped_without_test_import_paths(tmp_path):
    import os, subprocess, sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[3]
    env = {k: v for k, v in os.environ.items() if k != 'PYTHONPATH'}
    out = tmp_path / 'r.json'
    run = subprocess.run([sys.executable, str(root / 'scripts/backtest_little_rzy.py'), str(root / 'docs/2026-10-19/little-rzy-synthetic.csv'),
                          '--symbol', 'SYNTHETIC', '--timeframe', '5m', '--output', str(out)], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=300)
    assert run.returncode == 0, run.stderr[-2000:]
    assert json.loads(out.read_text())['variants']['baseline']['held_out']['contiguous_segments'] == 1
