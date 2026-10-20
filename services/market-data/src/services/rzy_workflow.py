"""Watchlist-scoped RZY persistence, morning preparation and independently opted-in mail."""
from __future__ import annotations
import copy
import hashlib
import os
import json
from uuid import uuid4
from datetime import datetime,timedelta,timezone
from html import escape
from zoneinfo import ZoneInfo
from sqlalchemy import select,update
from sqlalchemy.orm.attributes import flag_modified
from db import Stock,Price,TimeFrame,WatchlistItem,Watchlist,User,AlertPreference
from types import SimpleNamespace
from .alert_audience import may_send
from common.market_calendar import session_bounds,calendar_coverage
from common.price_basis import resolve_ohlc
from common.outbox import enqueue
from .little_rzy import Engine,Config,VERSION,stable,planning_reference
from .rzy_data import prepare_bars,aggregate_completed_bars,break_indices,MINUTES,utc
from .rzy_models import RzyStream,RzyEvent,RzyPreference,RzyStreamGeneration

NY=ZoneInfo('America/New_York')
EMAIL_TYPES=('little_rzy','short_term_digest')
# Measured 2026-10-19 over 95 streams: 96% of re-fetched OHLC values differ by <1e-6 relative
# (storage noise); volume is routinely restated. Decisions keep the volume known at the time.
NOISE_REL=1e-6
# A missing bar inside the current session may still be backfilled; wait before rebuilding.
GAP_GRACE=timedelta(minutes=30)
# Paper blockers that are true for every stored-price stream today; cancellations caused only
# by these are recorded but not emailed (the trigger email already says execution is unavailable).
STANDING_BLOCKERS={'data_basis_or_freshness_unverified','shortability_or_borrow_cost_unverified'}
MAINTENANCE_START=timedelta(minutes=50)
MAINTENANCE_END=timedelta(minutes=21)
MAINTENANCE_BATCH=60


def current_config():
    return Config(**json.loads(os.environ.get('LITTLE_RZY_CONFIG','{}')))


def _ohlc_revised(old,new):
    if old.get('closed_at')!=new.get('closed_at') or old.get('price_basis')!=new.get('price_basis'): return True
    return any(abs(old[k]-new[k])>NOISE_REL*max(abs(old[k]),abs(new[k])) for k in ('open','high','low','close'))


def _revision(checkpoint_bars,bars,evidence):
    """Why the stored history no longer matches what the checkpoint consumed, or None."""
    if not checkpoint_bars or not bars: return None,None
    current={b['time']:b for b in bars}; first=bars[0]['time']
    off_grid={x.get('time') for x in evidence.get('exclusions',[]) if x.get('reason')=='off_session_bar_grid'}
    for old in checkpoint_bars:
        if old['time']<first: continue
        new=current.get(old['time'])
        if old['time'] in off_grid: return 'checkpoint_contains_excluded_off_grid_bar',dict(time=old['time'])
        if new is None: return 'checkpoint_bar_missing_from_source',dict(time=old['time'])
        if _ohlc_revised(old,new):
            return 'stored_completed_bar_changed',dict(time=old['time'],before={k:old.get(k) for k in ('open','high','low','close','closed_at','price_basis')},
                after={k:new.get(k) for k in ('open','high','low','close','closed_at','price_basis')})
    return None,None


def _archive_summary(checkpoint,reason,detail,now):
    text=json.dumps(checkpoint,sort_keys=True,default=str)
    bars=checkpoint.get('bars') or []
    return dict(reason=reason,detail=detail,observed_at=now.isoformat(),checkpoint_sha256=hashlib.sha256(text.encode()).hexdigest(),
        checkpoint_bytes=len(text),first_bar=bars[0]['time'] if bars else None,last_bar=bars[-1]['time'] if bars else None,
        structures=checkpoint.get('structures',{}),positions=checkpoint.get('positions',{}),pivots=checkpoint.get('pivots',[]),
        reversal_context=checkpoint.get('reversal_context',{}),trend=checkpoint.get('trend'),epoch=checkpoint.get('epoch'),
        legacy_quarantine=checkpoint.get('requires_audited_replay'))


def watchlist_symbols(session,user_id):
    return set(session.scalars(select(Stock.symbol).join(WatchlistItem,Stock.id==WatchlistItem.stock_id)
        .outerjoin(Watchlist,Watchlist.id==WatchlistItem.watchlist_id)
        .where(((WatchlistItem.watchlist_id.is_(None)) & (WatchlistItem.user_id==user_id)) | (Watchlist.user_id==user_id),Stock.market=='US',Stock.active.is_(True),Stock.delisted.is_(False))).all())


def load_bars(session,symbol,timeframe,now,cache=None):
    if cache is not None and (symbol,timeframe) in cache: return cache[(symbol,timeframe)]
    if timeframe not in MINUTES: return [],{'status':'unsupported_timeframe'}
    if now.astimezone(NY).year>calendar_coverage()['nyse']: return [],{'status':'calendar_coverage_expired'}
    stock=session.scalar(select(Stock).where(Stock.symbol==symbol))
    if stock is None or stock.market!='US': return [],{'status':'US_stocks_only_or_unknown_symbol'}
    tf={'5m':TimeFrame.M5,'15m':TimeFrame.M15,'1h':TimeFrame.H1,'1d':TimeFrame.D1}[timeframe]
    rows=session.scalars(select(Price).where(Price.stock_id==stock.id,Price.timeframe==tf).order_by(Price.ts.desc()).limit(6000)).all()
    if not rows and timeframe in ('15m','1h'):
        source,evidence=load_bars(session,symbol,'5m',now,cache)
        result=aggregate_completed_bars(source,evidence,timeframe,now,session_bounds=session_bounds)
    else: result=prepare_bars(list(reversed(rows)),timeframe,now,session_bounds=session_bounds,resolve_ohlc=resolve_ohlc)
    if cache is not None: cache[(symbol,timeframe)]=result
    return result


def evaluate(session,symbol,timeframe,now=None,*,persist=False,config=None,bars_cache=None):
    """Advance one stream on its latest contiguous completed-bar segment.

    Stored history that no longer matches the checkpoint (material OHLC revision, a removed bar,
    an off-grid snapshot consumed by older code, or a gap after the checkpoint) rebuilds the
    stream deterministically from current stored bars as a new generation; the superseded state
    is summarised in little_rzy_stream_generations and its events stay in little_rzy_events.
    """
    now=utc(now or datetime.now(timezone.utc)); cfg=config or current_config()
    bars,evidence=load_bars(session,symbol,timeframe,now,bars_cache)
    evidence=dict(evidence)
    key=f'{symbol}:{timeframe}:{cfg.hash}'
    row=session.scalar(select(RzyStream).where(RzyStream.stream_key==key).with_for_update().execution_options(populate_existing=True)) if persist else session.get(RzyStream,key)
    prior=copy.deepcopy(row.checkpoint) if row else None
    def empty(status,**extra):
        return dict(status=status,evidence=evidence,events=[],new_events=[],structures=[],paper_positions=[],strategy_version=VERSION,config_hash=cfg.hash,**extra)
    if evidence['status'] in ('no_completed_bars','unsupported_timeframe','US_stocks_only_or_unknown_symbol','calendar_coverage_expired'):
        return empty(evidence['status'])
    breaks=break_indices(bars,timeframe,session_bounds)
    seg=breaks[-1] if breaks else 0
    segment=bars[seg:]
    evidence.update(segment_start=segment[0]['time'],segment_bars=len(segment),history_breaks=len(breaks))
    if breaks:
        evidence['latest_gap']=dict(after=bars[seg-1]['time'],after_closed=bars[seg-1]['closed_at'],before=bars[seg]['time'])
    recent_gap=bool(breaks) and now-utc(datetime.fromisoformat(bars[seg-1]['closed_at']))<GAP_GRACE+timedelta(minutes=MINUTES[timeframe])
    reason=detail=None
    if prior:
        if prior.get('requires_audited_replay'):
            reason,detail='legacy_quarantine',prior['requires_audited_replay']
        else:
            reason,detail=_revision(prior.get('bars',[]),bars,evidence)
            if reason is None and prior.get('bars') and prior['bars'][-1]['time']<segment[0]['time']:
                reason,detail='gap_after_checkpoint',evidence.get('latest_gap')
    if (prior is None or reason=='gap_after_checkpoint') and recent_gap:
        return empty('awaiting_backfill',reason='recent_missing_bar_within_grace')
    rebuilt=prior is not None and reason is not None
    if prior is None or rebuilt:
        engine=Engine(symbol,timeframe,cfg)
        generation=(prior.get('generation') or 1)+1 if rebuilt else 1
        engine.s.update(generation=generation,generation_started_at=now.isoformat(),generation_reason=reason or 'initial')
        todo=segment
    else:
        engine=Engine(symbol,timeframe,cfg,prior)
        last=engine.s['bars'][-1]['time'] if engine.s['bars'] else None
        todo=[b for b in segment if last is None or b['time']>last]
    events=[]
    for bar in todo: events.extend(engine.step(bar))
    processed=bool(todo)
    new_events=[]
    if persist and (row is None or processed or rebuilt):
        if rebuilt:
            session.add(RzyStreamGeneration(stream_key=key,generation=prior.get('generation') or 1,reason=reason,
                detail=_archive_summary(prior,reason,detail,now),archived_at=now.replace(tzinfo=None)))
        if row is None:
            row=RzyStream(stream_key=key,symbol=symbol,timeframe=timeframe,config_hash=cfg.hash,checkpoint={},updated_at=now.replace(tzinfo=None)); session.add(row)
        row.checkpoint=engine.s; flag_modified(row,'checkpoint'); row.updated_at=now.replace(tzinfo=None)
        known=set(session.scalars(select(RzyEvent.event_id).where(RzyEvent.event_id.in_([e['event_id'] for e in events])))) if events else set()
        for event in events:
            if event['event_id'] in known: continue
            known.add(event['event_id']); new_events.append(event)
            session.add(RzyEvent(event_id=event['event_id'],symbol=symbol,timeframe=timeframe,event_type=event['event_type'],payload=event,created_at=now.replace(tzinfo=None)))
    last_index=engine.last_index
    structures=[dict(st,planning_reference=planning_reference(st,engine.s['bars'][-1],last_index,engine.s['indicators'][-1],cfg)) for st in list(engine.s['structures'].values())[-20:]] if engine.s['bars'] else []
    if not persist and row is not None:
        saved=session.scalars(select(RzyEvent).where(RzyEvent.symbol==symbol,RzyEvent.timeframe==timeframe).order_by(RzyEvent.created_at.desc()).limit(200)).all()
        merged={r.event_id:r.payload for r in reversed(saved) if r.payload.get('config_hash')==cfg.hash}
        merged.update({e['event_id']:e for e in events}); events=list(merged.values())
    return dict(status=evidence['status'],evidence=evidence,events=events[-100:],new_events=new_events,structures=structures,
        paper_positions=[dict(structure_id=k,**p) for k,p in engine.s['positions'].items()][-50:],
        indicators=engine.s['indicators'][-1] if engine.s['indicators'] else None,
        price_grid=[dict({k:b[k] for k in ('time','open','high','low','close')}, **{k:ind[k] for k in ('middle_band','upper_band','lower_band')}) for b,ind in list(zip(engine.s['bars'],engine.s['indicators']))[-1000:]],
        configuration=__import__('dataclasses').asdict(cfg),
        strategy_version=VERSION,config_hash=cfg.hash,bootstrap=prior is None,rebuilt=rebuilt,rebuild_reason=reason if rebuilt else None,
        generation=engine.s.get('generation',1))


def eligible(session,user,pref,kind):
    if not (user and user.is_active and (user.email or '').strip()): return False
    # Explicit allowlist: arbitrary outbox types must never enter this sender.
    if kind == 'little_rzy': return may_send(session,user,'little_rzy')
    if kind == 'short_term_digest': return may_send(session,user,'short_term_digest')
    return False


def subscriptions(session,types):
    ids=session.scalars(select(AlertPreference.user_id).where(AlertPreference.alert_type.in_(types),AlertPreference.enabled.is_(True)).distinct()).all()
    return [session.get(RzyPreference,uid) or SimpleNamespace(user_id=uid,timeframes=['5m','15m','1h']) for uid in ids]


def render_event(event,paper_blockers=()):
    def number(key):
        value=event.get(key); return 'not available' if value is None else f'{value:.4f}'
    seq=str(event.get('sequence_number')) if event.get('sequence_confidence')=='known' else 'unknown (history begins mid-trend)'
    status=(('Paper execution unavailable for this stream ('+', '.join(paper_blockers)+'); this is a research signal and the paper entry will be cancelled.' if paper_blockers else
        'Paper entry pending next tradable bar, subject to all fill checks.') if event['event_type']=='TRIGGERED' else
        'Watch only; no continuation entry or automatic reversal.' if event['event_type'] in ('REVERSAL_WATCH','EXHAUSTION_WATCH') else
        'Paper market exit pending next bar open; protective stop remains active.' if event['event_type']=='EXIT_PENDING' else
        'Paper entry cancelled: '+str(event.get('rejection_reason')) if event['event_type']=='ENTRY_CANCELLED' else
        'Research paper exit recorded; consult the paper ledger for fill and net costs.')
    return (f"{VERSION} {event.get('position_action')} {event['symbol']} {event['timeframe']} on completed close {event['signal_time']}.\n"
        f"Sequence: {seq}. Entry reference: {number('planned_entry')}. Stop: {number('stop')}. Primary target: {number('primary_target')}. R/R: {number('reward_risk')}.\n"
        f"Event: {event['event_type']}. Provider: {event['provider']}; delay {event.get('delay','unknown')}.\n"
        f"Passed: {', '.join(event.get('passed_rules',[]))}. Failed: {', '.join(event.get('failed_rules',[])) or 'none recorded'}.\n"
        'Manage these independent email preferences in Settings → Short-term trading & Little RZY.\n'
        'Independent rule-based research signal, not the AI signal. Not a profitable-outcome claim. '+status+' No broker order.\n')


def emailable(event,paper_blockers=()):
    kind=event['event_type']
    if kind=='ENTRY_CANCELLED':
        # A cancellation caused only by standing data blockers repeats what the trigger email said.
        return not set(event.get('failed_rules') or []).issubset(STANDING_BLOCKERS)
    return kind in ('TRIGGERED','REVERSAL_WATCH','EXHAUSTION_WATCH','EXIT_PENDING','PAPER_EXIT')


def scan(session,now=None):
    now=utc(now or datetime.now(timezone.utc)); local=now.astimezone(NY)
    if local.year>calendar_coverage()['nyse']: return {'status':'calendar_coverage_expired','queued':0}
    bounds=session_bounds('US',local.date())
    if not bounds: return {'status':'outside_regular_session','queued':0}
    opening,closing=utc(bounds[0]),utc(bounds[1])
    # Pre-open maintenance absorbs overnight restatements (rebuilds) before the 09:10 digest
    # reads the streams. It never emails.
    maintenance=opening-MAINTENANCE_START<=now<opening-MAINTENANCE_END
    if not maintenance and not opening<=now<=closing+timedelta(minutes=5): return {'status':'outside_regular_session','queued':0}
    prefs=subscriptions(session,EMAIL_TYPES)
    audience={}; jobs=set()
    for pref in prefs:
        user=session.get(User,pref.user_id)
        symbols=watchlist_symbols(session,pref.user_id)
        audience[pref.user_id]=(user,pref,symbols)
        jobs.update((symbol,tf) for symbol in symbols for tf in pref.timeframes if tf in MINUTES)
    if maintenance:
        cfg=current_config(); window=(opening-MAINTENANCE_START).replace(tzinfo=None)
        keys={f'{sym}:{tf}:{cfg.hash}':(sym,tf) for sym,tf in jobs}
        fresh=set(session.scalars(select(RzyStream.stream_key).where(RzyStream.stream_key.in_(list(keys)),RzyStream.updated_at>=window))) if keys else set()
        jobs=set(sorted(v for k,v in keys.items() if k not in fresh)[:MAINTENANCE_BATCH])
    queued=0; statuses={}; cache={}; previous_symbol=None
    for symbol,tf in sorted(jobs):
        if symbol!=previous_symbol: cache.clear(); previous_symbol=symbol
        try: result=evaluate(session,symbol,tf,now,persist=True,bars_cache=cache)
        except (ValueError,TypeError) as exc:
            session.rollback();statuses[f'{symbol}:{tf}']='data_rejected: '+str(exc)[:160];continue
        statuses[f'{symbol}:{tf}']=result['status']+(f" rebuilt:{result['rebuild_reason']}" if result.get('rebuilt') else '')
        if maintenance:
            session.execute(update(RzyStream).where(RzyStream.stream_key==f"{symbol}:{tf}:{result['config_hash']}").values(updated_at=now.replace(tzinfo=None)))
        if maintenance or result.get('bootstrap') or result['status']!='research_only':
            session.commit();continue
        blockers=tuple(result['evidence'].get('paper_blockers') or ())
        for event in result['new_events']:
            if not emailable(event,blockers): continue
            signal=utc(datetime.fromisoformat(event['signal_time']))
            # Fresh only: a rebuild replays history, and a late scan must not mail stale signals.
            if now-signal>timedelta(minutes=MINUTES[tf]+3): continue
            for uid,(user,pref,symbols) in audience.items():
                if symbol not in symbols or tf not in pref.timeframes or not eligible(session,user,pref,'little_rzy'): continue
                body=render_event(event,blockers)
                from .email_service import _unsubscribe_footer
                footer_html,footer_text=_unsubscribe_footer('little_rzy',uid)
                _,created=enqueue(session,event_id=f"rzy:{uid}:{event['event_id']}",user_id=uid,recipient=user.email.strip(),alert_type='little_rzy',
                    subject=f"RZY {event['position_action']} · {symbol} · {tf}",body_text=body+footer_text,body_html='<pre>'+escape(body)+'</pre>'+footer_html,
                    expires_at=(signal+timedelta(minutes=MINUTES[tf]+5)).replace(tzinfo=None))
                queued+=int(created)
        # One stream is an atomic unit; do not retain every large JSON checkpoint or an idle
        # transaction for the entire watchlist scan. No provider calls occur in this transaction.
        session.commit()
    return {'status':'maintenance' if maintenance else 'ok','streams':len(jobs),'queued':queued,'stream_status':statuses}


def digest_preview(session,user_id,now=None):
    now=utc(now or datetime.now(timezone.utc)); pref=session.get(RzyPreference,user_id)
    symbols=watchlist_symbols(session,user_id); candidates=[]; exclusions=[]
    for symbol in sorted(symbols):
        cache={}
        for tf in (pref.timeframes if pref else ['5m','15m','1h']):
            try: result=evaluate(session,symbol,tf,now,bars_cache=cache)
            except (ValueError,TypeError) as exc:
                exclusions.append({'symbol':symbol,'timeframe':tf,'reason':'data_rejected: '+str(exc)[:160]});continue
            if result['status']!='research_only': exclusions.append({'symbol':symbol,'timeframe':tf,'reason':result['status']}); continue
            active=[s for s in result['structures'] if s['state']=='ARMED']
            if not active: exclusions.append({'symbol':symbol,'timeframe':tf,'reason':'no_armed_structure'}); continue
            for st in active:
                reference=st['planning_reference'];minimum=result['configuration']['min_rr']
                if reference['reward_risk'] is None or reference['reward_risk']<minimum or not reference['stop'] or reference['stop']<=0:
                    exclusions.append({'symbol':symbol,'timeframe':tf,'reason':'insufficient_planned_reward_risk'});continue
                candidates.append(dict(symbol=symbol,timeframe=tf,direction=st['direction'],structure_id=st['id'],target=st['target'],
                    provisional_stop=reference['stop'],entry_reference=reference['entry_reference'],trigger_line=reference['trendline'],planned_reward_risk=reference['reward_risk'],
                    sequence=st['sequence_number'],sequence_confidence=st['sequence_confidence'],data_as_of=result['evidence'].get('data_as_of'),
                    guidance=f'Wait for a completed trendline-rejection candle after the open; recheck gap, fresh data, stop and actual-fill R/R >={minimum:g}. Stop and entry shown here are provisional references, not confirmed orders.'))
    # Known early structures first, not a win-probability ranking. Then stable symbol order.
    candidates.sort(key=lambda c:(c['sequence_confidence']!='known',c['sequence'],c['symbol'],c['timeframe']))
    return dict(strategy_version=VERSION,scope='my US watchlist',schedule='20 minutes before US regular open',
        candidates=candidates[:10],total_candidates=len(candidates),exclusions=exclusions,
        ranking='Known early sequences first, then sequence/symbol/timeframe. Not a measured quality or win-rate score.',
        note='Uses prior completed-session structures. Intraday confirmation is required; a gap may invalidate every candidate. No qualifying candidates is a valid digest.')


def morning_digest(session,now=None):
    now=utc(now or datetime.now(timezone.utc)); d=now.astimezone(NY).date(); bounds=session_bounds('US',d)
    if d.year>calendar_coverage()['nyse']: return {'status':'calendar_coverage_expired','queued':0}
    if not bounds: return {'status':'market_closed','queued':0}
    due=utc(bounds[0])-timedelta(minutes=20)
    if not due<=now<due+timedelta(minutes=5): return {'status':'outside_digest_window','queued':0}
    queued=0
    for pref in subscriptions(session,('short_term_digest',)):
        user=session.get(User,pref.user_id)
        if not eligible(session,user,pref,'short_term_digest'): continue
        report=digest_preview(session,user.id,now)
        body=f"Short-term/day-trading watchlist preparation · {d}\n"+report['note']+'\n'+report['ranking']+'\n'
        if not report['candidates']: body+='No qualifying candidates. Do not force a trade.\n'
        for c in report['candidates']:
            body+=f"{c['symbol']} {c['direction'].upper()} {c['timeframe']}: entry reference {c['entry_reference']:.4f}; provisional stop {c['provisional_stop']:.4f}; measured target {c['target']:.4f}; planned R/R {c['planned_reward_risk']:.2f}; observed {c['data_as_of']}. {c['guidance']}\n"
        body+='Manage email preferences in Settings → Short-term trading & Little RZY.\n'
        body+='Excluded: '+', '.join(f"{x['symbol']} {x['timeframe']} ({x['reason']})" for x in report['exclusions'])
        from .email_service import _unsubscribe_footer
        footer_html,footer_text=_unsubscribe_footer('short_term_digest',user.id)
        _,created=enqueue(session,event_id='rzy-digest:'+stable(user.id,d)[:50],user_id=user.id,recipient=user.email.strip(),alert_type='short_term_digest',
            subject=f'Short-term watchlist digest · {d}',body_text=body+footer_text,body_html='<pre>'+escape(body)+'</pre>'+footer_html,expires_at=utc(bounds[0]).replace(tzinfo=None))
        queued+=int(created)
    return {'status':'ok','queued':queued}


def drain(session,send,now=None):
    from .earnings_outbox import deliver_batch
    def subscribed(row):
        # The sender reads the same explicit per-channel opt-in as the enqueuer. Unknown => off.
        session.expire_all()
        user=session.get(User,row.user_id); pref=session.get(RzyPreference,row.user_id)
        if not eligible(session,user,pref,row.alert_type): return False
        if row.alert_type=='little_rzy':
            event=session.get(RzyEvent,row.event_id.split(':')[-1])
            stream=session.get(RzyStream,f'{event.symbol}:{event.timeframe}:{event.payload["config_hash"]}') if event else None
            if not (event and stream) or stream.checkpoint.get('requires_audited_replay'): return False
            # An event recorded before the stream was rebuilt belongs to a superseded history.
            started=stream.checkpoint.get('generation_started_at')
            if started and event.created_at<utc(datetime.fromisoformat(started)).replace(tzinfo=None): return False
            return event.symbol in watchlist_symbols(session,row.user_id) and event.timeframe in (pref.timeframes if pref else ['5m','15m','1h'])
        return True
    return deliver_batch(session,owner='little-rzy:'+uuid4().hex,send=lambda row: send(row.recipient,row.subject,row.body_html,row.body_text),is_subscribed=subscribed,
        activated_at=datetime.min,alert_types=EMAIL_TYPES,limit=20,now=now.replace(tzinfo=None) if now else None)
