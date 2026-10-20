"""Explicit conversion of stored provider bars; no interpolation or live-data claims."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

MINUTES = {'5m':5, '15m':15, '1h':60, '1d':1440}
NY = ZoneInfo('America/New_York')


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def prepare_bars(rows, timeframe, now, *, session_bounds, resolve_ohlc):
    import pandas as pd
    if timeframe not in MINUTES: raise ValueError('Unsupported RZY timeframe')
    now=utc(now); accepted=[]; excluded=[]
    for row in rows:
        stamp=utc(row.ts)
        d=stamp.date() if timeframe=='1d' else stamp.astimezone(NY).date()
        bounds=session_bounds('US',d)
        if not bounds:
            excluded.append({'time':stamp.isoformat(),'reason':'non_regular_session'}); continue
        start,end=(utc(v) for v in bounds)
        closed=end if timeframe=='1d' else min(stamp+timedelta(minutes=MINUTES[timeframe]),end)
        if timeframe!='1d' and (getattr(row,'session','REGULAR')!='REGULAR' or not start<=stamp<end):
            excluded.append({'time':stamp.isoformat(),'reason':'outside_regular_session'}); continue
        if timeframe!='1d' and (stamp-start).total_seconds() % (MINUTES[timeframe]*60) != 0:
            excluded.append({'time':stamp.isoformat(),'reason':'off_session_bar_grid'}); continue
        if closed+timedelta(minutes=2)>now:
            excluded.append({'time':stamp.isoformat(),'reason':'incomplete_or_settling'}); continue
        accepted.append((row,stamp,closed))
    if not accepted: return [],dict(exclusions=excluded,status='no_completed_bars',provider='stored_prices_upstream_not_retained')
    frame=pd.DataFrame([{key:getattr(r,key,None) for key in ('open','high','low','close','adj_close')} for r,_,_ in accepted])
    basis=resolve_ohlc(frame)
    bars=[]; gaps=[]
    for i,(r,stamp,closed) in enumerate(accepted):
        if i and timeframe!='1d':
            prev=accepted[i-1][1]; prev_close=accepted[i-1][2]
            if stamp.astimezone(NY).date()==prev.astimezone(NY).date() and stamp>prev_close:
                gaps.append({'after':prev.isoformat(),'before':stamp.isoformat(),'reason':'missing_intraday_candles'})
        bars.append(dict(time=stamp.isoformat(),closed_at=closed.isoformat(),complete=True,
            **{key:float(basis[key].iloc[i]) for key in ('open','high','low','close')},volume=float(r.volume),
            provider='stored_prices_upstream_not_retained',delay='unknown',session='REGULAR',
            price_basis=basis['basis'],freshness='historical_completed',execution_eligible=False,
            shortable=None,borrow_rate_annual=None))
    if timeframe!='1d':
        for (_,prev,prev_closed),(_,cur,_) in zip(accepted,accepted[1:]):
            previous_day,cd=prev.astimezone(NY).date(),cur.astimezone(NY).date()
            if previous_day!=cd:
                previous_bounds=session_bounds('US',previous_day);current_bounds=session_bounds('US',cd)
                if previous_bounds and current_bounds and (prev_closed!=utc(previous_bounds[1]) or cur!=utc(current_bounds[0])):
                    gaps.append({'after':prev.isoformat(),'before':cur.isoformat(),'reason':'incomplete_regular_session_between_bars'})
                d=previous_day+timedelta(days=1)
                while d<cd:
                    if session_bounds('US',d):gaps.append({'date':d.isoformat(),'reason':'missing_intraday_session'})
                    d+=timedelta(days=1)
    # Detect absent completed daily sessions as well; never invent a bridging candle.
    if timeframe=='1d':
        for (_,prev,_),(_,cur,_) in zip(accepted,accepted[1:]):
            d=prev.date()+timedelta(days=1)
            while d<cur.date():
                if session_bounds('US',d): gaps.append({'date':d.isoformat(),'reason':'missing_daily_session'})
                d+=timedelta(days=1)
    latest=accepted[-1][2]; expected=None
    day=now.astimezone(NY).date()
    for _ in range(12):
        bounds=session_bounds('US',day)
        if bounds:
            lo,hi=(utc(v) for v in bounds)
            if timeframe=='1d' and hi+timedelta(minutes=2)<=now: expected=hi; break
            if timeframe!='1d' and lo<now:
                expected=min(hi,now-timedelta(minutes=2)); break
        day-=timedelta(days=1)
    stale=expected is None or (expected-latest)>timedelta(minutes=MINUTES[timeframe] if timeframe!='1d' else 2)
    return bars,dict(status='missing_candles' if gaps else 'stale' if stale else 'research_only',
        gaps=gaps,exclusions=excluded,provider='stored_prices_upstream_not_retained',data_as_of=latest.isoformat(),
        delay='unknown',price_basis=basis['basis'],price_basis_reason=basis['reason'],stale=stale,
        paper_blockers=['provider_delay_unverified','borrow_availability_not_stored','execution_basis_unverified'])


def aggregate_completed_bars(source, evidence, timeframe, now, *, session_bounds):
    """Derive session-anchored 15m/1h candles only from complete, contiguous 5m bins."""
    if timeframe not in ('15m','1h'): raise ValueError('Only 15m/1h aggregation is supported')
    evidence=dict(evidence,derived_from_timeframe='5m')
    if not source or evidence['status'] not in ('research_only','stale'): return [],evidence
    now=utc(now); buckets={}; bounds_cache={}; excluded=[]; gaps=[]; result=[]
    for bar in source:
        stamp=utc(datetime.fromisoformat(bar['time'])); day=stamp.astimezone(NY).date()
        if day not in bounds_cache: bounds_cache[day]=tuple(utc(v) for v in session_bounds('US',day))
        start,end=bounds_cache[day]
        offset=int((stamp-start).total_seconds()//(60*MINUTES[timeframe]))
        opening=start+timedelta(minutes=offset*MINUTES[timeframe])
        buckets.setdefault((opening,end),[]).append(bar)
    first_time=utc(datetime.fromisoformat(source[0]['time']))
    for (opening,end),members in sorted(buckets.items()):
        closing=min(opening+timedelta(minutes=MINUTES[timeframe]),end)
        if closing+timedelta(minutes=2)>now:
            excluded.append(dict(time=opening.isoformat(),reason='incomplete_aggregate'));continue
        expected=[opening+timedelta(minutes=5*i) for i in range(int((closing-opening).total_seconds()//300))]
        observed=[utc(datetime.fromisoformat(b['time'])) for b in members]
        if observed!=expected:
            # A bounded history query may start part-way through its oldest bin. This is
            # excluded warmup, never an invented candle. Any later missing bin fails closed.
            if opening<first_time and observed and observed[-1]+timedelta(minutes=5)==closing:
                excluded.append(dict(time=opening.isoformat(),reason='truncated_history_warmup_bin'));continue
            gaps.append(dict(time=opening.isoformat(),reason='missing_aggregate_constituents'));continue
        bar=dict(members[0]);bar.update(time=opening.isoformat(),closed_at=closing.isoformat(),
            open=members[0]['open'],high=max(b['high'] for b in members),low=min(b['low'] for b in members),
            close=members[-1]['close'],volume=sum(b['volume'] for b in members))
        result.append(bar)
    evidence.update(exclusions=evidence.get('exclusions',[])+excluded,gaps=evidence.get('gaps',[])+gaps)
    evidence['status']='missing_candles' if gaps else evidence['status'] if result else 'no_completed_bars'
    if result:evidence['data_as_of']=result[-1]['closed_at']
    return result,evidence
