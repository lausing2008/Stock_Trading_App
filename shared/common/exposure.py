"""M15 — atomic exposure reservation, so a concentration cap holds across concurrent entries.

THE DEFECT. Measured 2026-10-01: two individually legal entries opened **20.02% of equity in one
sector against a 15% cap**, because both were sized against the same pre-loop snapshot of open
positions and neither saw the other.

WHY RE-QUERYING IS NOT THE FIX, and this is the whole design argument. A fresh read per candidate
narrows the race window; it does not close it. Two writers — an entry scan and a conditional
order firing in the same moment, which are *different code paths* in this codebase — can both
read, both find room, and both write. Check-then-act is only safe when the check and the act are
one atomic step. That step is a reservation.

THE SERIALIZATION POINT is a row lock on the portfolio. Every reservation for a portfolio takes
it, so reservations for one portfolio are ordered and the sum they each see includes every
earlier one. Portfolios do not contend with each other.

    **On PostgreSQL** `SELECT ... FOR UPDATE` genuinely serialises them.
    **On SQLite** `with_for_update()` is accepted and IGNORED; SQLite serialises writers at the
    database level instead, which happens to produce the same outcome for a single-writer test
    but is NOT the same guarantee. The PostgreSQL concurrency test is the real evidence here;
    the SQLite tests establish the lifecycle, not the mutual exclusion.

FAIL CLOSED. Opening a position is the risk-INCREASING action. If committed exposure cannot be
established — a missing mark, an unreadable row — the reservation is refused. A protective exit
is never routed through this module and so is never blocked by it.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from db.models import PaperPortfolio, PaperTrade, PortfolioExposureReservation

RESERVED = "reserved"
CONSUMED = "consumed"
RELEASED = "released"
EXPIRED = "expired"

#: Short by design. A reservation exists only for the moment between deciding to enter and the
#: position existing. A long TTL turns a crashed worker into a portfolio-wide entry freeze.
DEFAULT_TTL_SECONDS = 120


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _same_bucket(a: str | None, b: str | None) -> bool:
    """Sector grouping, matching the entry gate's own rule: NULL is a real bucket that all
    unclassified stocks share, and it never merges with a named sector."""
    return (a is None) == (b is None) and (a == b or (a is None and b is None))


def active_reserved_value(session, portfolio_id: int, sector: str | None,
                          now: datetime | None = None) -> float:
    """Exposure currently RESERVED for a sector and not yet consumed, released or expired.

    Expiry is applied as a READ-TIME filter as well as by the sweep, so an abandoned reservation
    stops blocking entries the moment it lapses rather than when a sweep next happens to run.
    """
    now = now or utcnow()
    rows = session.execute(
        select(PortfolioExposureReservation).where(
            PortfolioExposureReservation.portfolio_id == portfolio_id,
            PortfolioExposureReservation.state == RESERVED,
            PortfolioExposureReservation.expires_at > now)).scalars().all()
    return float(sum(r.value for r in rows if _same_bucket(r.sector, sector)))


def committed_value(session, portfolio_id: int, sector: str | None, *, price_for
                    ) -> tuple[float, int]:
    """Exposure held by OPEN POSITIONS in this sector, read fresh. Returns `(value, n_fallback)`.

    READ FRESH, INSIDE THE LOCK — this is the part the first version of this module got wrong.
    It took `committed_value` as an argument from the caller, who computed it from
    `prefetched_open`: a snapshot taken once before the candidate loop. That preserved exactly
    the staleness the reservation exists to remove, so a position opened by an EARLIER candidate
    in the same cycle was still invisible, and the defect reproduced unchanged.

    Sector comes from `PaperTrade.sector`, snapshotted at entry, rather than from the live
    `Stock.sector`. That is deliberate: it asks "what exposure did we take in this sector",
    which a later reclassification of the stock should not retroactively rewrite.

    `price_for(trade) -> float | None` keeps the VALUATION the caller's definition while the ROW
    SET is this module's. `None` means no mark could be established for that position.
    """
    rows = session.execute(
        select(PaperTrade).where(PaperTrade.portfolio_id == portfolio_id,
                                 PaperTrade.stage == "open")).scalars().all()
    total = 0.0
    fallbacks = 0
    for t in rows:
        if not _same_bucket(t.sector, sector):
            continue
        price, is_fallback = price_for(t)
        if price is None:
            return float("nan"), fallbacks        # unestablishable; caller must fail closed
        if is_fallback:
            fallbacks += 1
        total += float(price) * float(t.shares)
    return total, fallbacks


def reserve(session, *, portfolio_id: int, intent_id: str, symbol: str, sector: str | None,
            value: float, equity: float, cap_pct: float, price_for,
            require_fresh_marks: bool = False, ttl_seconds: int = DEFAULT_TTL_SECONDS,
            now: datetime | None = None) -> tuple[PortfolioExposureReservation | None, str]:
    """Read committed exposure, add active reservations, and claim `value` — in ONE atomic step.

    Returns `(reservation, reason)`. A refusal returns `(None, reason)` and writes nothing.

    The portfolio row lock is taken FIRST, before anything is read. Reading before locking would
    reintroduce the race this function exists to remove.

    `require_fresh_marks` is the switch for the separate stale-mark finding: when True, an entry
    is refused if any open position in the sector had to be valued by fallback, because the cap
    cannot then be established reliably. It defaults to False, which preserves today's
    behaviour — the mechanism is built and tested, the policy is not switched on here.
    """
    now = now or utcnow()
    if equity <= 0:
        return None, "exposure_unknown_equity_non_positive"
    if value < 0:
        return None, "exposure_negative_value"

    # Serialization point. Everything after this, for THIS portfolio, is ordered.
    session.execute(
        select(PaperPortfolio.id).where(PaperPortfolio.id == portfolio_id).with_for_update())

    committed, fallbacks = committed_value(session, portfolio_id, sector, price_for=price_for)
    if committed != committed:                      # NaN: a position could not be valued
        return None, "exposure_unknown_mark"
    if require_fresh_marks and fallbacks:
        return None, "exposure_stale_mark"

    reserved = active_reserved_value(session, portfolio_id, sector, now=now)
    projected = committed + reserved + value
    if projected / equity > cap_pct:
        return None, "sector_cap"

    row = PortfolioExposureReservation(
        portfolio_id=portfolio_id, intent_id=intent_id, symbol=symbol, sector=sector,
        value=value, state=RESERVED, created_at=now,
        expires_at=now + timedelta(seconds=ttl_seconds))
    savepoint = session.begin_nested()
    try:
        session.add(row)
        savepoint.commit()
    except IntegrityError:
        savepoint.rollback()
        existing = session.query(PortfolioExposureReservation).filter_by(
            intent_id=intent_id).one_or_none()
        if existing is None:
            raise
        return existing, "already_reserved"
    return row, "reserved"


def consume(session, reservation: PortfolioExposureReservation, *, trade_id: int,
            now: datetime | None = None) -> bool:
    """The entry opened. Hand the exposure over to the open position.

    Compare-and-set on `state == RESERVED`, so a reservation already expired by the sweep cannot
    be consumed — the caller must then treat the entry as uncertain rather than assume the cap
    was honoured. Once consumed the reservation stops counting, because the open trade now
    carries that exposure and counting both would double it.
    """
    now = now or utcnow()
    result = session.execute(
        update(PortfolioExposureReservation)
        .where(PortfolioExposureReservation.id == reservation.id,
               PortfolioExposureReservation.state == RESERVED)
        .values(state=CONSUMED, trade_id=trade_id, terminal_at=now,
                terminal_reason="entry opened")
        .execution_options(synchronize_session=False))
    return result.rowcount == 1


def release(session, reservation: PortfolioExposureReservation, *, reason: str,
            now: datetime | None = None) -> bool:
    """The entry did not happen. Return the exposure immediately.

    Releasing only ever frees exposure the caller itself reserved and never opened, which is why
    this is safe to call on any failure path — including one where the caller is unsure whether
    the entry succeeded, since `consume` would already have won the compare-and-set if it had.
    """
    now = now or utcnow()
    result = session.execute(
        update(PortfolioExposureReservation)
        .where(PortfolioExposureReservation.id == reservation.id,
               PortfolioExposureReservation.state == RESERVED)
        .values(state=RELEASED, terminal_at=now, terminal_reason=reason[:255])
        .execution_options(synchronize_session=False))
    return result.rowcount == 1


def expire_stale(session, *, now: datetime | None = None) -> int:
    """Reclaim reservations whose worker died. Returns how many.

    Kept as rows rather than deleted: a reservation that expired without being consumed or
    released is evidence that a worker crashed mid-entry, and that is exactly what
    reconciliation needs to look at.
    """
    now = now or utcnow()
    result = session.execute(
        update(PortfolioExposureReservation)
        .where(PortfolioExposureReservation.state == RESERVED,
               PortfolioExposureReservation.expires_at <= now)
        .values(state=EXPIRED, terminal_at=now,
                terminal_reason="reservation expired; worker did not consume or release it")
        .execution_options(synchronize_session=False))
    return int(result.rowcount or 0)


def reconcile(session, *, portfolio_id: int, open_trade_ids: set[int],
              now: datetime | None = None) -> dict:
    """What disagrees between reservations and positions.

    Three disagreements are worth separating, because they mean different things:

      `consumed_without_open_trade` — a reservation says a position was opened and no such open
          trade exists. Either it closed normally, or the entry was rolled back after the
          reservation was consumed. Only the second is a defect.
      `expired_unconsumed` — a worker died holding exposure. Nothing was double-counted, but the
          cap was conservative for the TTL, and a rising count means workers are dying.
      `active` — currently claimed. Expected to be near zero at rest; a persistent non-zero at
          rest means reservations are leaking.
    """
    now = now or utcnow()
    rows = session.query(PortfolioExposureReservation).filter_by(
        portfolio_id=portfolio_id).all()
    consumed_orphans = [r.intent_id for r in rows
                        if r.state == CONSUMED and (r.trade_id not in open_trade_ids)]
    return {
        "total": len(rows),
        "active": sum(1 for r in rows if r.state == RESERVED and r.expires_at > now),
        "consumed": sum(1 for r in rows if r.state == CONSUMED),
        "released": sum(1 for r in rows if r.state == RELEASED),
        "expired_unconsumed": sum(1 for r in rows if r.state == EXPIRED),
        "consumed_without_open_trade": consumed_orphans,
        "reconciles": not consumed_orphans,
    }
