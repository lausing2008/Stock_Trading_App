"""Watchlist-scoped RZY persistence, morning preparation and independently opted-in mail."""
from __future__ import annotations
import copy
import math
import hashlib
import os
import json
from uuid import uuid4
from datetime import datetime,timedelta,timezone
from html import escape
from zoneinfo import ZoneInfo
from sqlalchemy import select,update,delete
from sqlalchemy.orm.attributes import flag_modified
from db import Stock,Price,TimeFrame,WatchlistItem,Watchlist,User,AlertPreference
from types import SimpleNamespace
from .alert_audience import may_send
from common.market_calendar import session_bounds,calendar_coverage
from common.price_basis import resolve_ohlc
from common.outbox import enqueue
from .little_rzy import Engine,Config,VERSION,stable,planning_reference,generation_event_id
from .rzy_data import prepare_bars,aggregate_completed_bars,break_indices,MINUTES,utc
from .rzy_models import RzyStream,RzyEvent,RzyPreference,RzyStreamGeneration,RzyStreamSnapshot,RzyScanRun,RzyOccurrence,RzyRebuild,RzyScanPhases
import time as _time
from .rzy_email import render_event,render_event_html,event_subject

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
# Completed 5m bars are restated intraday (measured 2026-10-20: 3,956 rebuilds in one session,
# median 11 min apart per stream). A stream rebuilds at most once per interval; in between it
# pauses (no bars processed on a known-restated history, no mail) instead of thrashing.
# Simulated on that session: 30 min -> 1,868 rebuilds, 60 -> 1,279, 120 -> 927.
REBUILD_MIN_INTERVAL=timedelta(minutes=30)
THROTTLED_REASONS={'stored_completed_bar_changed','checkpoint_bar_missing_from_source','checkpoint_contains_excluded_off_grid_bar'}
KEEP_GENERATIONS=5
# Occurrence/rebuild records are compact and outlive generation pruning (F08); bounded like scan runs.
RECORD_RETENTION=timedelta(days=35)
# The trade figures an email states; a rebuild that changes any of them must not send the old body.
FIGURE_KEYS=('primary_target','stop','planned_entry','D','reward_risk','event_type','direction')


#: Price figures equal within the bar-revision rounding bound (bar_provenance.revision_magnitude):
#: max(half a cent, 2e-5 x price). Measured Oct 21: 49 of 57 rebuild-changed signal figures were
#: float noise or rounding (e.g. KTOS entry 38.779998779 -> 38.78), which the exact comparison
#: below would have read as "the trade figures changed" and suppressed the email.
PRICE_FIGURES=('primary_target','stop','planned_entry','D')
FIGURE_ABS,FIGURE_REL=0.005,2e-5
#: reward:risk is derived from the prices; a sub-cent stop move on a tight setup moves it by ~0.1%.
#: Equal within 1% (or 0.01) unless the change crosses the configured minimum.
RR_REL,RR_ABS=0.01,0.01


def figures_equivalent(sent,current,min_rr=None):
    """Would the queued email still state the current setup? Price fields within the rounding
    bound, reward:risk within RR tolerance without crossing min_rr, everything else exactly."""
    for k in FIGURE_KEYS:
        a,b=sent.get(k),current.get(k)
        if a==b: continue
        if not (isinstance(a,(int,float)) and isinstance(b,(int,float))): return False
        if not (math.isfinite(a) and math.isfinite(b)): return False
        if k in PRICE_FIGURES:
            if abs(a-b)>max(FIGURE_ABS,FIGURE_REL*max(abs(a),abs(b)))+1e-12: return False
        elif k=='reward_risk':
            if abs(a-b)>max(RR_ABS,RR_REL*max(abs(a),abs(b))): return False
            if min_rr is not None and (a>=min_rr)!=(b>=min_rr): return False
        else:
            return False
    return True


def figures(payload):
    return {k:payload.get(k) for k in FIGURE_KEYS}


def _wall_clock():
    return datetime.now(timezone.utc)
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
    if timeframe in ('15m','1h'):
        # One canonical source per evaluation, never spliced (RZA-07): native rows win only when
        # they are current and at least as recent as bars derived from complete 5m constituents.
        source,evidence=load_bars(session,symbol,'5m',now,cache)
        derived=aggregate_completed_bars(source,evidence,timeframe,now,session_bounds=session_bounds)
        derived=(derived[0],dict(derived[1],source='derived_from_5m'))
        result=derived
        if rows:
            native=prepare_bars(list(reversed(rows)),timeframe,now,session_bounds=session_bounds,resolve_ohlc=resolve_ohlc)
            native=(native[0],dict(native[1],source='native'))
            if native[0] and native[1]['status']=='research_only' and (not derived[0] or native[1].get('data_as_of','')>=derived[1].get('data_as_of','')):
                result=(native[0],dict(native[1],alternative=dict(source='derived_from_5m',status=derived[1].get('status'),data_as_of=derived[1].get('data_as_of'))))
            else:
                result=(derived[0],dict(derived[1],alternative=dict(source='native',status=native[1].get('status'),data_as_of=native[1].get('data_as_of'))))
    else: result=prepare_bars(list(reversed(rows)),timeframe,now,session_bounds=session_bounds,resolve_ohlc=resolve_ohlc)
    if cache is not None: cache[(symbol,timeframe)]=result
    return result


def evaluate(session,symbol,timeframe,now=None,*,persist=False,config=None,bars_cache=None,allow_rebuild=True,clock=None,timings=None):
    """Advance one stream on its latest contiguous completed-bar segment.

    Stored history that no longer matches the checkpoint (material OHLC revision, a removed bar,
    an off-grid snapshot consumed by older code, or a gap after the checkpoint) rebuilds the
    stream deterministically from current stored bars as a new generation; the superseded state
    is summarised in little_rzy_stream_generations and its events stay in little_rzy_events.

    `now` is the DATA CUTOFF (deterministic replay); `clock` reads the actual wall time at which
    records are persisted (F09). They differ whenever a scan takes time.
    """
    now=utc(now or datetime.now(timezone.utc)); cfg=config or current_config()
    _t=_time.monotonic()
    bars,evidence=load_bars(session,symbol,timeframe,now,bars_cache)
    if timings is not None: timings['load_and_prepare_bars']=timings.get('load_and_prepare_bars',0.0)+_time.monotonic()-_t
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
    if prior and reason in THROTTLED_REASONS and prior.get('generation_started_at'):
        due=utc(datetime.fromisoformat(prior['generation_started_at']))+REBUILD_MIN_INTERVAL
        if now<due:
            if persist and not prior.get('restatement_pending'):
                prior['restatement_pending']=dict(reason=reason,detail=detail,observed_at=now.isoformat())
                row.checkpoint=prior; flag_modified(row,'checkpoint'); row.updated_at=now.replace(tzinfo=None)
            return empty('restatement_pending',reason=reason,rebuild_after=due.isoformat())
    if prior and reason is not None and not persist and not allow_rebuild:
        # Bulk read paths (digest preview) never rebuild hundreds of streams in memory; the
        # scheduled scan / pre-open maintenance persists rebuilds.
        return empty('rebuild_pending',reason=reason)
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
        persisted_at=utc((clock or _wall_clock)()).replace(tzinfo=None)
        if rebuilt:
            session.add(RzyRebuild(stream_key=key,symbol=symbol,timeframe=timeframe,generation=engine.s['generation'],
                reason=(reason or '')[:64],rebuilt_at=persisted_at))
            session.add(RzyStreamGeneration(stream_key=key,generation=prior.get('generation') or 1,reason=reason,
                detail=_archive_summary(prior,reason,detail,now),archived_at=now.replace(tzinfo=None)))
            session.flush()
            _prune_generations(session,key,symbol,timeframe,cfg.hash,engine.s['generation'])
        if row is None:
            row=RzyStream(stream_key=key,symbol=symbol,timeframe=timeframe,config_hash=cfg.hash,checkpoint={},updated_at=now.replace(tzinfo=None)); session.add(row)
        row.checkpoint=engine.s; flag_modified(row,'checkpoint'); row.updated_at=now.replace(tzinfo=None)
        # Pivots are kept in the structures; persisting every PIVOT_CONFIRMED (~65% of events)
        # would multiply table growth on each generation rebuild for no reader.
        stored=[e for e in events if e['event_type']!='PIVOT_CONFIRMED']
        known=set(session.scalars(select(RzyEvent.event_id).where(RzyEvent.event_id.in_([e['event_id'] for e in stored])))) if stored else set()
        for event in stored:
            if event['event_id'] in known: continue
            known.add(event['event_id']); new_events.append(event)
            session.add(RzyEvent(event_id=event['event_id'],symbol=symbol,timeframe=timeframe,event_type=event['event_type'],payload=event,created_at=persisted_at))
        _record_occurrences(session,key,symbol,timeframe,stored,now,persisted_at,
            'bootstrap' if prior is None else 'rebuild' if rebuilt else 'incremental')
    last_index=engine.last_index
    structures=[dict(st,planning_reference=planning_reference(st,engine.s['bars'][-1],last_index,engine.s['indicators'][-1],cfg)) for st in list(engine.s['structures'].values())[-20:]] if engine.s['bars'] else []
    generation=engine.s.get('generation',1)
    superseded=0
    if not persist and row is not None:
        saved=session.scalars(select(RzyEvent).where(RzyEvent.symbol==symbol,RzyEvent.timeframe==timeframe).order_by(RzyEvent.created_at.desc()).limit(500)).all()
        mine=[r for r in reversed(saved) if r.payload.get('config_hash')==cfg.hash]
        # The current view never mixes generations (RZA-01): older generations stay stored and
        # are counted, but are returned only on explicit request via superseded_events.
        merged={r.event_id:r.payload for r in mine if r.payload.get('generation',1)==generation}
        superseded=sum(1 for r in mine if r.payload.get('generation',1)!=generation)
        merged.update({e['event_id']:e for e in events}); events=list(merged.values())
    return dict(status=evidence['status'],evidence=evidence,events=events[-100:],new_events=new_events,structures=structures,
        paper_positions=[dict(structure_id=k,**p) for k,p in engine.s['positions'].items()][-50:],
        indicators=engine.s['indicators'][-1] if engine.s['indicators'] else None,
        price_grid=[dict({k:b[k] for k in ('time','open','high','low','close')}, **{k:ind[k] for k in ('middle_band','upper_band','lower_band')}) for b,ind in list(zip(engine.s['bars'],engine.s['indicators']))[-1000:]],
        configuration=__import__('dataclasses').asdict(cfg),
        strategy_version=VERSION,config_hash=cfg.hash,bootstrap=prior is None,rebuilt=rebuilt,rebuild_reason=reason if rebuilt else None,
        generation=generation,superseded_event_count=superseded)


def _record_occurrences(session,key,symbol,timeframe,stored,cutoff,persisted_at,observed_in):
    """First observation is written once; later generations only move latest_*."""
    if not stored: return
    ids=list({e['occurrence_id'] for e in stored})
    rows={r.occurrence_id:r for r in session.scalars(select(RzyOccurrence).where(RzyOccurrence.occurrence_id.in_(ids)))}
    for e in stored:
        occ=e['occurrence_id']; row=rows.get(occ)
        if row is None:
            signal=e.get('signal_time')
            row=RzyOccurrence(occurrence_id=occ,stream_key=key,symbol=symbol,timeframe=timeframe,event_type=e['event_type'][:32],
                signal_time=utc(datetime.fromisoformat(signal)).replace(tzinfo=None) if signal else None,
                first_cutoff=cutoff.replace(tzinfo=None),first_seen_at=persisted_at,first_generation=e.get('generation',1),
                observed_in=observed_in,first_figures=dict(figures(e),failed_rules=list(e.get('failed_rules') or [])),latest_generation=e.get('generation',1),latest_event_id=e['event_id'])
            session.add(row); rows[occ]=row
        elif e.get('generation',1)>=row.latest_generation:
            row.latest_generation=e.get('generation',1); row.latest_event_id=e['event_id']


def _prune_generations(session,key,symbol,timeframe,config_hash,current):
    """Keep the last KEEP_GENERATIONS generations of archives and stored events per stream."""
    keep_ids=session.scalars(select(RzyStreamGeneration.id).where(RzyStreamGeneration.stream_key==key)
        .order_by(RzyStreamGeneration.id.desc()).limit(KEEP_GENERATIONS)).all()
    if keep_ids:
        for old in session.scalars(select(RzyStreamGeneration).where(RzyStreamGeneration.stream_key==key,RzyStreamGeneration.id.notin_(keep_ids))).all():
            session.delete(old)
    oldest=current-KEEP_GENERATIONS+1
    for ev in session.scalars(select(RzyEvent).where(RzyEvent.symbol==symbol,RzyEvent.timeframe==timeframe)).all():
        if ev.payload.get('config_hash')==config_hash and ev.payload.get('generation',1)<oldest:
            session.delete(ev)


def superseded_events(session,symbol,timeframe,config=None,limit=200):
    """Explicit historical view: stored events of generations other than the current one."""
    cfg=config or current_config(); row=session.get(RzyStream,f'{symbol}:{timeframe}:{cfg.hash}')
    current=row.checkpoint.get('generation',1) if row else 1
    saved=session.scalars(select(RzyEvent).where(RzyEvent.symbol==symbol,RzyEvent.timeframe==timeframe).order_by(RzyEvent.created_at.desc()).limit(2000)).all()
    rows=[dict(r.payload,generation=r.payload.get('generation',1),recorded_at=r.created_at.isoformat()) for r in saved
          if r.payload.get('config_hash')==cfg.hash and r.payload.get('generation',1)!=current]
    return dict(current_generation=current,events=rows[:limit])


def eligible(session,user,pref,kind):
    if not (user and user.is_active and (user.email or '').strip()): return False
    # Explicit allowlist: arbitrary outbox types must never enter this sender.
    if kind == 'little_rzy': return may_send(session,user,'little_rzy')
    if kind == 'short_term_digest': return may_send(session,user,'short_term_digest')
    return False


def subscriptions(session,types):
    ids=session.scalars(select(AlertPreference.user_id).where(AlertPreference.alert_type.in_(types),AlertPreference.enabled.is_(True)).distinct()).all()
    return [session.get(RzyPreference,uid) or SimpleNamespace(user_id=uid,timeframes=['5m','15m','1h']) for uid in ids]


def emailable(event,paper_blockers=()):
    kind=event['event_type']
    if kind=='ENTRY_CANCELLED':
        # A cancellation caused only by standing data blockers repeats what the trigger email said.
        return not set(event.get('failed_rules') or []).issubset(STANDING_BLOCKERS)
    return kind in ('TRIGGERED','REVERSAL_WATCH','EXHAUSTION_WATCH','EXIT_PENDING','PAPER_EXIT')


def scan(session,now=None,clock=None):
    started=_time.monotonic()
    now=utc(now or datetime.now(timezone.utc)); local=now.astimezone(NY)
    # `now` is the scan's data cutoff. Elapsed processing time is real (F09): later streams are
    # evaluated, persisted and freshness-checked at their own actual time, not the scan start.
    clock=clock or (lambda: now+timedelta(seconds=_time.monotonic()-started))
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
    queued=0; statuses={}; cache={}; previous_symbol=None; emailable_new=0; too_late=0
    # load_and_prepare_bars is a SUBSET of evaluate_total (never add them); snapshot_and_commit
    # includes email rendering/enqueue; every stream path stops both timers.
    phases={'load_and_prepare_bars':0.0,'evaluate_total':0.0,'snapshot_and_commit':0.0}
    for symbol,tf in sorted(jobs):
        if symbol!=previous_symbol: cache.clear(); previous_symbol=symbol
        _t=_time.monotonic()
        try: result=evaluate(session,symbol,tf,now,persist=True,bars_cache=cache,clock=clock,timings=phases)
        except (ValueError,TypeError) as exc:
            phases['evaluate_total']+=_time.monotonic()-_t; _t=_time.monotonic()
            session.rollback();statuses[f'{symbol}:{tf}']='data_rejected: '+str(exc)[:160]
            save_snapshot(session,symbol,tf,current_config().hash,now,'data_rejected',stream_summary(symbol,tf,error=exc));session.commit()
            phases['snapshot_and_commit']+=_time.monotonic()-_t;continue
        phases['evaluate_total']+=_time.monotonic()-_t; _t=_time.monotonic()
        save_snapshot(session,symbol,tf,result['config_hash'],now,result['status'],stream_summary(symbol,tf,result))
        statuses[f'{symbol}:{tf}']=result['status']+(f" rebuilt:{result['rebuild_reason']}" if result.get('rebuilt') else '')
        if maintenance:
            session.execute(update(RzyStream).where(RzyStream.stream_key==f"{symbol}:{tf}:{result['config_hash']}").values(updated_at=now.replace(tzinfo=None)))
        if maintenance or result.get('bootstrap') or result['status']!='research_only':
            session.commit();phases['snapshot_and_commit']+=_time.monotonic()-_t;continue
        blockers=tuple(result['evidence'].get('paper_blockers') or ())
        for event in result['new_events']:
            if not emailable(event,blockers): continue
            emailable_new+=1
            signal=utc(datetime.fromisoformat(event['signal_time']))
            # Fresh only: a rebuild replays history, and a late scan must not mail stale signals.
            # Measured at the actual time of this check, not the scan start (F09).
            checked=utc(clock())
            if checked-signal>timedelta(minutes=MINUTES[tf]+3):
                too_late+=1; continue
            cfg=current_config()
            for uid,(user,pref,symbols) in audience.items():
                if symbol not in symbols or tf not in pref.timeframes or not eligible(session,user,pref,'little_rzy'): continue
                shown=dict(evaluated_at=checked,rendered_at=utc(clock()),config=cfg)
                body=render_event(event,blockers,**shown)
                from .email_service import _unsubscribe_footer
                footer_html,footer_text=_unsubscribe_footer('little_rzy',uid)
                # Keyed by occurrence, not generation: a rebuild never re-mails the same event.
                _,created=enqueue(session,event_id=f"rzy:{uid}:{event.get('occurrence_id',event['event_id'])}",user_id=uid,recipient=user.email.strip(),alert_type='little_rzy',
                    subject=event_subject(event),body_text=body+footer_text,body_html=render_event_html(event,blockers,**shown)+footer_html,
                    expires_at=(signal+timedelta(minutes=MINUTES[tf]+5)).replace(tzinfo=None))
                queued+=int(created)
                if created:
                    occ=session.get(RzyOccurrence,event['occurrence_id'])
                    if occ is not None and occ.queued_generation is None:
                        occ.queued_generation=event.get('generation',1); occ.queued_figures=figures(event)
                        occ.queued_at=utc(clock()).replace(tzinfo=None)
        # One stream is an atomic unit; do not retain every large JSON checkpoint or an idle
        # transaction for the entire watchlist scan. No provider calls occur in this transaction.
        session.commit()
        phases['snapshot_and_commit']+=_time.monotonic()-_t
    duration=_time.monotonic()-started
    counts={}
    for v in statuses.values(): k=v.split(' ')[0].split(':')[0]; counts[k]=counts.get(k,0)+1
    phases['other']=max(0.0,duration-phases['evaluate_total']-phases['snapshot_and_commit'])
    session.merge(RzyScanPhases(scan_time=now.replace(tzinfo=None),mode='maintenance' if maintenance else 'session',
        phases={k:round(v,3) for k,v in phases.items()}))
    session.execute(delete(RzyScanPhases).where(RzyScanPhases.scan_time<(now-timedelta(days=35)).replace(tzinfo=None)))
    session.add(RzyScanRun(scan_time=now.replace(tzinfo=None),mode='maintenance' if maintenance else 'session',duration_seconds=round(duration,3),
        streams=len(jobs),statuses=counts,new_emailable_events=emailable_new,too_late_to_mail=too_late,queued=queued))
    session.execute(delete(RzyScanRun).where(RzyScanRun.scan_time<(now-timedelta(days=35)).replace(tzinfo=None)))
    horizon=(now-RECORD_RETENTION).replace(tzinfo=None)
    session.execute(delete(RzyOccurrence).where(RzyOccurrence.first_seen_at<horizon))
    session.execute(delete(RzyRebuild).where(RzyRebuild.rebuilt_at<horizon))
    session.commit()
    return {'status':'maintenance' if maintenance else 'ok','streams':len(jobs),'queued':queued,'stream_status':statuses,
            'duration_seconds':round(duration,1),'too_late_to_mail':too_late}


GUIDANCE='Wait for a completed trendline-rejection candle after the open; recheck gap, fresh data, stop and actual-fill R/R >={minimum:g}. Stop and entry shown here are provisional references, not confirmed orders.'
DIGEST_RANKING='Known early sequences first, then sequence/symbol/timeframe. Not a measured quality or win-rate score.'
DIGEST_NOTE='Uses prior completed-session structures. Intraday confirmation is required; a gap may invalidate every candidate. No qualifying candidates is a valid digest.'


def stream_summary(symbol,tf,result=None,error=None):
    """One stream's digest contribution: candidates, or the single reason it has none."""
    if error is not None:
        return dict(candidates=[],exclusion={'symbol':symbol,'timeframe':tf,'reason':'data_rejected: '+str(error)[:160]})
    if result['status']!='research_only':
        ev=result.get('evidence') or {}
        return dict(candidates=[],exclusion={'symbol':symbol,'timeframe':tf,'reason':ev.get('stale_reason') or result['status'],
            'data_as_of':ev.get('data_as_of'),'expected_latest_close':ev.get('expected_latest_close'),'source':ev.get('source')})
    active=[s for s in result['structures'] if s['state']=='ARMED']
    if not active: return dict(candidates=[],exclusion={'symbol':symbol,'timeframe':tf,'reason':'no_armed_structure'})
    minimum=result['configuration']['min_rr']; candidates=[]
    for st in active:
        reference=st['planning_reference']
        if reference['reward_risk'] is None or reference['reward_risk']<minimum or not reference['stop'] or reference['stop']<=0: continue
        candidates.append(dict(symbol=symbol,timeframe=tf,direction=st['direction'],structure_id=st['id'],target=st['target'],
            provisional_stop=reference['stop'],entry_reference=reference['entry_reference'],trigger_line=reference['trendline'],planned_reward_risk=reference['reward_risk'],
            sequence=st['sequence_number'],sequence_confidence=st['sequence_confidence'],data_as_of=result['evidence'].get('data_as_of'),
            guidance=GUIDANCE.format(minimum=minimum),min_reward_risk=minimum))
    return dict(candidates=candidates,exclusion=None if candidates else {'symbol':symbol,'timeframe':tf,'reason':'insufficient_planned_reward_risk'})


def _rank(candidates):
    # Known early structures first, not a win-probability ranking. Then stable symbol order.
    return sorted(candidates,key=lambda c:(c['sequence_confidence']!='known',c['sequence'],c['symbol'],c['timeframe']))


# A snapshot is CURRENT only if a scan wrote it recently enough to reflect the latest completed
# bars (F07): during a session, within this grace of now; outside one, no older than the last
# in-session scan before that session's close. Older rows are historical, never "usable now".
SNAPSHOT_GRACE=timedelta(minutes=15)


def snapshot_current_since(now):
    """Earliest checked_at (naive UTC) that still counts as a current snapshot at `now`."""
    now=utc(now); day=now.astimezone(NY).date()
    for back in range(0,15):
        bounds=session_bounds('US',day-timedelta(days=back))
        if not bounds: continue
        opening,closing=utc(bounds[0]),utc(bounds[1])
        if now<opening: continue          # before this session's open: the previous close governs
        if now<=closing+timedelta(minutes=5):
            return (now-SNAPSHOT_GRACE).replace(tzinfo=None)
        return (closing-SNAPSHOT_GRACE).replace(tzinfo=None)
    return (now-SNAPSHOT_GRACE).replace(tzinfo=None)


def save_snapshot(session,symbol,tf,config_hash,now,status,summary):
    session.merge(RzyStreamSnapshot(stream_key=f'{symbol}:{tf}:{config_hash}',symbol=symbol,timeframe=tf,config_hash=config_hash,
        checked_at=now.replace(tzinfo=None),status=status[:48],summary=summary))


def digest_preview(session,user_id,now=None):
    """Live computation, used by the scheduled 09:10 digest (not by the interactive preview)."""
    now=utc(now or datetime.now(timezone.utc)); pref=session.get(RzyPreference,user_id)
    symbols=watchlist_symbols(session,user_id); candidates=[]; exclusions=[]
    for symbol in sorted(symbols):
        cache={}
        for tf in (pref.timeframes if pref else ['5m','15m','1h']):
            try: summary=stream_summary(symbol,tf,evaluate(session,symbol,tf,now,bars_cache=cache,allow_rebuild=False))
            except (ValueError,TypeError) as exc: summary=stream_summary(symbol,tf,error=exc)
            candidates.extend(summary['candidates'])
            if summary['exclusion']: exclusions.append(summary['exclusion'])
    candidates=_rank(candidates)
    return dict(strategy_version=VERSION,scope='my US watchlist',schedule='20 minutes before US regular open',
        candidates=candidates[:10],total_candidates=len(candidates),exclusions=exclusions,ranking=DIGEST_RANKING,note=DIGEST_NOTE)


def digest_snapshot(session,user_id,now=None):
    """Interactive preview: reads the latest per-stream scan snapshots; never evaluates streams."""
    now=utc(now or datetime.now(timezone.utc)); cfg=current_config(); pref=session.get(RzyPreference,user_id)
    symbols=sorted(watchlist_symbols(session,user_id)); tfs=pref.timeframes if pref else ['5m','15m','1h']
    keys=[f'{sym}:{tf}:{cfg.hash}' for sym in symbols for tf in tfs]
    rows={r.stream_key:r for r in session.scalars(select(RzyStreamSnapshot).where(RzyStreamSnapshot.stream_key.in_(keys)))} if keys else {}
    base=dict(strategy_version=VERSION,scope='my US watchlist',schedule='20 minutes before US regular open',ranking=DIGEST_RANKING,note=DIGEST_NOTE)
    if not rows:
        return dict(base,available=False,candidates=[],total_candidates=0,exclusions=[],
            message='Preview not available yet: no scan snapshot exists for your watchlist. Snapshots are saved by the scheduled scan before and during the US session.')
    candidates=[]; exclusions=[]; since=snapshot_current_since(now); expired=0
    for sym in symbols:
        for tf in tfs:
            r=rows.get(f'{sym}:{tf}:{cfg.hash}')
            if r is None: exclusions.append({'symbol':sym,'timeframe':tf,'reason':'not_evaluated_yet'}); continue
            if r.checked_at<since:
                # Historical only: an old 'research_only' result is not a current candidate.
                expired+=1
                exclusions.append({'symbol':sym,'timeframe':tf,'reason':'snapshot_expired','checked_at':r.checked_at.isoformat(),
                    'historical_status':r.status,'historical_candidates':len(r.summary.get('candidates',[]))}); continue
            candidates.extend(r.summary.get('candidates',[]))
            if r.summary.get('exclusion'): exclusions.append(dict(r.summary['exclusion'],checked_at=r.checked_at.isoformat()))
    times=[r.checked_at for r in rows.values()]
    oldest,newest=min(times),max(times)
    candidates=_rank(candidates)
    return dict(base,available=True,snapshot_from=oldest.isoformat(),snapshot_to=newest.isoformat(),
        snapshot_age_minutes=round((now.replace(tzinfo=None)-oldest).total_seconds()/60,1),
        streams_with_snapshot=len(rows),streams_expected=len(keys),streams_current=len(rows)-expired,streams_expired=expired,
        current_since=since.isoformat(),
        candidates=candidates[:10],total_candidates=len(candidates),exclusions=exclusions,
        freshness_note='Saved scan results, not recomputed for this request. Only snapshots written by a scan since the time shown are used; older ones are listed as expired and their candidates are not shown.')


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
        # Presentation only (rzy_email.render_digest): the same candidates and exclusions.
        from .rzy_email import render_digest
        body,body_html=render_digest(report,d)
        from .email_service import _unsubscribe_footer
        footer_html,footer_text=_unsubscribe_footer('short_term_digest',user.id)
        _,created=enqueue(session,event_id='rzy-digest:'+stable(user.id,d)[:50],user_id=user.id,recipient=user.email.strip(),alert_type='short_term_digest',
            subject=f'Short-term watchlist digest · {d}',body_text=body+footer_text,body_html=body_html+footer_html,expires_at=utc(bounds[0]).replace(tzinfo=None))
        queued+=int(created)
    return {'status':'ok','queued':queued}


def rzy_send_check(session,row):
    """(send?, reason) for one queued little_rzy row, from its occurrence's CURRENT generation.

    F02: the event is located through its occurrence record and the stream's current generation,
    never by guessing a bounded range of generation ids. Each refusal names its real cause.
    """
    occurrence=row.event_id.split(':')[-1]
    occ=session.get(RzyOccurrence,occurrence)
    if occ is not None:
        stream=session.get(RzyStream,occ.stream_key)
    else:
        # Queued before occurrence records existed: generation 1 kept the legacy id.
        legacy=session.get(RzyEvent,occurrence)
        stream=legacy and session.get(RzyStream,f'{legacy.symbol}:{legacy.timeframe}:{legacy.payload["config_hash"]}')
    if stream is None: return False,'superseded: stream or occurrence record not found'
    if stream.checkpoint.get('requires_audited_replay') or stream.checkpoint.get('restatement_pending'):
        return False,'paused: stream awaiting a rebuild after restated bars'
    current=stream.checkpoint.get('generation',1)
    event=session.get(RzyEvent,generation_event_id(occurrence,current))
    if event is None: return False,'superseded: occurrence absent from the current generation'
    sent_figures=(occ.queued_figures or occ.first_figures) if occ is not None else None
    if sent_figures is not None and not figures_equivalent(sent_figures,figures(event.payload),current_config().min_rr):
        return False,'superseded: a rebuild changed the trade figures in the queued message'
    pref=session.get(RzyPreference,row.user_id)
    if event.symbol not in watchlist_symbols(session,row.user_id): return False,'recipient removed the symbol from the watchlist'
    if event.timeframe not in (pref.timeframes if pref else ['5m','15m','1h']): return False,'recipient turned off this timeframe'
    return True,'subscribed'


def drain(session,send,now=None):
    from .earnings_outbox import deliver_batch
    from db import NotificationOutbox
    why={}
    def subscribed(row):
        # The sender reads the same explicit per-channel opt-in as the enqueuer. Unknown => off.
        session.expire_all()
        user=session.get(User,row.user_id); pref=session.get(RzyPreference,row.user_id)
        if not eligible(session,user,pref,row.alert_type): return False
        if row.alert_type=='little_rzy':
            ok,reason=rzy_send_check(session,row)
            if not ok: why[row.id]=reason
            return ok
        return True
    out=deliver_batch(session,owner='little-rzy:'+uuid4().hex,send=lambda row: send(row.recipient,row.subject,row.body_html,row.body_text),is_subscribed=subscribed,
        activated_at=datetime.min,alert_types=EMAIL_TYPES,limit=20,now=now.replace(tzinfo=None) if now else None)
    # The shared gate reports every refusal as an opt-out; record the real cause instead.
    for row_id,reason in why.items():
        session.execute(update(NotificationOutbox).where(NotificationOutbox.id==row_id,NotificationOutbox.state=='suppressed',
            NotificationOutbox.terminal_reason=='recipient opted out').values(terminal_reason=reason[:200]))
    if why: session.commit()
    return out
