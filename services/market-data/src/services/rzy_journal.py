"""Durable simulator observations. Replays never become forward performance evidence."""
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import math
from sqlalchemy import select
from .little_rzy import stable, VERSION
from .rzy_data import MINUTES, utc
from .rzy_models import RzyPaperJournal, RzyStream


def record_positions(session, key, engine, events, observed_at, origin):
    positions=engine.s['positions']; generation=engine.s.get('generation',1)
    # Incremental passes touch only positions which emitted a lifecycle event. Initial
    # observations import the retained cache explicitly, without pretending it is a backtest.
    touched=set(positions) if origin!='incremental' else {e.get('structure_id') for e in events} & set(positions)
    ids={k:stable(key,generation,k) for k in touched}
    rows={r.journal_id:r for r in session.scalars(select(RzyPaperJournal).where(RzyPaperJournal.journal_id.in_(list(ids.values()))))} if ids else {}
    for ident,jid in ids.items():
        p=positions[ident]; st=engine.s['structures'][ident]
        lifecycle=[deepcopy(e) for e in events if e.get('structure_id')==ident and e['event_type'] in
                   ('TRIGGERED','ENTRY_CANCELLED','PAPER_FILL','PAPER_EXIT','EXIT_PENDING')]
        row=rows.get(jid)
        if row is None:
            trigger=next((e for e in lifecycle if e['event_type']=='TRIGGERED'),None)
            fresh=trigger and timedelta(0)<=utc(observed_at)-utc(datetime.fromisoformat(trigger['signal_time']))<=timedelta(minutes=MINUTES[engine.timeframe]+3)
            cohort='forward_observation' if origin=='incremental' and fresh else origin if origin!='incremental' else 'imported_or_late'
            row=RzyPaperJournal(journal_id=jid,stream_key=key,symbol=engine.symbol,timeframe=engine.timeframe,
                config_hash=engine.config.hash,generation=generation,structure_id=ident,observed_in=cohort,
                first_seen_at=observed_at,updated_at=observed_at,payload={})
            session.add(row)
        old=row.payload or {}; history=old.get('lifecycle',[])
        known={e['event_id'] for e in history}
        history=history+[e for e in lifecycle if e['event_id'] not in known]
        payload=dict(position=deepcopy(p),direction=st['direction'],stop=st.get('stop'),target=st.get('target'),
            planned_entry=st.get('planned_entry'),strategy_version=VERSION,configuration=asdict(engine.config),lifecycle=history)
        if payload!=old:
            row.payload=payload; row.updated_at=observed_at


def _finite(value):
    return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)


def summarize(rows):
    """Only closed, non-cancelled, current-generation forward observations are scored.

    This is descriptive, not a calibration or promotion gate. Currency P&L is not pooled
    across stocks: R normalises one-share net P&L by the initial fill-to-stop risk.
    """
    forward=[r for r in rows if not r['superseded'] and r['observed_in']=='forward_observation']
    closed=[r for r in forward if r['state']=='CLOSED' and not r.get('cancelled')]
    scored=[]
    for r in closed:
        risk=(r['fill']-r['stop']) * (1 if r['direction']=='long' else -1) if _finite(r.get('stop')) and _finite(r.get('fill')) else 0
        if _finite(risk) and risk>0 and _finite(r.get('net')) and _finite(r['net']/risk):
            scored.append((r,r['net']/risk))
    ordered=sorted(scored,key=lambda item:(item[0].get('closed_at') or '',item[0]['journal_id']))
    equity=peak=drawdown=0.
    for _,value in ordered:
        equity+=value; peak=max(peak,equity); drawdown=max(drawdown,peak-equity)
    values=[v for _,v in scored]; n=len(values)
    return dict(forward_observations=len(forward),cancelled=sum(bool(r.get('cancelled')) for r in forward),
        unresolved=sum(r['state']!='CLOSED' for r in forward),closed_trades=len(closed),scored_trades=n,
        unscorable_closed=len(closed)-n,win_rate=sum(v>0 for v in values)/n if n else None,
        net_expectancy_r=sum(values)/n if n else None,average_win_r=_mean([v for v in values if v>0]),
        average_loss_r=_mean([v for v in values if v<=0]),realized_drawdown_r=drawdown if n else None,
        replay_or_imported=sum(r['observed_in']!='forward_observation' for r in rows),
        superseded=sum(r['superseded'] for r in rows),
        conclusion='No demonstrated edge. Descriptive simulator observations only; no live-trading approval.')


def _mean(values):
    return sum(values)/len(values) if values else None


def paper_report(session,symbols,config_hash,now=None):
    since=(utc(now or datetime.now(timezone.utc))-timedelta(days=90)).replace(tzinfo=None)
    journals=session.scalars(select(RzyPaperJournal).where(RzyPaperJournal.symbol.in_(symbols),RzyPaperJournal.config_hash==config_hash,RzyPaperJournal.first_seen_at>=since)
        .order_by(RzyPaperJournal.first_seen_at,RzyPaperJournal.journal_id)).all() if symbols else []
    streams=session.scalars(select(RzyStream).where(RzyStream.symbol.in_(symbols),RzyStream.config_hash==config_hash)).all() if symbols else []
    generations={r.stream_key:r.checkpoint.get('generation',1) for r in streams}
    rows=[]
    for r in journals:
        p=r.payload; exits=[e for e in p.get('lifecycle',[]) if e['event_type']=='PAPER_EXIT' and e.get('paper_state')=='CLOSED']
        rows.append(dict(p.get('position',{}),journal_id=r.journal_id,symbol=r.symbol,timeframe=r.timeframe,
            config_hash=r.config_hash,generation=r.generation,structure_id=r.structure_id,observed_in=r.observed_in,
            first_seen_at=r.first_seen_at.isoformat(),updated_at=r.updated_at.isoformat(),
            superseded=generations.get(r.stream_key)!=r.generation,direction=p['direction'],stop=p.get('stop'),target=p.get('target'),
            closed_at=exits[-1]['signal_time'] if exits else None,lifecycle=p.get('lifecycle',[])))
    groups={}
    for field in ('timeframe','direction'):
        groups[field]={v:summarize([r for r in rows if r[field]==v]) for v in sorted({r[field] for r in rows})}
    return dict(summary=summarize(rows),groups=groups,journal=rows[-200:],journal_total=len(rows),window_started_at=since.isoformat(),
        recording_since=min((r.first_seen_at for r in journals),default=None),
        units='Net R per one-share simulated trade; configured spread/slippage, commissions and borrow apply. Realised drawdown in exit order, not portfolio or mark-to-market drawdown.',
        coverage='Last 90 days by first observation, current configuration and your current watchlist. Recording starts with this release; retained checkpoint imports and rebuilt generations are labelled. No complete historical reconstruction. Superseded generations are kept but excluded from scores; older journal rows remain stored.')
