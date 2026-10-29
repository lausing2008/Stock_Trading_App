"""Read one session's 5m bars and their provenance in ONE consistent snapshot (design §4).

Reuses RZY's bar preparation (grid, regular session, 2-minute settle, gaps, staleness) and the
window price basis. Adds what the Intraday contract needs: per-bar raw/source/input hashes, the
15m context derived only from complete 5m constituents (one traceable source), and honest times:
`readable_at` is an eligibility bound, `input_read_at` is when these exact values were read, and
first availability stays unknown. US only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from sqlalchemy import select, text

from common.market_calendar import session_bounds
from common.price_basis import resolve_ohlc

from ..rzy_data import aggregate_completed_bars, prepare_bars
from . import contract as c

OHLCV = ("open", "high", "low", "close", "volume")


@dataclass
class Snapshot:
    status: str                         # ok | not_a_session | no_completed_bars | stale
    session_date: date
    data_cutoff: datetime
    read_at: datetime | None = None
    bars_5m: list = field(default_factory=list)     # InputRecord, role 'lookback' until chosen
    bars_15m: list = field(default_factory=list)
    evidence: dict = field(default_factory=dict)


def _naive(dt):
    return c.utc(dt).replace(tzinfo=None)


def read_snapshot(engine, *, stock_id: int, symbol: str, market: str, session_date: date, data_cutoff: datetime,
                  mode: str = "observed", clock=None) -> Snapshot:
    """`data_cutoff` is the evaluation's cutoff (bars must be readable by then). In observed mode
    `input_read_at` is the real time the snapshot was read; in replay it is the cutoff, labelled
    `replay_logical`."""
    from db import Price, TimeFrame
    from ..bar_provenance import BarProvenance, BarSessionAuthority
    if mode not in ("observed", "replay"):
        raise c.ContractViolation(f"unknown mode {mode!r}")
    times = c.session_times(market, session_date)          # US only; coverage stop
    cutoff = c.utc(data_cutoff)
    if times is None:
        return Snapshot("not_a_session", session_date, cutoff)
    lo, hi = _naive(times.open), _naive(times.close)
    pg = engine.dialect.name == "postgresql"
    conn_cm = engine.connect()
    with conn_cm as conn:
        if pg:
            conn = conn.execution_options(isolation_level="REPEATABLE READ")
        with conn.begin():
            if pg:
                conn.execute(text("SET TRANSACTION READ ONLY"))
            rows = conn.execute(select(Price.ts, Price.open, Price.high, Price.low, Price.close, Price.adj_close,
                                       Price.volume, Price.session).where(
                Price.stock_id == stock_id, Price.timeframe == TimeFrame.M5, Price.ts >= lo, Price.ts < hi)
                .order_by(Price.ts)).all()
            prov = {p.ts: p for p in conn.execute(select(BarProvenance).where(
                BarProvenance.stock_id == stock_id, BarProvenance.timeframe == "5m",
                BarProvenance.ts >= lo, BarProvenance.ts < hi)).all()}
            auth = conn.execute(select(BarSessionAuthority.trace_failed).where(
                BarSessionAuthority.stock_id == stock_id, BarSessionAuthority.timeframe == "5m",
                BarSessionAuthority.session_date == datetime(session_date.year, session_date.month,
                                                             session_date.day))).scalar()
            read_at = c.utc((clock or (lambda: datetime.now(timezone.utc)))())
    if mode == "replay":
        read_at, read_basis = cutoff, "replay_logical"
    else:
        read_basis = "observed"
        if read_at < cutoff:
            raise c.ContractViolation("snapshot read before its own data cutoff")

    bars, evidence = prepare_bars(rows, "5m", cutoff, session_bounds=session_bounds, resolve_ohlc=resolve_ohlc)
    snap = Snapshot("ok", session_date, cutoff, read_at, evidence=evidence)
    if not bars:
        snap.status = "no_completed_bars"
        return snap
    raw_by_ts = {c.utc(r.ts): r for r in rows}
    trace_failed = bool(auth)
    reason = evidence.get("price_basis_reason")
    for b in bars:
        t = c.utc(datetime.fromisoformat(b["time"]))
        r = raw_by_ts[t]
        p = prov.get(_naive(t))
        consistent = p is not None and all(getattr(p, k) == getattr(r, k) for k in OHLCV)
        provider = p.provider if consistent and not trace_failed else None
        basis = ("trace_failed" if trace_failed else "provenance_matches" if consistent
                 else "provenance_missing" if p is None else "provenance_mismatch")
        # Evidence first; every stored hash is computed FROM it, so storage alone recomputes them.
        ev = c.bar_evidence(
            dict(stock_id=stock_id, symbol=symbol.upper(), timeframe="5m", bar_open=t, open=r.open, high=r.high,
                 low=r.low, close=r.close, adj_close=r.adj_close, volume=r.volume, session=r.session),
            provider, basis,
            dict(timeframe="5m", bar_open=t, open=b["open"], high=b["high"], low=b["low"], close=b["close"],
                 volume=b["volume"], price_basis=b["price_basis"], price_basis_reason=reason))
        h = c.hashes_from_evidence(ev)
        snap.bars_5m.append(c.InputRecord(
            role="lookback", timeframe="5m", bar_open=t, source="native",
            values={k: b[k] for k in OHLCV} | {"price_basis": b["price_basis"]},
            raw_hash=h["raw_hash"], source_hash=h["source_hash"], input_hash=h["input_hash"], derived_hash=None,
            readable_at=c.readable_at(t, "5m", times), input_read_at=read_at, read_clock_basis=read_basis,
            evidence=ev, extra={"provider": provider or "unknown", "provider_basis": basis}))
    by_open = {r.bar_open: r for r in snap.bars_5m}
    derived, dev = aggregate_completed_bars(bars, evidence, "15m", cutoff, session_bounds=session_bounds)
    for d in derived:
        t = c.utc(datetime.fromisoformat(d["time"]))
        members = [by_open[m] for m in sorted(by_open) if t <= m < c.utc(datetime.fromisoformat(d["closed_at"]))]
        ev = c.derived_evidence(
            dict(timeframe="15m", bar_open=t, open=d["open"], high=d["high"], low=d["low"], close=d["close"],
                 volume=d["volume"], price_basis=d["price_basis"], price_basis_reason=reason),
            [m.evidence for m in members])
        h = c.hashes_from_evidence(ev)
        snap.bars_15m.append(c.InputRecord(
            role="context", timeframe="15m", bar_open=t, source="derived_from_5m",
            values={k: d[k] for k in OHLCV} | {"price_basis": d["price_basis"]},
            raw_hash=None, source_hash=None, input_hash=h["input_hash"], derived_hash=h["derived_hash"],
            readable_at=c.readable_at(t, "15m", times), input_read_at=read_at, read_clock_basis=read_basis,
            evidence=ev, extra={"constituents_unknown_source": sum(m.extra["provider"] == "unknown" for m in members)}))
    snap.evidence = dict(evidence, derived_15m=dict(status=dev.get("status"), gaps=dev.get("gaps", [])))
    if evidence.get("stale"):
        snap.status = "stale"
    return snap
