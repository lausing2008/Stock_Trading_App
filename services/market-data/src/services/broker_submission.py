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

#: ── POLICY CONSTANTS. These are DECISIONS, not bug corrections. ───────────────────────────
#: Both bound when a recorded intent may still become a REAL order, and both are prerequisites
#: the lifecycle review named for activating M25. They are tracked separately from the
#: correctness fixes in this module and await explicit sign-off; the values below are the
#: conservative end of the defensible range, not a tuned result.
#:
#: INTENT_MAX_AGE_SECONDS — the entry scan runs every 5 minutes, so an intent older than three
#: cycles was not produced by the conditions now in front of us. A market order carrying a
#: decision made hours ago is priced by the market at dispatch, not by anything anyone approved.
INTENT_MAX_AGE_SECONDS = 900

#: MAX_ENTRY_PRICE_DRIFT_PCT — the position was SIZED against `entry_price`; a fill far from it
#: is a different position than the one the risk checks passed. Expressed against the intent's
#: own entry price so it means the same thing for a $8 stock and an $800 one.
MAX_ENTRY_PRICE_DRIFT_PCT = 1.0


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _naive_utc(dt: datetime) -> datetime:
    """A tz-aware datetime converted to naive UTC; a naive one returned unchanged."""
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo is not None else dt


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


def _eligibility() -> list:
    """SF-01: THE ONE eligibility predicate, shared by discovery and the atomic claim.

    `claimable()` tested five conditions; `begin_submission()` re-tested only two — the row id
    and a retryable state. Everything else was established at SELECT time and never rechecked
    inside the compare-and-set, so any of it could go stale in the window between them.

    WITNESS (reproduced from this audit's own probe): select an open/pending intent, let
    another session commit its closure, then call the real `begin_submission()` on the stale
    object. It returns True, and the row it refreshes reads `stage='closed'` with
    `broker_submission_state='submitting'`. `submit_pending()` then proceeds from a successful
    claim straight to the provider callback with no further stage check — a closed position
    claimed for a real broker order.

    Returning the conditions as a list rather than duplicating them is the point: a predicate
    that exists twice is a predicate that will disagree with itself, which is exactly what
    happened here.
    """
    return [
        PaperTrade.broker_submission_state.in_(RETRYABLE),
        PaperTrade.broker_order_id.is_(None),
        PaperTrade.stage == "open",
        # ROUTE RESOLVED PER INTENT. Only intents this dispatcher owns are claimable, so an
        # intent created under the legacy path can never be picked up here after a flag change
        # or a restart — which would route one intent through both submission paths.
        PaperTrade.broker_submission_path == "deferred",
        PaperTrade.broker_submit_attempts < MAX_SUBMIT_ATTEMPTS,
        # THE PORTFOLIO MUST STILL BE BROKER-LINKED. Measured gap (scenario R8): an intent is
        # recorded while a broker is connected; the connection is then removed; the dispatcher
        # submitted anyway, because eligibility asked only about the TRADE. A real order placed
        # into an account the user has since disconnected is the clearest possible case of
        # acting on authority that was withdrawn. A correlated subquery rather than a join, so
        # the same predicate still drops into the compare-and-set UPDATE unchanged.
        PaperTrade.portfolio_id.in_(
            select(PaperPortfolio.id).where(PaperPortfolio.broker_connection_id.is_not(None))),
    ]


def claimable(session, *, portfolio_id: int | None = None, limit: int = 20) -> list[PaperTrade]:
    """Trades whose broker order has not been placed and may still be attempted.

    `submitting` and `unknown` are deliberately EXCLUDED. Either may correspond to a REAL order
    at the broker, and picking one up again would place a duplicate. Those go to
    `needs_reconciliation`, which requires external evidence to resolve.
    """
    stmt = select(PaperTrade).where(*_eligibility())
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
        # SF-01: the FULL predicate, re-evaluated inside the same statement that writes the
        # claim. Checking only id+state meant every other condition was as old as the SELECT
        # that found this row — and a position closed in that window was still claimable.
        .where(PaperTrade.id == trade.id, *_eligibility())
        .values(broker_submission_state=SUBMITTING,
                broker_submit_attempts=PaperTrade.broker_submit_attempts + 1,
                broker_submitted_at=now)
        .execution_options(synchronize_session=False))
    if result.rowcount == 1:
        session.refresh(trade)
        return True
    return False


def intent_age_seconds(trade: PaperTrade, now: datetime) -> float | None:
    """How long ago this intent was recorded, or None if that cannot be established.

    `entry_time` IS the intent time: `mark_pending` runs inside the entry transaction, so the
    trade row and its intent become durable together. Reading it here rather than adding a
    second timestamp keeps one fact in one column — two timestamps that should always agree are
    two timestamps that will eventually disagree.
    """
    if trade.entry_time is None:
        return None
    # MIXED AWARENESS IS REAL HERE, not hypothetical: the column is naive `DateTime`, but
    # `conditional_orders` builds its timestamps with `datetime.now(timezone.utc)` and an
    # in-memory trade can therefore carry tzinfo before it has ever round-tripped through the
    # database. Subtracting those two raises TypeError — which, in a control that BLOCKS real
    # orders, would surface as a crash in the dispatch loop rather than as a refusal. Both sides
    # are normalised to naive UTC instead.
    return (_naive_utc(now) - _naive_utc(trade.entry_time)).total_seconds()


def price_drift_pct(trade: PaperTrade, quoted: float | None) -> float | None:
    """Signed drift of the current quote from the price this position was SIZED against.

    None means NOT MEASURED — no quote, or no entry price to compare against. It does not mean
    zero drift, and callers must not treat it as such.
    """
    if quoted is None or not trade.entry_price:
        return None
    entry = float(trade.entry_price)
    if entry == 0:
        return None
    return (float(quoted) - entry) / entry * 100.0


def dispatch_block_reason(trade: PaperTrade, *, now: datetime, quoted: float | None,
                          max_age_seconds: float = INTENT_MAX_AGE_SECONDS,
                          max_drift_pct: float = MAX_ENTRY_PRICE_DRIFT_PCT) -> str | None:
    """Why this intent must NOT be sent to the broker right now, or None to proceed.

    FAILS CLOSED, deliberately, and for the reason AUD-B01-PREFLIGHTFAILCLOSED already settled
    on the buying-power check: a control that cannot be evaluated has not passed. An unmeasurable
    age or an unavailable quote blocks the order rather than waving it through — nothing is lost
    by waiting for the next cycle, whereas a real order placed on an unverified price cannot be
    taken back.
    """
    age = intent_age_seconds(trade, now)
    if age is None:
        return "intent_age_unverifiable: no entry_time to date this intent from"
    if age > max_age_seconds:
        return (f"intent_expired: recorded {age / 60:.1f} min ago, limit "
                f"{max_age_seconds / 60:.0f} min")
    drift = price_drift_pct(trade, quoted)
    if drift is None:
        return "price_unverifiable: no current quote to check the sized entry price against"
    if abs(drift) > max_drift_pct:
        return (f"price_drift: {drift:+.2f}% from the sized entry price "
                f"{float(trade.entry_price):.4f}, limit {max_drift_pct:.2f}%")
    return None


def expired_intents(session, *, now: datetime | None = None,
                    max_age_seconds: float = INTENT_MAX_AGE_SECONDS,
                    limit: int = 100) -> list[PaperTrade]:
    """Recorded intents too old to dispatch, which therefore sit `pending` indefinitely.

    They are deliberately NOT auto-settled. `rejected` would assert the broker refused them,
    which is false — nothing was ever sent — and there is no state meaning "we declined to
    send this", so inventing one by overloading an existing value would corrupt the very
    distinction this module exists to preserve. They are listed instead, so that a person can
    see intents the dispatcher is declining rather than discovering a silent backlog.
    """
    now = now or utcnow()
    rows = session.execute(select(PaperTrade).where(*_eligibility()).limit(limit)).scalars().all()
    return [t for t in rows
            if (intent_age_seconds(t, now) or 0) > max_age_seconds]


def closure_disposition(trade: PaperTrade) -> dict | None:
    """What a CLOSURE must account for on this trade, or None when the broker is not involved.

    THE GAP THIS CLOSES (measured on real PostgreSQL, scenario R4): when the dispatcher wins the
    race, the row commits to `submitting` and the provider call is in flight; a closure landing
    immediately after set `stage='closed'` and recorded NOTHING about it. The position then reads
    as flat locally while a real order may be live at the broker — and the closure, which is the
    last thing to touch the row, left no trace that anyone needed to look.

    Returning a description rather than acting is deliberate: the disposition is the same
    wherever a trade closes, but the scheduled exit, the manual exit, the liquidation and the
    conditional-order path each record it in their own idiom.
    """
    if not retains_reserved_exposure(trade):
        return None
    return {
        "state": trade.broker_submission_state,
        "client_order_id": trade.broker_client_order_id,
        "order_id": trade.broker_order_id,
        "confirmed_fill": confirmed_broker_fill(trade),
        "needs_reconciliation": trade.broker_order_id is None,
        "note": ("closed locally while a real broker order may exist; exposure stays reserved "
                 "and this trade needs reconciliation against the broker's own record"),
    }


def record_closure_disposition(trade: PaperTrade, *, actor: str,
                               now: datetime | None = None) -> dict | None:
    """Stamp the disposition onto the trade so the closure is not silent. No-op when None.

    Written to `broker_error` — the existing free-text broker field — rather than a new column:
    this must be recordable on every close path today, and a disposition nobody can read until a
    migration lands is a disposition that does not exist.
    """
    disposition = closure_disposition(trade)
    if disposition is None:
        return None
    stamp = (now or utcnow()).isoformat(timespec="seconds")
    trade.broker_error = (
        f"closed_with_open_broker_intent at {stamp} by {actor}: "
        f"state={disposition['state']} client_order_id={disposition['client_order_id']} "
        f"{disposition['note']}")[:512]
    log.warning("broker.closed_with_open_intent", trade_id=trade.id, symbol=trade.symbol,
                actor=actor, **{k: v for k, v in disposition.items() if k != "note"})
    return disposition


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


def submit_pending(session, *, place, commit, quote, classify=None,
                   portfolio_id: int | None = None,
                   limit: int = 20, now: datetime | None = None) -> dict:
    """Submit every claimable entry. `place(session, trade, portfolio)` does the real call.

    THE ORDER IS THE DESIGN:
        1. the trade row is already committed (the caller did that),
        2. the dispatch controls — age and price drift — checked BEFORE the claim, so a blocked
           intent does not burn one of its three attempts,
        3. `begin_submission` + COMMIT — durable evidence a call is about to happen,
        4. the broker call,
        5. settle + commit.

    `quote(trade) -> float | None` is REQUIRED, not optional with a default. A caller with no
    quote source must say so by passing one that returns None, which blocks every dispatch and
    records why. The alternative — defaulting it — means forgetting the price check looks
    exactly like having no prices, and one of those two should be a loud error.

    A crash at any point leaves a state that says what is known: `pending` (nothing sent),
    `submitting` (may exist — reconcile), `submitted` or `failed`.
    """
    now = now or utcnow()
    out = {"claimed": 0, "submitted": 0, "rejected": 0, "unknown": 0, "lost_race": 0,
           "blocked": 0}
    rows = claimable(session, portfolio_id=portfolio_id, limit=limit)
    out["claimed"] = len(rows)
    for trade in rows:
        # BEFORE the claim: a control that blocks must not consume an attempt, or three blocked
        # cycles would exhaust the intent without the broker ever being contacted.
        blocked = dispatch_block_reason(trade, now=now, quoted=quote(trade))
        if blocked:
            out["blocked"] += 1
            trade.broker_error = f"dispatch blocked: {blocked}"[:512]
            commit()
            log.info("broker.dispatch_blocked", trade_id=trade.id, symbol=trade.symbol,
                     reason=blocked)
            continue
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
