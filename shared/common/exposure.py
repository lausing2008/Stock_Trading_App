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
    **On SQLite** there is no PostgreSQL-equivalent row lock — `with_for_update()` is accepted
    and ignored, and writers are serialised at the database level instead. That produces the
    same outcome for a single-writer test without being the same guarantee. The PostgreSQL
    tests are the real evidence for mutual exclusion; the SQLite tests establish the lifecycle.

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
#: The entry is being created RIGHT NOW: the trade row is about to be written, or has been
#: written and its broker outcome is not yet known. A reservation in this state must NEVER be
#: reclaimed by the expiry sweep — releasing capacity while an entry is mid-commit is how the
#: cap gets exceeded by the very mechanism meant to enforce it. Stale `committing` rows are
#: surfaced for reconciliation instead, never auto-released.
COMMITTING = "committing"
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
            PortfolioExposureReservation.state.in_((RESERVED, COMMITTING)))).scalars().all()
    # `committing` always counts, regardless of age: the capacity is in use by an entry that may
    # already exist. `reserved` counts only while live, so a lapsed one stops blocking at read
    # time rather than waiting for the sweep.
    return float(sum(r.value for r in rows
                     if _same_bucket(r.sector, sector)
                     and (r.state == COMMITTING or r.expires_at > now)))


def committed_value(session, portfolio_id: int, sector: str | None, *, price_for
                    ) -> tuple[float, list]:
    """Exposure held by OPEN POSITIONS in this sector, read fresh.

    Returns `(value, fallbacks)` where `fallbacks` is one record per position priced from a
    substitute source — symbol, provenance, value and shares — so the shadow measurement can
    say WHICH exposure was uncertain and by how much, not merely how many positions were.

    READ FRESH, INSIDE THE LOCK — this is the part the first version of this module got wrong.
    It took `committed_value` as an argument from the caller, who computed it from
    `prefetched_open`: a snapshot taken once before the candidate loop. That preserved exactly
    the staleness the reservation exists to remove, so a position opened by an EARLIER candidate
    in the same cycle was still invisible, and the defect reproduced unchanged.

    Sector comes from `PaperTrade.sector`, snapshotted at entry, rather than from the live
    `Stock.sector`. That is deliberate: it asks "what exposure did we take in this sector",
    which a later reclassification of the stock should not retroactively rewrite.

    `price_for(trade) -> (price, provenance)` keeps the VALUATION the caller's definition while
    the ROW SET is this module's. `provenance` is `None` for a live mark, or a short string
    naming the substitute source ("entry_price", "last_close", ...). A string is what makes a
    fallback distinguishable from a live mark AFTER the fact, which a boolean does not.

    TWO DIFFERENT KINDS OF NOT-KNOWING, and they must not be conflated:

      `price is None`  — the position is UNVALUABLE. No number can be produced, so the cap
                         cannot be computed at all and the entry fails closed.
      `is_fallback`    — a number WAS produced, from a stale or substitute source (today: the
                         entry price). It is a number, and it is not current exposure. It can
                         understate or overstate, and the caller is told so rather than being
                         left to assume the figure is live.

    NOTE, because it changes what "fails closed" currently buys: the production caller's
    `price_for` substitutes `entry_price` whenever a live mark is missing and therefore NEVER
    returns None. `exposure_unknown_mark` is consequently unreachable from the live entry path
    today — it guards a future caller that reports unvaluable positions honestly. The condition
    that actually occurs in production is the FALLBACK, and it is currently permitted.
    """
    rows = session.execute(
        select(PaperTrade).where(PaperTrade.portfolio_id == portfolio_id,
                                 PaperTrade.stage == "open")).scalars().all()
    total = 0.0
    fallbacks: list = []
    for t in rows:
        if not _same_bucket(t.sector, sector):
            continue
        price, provenance = price_for(t)
        if price is None:
            # UNVALUABLE: no number at all. Distinct from a fallback, which produces one.
            return float("nan"), fallbacks
        value = float(price) * float(t.shares)
        if provenance is not None:
            fallbacks.append({"symbol": t.symbol, "provenance": str(provenance),
                              "value": value, "shares": float(t.shares)})
        total += value
    return total, fallbacks


def reserve(session, *, portfolio_id: int, intent_id: str, symbol: str, sector: str | None,
            value: float, equity: float, cap_pct: float, price_for,
            require_fresh_marks: bool = False, ttl_seconds: int = DEFAULT_TTL_SECONDS,
            telemetry: dict | None = None,
            now: datetime | None = None) -> tuple[PortfolioExposureReservation | None, str]:
    """Read committed exposure, add active reservations, and claim `value` — in ONE atomic step.

    Returns `(reservation, reason)`. A refusal returns `(None, reason)` and writes nothing.

    The portfolio row lock is taken FIRST, before anything is read. Reading before locking would
    reintroduce the race this function exists to remove.

    `require_fresh_marks` is the switch for the separate stale-mark finding: when True, an entry
    is refused if any open position in the sector had to be valued by fallback, because current
    exposure cannot then be established. It defaults to False, which preserves today's
    behaviour — the mechanism is built and tested, the policy is not switched on here.

    `telemetry` is an optional out-parameter (the pattern the anti-chase counters already use).
    It records what a fresh-mark policy WOULD have done without changing what happens, so the
    decision to enable it can rest on a measured block rate rather than a guess.
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
    unvaluable = committed != committed             # NaN: a position could not be valued at all
    if telemetry is not None:
        telemetry["fallback_marks"] = len(fallbacks)
        telemetry["fallback_detail"] = fallbacks
        telemetry["fallback_value"] = sum(f["value"] for f in fallbacks)
        telemetry["unvaluable"] = unvaluable
        telemetry["would_block_on_fresh_marks"] = bool(fallbacks)
    if unvaluable:
        return None, "exposure_unknown_mark"
    if require_fresh_marks and fallbacks:
        return None, "exposure_stale_mark"

    reserved = active_reserved_value(session, portfolio_id, sector, now=now)
    projected = committed + reserved + value
    over_cap = projected / equity > cap_pct
    if telemetry is not None:
        telemetry["committed"] = committed
        telemetry["reserved"] = reserved
        telemetry["projected_pct"] = projected / equity
        telemetry["cap_pct"] = cap_pct
        telemetry["would_exceed_cap"] = over_cap
        # The question the policy decision actually turns on: would ENFORCING freshness have
        # changed this entry's outcome? Only if a fallback was used AND the entry was otherwise
        # going to be allowed. A fallback on an entry the cap already refuses changes nothing.
        telemetry["decision_would_change"] = bool(fallbacks) and not over_cap
    if over_cap:
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


def begin_commit(session, reservation: PortfolioExposureReservation,
                 now: datetime | None = None) -> bool:
    """Mark the reservation as mid-commit, immediately before the trade row is written.

    THE GAP THIS CLOSES. Between `reserve()` and `consume()` the worker does real work — sizing,
    slippage, constructing the trade, and then a broker submission whose outcome may be unknown.
    If that window outlasts the TTL, the expiry sweep would reclaim the capacity while the entry
    was still completing, and another candidate could take room that is about to be occupied.
    Worse, the broker outcome may be UNKNOWN, so nobody can say whether the position exists.

    `committing` is excluded from the sweep for exactly that reason. A worker that dies here
    leaves a row that is neither released nor consumed — which is the honest state, and the one
    reconciliation needs to see.
    """
    now = now or utcnow()
    result = session.execute(
        update(PortfolioExposureReservation)
        .where(PortfolioExposureReservation.id == reservation.id,
               PortfolioExposureReservation.state == RESERVED)
        .values(state=COMMITTING)
        .execution_options(synchronize_session=False))
    return result.rowcount == 1


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
               PortfolioExposureReservation.state.in_((RESERVED, COMMITTING)))
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
    # `state == RESERVED` only. A `committing` row is deliberately NOT swept: the entry may
    # already exist, or its broker outcome may be unknown, and reclaiming its capacity would let
    # another candidate take room that is about to be occupied.
    result = session.execute(
        update(PortfolioExposureReservation)
        .where(PortfolioExposureReservation.state == RESERVED,
               PortfolioExposureReservation.expires_at <= now)
        .values(state=EXPIRED, terminal_at=now,
                terminal_reason="reservation expired; worker did not consume or release it")
        .execution_options(synchronize_session=False))
    return int(result.rowcount or 0)


def stale_committing(session, *, older_than_seconds: int = 900,
                     now: datetime | None = None) -> list:
    """Reservations stuck mid-commit. For REVIEW — nothing here releases them.

    A row sitting in `committing` means a worker died between deciding to open a position and
    recording that it had. Either the trade exists (and the reservation should have been
    consumed) or it does not (and the capacity is being held by a ghost). Only evidence can say
    which, so this surfaces them rather than guessing — the same reasoning as the outbox's
    `unknown` outcomes.
    """
    now = now or utcnow()
    cutoff = now - timedelta(seconds=older_than_seconds)
    return (session.query(PortfolioExposureReservation)
            .filter(PortfolioExposureReservation.state == COMMITTING,
                    PortfolioExposureReservation.created_at <= cutoff)
            .order_by(PortfolioExposureReservation.created_at).all())


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
        "committing": sum(1 for r in rows if r.state == COMMITTING),
        "consumed": sum(1 for r in rows if r.state == CONSUMED),
        "released": sum(1 for r in rows if r.state == RELEASED),
        "expired_unconsumed": sum(1 for r in rows if r.state == EXPIRED),
        "consumed_without_open_trade": consumed_orphans,
        "reconciles": not consumed_orphans,
    }
