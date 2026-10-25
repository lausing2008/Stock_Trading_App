"""Completed source candles, actual exchange sessions, and no interpolation."""
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
import pytest
import importlib.util
from pathlib import Path
# Load the shipped pure helpers without the suite's container-dependency common stub.
def helper(name):
    spec=importlib.util.spec_from_file_location('rzy_test_'+name,Path(__file__).resolve().parents[3]/'shared/common'/f'{name}.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
calendar=helper('market_calendar')
session_bounds=calendar.session_bounds
resolve_ohlc=helper('price_basis').resolve_ohlc
from src.services.rzy_data import prepare_bars,aggregate_completed_bars,break_indices


def source(day=datetime(2026,10,16,tzinfo=timezone.utc), count=78):
    start,end=session_bounds('US',day.date())
    start=start.astimezone(timezone.utc);end=end.astimezone(timezone.utc)
    rows=[SimpleNamespace(ts=start+timedelta(minutes=5*i),open=100+i,high=102+i,low=99+i,close=101+i,adj_close=None,volume=10,session='REGULAR') for i in range(count)]
    bars,evidence=prepare_bars(rows,'5m',end+timedelta(minutes=2),session_bounds=session_bounds,resolve_ohlc=resolve_ohlc)
    return bars,evidence,end+timedelta(minutes=2)


@pytest.mark.parametrize('tf,count,last_members',[('15m',26,3),('1h',7,6)])
def test_session_anchored_ohlcv_and_short_final_hour(tf,count,last_members):
    bars,evidence,now=source();grouped,info=aggregate_completed_bars(bars,evidence,tf,now,session_bounds=session_bounds)
    assert len(grouped)==count and info['status']=='research_only'
    assert info['derived_from_timeframe']=='5m'
    assert grouped[0]['time']==bars[0]['time']
    size=3 if tf=='15m' else 12
    assert grouped[0]['open']==100 and grouped[0]['high']==102+size-1
    assert grouped[0]['low']==99 and grouped[0]['close']==101+size-1
    assert grouped[-1]['volume']==10*last_members
    assert grouped[-1]['closed_at']==bars[-1]['closed_at']
    assert grouped[0]['execution_eligible'] is False and grouped[0]['delay']=='unknown'


def test_forming_aggregate_is_never_emitted():
    bars,evidence,now=source();cutoff=datetime.fromisoformat(bars[1]['closed_at'])+timedelta(minutes=2)
    grouped,info=aggregate_completed_bars(bars[:2],evidence,'15m',cutoff,session_bounds=session_bounds)
    assert grouped==[] and info['status']=='no_completed_bars'
    assert info['exclusions'][-1]['reason']=='incomplete_aggregate'


def test_one_missing_latest_five_minute_candle_is_stale_after_settling():
    start,_=session_bounds('US',datetime(2026,10,16).date())
    row=SimpleNamespace(ts=start,open=100.,high=101.,low=99.,close=100.,adj_close=None,volume=10.,session='REGULAR')
    _,current=prepare_bars([row],'5m',start+timedelta(minutes=11),session_bounds=session_bounds,resolve_ohlc=resolve_ohlc)
    assert current['status']=='research_only'  # next bin is still settling
    _,missing=prepare_bars([row],'5m',start+timedelta(minutes=12),session_bounds=session_bounds,resolve_ohlc=resolve_ohlc)
    assert missing['status']=='stale'
    assert datetime.fromisoformat(missing['expected_latest_close'])==start+timedelta(minutes=10)


def test_missing_constituent_is_not_interpolated():
    bars,evidence,now=source();bars.pop(4)
    grouped,info=aggregate_completed_bars(bars,evidence,'15m',now,session_bounds=session_bounds)
    assert info['gaps'][-1]['reason']=='missing_aggregate_constituents'
    # The incomplete bin is withheld, and the hole splits segments instead of being bridged.
    assert bars[3]['time'] not in [g['time'] for g in grouped]
    assert break_indices(grouped,'15m',session_bounds)==[1]


def test_old_truncated_query_bin_is_excluded_warmup():
    bars,evidence,now=source();grouped,info=aggregate_completed_bars(bars[1:],evidence,'15m',now,session_bounds=session_bounds)
    assert len(grouped)==25 and info['status']=='research_only'
    assert grouped[0]['time']==bars[3]['time']
    assert info['exclusions'][-1]['reason']=='truncated_history_warmup_bin'


def test_early_close_has_a_complete_short_hour_without_an_overnight_bridge():
    bars,evidence,now=source(datetime(2026,11,27,tzinfo=timezone.utc),42)
    grouped,info=aggregate_completed_bars(bars,evidence,'1h',now,session_bounds=session_bounds)
    assert len(grouped)==4 and info['status']=='research_only'
    assert grouped[-1]['volume']==60
    assert datetime.fromisoformat(grouped[-1]['closed_at'])-datetime.fromisoformat(grouped[-1]['time'])==timedelta(minutes=30)


def test_gapped_or_stale_input_does_not_become_fresh_verified_data():
    bars,evidence,now=source()
    grouped,info=aggregate_completed_bars(bars,dict(evidence,status='missing_candles'),'1h',now,session_bounds=session_bounds)
    assert grouped==[] and info['status']=='missing_candles'
    _,info=aggregate_completed_bars(bars,dict(evidence,status='stale',stale=True),'1h',now,session_bounds=session_bounds)
    assert info['status']=='stale' and info['stale']


@pytest.mark.parametrize('day',sorted(calendar.NYSE_EARLY_CLOSES))
def test_shared_calendar_uses_the_same_early_close_for_bounds_and_live_gate(day):
    opening,closing=session_bounds('US',day)
    assert (closing.hour,closing.minute)==(13,0)
    assert calendar.is_regular_session('US',closing-timedelta(seconds=1))
    assert not calendar.is_regular_session('US',closing)


def test_full_days_and_hong_kong_lunch_are_preserved():
    assert session_bounds('US',datetime(2026,7,2).date())[1].hour==16
    assert session_bounds('US',datetime(2026,7,3).date()) is None
    hk_open,hk_close=session_bounds('HK',datetime(2026,10,20).date())
    assert hk_close.hour==16 and calendar.is_regular_session('HK',hk_open)
    assert not calendar.is_regular_session('HK',hk_open.replace(hour=12,minute=30))


def test_early_close_dates_cover_the_same_years_as_the_holiday_table():
    assert {d.year for d in calendar.NYSE_EARLY_CLOSES}=={d.year for d in calendar.NYSE_HOLIDAYS}


def test_off_grid_snapshot_is_excluded_instead_of_becoming_a_second_candle():
    start,end=session_bounds('US',datetime(2026,10,16).date())
    rows=[SimpleNamespace(ts=start+timedelta(minutes=5*i),open=100,high=102,low=99,close=101,adj_close=None,volume=10,session='REGULAR') for i in range(78)]
    rows.insert(1,SimpleNamespace(ts=start+timedelta(seconds=1),open=100,high=999,low=99,close=101,adj_close=None,volume=1000000,session='REGULAR'))
    bars,evidence=prepare_bars(rows,'5m',end+timedelta(minutes=2),session_bounds=session_bounds,resolve_ohlc=resolve_ohlc)
    assert len(bars)==78 and max(b['high'] for b in bars)==102
    assert evidence['exclusions'][0]['reason']=='off_session_bar_grid'
    grouped,info=aggregate_completed_bars(bars,evidence,'15m',end+timedelta(minutes=2),session_bounds=session_bounds)
    assert len(grouped)==26 and sum(b['volume'] for b in grouped)==780
    assert info['status']=='research_only'
