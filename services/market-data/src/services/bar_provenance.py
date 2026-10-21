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

Daily bars ('1d', tracker 457 follow-up, 2026-10-21): a 13:30 UTC yfinance daily fetch moved MU's
past completed closes slightly (50-bar mean 984.22686 -> 984.23126). Daily ingest re-fetches from
6 days before the last stored bar on every refresh, possibly from a different provider, so '1d' is
traced too. A daily row's key is the session's wall-clock date at 00:00 with the offset DROPPED
(not converted) - exactly how PostgreSQL stores a tz-aware value in a naive column - so its key is
`ts.replace(tzinfo=None)`, not a UTC conversion. A daily bar is completed once that session's
regular close (common.market_calendar.session_bounds, early closes included) has passed; a
non-session date counts as completed. Only rows inside RETENTION are traced (bounded), there is no
off-grid check, and bar_age_minutes is minutes since that session's close.

Magnitude (rounding vs material) is derived from the stored before/after values by
revision_magnitude(); it is not a column, because bar_revisions already exists in production and
create_all() never adds columns to an existing table.

It must never break ingestion: callers wrap it, and it runs inside a SAVEPOINT.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import DateTime, Float, Integer, String, JSON, Boolean, select, delete
from sqlalchemy.orm import Mapped, mapped_column

from db import Base, Price

NOISE_REL = 1e-6
TRACKED = {'5m', '15m', '1h', '1d'}
MINUTES = {'5m': 5, '15m': 15, '1h': 60}
RETENTION = timedelta(days=35)
OHLC = ('open', 'high', 'low', 'close')

#: Rounding-scale bound for ONE changed OHLC field: |after - before| <= max(ROUNDING_ABS,
#: ROUNDING_REL * max(|before|, |after|)). Anything larger is 'material'.
#: - ROUNDING_ABS = 0.005: half a cent, the most a value can move when one source rounds to the
#:   cent and another does not (or rounds the other way). Dominates below $250.
#: - ROUNDING_REL = 2e-5: float32 has a 24-bit mantissa (eps ~6e-8, so ~1.2e-7 per round trip)
#:   and providers that recompute split/dividend-adjusted prices from a factor quoted to ~5-6
#:   significant digits move a price by up to ~1e-5 relative. 2e-5 sits above both with margin,
#:   while staying well under a real tick-level correction on low-priced names (one cent on a
#:   $10 stock is 1e-3, 50x the bound). Dominates above $250 (e.g. $0.0197 at MU's ~$984).
#: These are reading thresholds, not trading ones: every revision row keeps max_rel_change and
#: the full before/after, so the cut can be re-drawn later without a re-trace.
ROUNDING_ABS = 0.005
ROUNDING_REL = 2e-5


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


def revision_magnitude(before, after):
    """'rounding' if every OHLC field that changed beyond NOISE_REL moved by at most
    max(ROUNDING_ABS, ROUNDING_REL * scale), else 'material'. Pure; reads stored dicts.
    The 1e-9 slack keeps a change of exactly the bound (10.105 - 10.1 = 0.0050000000000008 in
    binary floating point) on the rounding side. A revision with no OHLC field changed (cannot
    be recorded) is reported as 'rounding'."""
    for k in OHLC:
        a, b = float(before[k]), float(after[k])
        if _rel(a, b) > NOISE_REL and abs(a - b) > max(ROUNDING_ABS, ROUNDING_REL * max(abs(a), abs(b))) + 1e-9:
            return 'material'
    return 'rounding'


def _market(stock):
    m = getattr(stock, 'market', None)
    return str(getattr(m, 'value', m) or 'US').upper()


def _daily_close_utc(market, day_ts):
    """Naive-UTC instant the session dated `day_ts` closed; None for a non-session date."""
    from common.market_calendar import session_bounds
    bounds = session_bounds(market, day_ts.date())
    return None if bounds is None else bounds[1].astimezone(timezone.utc).replace(tzinfo=None)


def observe_bar_writes(session, stock, timeframe, rows, provider, now=None):
    """Record provenance/revisions for `rows` about to be upserted. Returns counts."""
    if timeframe not in TRACKED or not rows:
        return {}
    now = _naive_utc(now or datetime.now(timezone.utc))
    daily = timeframe == '1d'
    key = (lambda v: v.replace(tzinfo=None)) if daily else _naive_utc
    if daily:
        # Bounded: a first/full-history fetch would otherwise add (then prune) thousands of rows.
        rows = [r for r in rows if key(r['ts']) >= now - RETENTION]
        if not rows:
            return {}
        market = _market(stock)
    tf_value = rows[0]['timeframe']
    keys = sorted({key(r['ts']) for r in rows})
    lo, hi = keys[0], keys[-1]
    stored = {_naive_utc(p.ts): p for p in session.scalars(select(Price).where(
        Price.stock_id == stock.id, Price.timeframe == tf_value, Price.ts >= lo, Price.ts <= hi))}
    prov = {p.ts: p for p in session.scalars(select(BarProvenance).where(
        BarProvenance.stock_id == stock.id, BarProvenance.timeframe == timeframe,
        BarProvenance.ts >= lo, BarProvenance.ts <= hi))}
    step = None if daily else timedelta(minutes=MINUTES[timeframe])
    counts = dict(new=0, unchanged=0, ohlc_revisions=0, volume_only=0, provider_switch_same_values=0, off_grid=0)
    for r in rows:
        ts = key(r['ts'])
        new = {k: float(r[k]) for k in ('open', 'high', 'low', 'close', 'volume')}
        off_grid = False if daily else (ts.second != 0 or ts.microsecond != 0
                                        or (ts.minute % MINUTES[timeframe] != 0 and timeframe != '1h'))
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
            if daily:
                # Non-session date: no close to wait for; age is measured from the date's end.
                closes_at = _daily_close_utc(market, ts) or ts + timedelta(days=1)
            else:
                closes_at = ts + step
            completed = now >= closes_at
            session.add(BarRevision(stock_id=stock.id, symbol=stock.symbol, timeframe=timeframe, ts=ts, fetched_at=now,
                bar_age_minutes=(now - closes_at).total_seconds() / 60, previous_provider=previous, provider=provider,
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


class DailyIngestRecord(Base):
    """Per-symbol daily-bar fetch evidence: is THIS symbol's latest close refreshed?

    A scheduler batch recorded "ok" proves only that the batch ran; ingest_universe logs a
    failing symbol and carries on. Consumers that must not read a forming or unrefreshed daily
    row as a close (Game Plan, reports) check this row: a successful fetch at or after a
    session's post-close ingest start whose provider response contained that session.
    One row per stock, overwritten by each fetch; failures keep the last success.
    """
    __tablename__ = 'daily_ingest_records'
    stock_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    #: newest session date present in that successful provider response
    last_observed_session: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(300), nullable=True)


def record_daily_fetch(session, stock, *, fetched_at, ok, observed_session=None, provider=None, error=None):
    """Upsert the stock's fetch evidence. Callers wrap it; it must never break ingestion."""
    row = session.get(DailyIngestRecord, stock.id)
    if row is None:
        row = DailyIngestRecord(stock_id=stock.id, symbol=stock.symbol)
        session.add(row)
    when = _naive_utc(fetched_at)
    if ok:
        row.last_success_at, row.last_provider = when, provider
        # Stored like Price.ts: the session's wall-clock date, offset dropped (not converted),
        # so it compares directly with the D1 row keys.
        row.last_observed_session = (observed_session.replace(tzinfo=None)
                                     if observed_session is not None and observed_session.tzinfo else observed_session)
    else:
        row.last_failure_at, row.last_error = when, (error or '')[:300]
