"""M20 — the transactional outbox: enqueue, lease, settle.

THE CONTRACT. A notification is persisted in the SAME transaction as the decision that caused
it, before any provider call is attempted. The send then happens later, from a worker that
claims rows under a lease. That ordering is what makes delivery survivable: a crash between
"decided" and "sent" leaves a durable row to retry, not a silently lost alert.

WHAT THIS IS NOT. It is not a queue abstraction, a scheduler, or a provider client. It is the
state machine and the claim protocol, as pure session-level functions, so the same code is
exercised by a real database in tests and in production rather than mocked at one level and
trusted at another.

TIMESTAMPS ARE NAIVE UTC INSTANTS, matching the rest of this schema. They are instants, never
dates: calling `.date()` on one re-creates `docs/incidents/utc-vs-et-date-boundary.md`, where a
UTC instant reads one day ahead for 4-5 hours every evening. Session/calendar logic belongs
upstream, in the code that sets `expires_at`.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import Enum

from sqlalchemy import and_, or_, update
from sqlalchemy.exc import IntegrityError

from db.models import NotificationOutbox

#: Terminal states. Nothing leaves these without human or reconciliation action.
TERMINAL = ("accepted", "unknown", "dead_letter", "expired", "suppressed")

PENDING = "pending"
LEASED = "leased"
ACCEPTED = "accepted"
UNKNOWN = "unknown"
DEAD_LETTER = "dead_letter"
EXPIRED = "expired"
SUPPRESSED = "suppressed"

#: WHAT THIS SYSTEM DOES NOT PROMISE: exactly-once email delivery.
#:
#: Without provider-supported idempotency keys or after-the-fact reconciliation against the
#: provider's own record, a crash in the window between the provider accepting a message and
#: this process committing that fact is UNRECOVERABLE as a certainty. The row says `leased`;
#: the message may or may not have been sent. Retrying risks a duplicate; not retrying risks a
#: loss. There is no local state that distinguishes them.
#:
#: The design therefore aims at at-least-once delivery with a bounded, AUDITABLE duplicate risk,
#: and marks the ambiguous cases `unknown` instead of guessing. Anyone reporting a delivery rate
#: from this table must report `unknown` alongside it, never fold it into either side.
EXACTLY_ONCE = False

DEFAULT_LEASE_SECONDS = 120
DEFAULT_MAX_ATTEMPTS = 5


def utcnow() -> datetime:
    """Naive UTC, matching the schema. Centralised so a timezone change is one edit."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def backoff_seconds(attempt: int, base: int = 30, cap: int = 3600) -> int:
    """Exponential backoff, capped.

    Capped rather than unbounded because an alert that becomes deliverable after eight hours is
    usually one that should have expired instead — `expires_at` is the right control for "too
    late to matter", and backoff should not quietly become one.
    """
    if attempt < 1:
        attempt = 1
    return min(cap, base * (2 ** (attempt - 1)))


def enqueue(session, *, event_id: str, recipient: str, subject: str, body_html: str,
            body_text: str, alert_type: str | None = None, user_id: int | None = None,
            channel: str = "email", expires_at: datetime | None = None,
            max_attempts: int = DEFAULT_MAX_ATTEMPTS, available_at: datetime | None = None,
            reconstructed: bool = False) -> tuple[NotificationOutbox, bool]:
    """Persist one notification. Returns `(row, created)`.

    IDEMPOTENT BY DATABASE CONSTRAINT, not by a prior read. The check-then-insert version of
    this function has a race between the check and the insert that two scheduler workers hit
    routinely; here the UNIQUE constraint on `event_id` decides, and the loser reads back the
    winner's row. `created=False` is a normal outcome and means "already enqueued", never an
    error.

    The caller must commit. This deliberately does not, so the notification lands in the SAME
    transaction as the decision that produced it — the entire point of an outbox.
    """
    if not (event_id or "").strip():
        raise ValueError("event_id is required: it is the idempotency key.")
    if not (recipient or "").strip():
        raise ValueError("recipient is required.")

    now = utcnow()
    row = NotificationOutbox(
        event_id=event_id, recipient=recipient, subject=subject, body_html=body_html,
        body_text=body_text, alert_type=alert_type, user_id=user_id, channel=channel,
        state=PENDING, attempts=0, max_attempts=max_attempts,
        available_at=available_at or now, expires_at=expires_at, created_at=now,
        reconstructed=reconstructed,
    )
    savepoint = session.begin_nested()
    try:
        session.add(row)
        savepoint.commit()
        return row, True
    except IntegrityError:
        savepoint.rollback()
        # NOT every IntegrityError is a duplicate event_id. A foreign-key violation on `user_id`
        # or a future constraint would raise the same class, and reporting that as "already
        # enqueued" would silently drop a notification that was never stored. Confirm the row
        # actually exists; if it does not, the original error stands and the caller must see it.
        existing = session.query(NotificationOutbox).filter(
            NotificationOutbox.event_id == event_id).one_or_none()
        if existing is None:
            raise
        return existing, False


def claim(session, *, owner: str, limit: int = 20,
          lease_seconds: int = DEFAULT_LEASE_SECONDS,
          now: datetime | None = None) -> list[NotificationOutbox]:
    """Lease up to `limit` due notifications for `owner`.

    COMPARE-AND-SET, VERIFIED BY ROWCOUNT. Each row is claimed with an UPDATE whose WHERE clause
    re-states every precondition; a rowcount of 0 means another worker won the race and this
    worker simply moves on. Expressed this way rather than with `FOR UPDATE SKIP LOCKED` so the
    identical code path runs on the SQLite used by the real-database tests and the PostgreSQL
    used in production — a claim protocol that is only exercised through a mock is not a claim
    protocol that has been tested.

    A row is claimable when it is `pending`, or `leased` with an EXPIRED lease. The second case
    is the recovery path: a worker that dies mid-send holds its lease only until it lapses.

    ATTEMPTS INCREMENT ON CLAIM, not on send. A message that crashes its worker before the send
    completes still burns an attempt, so a poison payload cannot spin forever through a fleet of
    restarting workers. The cost is that a worker killed between claim and send consumes one of
    five attempts without having tried — accepted deliberately, because unbounded retry of a
    crashing message is the worse failure.
    """
    now = now or utcnow()
    claimable = (
        NotificationOutbox.available_at <= now,
        or_(NotificationOutbox.state == PENDING,
            # A lapsed lease is reclaimable ONLY if no dispatch was in flight. If
            # `dispatch_started_at` is set, the worker reached the provider and never returned,
            # so the send outcome is unknown and resending would risk a duplicate. Those rows
            # belong to `quarantine_uncertain()`, not to the claim path — excluded here so the
            # order the two run in cannot change the outcome.
            and_(NotificationOutbox.state == LEASED,
                 NotificationOutbox.dispatch_started_at.is_(None),
                 NotificationOutbox.lease_expires_at.isnot(None),
                 NotificationOutbox.lease_expires_at <= now)),
    )
    candidates = (session.query(NotificationOutbox)
                  .filter(*claimable)
                  .order_by(NotificationOutbox.available_at, NotificationOutbox.id)
                  .limit(limit).all())

    claimed: list[NotificationOutbox] = []
    lease_until = now + timedelta(seconds=lease_seconds)
    for row in candidates:
        # Expiry is re-checked HERE as well as at send time. A row that went stale while sitting
        # in the queue must not be leased at all: leasing it would burn an attempt on something
        # that can never legitimately be sent.
        if row.expires_at is not None and row.expires_at <= now:
            continue
        result = session.execute(
            update(NotificationOutbox)
            .where(NotificationOutbox.id == row.id,
                   NotificationOutbox.state == row.state,
                   NotificationOutbox.attempts == row.attempts)
            .values(state=LEASED, lease_owner=owner, lease_expires_at=lease_until,
                    attempts=NotificationOutbox.attempts + 1, last_attempt_at=now)
            .execution_options(synchronize_session=False))
        if result.rowcount == 1:
            session.refresh(row)
            claimed.append(row)
    return claimed


def _settle(session, row: NotificationOutbox, owner: str, values: dict,
            now: datetime) -> bool:
    """Apply a terminal outcome ONLY IF this worker still holds the lease.

    WHY OWNERSHIP IS CHECKED AT SETTLE AND NOT ONLY AT CLAIM. A worker can be paused — GC, a
    slow provider call, an overloaded host — for longer than its lease. Meanwhile another worker
    legitimately reclaims the row and sends it. If the first worker then wrote its outcome
    unconditionally, it would overwrite the second worker's record with a verdict about a send
    that is no longer the one that happened: an expired worker acknowledging someone else's
    work.

    The UPDATE re-states ownership in its WHERE clause, so a lapsed worker's write affects zero
    rows and is reported back as False. A caller receiving False must NOT assume its outcome was
    recorded — its send may still have happened, which is exactly the `unknown` case.
    """
    result = session.execute(
        update(NotificationOutbox)
        .where(NotificationOutbox.id == row.id,
               NotificationOutbox.state == LEASED,
               NotificationOutbox.lease_owner == owner,
               NotificationOutbox.lease_expires_at > now)
        .values(**values)
        .execution_options(synchronize_session=False))
    won = result.rowcount == 1
    if won:
        session.refresh(row)
    return won


def mark_accepted(session, row: NotificationOutbox, *, owner: str,
                  now: datetime | None = None, provider_message_id: str | None = None,
                  detail: str | None = None) -> bool:
    """The PROVIDER accepted the message. Returns whether this worker still owned the lease.

    Terminal for this process, and explicitly NOT proof of arrival — see `delivery_status`,
    which only a provider callback may set.
    """
    now = now or utcnow()
    return _settle(session, row, owner, dict(
        state=ACCEPTED, accepted_at=now, terminal_at=now,
        terminal_reason=detail or "provider accepted", provider_message_id=provider_message_id,
        lease_owner=None, lease_expires_at=None, last_error=None), now)


def mark_unknown(session, row: NotificationOutbox, *, owner: str, detail: str,
                 now: datetime | None = None) -> bool:
    """The send outcome is AMBIGUOUS — e.g. a timeout after the provider may have accepted.

    Deliberately NOT a failure and NOT a success. Marking it failed would license a retry that
    may duplicate a message the recipient already has; marking it accepted would silently drop
    one that never went. It stays `unknown` until reconciled against the provider's own record,
    and it is counted separately in every delivery figure.
    """
    now = now or utcnow()
    return _settle(session, row, owner, dict(
        state=UNKNOWN, terminal_at=now,
        terminal_reason=f"ambiguous send outcome: {detail}"[:255],
        last_error=detail[:512], lease_owner=None, lease_expires_at=None), now)


def record_delivery(session, row: NotificationOutbox, *, status: str,
                    now: datetime | None = None) -> None:
    """Record an OBSERVED delivery outcome from a provider callback.

    The only legitimate writer of `delivery_status`. Acceptance never implies delivery, so
    nothing in the send path may call this — absence of a status means UNOBSERVED, which is the
    normal state for almost every row and must never be rendered as "delivered".
    """
    if status not in ("delivered", "bounced", "complaint", "deferred"):
        raise ValueError(f"unknown delivery status {status!r}")
    row.delivery_status = status
    row.delivery_observed_at = now or utcnow()


def mark_failed(session, row: NotificationOutbox, *, owner: str, error: str,
                now: datetime | None = None) -> str | None:
    """Record a DEFINITE failure — the provider rejected it, or nothing was sent.

    Returns the resulting state, or None if this worker no longer owned the lease.

    Only for outcomes known not to have been accepted. A timeout is NOT one of those: use
    `mark_unknown`. Dead-letters once attempts reach `max_attempts`; the count already includes
    this attempt because `claim()` incremented it. A dead-lettered row is KEPT, never deleted —
    the evidence that an alert could not be delivered is exactly what the Redis-TTL scheme
    destroyed.
    """
    now = now or utcnow()
    err = (error or "")[:512]
    if row.attempts >= row.max_attempts:
        values = dict(state=DEAD_LETTER, terminal_at=now, last_error=err,
                      terminal_reason=f"attempts exhausted ({row.attempts}/{row.max_attempts})",
                      lease_owner=None, lease_expires_at=None)
        outcome = DEAD_LETTER
    else:
        values = dict(state=PENDING, last_error=err, lease_owner=None, lease_expires_at=None,
                      available_at=now + timedelta(seconds=backoff_seconds(row.attempts)))
        outcome = PENDING
    return outcome if _settle(session, row, owner, values, now) else None


def expire_stale(session, *, now: datetime | None = None) -> int:
    """Move past-deadline non-terminal rows to `expired`. Returns how many.

    A stale alert is withheld deliberately, and the row says so. This is the distinction the
    recovery-manifest work turned on: a SELECT signal from four days ago is not news, and
    sending it late is worse than not sending it — but silently dropping it leaves no trace that
    anything was withheld.
    """
    now = now or utcnow()
    result = session.execute(
        update(NotificationOutbox)
        .where(NotificationOutbox.state.in_((PENDING, LEASED)),
               NotificationOutbox.expires_at.isnot(None),
               NotificationOutbox.expires_at <= now)
        .values(state=EXPIRED, terminal_at=now, lease_owner=None, lease_expires_at=None,
                terminal_reason="expired before delivery; withheld as stale")
        .execution_options(synchronize_session=False))
    return int(result.rowcount or 0)


def mark_expired(session, row: NotificationOutbox, *, owner: str, reason: str,
                 now: datetime | None = None) -> bool:
    """Terminal-expire a LEASED row whose deadline passed while it was held."""
    now = now or utcnow()
    return _settle(session, row, owner, dict(
        state=EXPIRED, terminal_at=now, terminal_reason=reason[:255],
        lease_owner=None, lease_expires_at=None, dispatch_started_at=None), now)


def suppress(session, row: NotificationOutbox, *, reason: str,
             now: datetime | None = None) -> None:
    """Withhold deliberately — an opt-out, a disabled alert type, a paused experiment.

    Separate from `expired` (too late) and `dead_letter` (could not send). "We chose not to"
    is a third fact and conflating it with either of the others misreports the delivery rate.
    """
    now = now or utcnow()
    row.state = SUPPRESSED
    row.terminal_at = now
    row.terminal_reason = reason
    row.lease_owner = None
    row.lease_expires_at = None


def release(session, row: NotificationOutbox, *, owner: str,
            now: datetime | None = None) -> bool:
    """Give a lease back without consuming the outcome — a clean shutdown mid-batch.

    The attempt already consumed by `claim()` is NOT refunded. Pretending the attempt never
    happened would let a message that reliably kills its worker at claim time retry forever.
    """
    now = now or utcnow()
    return _settle(session, row, owner, dict(
        state=PENDING, lease_owner=None, lease_expires_at=None, available_at=now), now)


class SendGate(str, Enum):
    """What the pre-send recheck decided."""
    SEND = "send"
    SUPPRESS = "suppress"   # terminal: the recipient opted out
    EXPIRE = "expire"       # terminal: too late to be worth sending
    DEFER = "defer"         # TRANSIENT: we could not establish consent — try again later


def may_send(row: NotificationOutbox, *, now: datetime | None = None,
             is_subscribed=None) -> tuple[SendGate, str]:
    """Re-check, IMMEDIATELY BEFORE the provider call, that this should still go out.

    Conditions change between enqueue and send — that gap is the whole point of an outbox, and
    it is also long enough for an alert to go stale or a recipient to unsubscribe. Checking only
    at enqueue would deliver alerts that were valid when queued and are not when sent.

    AN UNREADABLE PREFERENCE DEFERS. It does not send.

    This repo's established alert-preference rule is fail-open: absence of a preference means
    subscribed, and a lookup error must not silently suppress every alert. That rule was written
    when there was no durable queue, so the only alternatives were "send anyway" or "lose the
    alert" — and sending was the lesser harm. **The outbox removes that dilemma.** A deferred
    notification is not lost; it is retried when the preference source returns. Sending on an
    unreadable preference, by contrast, is an unenforced opt-out — it delivers mail to someone
    who may have asked not to receive it, and no amount of logging undoes that.

    So the fail-open rule still holds where a notification would otherwise be dropped, and does
    not hold here. Deferral is counted separately from send attempts (see `defers`) so that an
    outage of the preference source cannot dead-letter the backlog.
    """
    now = now or utcnow()
    if row.state != LEASED:
        return SendGate.DEFER, f"not leased (state={row.state})"
    if row.expires_at is not None and row.expires_at <= now:
        return SendGate.EXPIRE, "expired before send"
    if is_subscribed is None:
        # Not "assume consent". The caller did not supply a preference source, so consent is
        # unestablished, and unestablished consent is not consent.
        return SendGate.DEFER, "no preference source supplied; consent unestablished"
    try:
        subscribed = is_subscribed(row)
    except Exception as exc:                      # noqa: BLE001
        return SendGate.DEFER, f"preference lookup failed, deferring: {exc}"
    if subscribed is None:
        return SendGate.DEFER, "preference lookup returned unknown"
    return (SendGate.SEND, "subscribed") if subscribed else \
        (SendGate.SUPPRESS, "recipient opted out")


def defer(session, row: NotificationOutbox, *, owner: str, reason: str,
          now: datetime | None = None) -> str | None:
    """Put a notification back WITHOUT consuming a send attempt.

    Refunds the attempt that `claim()` consumed, because nothing was sent — the alternative
    would let an hour of preference-source downtime exhaust `max_attempts` and dead-letter a
    backlog of perfectly deliverable alerts. Deferrals have their own cap so a permanently
    broken preference source cannot defer forever in silence; exceeding it dead-letters with a
    reason that names the real cause.
    """
    now = now or utcnow()
    if row.defers + 1 >= row.max_defers:
        values = dict(state=DEAD_LETTER, terminal_at=now, defers=NotificationOutbox.defers + 1,
                      attempts=NotificationOutbox.attempts - 1,
                      lease_owner=None, lease_expires_at=None, dispatch_started_at=None,
                      last_error=reason[:512],
                      terminal_reason=f"deferred {row.defers + 1} times without establishing "
                                      f"consent: {reason}"[:255])
        outcome = DEAD_LETTER
    else:
        values = dict(state=PENDING, defers=NotificationOutbox.defers + 1,
                      attempts=NotificationOutbox.attempts - 1,
                      lease_owner=None, lease_expires_at=None, dispatch_started_at=None,
                      last_error=reason[:512],
                      available_at=now + timedelta(seconds=backoff_seconds(row.defers + 1)))
        outcome = PENDING
    return outcome if _settle(session, row, owner, values, now) else None


def begin_dispatch(session, row: NotificationOutbox, *, owner: str,
                   now: datetime | None = None) -> bool:
    """Record that a provider call is ABOUT to be made, and commit it.

    THE ACCEPTANCE/CRASH GAP. If the process dies after the provider accepts a message but
    before that fact is committed, nothing local distinguishes it from a crash that happened
    before the call was made. Without this marker a reclaiming worker sees only "leased, lease
    lapsed" and would resend — duplicating a message the recipient already has.

    With it, the reclaiming worker can tell the two apart: a dispatch timestamp from a previous
    attempt means the send was IN FLIGHT and its outcome is genuinely unknown. The caller must
    commit after this returns, before touching the provider — an uncommitted marker records
    nothing.
    """
    now = now or utcnow()
    return _settle(session, row, owner,
                   dict(dispatch_started_at=now,
                        lease_expires_at=row.lease_expires_at), now)


def quarantine_uncertain(session, *, now: datetime | None = None) -> int:
    """Move in-flight rows whose worker died into `unknown`, instead of resending them.

    A row is uncertain when its lease has lapsed while `dispatch_started_at` is set: the worker
    reached the provider and never came back. Returns how many were quarantined. These land in
    the review queue; nothing retries them automatically.
    """
    now = now or utcnow()
    result = session.execute(
        update(NotificationOutbox)
        .where(NotificationOutbox.state == LEASED,
               NotificationOutbox.dispatch_started_at.isnot(None),
               NotificationOutbox.lease_expires_at.isnot(None),
               NotificationOutbox.lease_expires_at <= now)
        .values(state=UNKNOWN, terminal_at=now, lease_owner=None, lease_expires_at=None,
                terminal_reason="worker died in flight; provider may have accepted")
        .execution_options(synchronize_session=False))
    return int(result.rowcount or 0)


# ── Reconciliation and the review queue ───────────────────────────────────────────────────────

def needs_review(session, *, limit: int = 100, now: datetime | None = None) -> list:
    """Unresolved ambiguous outcomes, oldest first.

    WHY THIS EXISTS. Never auto-retrying an `unknown` avoids duplicates, but on its own it
    strands the notification forever: nobody is told, and the recipient simply never hears. The
    absence of a retry is only defensible if something surfaces these for a decision.
    """
    return (session.query(NotificationOutbox)
            .filter(NotificationOutbox.state == UNKNOWN,
                    NotificationOutbox.reconciled_at.is_(None))
            .order_by(NotificationOutbox.terminal_at)
            .limit(limit).all())


def reconcile_unknown(session, row: NotificationOutbox, *, resolution: str, evidence: str,
                      actor: str, now: datetime | None = None) -> None:
    """Resolve one ambiguous outcome against EXTERNAL evidence — a provider log, a bounce
    record, a recipient confirming receipt.

    `resolution` is `accepted` (it did go out) or `dead_letter` (it did not). Both are terminal.

    AUDITABLE: actor, prior state, verdict and evidence are all recorded. This is the one
    transition that is a human overriding the system's own record, so "the state changed" is not
    an adequate trail — someone has to be accountable for the claim.

    IDEMPOTENT AND NON-CONFLICTING: only an `unknown` row can be reconciled, so a second call —
    whether repeating the same verdict or asserting a conflicting one — is REJECTED rather than
    silently overwriting the first. The first decision and its evidence stand until someone
    deliberately reopens the row.

    NEVER RESENDS. There is no resolution that returns the row to `pending`. Reconciliation
    records what happened to a message whose content may now be stale; re-queueing it would send
    expired content under cover of an audit action.
    """
    if row.state != UNKNOWN:
        raise ValueError(
            f"only `unknown` rows are reconciled, not {row.state!r}. If this row was already "
            f"reconciled (by {row.reconciled_by!r} at {row.reconciled_at}), that decision "
            f"stands; reopening it is a separate, deliberate act.")
    if resolution not in (ACCEPTED, DEAD_LETTER):
        raise ValueError(
            f"resolution must be {ACCEPTED!r} or {DEAD_LETTER!r}. There is deliberately no "
            f"'retry' resolution: the content may be stale, and re-queueing it would resend "
            f"expired content under an audit action.")
    if not (evidence or "").strip():
        raise ValueError("reconciliation requires evidence; a bare verdict is a guess")
    if not (actor or "").strip():
        raise ValueError("reconciliation requires an actor; an override needs someone "
                         "accountable for it")
    now = now or utcnow()
    row.reconciled_from_state = row.state
    row.state = resolution
    row.reconciled_at = now
    row.reconciled_by = actor
    row.reconciliation_evidence = evidence
    row.terminal_at = now
    row.terminal_reason = f"reconciled as {resolution} by {actor}"[:255]
    if resolution == ACCEPTED and row.accepted_at is None:
        # Reconstructed, not observed — the acceptance instant is unknown, so no send timestamp
        # is invented for it.
        row.reconstructed = True



# ── Migration safety ──────────────────────────────────────────────────────────────────────────

def backfill(session, *, event_id: str, recipient: str, subject: str, body_html: str,
             body_text: str, event_time: datetime, cutover_at: datetime,
             **kw) -> tuple[NotificationOutbox, bool]:
    """Record a HISTORICAL notification without sending it.

    THE BURST THIS PREVENTS. When the outbox goes live, the obvious migration is to enqueue
    everything the old scheme would have considered outstanding — every untriggered alert, every
    event whose Redis dedup marker has since expired. Those markers expire on a TTL, so from the
    outbox's point of view a large backlog of historical events looks brand new and eligible.
    Enqueuing them as `pending` would send a mass of stale email to real recipients in one
    sweep: a self-inflicted incident, and one that is unrecoverable once the provider accepts it.

    So anything originating before `cutover_at` is stored `suppressed` and `reconstructed=True`.
    It is auditable, it holds the `event_id` so the live path cannot later re-enqueue a
    duplicate of it, and it is never delivered. Rows at or after cutover enqueue normally.

    `reconstructed=True` also carries the framework's requirement that backfills are "labeled
    reconstructed with uncertainty, never given invented send/fill timestamps" — so no
    `accepted_at` is fabricated for them.
    """
    historical = event_time < cutover_at
    row, created = enqueue(
        session, event_id=event_id, recipient=recipient, subject=subject,
        body_html=body_html, body_text=body_text, reconstructed=historical, **kw)
    if created and historical:
        suppress(session, row,
                 reason=f"pre-cutover backfill ({event_time.isoformat()} < "
                        f"{cutover_at.isoformat()}); withheld to prevent a historical burst")
    return row, created


# ── Operational visibility ────────────────────────────────────────────────────────────────────

def stats(session, *, now: datetime | None = None) -> dict:
    """Queue health. Every field the framework's Notifications and Timeliness panels need.

    `reconciles` is the point of the function: the per-state counts must sum to the table total.
    If they do not, a state was written that this module does not know about, and every rate
    derived from these counts is suspect — which is a fact the dashboard must show rather than
    average away.
    """
    now = now or utcnow()
    counts = {}
    for state in (PENDING, LEASED, ACCEPTED, UNKNOWN, DEAD_LETTER, EXPIRED, SUPPRESSED):
        counts[state] = int(session.query(NotificationOutbox)
                            .filter(NotificationOutbox.state == state).count())
    total = int(session.query(NotificationOutbox).count())

    oldest = (session.query(NotificationOutbox)
              .filter(NotificationOutbox.state == PENDING)
              .order_by(NotificationOutbox.created_at).first())
    # Queue AGE, not queue depth: a shallow queue whose oldest item is six hours old is a worse
    # problem than a deep one that drains, and depth alone hides it.
    oldest_age = (now - oldest.created_at).total_seconds() if oldest else None

    attempted = int(session.query(NotificationOutbox)
                    .filter(NotificationOutbox.attempts > 0).count())
    retried = int(session.query(NotificationOutbox)
                  .filter(NotificationOutbox.attempts > 1).count())
    observed = int(session.query(NotificationOutbox)
                   .filter(NotificationOutbox.delivery_status.isnot(None)).count())

    oldest_unknown = (session.query(NotificationOutbox)
                      .filter(NotificationOutbox.state == UNKNOWN,
                              NotificationOutbox.reconciled_at.is_(None))
                      .order_by(NotificationOutbox.terminal_at).first())
    # Stranding is the cost of never auto-retrying an ambiguous outcome. If this age grows,
    # notifications are sitting unresolved and nobody has looked.
    unknown_age = ((now - oldest_unknown.terminal_at).total_seconds()
                   if oldest_unknown is not None and oldest_unknown.terminal_at else None)
    deferred = int(session.query(NotificationOutbox)
                   .filter(NotificationOutbox.defers > 0).count())
    in_flight = int(session.query(NotificationOutbox)
                    .filter(NotificationOutbox.dispatch_started_at.isnot(None),
                            NotificationOutbox.state == LEASED).count())
    return {
        "total": total,
        "by_state": counts,
        "awaiting_review": int(session.query(NotificationOutbox)
                               .filter(NotificationOutbox.state == UNKNOWN,
                                       NotificationOutbox.reconciled_at.is_(None)).count()),
        "oldest_unknown_age_seconds": unknown_age,
        "deferred_at_least_once": deferred,
        "in_flight": in_flight,
        "reconciles": sum(counts.values()) == total,
        "oldest_pending_age_seconds": oldest_age,
        "attempted": attempted,
        "retried": retried,
        # Separated on purpose. Acceptance is what this process observed; delivery is what the
        # provider reported back. `delivery_unobserved` is the normal majority and is NOT a
        # failure — but it is also not a delivery, and the two must never be summed.
        "provider_accepted": counts[ACCEPTED],
        "ambiguous_unknown": counts[UNKNOWN],
        "delivery_observed": observed,
        "delivery_unobserved": total - observed,
        "exactly_once_guaranteed": EXACTLY_ONCE,
    }
