"""M25-BROKER-COMMIT-BOUNDARY — submit broker entries AFTER the trade is durable.

THE DEFECT. `_place_broker_entry` is called from inside `_open_paper_trade`, which contains no
`session.commit()`; the caller commits afterwards. So a real broker order was submitted from
within an uncommitted transaction. A crash or rollback between the broker accepting and that
commit left:

  * a REAL accepted order at the broker,
  * NO local trade row at all,
  * an exposure reservation reverting to `reserved` and expiring — capacity released for
    exposure that exists at the broker and that nothing locally knows about.

Pre-existing, and not caused by the exposure-reservation work; the reservation made the boundary
visible by asking what happens to capacity across that crash.

THE SHAPE OF THE FIX is the one the notification outbox already uses, deliberately reused rather
than reinvented: **record durable intent, commit, then dispatch, then reconcile anything whose
outcome is unknown.** The trade row is the intent. `pending` means it exists and nothing has been
sent; `submitting` is committed immediately before the provider call so a crash there is
detectable; `unknown` is the honest terminal state when the outcome cannot be established.

WHAT IS NOT PROMISED: that a broker order is never duplicated. A crash between the broker
accepting and this process recording it is locally indistinguishable from a crash before the
call. The design makes that window narrow, detectable, and auditable — it does not make it
impossible, and an `unknown` row is never retried automatically because a blind retry is exactly
how a duplicate real order gets placed.
"""
from __future__ import annotations

from datetime import datetime, timezone

from common.logging import get_logger
from db import PaperPortfolio, PaperTrade
from sqlalchemy import select, update

log = get_logger(__name__)

NOT_REQUIRED = None
PENDING = "pending"
SUBMITTING = "submitting"
SUBMITTED = "submitted"
UNKNOWN = "unknown"
FAILED = "failed"

#: Terminal for this process. `unknown` is terminal pending human/automated reconciliation.
TERMINAL = (SUBMITTED, UNKNOWN)

#: Admin flag. ABSENT = OFF = the historical inline behaviour, so deploying this module changes
#: nothing. Switching it moves WHERE a real order is placed relative to the commit, which is a
#: change to live execution ordering and belongs to a person, not to a deploy.
ROLLOUT_KEY = "stockai:admin:feature:broker_submit_after_commit"

MAX_SUBMIT_ATTEMPTS = 3


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def submit_after_commit_enabled(redis_client) -> bool:
    """Only an explicit "1" turns this on. Absent, unreadable or anything else reads as OFF —
    an unknown configuration is never an implicit opt-in to a different execution ordering."""
    if redis_client is None:
        return False
    try:
        return redis_client.get(ROLLOUT_KEY) == "1"
    except Exception:                                   # noqa: BLE001
        return False


def mark_pending(trade: PaperTrade) -> None:
    """Record the INTENT to submit. Called inside the entry transaction, so it commits with the
    trade — which is the whole point: the local record exists before the broker hears anything."""
    trade.broker_submission_state = PENDING


def claimable(session, *, portfolio_id: int | None = None, limit: int = 20) -> list[PaperTrade]:
    """Trades whose broker order has not been placed and may still be attempted.

    `submitting` is deliberately EXCLUDED. A row left there means a worker died mid-call and the
    order may exist; picking it up again would risk a duplicate. Those go to `needs_reconciliation`.
    """
    stmt = select(PaperTrade).where(
        PaperTrade.broker_submission_state.in_((PENDING, FAILED)),
        PaperTrade.broker_order_id.is_(None),
        PaperTrade.stage == "open",
        PaperTrade.broker_submit_attempts < MAX_SUBMIT_ATTEMPTS)
    if portfolio_id is not None:
        stmt = stmt.where(PaperTrade.portfolio_id == portfolio_id)
    return list(session.execute(stmt.limit(limit)).scalars().all())


def begin_submission(session, trade: PaperTrade, *, now: datetime | None = None) -> bool:
    """Compare-and-set into `submitting`. The caller MUST commit before contacting the broker.

    An uncommitted marker records nothing, which would leave the exact gap this module exists to
    close. Returns False if another worker already moved the row.
    """
    now = now or utcnow()
    result = session.execute(
        update(PaperTrade)
        .where(PaperTrade.id == trade.id,
               PaperTrade.broker_submission_state.in_((PENDING, FAILED)))
        .values(broker_submission_state=SUBMITTING,
                broker_submit_attempts=PaperTrade.broker_submit_attempts + 1,
                broker_submitted_at=now)
        .execution_options(synchronize_session=False))
    if result.rowcount == 1:
        session.refresh(trade)
        return True
    return False


def settle(session, trade: PaperTrade, *, outcome: str, error: str | None = None,
           now: datetime | None = None) -> None:
    """Record the result of a submission attempt. `outcome` is one of SUBMITTED/FAILED/UNKNOWN."""
    if outcome not in (SUBMITTED, FAILED, UNKNOWN):
        raise ValueError(f"unknown submission outcome {outcome!r}")
    trade.broker_submission_state = outcome
    if error:
        trade.broker_error = error[:512]
    if outcome == SUBMITTED:
        trade.broker_submitted_at = now or utcnow()


def needs_reconciliation(session, *, limit: int = 100) -> list[PaperTrade]:
    """Trades whose broker outcome is not known, oldest first.

    Two populations, and both belong here:
      `unknown`    — the call was made and the result could not be established.
      `submitting` — a worker died mid-call and never came back.

    Neither is retried automatically. Resolving one needs EXTERNAL evidence — the broker's own
    order history — because only the broker knows whether an order exists.
    """
    return list(session.execute(
        select(PaperTrade)
        .where(PaperTrade.broker_submission_state.in_((UNKNOWN, SUBMITTING)),
               PaperTrade.broker_order_id.is_(None))
        .order_by(PaperTrade.broker_submitted_at)
        .limit(limit)).scalars().all())


def submit_pending(session, *, place, commit, portfolio_id: int | None = None,
                   limit: int = 20, now: datetime | None = None) -> dict:
    """Submit every claimable entry. `place(session, trade, portfolio)` does the real call.

    THE ORDER IS THE DESIGN:
        1. the trade row is already committed (the caller did that),
        2. `begin_submission` + COMMIT — durable evidence a call is about to happen,
        3. the broker call,
        4. settle + commit.

    A crash at any point leaves a state that says what is known: `pending` (nothing sent),
    `submitting` (may exist — reconcile), `submitted` or `failed`.
    """
    now = now or utcnow()
    out = {"claimed": 0, "submitted": 0, "failed": 0, "unknown": 0, "lost_race": 0}
    rows = claimable(session, portfolio_id=portfolio_id, limit=limit)
    out["claimed"] = len(rows)
    for trade in rows:
        if not begin_submission(session, trade, now=now):
            out["lost_race"] += 1
            continue
        commit()                       # durable BEFORE the broker is contacted
        portfolio = session.get(PaperPortfolio, trade.portfolio_id)
        try:
            place(session, trade, portfolio)
        except TimeoutError as exc:
            # The broker may already have accepted. Neither success nor failure is known.
            settle(session, trade, outcome=UNKNOWN, error=str(exc) or "timeout", now=now)
            out["unknown"] += 1
            log.warning("broker.submission_unknown", trade_id=trade.id, symbol=trade.symbol,
                        error=str(exc))
            commit(); continue
        except Exception as exc:                        # noqa: BLE001
            settle(session, trade, outcome=FAILED, error=str(exc), now=now)
            out["failed"] += 1
            commit(); continue

        # `place` records broker_order_id on success. Its absence after a call that did not
        # raise is NOT a success — the historical implementation swallows errors internally and
        # falls back to the simulated entry, so a missing id means no order was placed.
        if trade.broker_order_id:
            settle(session, trade, outcome=SUBMITTED, now=now)
            out["submitted"] += 1
        else:
            settle(session, trade, outcome=FAILED,
                   error="broker call returned without an order id", now=now)
            out["failed"] += 1
        commit()
    return out
