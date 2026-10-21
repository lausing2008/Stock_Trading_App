"""Observation-only trace of intraday bar writes: who wrote each bar and what changed.

Context: on 2026-10-20 completed 5m bars were restated intraday (3,956 Little RZY rebuilds;
median change 0.077%, all four OHLC fields together, mostly on bars 1-6 h old). US 5m routing
is unusual_whales -> yfinance -> polygon, and every intraday fetch re-upserts the whole day, so
one hypothesis is vendors overwriting each other. Stored prices never recorded their provider,
so the hypothesis could not be tested.

This module records, BEFORE the ingestion upsert and without changing what is written:
- bar_provenance: the provider that last wrote each intraday bar, first/last write times and the
  stored values. Written only for a new bar, a changed bar or a provider change (bounded).
- bar_revisions: one row per completed-bar OHLC change beyond float noise, with previous and new
  provider, old/new OHLCV, changed fields, relative size, bar age at fetch time and a class:
  cross_provider | same_provider | previous_provider_unknown.
Volume-only changes and same-value provider switches are counted on the provenance row.

It must never break ingestion: callers wrap it, and it runs inside a SAVEPOINT.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import DateTime, Float, Integer, String, JSON, Boolean, select, delete
from sqlalchemy.orm import Mapped, mapped_column

from db import Base, Price

NOISE_REL = 1e-6
TRACKED = {'5m', '15m', '1h'}
MINUTES = {'5m': 5, '15m': 15, '1h': 60}
RETENTION = timedelta(days=35)


class BarProvenance(Base):
    __tablename__ = 'bar_provenance'
    stock_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    timeframe: Mapped[str] = mapped_column(String(8), primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, primary_key=True)
    provider: Mapped[str] = mapped_column(String(32))
    first_written_at: Mapped[datetime] = mapped_column(DateTime)
    last_written_at: Mapped[datetime] = mapped_column(DateTime)
    last_changed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float)
    ohlc_revisions: Mapped[int] = mapped_column(Integer, default=0)
    volume_only_revisions: Mapped[int] = mapped_column(Integer, default=0)
    provider_switches_same_values: Mapped[int] = mapped_column(Integer, default=0)
    off_grid: Mapped[bool] = mapped_column(Boolean, default=False)


class BarRevision(Base):
    __tablename__ = 'bar_revisions'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stock_id: Mapped[int] = mapped_column(Integer, index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    timeframe: Mapped[str] = mapped_column(String(8))
    ts: Mapped[datetime] = mapped_column(DateTime, index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    bar_age_minutes: Mapped[float] = mapped_column(Float)
    previous_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    provider: Mapped[str] = mapped_column(String(32))
    classification: Mapped[str] = mapped_column(String(32), index=True)
    changed_fields: Mapped[list] = mapped_column(JSON)
    max_rel_change: Mapped[float] = mapped_column(Float)
    before: Mapped[dict] = mapped_column(JSON)
    after: Mapped[dict] = mapped_column(JSON)


def _naive_utc(value):
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _rel(a, b):
    return abs(a - b) / max(abs(a), abs(b)) if max(abs(a), abs(b)) else 0.0


def classify(previous_provider, provider):
    if previous_provider is None:
        return 'previous_provider_unknown'
    return 'same_provider' if previous_provider == provider else 'cross_provider'


def observe_bar_writes(session, stock, timeframe, rows, provider, now=None):
    """Record provenance/revisions for `rows` about to be upserted. Returns counts."""
    if timeframe not in TRACKED or not rows:
        return {}
    now = _naive_utc(now or datetime.now(timezone.utc))
    tf_value = rows[0]['timeframe']
    keys = sorted({_naive_utc(r['ts']) for r in rows})
    lo, hi = keys[0], keys[-1]
    stored = {_naive_utc(p.ts): p for p in session.scalars(select(Price).where(
        Price.stock_id == stock.id, Price.timeframe == tf_value, Price.ts >= lo, Price.ts <= hi))}
    prov = {p.ts: p for p in session.scalars(select(BarProvenance).where(
        BarProvenance.stock_id == stock.id, BarProvenance.timeframe == timeframe,
        BarProvenance.ts >= lo, BarProvenance.ts <= hi))}
    step = timedelta(minutes=MINUTES[timeframe])
    counts = dict(new=0, unchanged=0, ohlc_revisions=0, volume_only=0, provider_switch_same_values=0, off_grid=0)
    for r in rows:
        ts = _naive_utc(r['ts'])
        new = {k: float(r[k]) for k in ('open', 'high', 'low', 'close', 'volume')}
        off_grid = ts.second != 0 or ts.microsecond != 0 or (ts.minute % MINUTES[timeframe] != 0 and timeframe != '1h')
        counts['off_grid'] += off_grid
        p = prov.get(ts)
        old = stored.get(ts)
        if old is None and p is None:
            session.add(BarProvenance(stock_id=stock.id, timeframe=timeframe, ts=ts, provider=provider,
                first_written_at=now, last_written_at=now, off_grid=off_grid, ohlc_revisions=0,
                volume_only_revisions=0, provider_switches_same_values=0, **new))
            counts['new'] += 1
            continue
        before = ({k: float(getattr(old, k)) for k in ('open', 'high', 'low', 'close', 'volume')} if old is not None
                  else {k: getattr(p, k) for k in ('open', 'high', 'low', 'close', 'volume')})
        changed = [k for k in ('open', 'high', 'low', 'close') if _rel(before[k], new[k]) > NOISE_REL]
        volume_changed = before['volume'] != new['volume']
        previous = p.provider if p is not None else None
        if p is None:
            p = BarProvenance(stock_id=stock.id, timeframe=timeframe, ts=ts, provider=provider, first_written_at=now,
                              last_written_at=now, off_grid=off_grid, ohlc_revisions=0, volume_only_revisions=0,
                              provider_switches_same_values=0, **before)
            session.add(p)
        if changed:
            completed = now >= ts + step
            session.add(BarRevision(stock_id=stock.id, symbol=stock.symbol, timeframe=timeframe, ts=ts, fetched_at=now,
                bar_age_minutes=(now - (ts + step)).total_seconds() / 60, previous_provider=previous, provider=provider,
                classification=classify(previous, provider) + ('' if completed else '_forming_bar'),
                changed_fields=changed + (['volume'] if volume_changed else []),
                max_rel_change=max(_rel(before[k], new[k]) for k in changed), before=before, after=new))
            p.ohlc_revisions = (p.ohlc_revisions or 0) + 1
            p.last_changed_at = now
            counts['ohlc_revisions'] += 1
        elif volume_changed:
            p.volume_only_revisions = (p.volume_only_revisions or 0) + 1
            counts['volume_only'] += 1
        elif previous is not None and previous != provider:
            p.provider_switches_same_values = (p.provider_switches_same_values or 0) + 1
            counts['provider_switch_same_values'] += 1
        else:
            counts['unchanged'] += 1
            continue
        p.provider = provider
        p.last_written_at = now
        for k, v in new.items():
            setattr(p, k, v)
    if counts['new'] or counts['ohlc_revisions']:
        session.execute(delete(BarProvenance).where(BarProvenance.stock_id == stock.id, BarProvenance.ts < now - RETENTION))
        session.execute(delete(BarRevision).where(BarRevision.stock_id == stock.id, BarRevision.fetched_at < now - RETENTION))
    return counts
