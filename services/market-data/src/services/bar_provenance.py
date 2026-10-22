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


# ── One authoritative provider per completed bar (OD-01 fix 1, 2026-10-22) ──────────────────────
# Oct 21 evidence: 88% of attributable completed-bar revisions were Unusual Whales and yfinance
# alternately rewriting the same bars (each incremental fetch re-upserts the whole day from
# whichever provider answered); they drove 81% of 2,413 Little RZY rebuilds and the pauses that
# suppressed its emails. The rule below is ENFORCEMENT, unlike the observation-only trace above.
#
# Authority is explicit per (stock, timeframe, session date) in bar_session_authority: the
# market's PRIMARY provider by static routing (adapters.primary_provider — a temporary cooldown
# cannot make the fallback the authority). A stored bar is OWNED by its writer only once it has
# been written AFTER the bar completed (provenance last_written_at >= completion): a snapshot
# taken while the bar was forming is never protected against the complete bar. For each row:
#   forming bar, or stored bar last written while forming   -> written by any provider
#   new bar (nothing stored)                                 -> written (a fallback may fill a gap)
#   owned by THIS provider                                   -> written (same-provider correction)
#   owned by a fallback / writer unknown, from the authority -> written (the authority takes it)
#   owned by the authority (or unknown writer), from fallback-> NOT written ("protected")
# A fallback can therefore fill gaps and complete forming snapshots, the authority replaces a
# fallback's bar once and then owns it, and no completed bar can be rewritten back and forth.
# If the provenance trace fails for a write (writer unknown afterwards), the session is marked
# trace_failed and fallbacks may then only add new bars to it (fail closed).

#: An authority decision that could not be made: fallbacks may only ADD bars (fail closed).
AUTHORITY_FAILED_KEY = 'stockai:metric:bar_authority_check_failed'


class BarSessionAuthority(Base):
    __tablename__ = 'bar_session_authority'
    stock_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    timeframe: Mapped[str] = mapped_column(String(8), primary_key=True)
    session_date: Mapped[datetime] = mapped_column(DateTime, primary_key=True)
    authority: Mapped[str] = mapped_column(String(32))
    recorded_at: Mapped[datetime] = mapped_column(DateTime)
    #: row-fetch events refused / fallback bars taken over by the authority (event counts, not bars)
    protected: Mapped[int] = mapped_column(Integer, default=0)
    replaced: Mapped[int] = mapped_column(Integer, default=0)
    #: largest relative OHLC difference of a refused fallback row vs the stored bar (visibility
    #: into a systematically different authority; the refused values are otherwise discarded)
    max_protected_divergence: Mapped[float | None] = mapped_column(Float, nullable=True)
    trace_failed: Mapped[bool] = mapped_column(Boolean, default=False)


def _completed_at(timeframe, ts, market):
    if timeframe == '1d':
        return _daily_close_utc(market, ts) or ts + timedelta(days=1)
    end = ts + timedelta(minutes=MINUTES[timeframe])
    if timeframe == '1h':
        # The 15:30 ET hour bar ends at the close, not at 16:30.
        close = _daily_close_utc(market, datetime(*_local_date(ts, market)))
        if close is not None and ts < close < end:
            return close
    return end


def _local_date(ts, market):
    from zoneinfo import ZoneInfo
    tz = ZoneInfo('Asia/Hong_Kong' if market == 'HK' else 'America/New_York')
    local = ts.replace(tzinfo=timezone.utc).astimezone(tz)
    return local.year, local.month, local.day


def _session_date(timeframe, ts, market):
    """Exchange-local date for intraday (UTC ts), the stored date for daily."""
    if timeframe == '1d':
        return datetime(ts.year, ts.month, ts.day)
    return datetime(*_local_date(ts, market))


def _session_record(session, stock_id, timeframe, d, authority, now):
    """Get-or-create without a race: INSERT ... ON CONFLICT DO NOTHING, then read (PostgreSQL);
    a plain get-then-add elsewhere (tests on SQLite)."""
    if session.get_bind().dialect.name == 'postgresql':
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        session.execute(pg_insert(BarSessionAuthority).values(
            stock_id=stock_id, timeframe=timeframe, session_date=d, authority=authority, recorded_at=now,
            protected=0, replaced=0, trace_failed=False).on_conflict_do_nothing())
        return session.get(BarSessionAuthority, (stock_id, timeframe, d), populate_existing=True)
    rec = session.get(BarSessionAuthority, (stock_id, timeframe, d))
    if rec is None:
        rec = BarSessionAuthority(stock_id=stock_id, timeframe=timeframe, session_date=d, authority=authority,
                                  recorded_at=now, protected=0, replaced=0, trace_failed=False)
        session.add(rec); session.flush()
    return rec


def authorize_bar_writes(session, stock, timeframe, rows, provider, authority, now=None):
    """(rows allowed to be written, counts). Enforces the rule above for TRACKED timeframes."""
    if timeframe not in TRACKED or not rows or not authority:
        return rows, {}
    now = _naive_utc(now or datetime.now(timezone.utc))
    daily = timeframe == '1d'
    key = (lambda v: v.replace(tzinfo=None)) if daily else _naive_utc
    market = _market(stock)
    tf_value = rows[0]['timeframe']
    keys = sorted({key(r['ts']) for r in rows})
    lo, hi = keys[0], keys[-1]
    stored = {_naive_utc(p.ts): p for p in session.execute(select(
        Price.ts, Price.open, Price.high, Price.low, Price.close).where(
        Price.stock_id == stock.id, Price.timeframe == tf_value, Price.ts >= lo, Price.ts <= hi))}
    writer = {p.ts: (p.provider, p.last_written_at) for p in session.scalars(select(BarProvenance).where(
        BarProvenance.stock_id == stock.id, BarProvenance.timeframe == timeframe,
        BarProvenance.ts >= lo, BarProvenance.ts <= hi))}
    horizon = now - RETENTION
    sessions = {}
    for k in keys:
        d = _session_date(timeframe, k, market)
        if d not in sessions:
            # Sessions older than the retention window get no record (a full-history fetch would
            # otherwise create and immediately prune hundreds); the rule still applies to them.
            sessions[d] = (_session_record(session, stock.id, timeframe, d, authority, now) if d >= horizon - timedelta(days=1)
                           else BarSessionAuthority(authority=authority, protected=0, replaced=0, trace_failed=False))
    if any(d >= horizon - timedelta(days=1) for d in sessions):
        session.execute(delete(BarSessionAuthority).where(
            BarSessionAuthority.stock_id == stock.id, BarSessionAuthority.session_date < horizon))
    allowed, counts = [], dict(allowed=0, protected=0, replaced=0)
    for r in rows:
        ts = key(r['ts'])
        rec = sessions[_session_date(timeframe, ts, market)]
        auth = rec.authority
        done_at = _completed_at(timeframe, ts, market)
        prev, written_at = writer.get(ts, (None, None))
        owned = written_at is not None and written_at >= done_at
        if now < done_at or ts not in stored:
            ok, why = True, 'forming_or_new'
        elif rec.trace_failed and provider != auth:
            ok, why = False, 'trace_failed'           # writer unreliable: fallback may only add bars
        elif prev is not None and not owned:
            ok, why = True, 'completing_forming_snapshot'
        elif prev == provider:
            ok, why = True, 'same_provider'
        elif provider == auth:
            ok, why = True, 'authority_takes_over'
        else:
            ok, why = False, 'protected'
        if ok:
            allowed.append(r); counts['allowed'] += 1
            if why == 'authority_takes_over' and prev is not None:
                counts['replaced'] += 1; rec.replaced = (rec.replaced or 0) + 1
        else:
            counts['protected'] += 1; rec.protected = (rec.protected or 0) + 1
            old = stored[ts]
            div = max(_rel(float(getattr(old, f)), float(r[f])) for f in OHLC)
            rec.max_protected_divergence = max(rec.max_protected_divergence or 0.0, div)
    return allowed, counts


def mark_trace_failed(session, stock, timeframe, rows):
    """The provenance trace failed after an authorised write: the writer of these bars is now
    unknown, so their sessions fail closed for fallbacks (see authorize_bar_writes)."""
    if timeframe not in TRACKED or not rows:
        return
    daily = timeframe == '1d'
    key = (lambda v: v.replace(tzinfo=None)) if daily else _naive_utc
    market = _market(stock)
    for d in {_session_date(timeframe, key(r['ts']), market) for r in rows}:
        rec = session.get(BarSessionAuthority, (stock.id, timeframe, d))
        if rec is not None:
            rec.trace_failed = True


def fail_closed_rows(rows, timeframe, provider, authority, head):
    """When no authority decision could be made: the authority writes as before; a fallback may
    only ADD bars newer than the stored head, never rewrite a stored bar."""
    if timeframe not in TRACKED or provider == authority or head is None:
        return rows
    h = _naive_utc(head) if timeframe != '1d' else head.replace(tzinfo=None)
    key = (lambda v: v.replace(tzinfo=None)) if timeframe == '1d' else _naive_utc
    return [r for r in rows if key(r['ts']) > h]
