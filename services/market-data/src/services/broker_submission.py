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
NOT_ATTEMPTED = "not_attempted"
PENDING = "pending"
SUBMITTING = "submitting"
SUBMITTED = "submitted"
REJECTED = "rejected"
UNKNOWN = "unknown"

#: Retryable: definitely not accepted, so a fresh attempt cannot duplicate anything.
RETRYABLE = (PENDING, NOT_ATTEMPTED, REJECTED)

#: Terminal for this process. `unknown` is terminal pending RECONCILIATION — it may still be
#: resolved later to a confirmed fill, rejection or cancellation. Terminal means "this process
#: will not act on it again", NOT "no further fact about it can ever be recorded".
TERMINAL = (SUBMITTED, UNKNOWN)

#: States that may still correspond to a REAL order at the broker, so they must keep their
#: reserved exposure and must never be reported as a confirmed broker fill.
MAY_EXIST_AT_BROKER = (SUBMITTING, SUBMITTED, UNKNOWN)

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


def new_client_order_id(portfolio_id: int, symbol: str) -> str:
    """A stable identity for ONE intent, minted before any broker contact.

    Without it an `unknown` outcome is unresolvable: the broker's order list cannot be matched
    back to this trade except by guessing from symbol and quantity, which is exactly how a
    reconciliation ends up confirming the wrong order. Where the broker supports a client order
    id this is the value to send; where it does not, it is still the local join key.
    """
    from uuid import uuid4
    # <= 20 CHARACTERS. E*Trade truncates `clientOrderId` at 20, and a truncated id is not an
    # id — two intents could collide after the cut, which is worse than having none. Keeping it
    # short here means the SAME value reaches every broker intact.
    return f"pt{portfolio_id}x{uuid4().hex[:14]}"[:20]


def mark_pending(trade: PaperTrade, *, path: str = "deferred") -> None:
    """Record the INTENT to submit, with its identity and its route.

    Called inside the entry transaction so all three commit with the trade — which is the whole
    point: the local record exists, with a stable identity, before the broker hears anything.

    `path` is persisted rather than re-read from the flag at dispatch time. A flag change or a
    restart between entry and submission must not be able to route the SAME intent through both
    the legacy inline path and this one.
    """
    trade.broker_submission_state = PENDING
    trade.broker_submission_path = path
    if not trade.broker_client_order_id:
        trade.broker_client_order_id = new_client_order_id(trade.portfolio_id, trade.symbol)


def identity_is_transmittable(broker) -> bool:
    """Can this adapter actually SEND the client order id?

    Storing an identity locally helps only if the broker receives it — otherwise an `unknown`
    is still unresolvable except by matching symbol, quantity and time. An adapter that cannot
    transmit it must be recorded as such rather than assumed capable.
    """
    return bool(getattr(broker, "supports_client_order_id", False))


def retains_reserved_exposure(trade: PaperTrade) -> bool:
    """Does this trade still hold exposure that may exist at the broker?

    True for `submitting`, `submitted` and `unknown`. Capacity must not be released for any of
    them, and none may be reported as a CONFIRMED broker fill — `broker_fill_confirmed` is the
    only thing that says that.
    """
    return trade.broker_submission_state in MAY_EXIST_AT_BROKER


def confirmed_broker_fill(trade: PaperTrade) -> bool:
    """Only an observed fill counts. An `unknown` submission is NOT a fill, and must never be
    folded into executed-trade statistics as though it were one."""
    return bool(trade.broker_fill_confirmed) and bool(trade.broker_order_id)


def claimable(session, *, portfolio_id: int | None = None, limit: int = 20) -> list[PaperTrade]:
    """Trades whose broker order has not been placed and may still be attempted.

    `submitting` and `unknown` are deliberately EXCLUDED. Either may correspond to a REAL order
    at the broker, and picking one up again would place a duplicate. Those go to
    `needs_reconciliation`, which requires external evidence to resolve.
    """
    stmt = select(PaperTrade).where(
        PaperTrade.broker_submission_state.in_(RETRYABLE),
        PaperTrade.broker_order_id.is_(None),
        PaperTrade.stage == "open",
        # ROUTE RESOLVED PER INTENT. Only intents this dispatcher owns are claimable, so an
        # intent created under the legacy path can never be picked up here after a flag change
        # or a restart — which would route one intent through both submission paths.
        PaperTrade.broker_submission_path == "deferred",
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
               PaperTrade.broker_submission_state.in_(RETRYABLE))
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
    """Record the result of a submission attempt.

    `outcome` is SUBMITTED, REJECTED or UNKNOWN. There is deliberately no generic "failed":
    the distinction between "definitely not accepted" and "might have been accepted" is the
    whole safety property, and a single bucket erases it.
    """
    if outcome not in (SUBMITTED, REJECTED, UNKNOWN):
        raise ValueError(
            f"unknown submission outcome {outcome!r}. Use REJECTED only with explicit evidence "
            f"of rejection; anything ambiguous is UNKNOWN.")
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


#: A broker lookup that returns "not found" is NOT evidence the order does not exist. It can
#: mean propagation delay, a wrong account scope, or a transient outage. Treating it as proof
#: of absence would authorise a resubmission for an order that is simply not visible YET.
NOT_FOUND_IS_NOT_ABSENCE = (
    "a `not found` lookup is not evidence of non-existence; it may be propagation delay, the "
    "wrong account scope, or a transient outage. Re-check before resolving.")


def reconcile_submission(session, trade: PaperTrade, *, resolution: str, evidence: str,
                         actor: str, order_id: str | None = None,
                         now: datetime | None = None) -> None:
    """Resolve an `unknown` or stuck `submitting` row against EXTERNAL evidence.

    TERMINAL DOES NOT MEAN UNRECORDABLE. "Never automatically resubmit" is the safety property;
    it must not prevent recording a fill, rejection or cancellation that is later confirmed by
    the broker's own record. This is the only path that may change such a row, and it demands
    an actor and evidence for the same reason the notification outbox does — a reconciliation
    without evidence is a guess wearing a decision's clothes.
    """
    if trade.broker_submission_state not in (UNKNOWN, SUBMITTING):
        raise ValueError(
            f"only `unknown` or `submitting` rows are reconciled, not "
            f"{trade.broker_submission_state!r}")
    if resolution not in (SUBMITTED, REJECTED):
        raise ValueError(f"resolution must be {SUBMITTED!r} or {REJECTED!r}")
    if not (evidence or "").strip():
        raise ValueError("reconciliation requires evidence from the broker's own record")
    if not (actor or "").strip():
        raise ValueError("reconciliation requires an accountable actor")
    # EVIDENCE APPROPRIATE TO THE VERDICT, not one rule for both.
    #
    # `submitted` asserts an order EXISTS, so it needs that order's identity — otherwise the
    # claim cannot be checked against the broker's record afterwards.
    #
    # `rejected` asserts an order does NOT exist, and a definitive pre-submission failure
    # legitimately has no order id. Demanding one would force a FABRICATED id to record a true
    # fact, which is worse than the gap it closes.
    if resolution == SUBMITTED and not (order_id or trade.broker_order_id):
        raise ValueError(
            "resolving to `submitted` requires the broker's order id — without it the claim "
            "cannot be checked against the broker's record later")
    now = now or utcnow()
    if order_id:
        trade.broker_order_id = order_id
    trade.broker_submission_state = resolution
    trade.broker_submitted_at = trade.broker_submitted_at or now
    trade.broker_error = f"reconciled as {resolution} by {actor}: {evidence}"[:512]


def submit_pending(session, *, place, commit, classify=None,
                   portfolio_id: int | None = None,
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
    out = {"claimed": 0, "submitted": 0, "rejected": 0, "unknown": 0, "lost_race": 0}
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
        except Exception as exc:                        # noqa: BLE001
            # ANY escaping exception is UNKNOWN unless the caller can prove otherwise. A
            # timeout, a parse failure, a connection reset — none of them establishes that the
            # broker did not accept the order.
            verdict = classify(trade, exc) if classify else None
            outcome = REJECTED if verdict == REJECTED else UNKNOWN
            settle(session, trade, outcome=outcome, error=str(exc), now=now)
            out["rejected" if outcome == REJECTED else "unknown"] += 1
            log.warning("broker.submission_%s" % outcome, trade_id=trade.id,
                        symbol=trade.symbol, error=str(exc))
            commit(); continue

        # A MISSING ORDER ID IS `unknown`, NOT A FAILURE. The historical
        # `_place_broker_entry` catches every exception from `place_order` and returns
        # normally, so from here a swallowed timeout AFTER acceptance and a clean rejection are
        # indistinguishable — both leave no order id. Calling that "failed" would license a
        # REPLACEMENT ORDER for one that may already exist. Only explicit evidence, supplied by
        # `classify`, may downgrade it to `rejected`.
        if trade.broker_order_id:
            settle(session, trade, outcome=SUBMITTED, now=now)
            out["submitted"] += 1
        else:
            verdict = classify(trade, None) if classify else None
            outcome = REJECTED if verdict == REJECTED else UNKNOWN
            settle(session, trade, outcome=outcome,
                   error=trade.broker_error or "no order id and no evidence of rejection",
                   now=now)
            out["rejected" if outcome == REJECTED else "unknown"] += 1
        commit()
    return out
