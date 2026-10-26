"""Observation of committed ingest batches; never an execution-quality certification.

Counts start after OHLCV validation, before duplicate/off-grid/authority filtering.
Written means insert-or-update operations, not unique bars or new trades. Rows are
committed with prices; callers isolate instrumentation failures in a SAVEPOINT.
"""
from collections import Counter
from datetime import datetime, timedelta, timezone, date
from uuid import uuid4
from zoneinfo import ZoneInfo
from sqlalchemy import Boolean, Date, DateTime, Integer, String, case, select, delete, func
from sqlalchemy.orm import Mapped, mapped_column
from db import Base

TRACKED={'5m':5,'15m':15,'1h':60}
RETENTION=timedelta(days=35)


class BarIngestObservation(Base):
    __tablename__='bar_ingest_observations'
    batch_id: Mapped[str]=mapped_column(String(32),primary_key=True)
    session_day: Mapped[date]=mapped_column(Date,primary_key=True)
    session_kind: Mapped[str]=mapped_column(String(12),primary_key=True)
    stock_id: Mapped[int]=mapped_column(Integer,index=True)
    symbol: Mapped[str]=mapped_column(String(32))
    market: Mapped[str]=mapped_column(String(8))
    timeframe: Mapped[str]=mapped_column(String(8))
    provider: Mapped[str]=mapped_column(String(48))
    observed_at: Mapped[datetime]=mapped_column(DateTime,index=True)
    received: Mapped[int]=mapped_column(Integer)
    duplicate_dropped: Mapped[int]=mapped_column(Integer)
    off_grid_rejected: Mapped[int]=mapped_column(Integer)
    authority_rejected: Mapped[int]=mapped_column(Integer)
    written: Mapped[int]=mapped_column(Integer)
    forced: Mapped[bool]=mapped_column(Boolean)


class BarPostCloseWrite(Base):
    __tablename__='bar_post_close_writes'
    stock_id: Mapped[int]=mapped_column(Integer,primary_key=True)
    timeframe: Mapped[str]=mapped_column(String(8),primary_key=True)
    ts: Mapped[datetime]=mapped_column(DateTime,primary_key=True,index=True)
    completed_at: Mapped[datetime]=mapped_column(DateTime,index=True)
    observed_at: Mapped[datetime]=mapped_column(DateTime,index=True)
    provider: Mapped[str]=mapped_column(String(48))


def naive_utc(value):
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value


def regular_us_close(ts,timeframe):
    """RZY's session-anchored regular US bars only, including short final hours."""
    from common.market_calendar import session_bounds
    local=ts.replace(tzinfo=timezone.utc).astimezone(ZoneInfo('America/New_York'))
    bounds=session_bounds('US',local.date())
    if not bounds:return None
    start,end=(naive_utc(v) for v in bounds)
    if not start<=ts<end or (ts-start).total_seconds()%(TRACKED[timeframe]*60):return None
    return min(end,ts+timedelta(minutes=TRACKED[timeframe]))


def record_ingest_observation(session,stock,timeframe,received,written,off_grid,provider,*,forced=False,now=None):
    if timeframe not in TRACKED or not received:return
    now=naive_utc(now or datetime.now(timezone.utc))
    market='HK' if stock.symbol.endswith('.HK') else getattr(stock.market,'value',stock.market)
    tz=ZoneInfo('Asia/Hong_Kong' if market=='HK' else 'America/New_York')
    def key(row):return (naive_utc(row['ts']).replace(tzinfo=timezone.utc).astimezone(tz).date(),row.get('session','REGULAR'))
    unique={naive_utc(r['ts']):r for r in received}
    allowed={naive_utc(r['ts']) for r in written}; rejected={naive_utc(r['ts']) for r in off_grid}
    if not allowed.isdisjoint(rejected) or not (allowed|rejected)<=set(unique):
        raise ValueError('Ingest observation rows do not partition the received keys')
    counts={}
    for row in received:counts.setdefault(key(row),Counter())['received']+=1
    for ts,row in unique.items():
        c=counts[key(row)];c['unique']+=1
        c['written' if ts in allowed else 'off_grid_rejected' if ts in rejected else 'authority_rejected']+=1
    batch=uuid4().hex
    for (day,kind),c in counts.items():
        session.add(BarIngestObservation(batch_id=batch,session_day=day,session_kind=kind,
            stock_id=stock.id,symbol=stock.symbol,market=market,timeframe=timeframe,provider=provider,
            observed_at=now,received=c['received'],duplicate_dropped=c['received']-c['unique'],
            off_grid_rejected=c['off_grid_rejected'],authority_rejected=c['authority_rejected'],
            written=c['written'],forced=forced))
    # US RZY frames only. This is the first OBSERVED post-close write since instrumentation,
    # not first-ever ingestion, provider finality, or transaction commit time.
    candidates=[]
    for row in written if market=='US' else []:
        if row.get('session','REGULAR')!='REGULAR':continue
        ts=naive_utc(row['ts']);close=regular_us_close(ts,timeframe)
        if close and close<=now and ts>=now-RETENTION:
            candidates.append(dict(stock_id=stock.id,timeframe=timeframe,ts=ts,completed_at=close,observed_at=now,provider=provider))
    if candidates:
        dialect=session.get_bind().dialect.name
        if dialect=='postgresql':from sqlalchemy.dialects.postgresql import insert
        elif dialect=='sqlite':from sqlalchemy.dialects.sqlite import insert
        else:raise ValueError('Unsupported observation dialect')
        # Most re-fetches repeat already observed bars. Avoid writing them again.
        stamps=[r['ts'] for r in candidates]
        known=dict(session.execute(select(BarPostCloseWrite.ts,BarPostCloseWrite.observed_at).where(BarPostCloseWrite.stock_id==stock.id,
            BarPostCloseWrite.timeframe==timeframe,BarPostCloseWrite.ts.in_(stamps))).all())
        candidates=[r for r in candidates if r['ts'] not in known or r['observed_at']<known[r['ts']]]
        if candidates:
            stmt=insert(BarPostCloseWrite).values(candidates)
            earlier=stmt.excluded.observed_at<BarPostCloseWrite.observed_at
            session.execute(stmt.on_conflict_do_update(index_elements=['stock_id','timeframe','ts'],set_={
                'observed_at':case((earlier,stmt.excluded.observed_at),else_=BarPostCloseWrite.observed_at),
                'provider':case((earlier,stmt.excluded.provider),else_=BarPostCloseWrite.provider)}))
    # Index-bounded cleanup, scoped to this stock; no blanket scan on every ingestion.
    session.execute(delete(BarIngestObservation).where(BarIngestObservation.stock_id==stock.id,BarIngestObservation.observed_at<now-RETENTION))
    session.execute(delete(BarPostCloseWrite).where(BarPostCloseWrite.stock_id==stock.id,BarPostCloseWrite.ts<now-RETENTION))


def ingest_totals(session,*,since=None,until=None,session_day=None):
    fields=('received','duplicate_dropped','off_grid_rejected','authority_rejected','written')
    dimensions=(BarIngestObservation.session_day,BarIngestObservation.session_kind,BarIngestObservation.market,
        BarIngestObservation.timeframe,BarIngestObservation.provider)
    query=select(*dimensions,func.count().label('batch_session_groups'),
        *(func.sum(getattr(BarIngestObservation,k)).label(k) for k in fields))
    if since is not None:query=query.where(BarIngestObservation.observed_at>=naive_utc(since))
    if until is not None:query=query.where(BarIngestObservation.observed_at<naive_utc(until))
    if session_day is not None:query=query.where(BarIngestObservation.session_day==session_day)
    return [dict(session_day=r.session_day.isoformat(),session_kind=r.session_kind,market=r.market,
        timeframe=r.timeframe,provider=r.provider,batch_session_groups=r.batch_session_groups,
        **{k:int(getattr(r,k)) for k in fields}) for r in session.execute(query.group_by(*dimensions).order_by(*dimensions))]


def purge_observations(session,now):
    """Indexed cleanup also reaches symbols no longer fetched; run in the existing scan."""
    cutoff=naive_utc(now)-RETENTION
    session.execute(delete(BarIngestObservation).where(BarIngestObservation.observed_at<cutoff))
    session.execute(delete(BarPostCloseWrite).where(BarPostCloseWrite.ts<cutoff))
