"""M20's first bounded integration: earnings release-phase alerts through the outbox.

WHY THIS FAMILY FIRST. It is the one the MU incident actually broke. On 2026-09-30 the Q4
results headline reached the database 5.08 seconds after publication and no alert went out,
because a "Micron Earnings Ahead" preview had already consumed that day's single Redis dedup
slot. MU-02 split the slot per release phase; this closes the other half — the delivery itself —
by replacing a TTL marker with a durable, idempotent record.

WHAT THIS DOES NOT DO, STATED PLAINLY. **The outbox makes no claim about whether the headline
was classified correctly.** `phase` here is the classifier's LABEL, carried through so delivery
is idempotent per label; it is not evidence that the right release was identified.
`earnings_phase.KNOWN_UNRESOLVED_RISK` — a newly published retrospective article about a past
quarter passing issuer, freshness and period checks — is untouched by anything in this file and
needs authoritative release identity (M19/M23). A notification recorded `accepted` here means
exactly "the provider took this message", never "this was the right release".

ROLLOUT IS OFF BY DEFAULT and the two paths are mutually exclusive by construction: in `off`
the legacy sender runs alone, in `outbox` the outbox runs alone. There is no mode in which both
deliver, because that is how a cutover double-sends.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from common import outbox as _ob

#: Admin flag. Absent or unrecognised reads as OFF — an unknown configuration is never an
#: implicit opt-in to a new delivery path.
ROLLOUT_KEY = "stockai:admin:feature:earnings_outbox_rollout"

OFF = "off"            # legacy sender only. The outbox is not written to at all.
SHADOW = "shadow"      # enqueue and evaluate, but never dispatch. Legacy still delivers.
OUTBOX = "outbox"      # the outbox delivers. The legacy sender is skipped entirely.
MODES = (OFF, SHADOW, OUTBOX)


def rollout_mode(redis_client) -> str:
    """Current mode. Any failure, absence or unrecognised value reads as OFF."""
    if redis_client is None:
        return OFF
    try:
        raw = redis_client.get(ROLLOUT_KEY)
    except Exception:                                  # noqa: BLE001
        return OFF
    if raw is None:
        return OFF
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    mode = str(raw).strip().lower()
    return mode if mode in MODES else OFF


#: When the outbox was ACTIVATED. Rows queued before this instant were queued in `shadow`,
#: which means the LEGACY sender already delivered them — draining them now would send every
#: one a second time.
ACTIVATED_AT_KEY = "stockai:admin:feature:earnings_outbox_activated_at"


def activation_watermark(redis_client) -> datetime | None:
    """The instant the outbox took over delivery, or None if unknown/unset.

    None is not "no restriction" — see `deliver_batch`, which refuses to deliver anything
    without it. A mode flip with no watermark cannot distinguish a shadow row (already
    delivered by legacy) from a live one, and guessing wrong duplicates real mail.
    """
    if redis_client is None:
        return None
    try:
        raw = redis_client.get(ACTIVATED_AT_KEY)
    except Exception:                                  # noqa: BLE001
        return None
    if not raw:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    try:
        return datetime.fromisoformat(str(raw).strip().replace("Z", ""))
    except Exception:                                  # noqa: BLE001
        return None


def legacy_should_send(mode: str) -> bool:
    return mode in (OFF, SHADOW)


def outbox_should_send(mode: str) -> bool:
    return mode == OUTBOX


def event_id_for(user_id, symbol: str, event_date, phase: str) -> str:
    """The idempotency key, deliberately the SAME SHAPE as the legacy Redis dedup key
    (`stockai:early_earnings_news:{uid}:{sym}:{event_date}:{phase}`).

    Identical shape is what makes a safe cutover checkable: the legacy marker for an event maps
    one-to-one onto the outbox row for the same event, so "has the old path already handled
    this" is answerable without a heuristic.
    """
    return f"stockai:early_earnings_news:{user_id}:{symbol}:{event_date}:{phase}"


def legacy_marker_exists(redis_client, event_id: str) -> bool | None:
    """Has the legacy TTL path already handled this event? True / False / None = unknown.

    None matters: if Redis is unreachable we cannot tell whether the old path sent this, and
    treating that as "not sent" during a cutover is exactly how a historical replay happens.
    """
    if redis_client is None:
        return None
    try:
        return bool(redis_client.exists(event_id))
    except Exception:                                  # noqa: BLE001
        return None


def enqueue_phase_alert(session, *, user_id, recipient: str, symbol: str, event_date,
                        phase: str, subject: str, body_html: str, body_text: str,
                        redis_client=None, cutover_at: datetime | None = None,
                        event_time: datetime | None = None,
                        ttl_hours: int = 48) -> tuple[object, bool, str]:
    """Enqueue one phase alert. Returns `(row, created, disposition)`.

    CONFIRMED SUPPRESSION AND UNCERTAINTY ARE DIFFERENT, and conflating them loses alerts:

      * a legacy marker EXISTS       -> `suppressed`. Confirmed: the old path handled it.
      * the event predates cutover   -> `suppressed` + `reconstructed`. Confirmed by date.
      * the marker state is UNKNOWN  -> left PENDING, rechecked before every send attempt.

    The third case used to be suppressed too, on the reasoning that withholding beats a possible
    duplicate. That is wrong as a PERMANENT verdict: Redis being unreachable for one second
    would have permanently cancelled a real alert, recorded as though delivery had been
    confirmed. "We could not tell" is not "already delivered". The row stays deliverable and the
    worker re-checks at send time, deferring while the answer is still unknown and dead-lettering
    into the review queue if it never becomes knowable.

    In every case the row is written, so the event_id is held and the live path cannot later
    enqueue a duplicate of the same event.
    """
    event_id = event_id_for(user_id, symbol, event_date, phase)
    event_time = event_time or _ob.utcnow()
    expires_at = event_time + timedelta(hours=ttl_hours)

    marker = legacy_marker_exists(redis_client, event_id)
    if cutover_at is not None and event_time < cutover_at:
        row, created = _ob.backfill(
            session, event_id=event_id, recipient=recipient, subject=subject,
            body_html=body_html, body_text=body_text, event_time=event_time,
            cutover_at=cutover_at, alert_type="early_earnings_news", user_id=user_id,
            expires_at=expires_at)
        return row, created, "pre_cutover"

    row, created = _ob.enqueue(
        session, event_id=event_id, recipient=recipient, subject=subject,
        body_html=body_html, body_text=body_text, alert_type="early_earnings_news",
        # Availability derives from the EVENT, not from when the row happened to be written.
        # Deriving it from the wall clock made the fixtures clock-dependent — a probe pinned to
        # a fixed instant stopped being able to claim its own rows once real time passed it,
        # the same stale-literal failure as the hardcoded option expiries fixed earlier.
        user_id=user_id, expires_at=expires_at, available_at=event_time)
    if not created:
        return row, False, "already_enqueued"
    if marker is True:
        _ob.suppress(session, row, reason="legacy TTL marker already handled this event")
        return row, True, "legacy_already_sent"
    if marker is None:
        # Deliberately NOT suppressed. Left pending and deferred at send time until the marker
        # becomes readable; see this function's docstring.
        row.last_error = "legacy marker state unknown at enqueue; will recheck before sending"
        row.available_at = event_time + timedelta(seconds=_ob.backoff_seconds(1))
        return row, True, "legacy_state_unknown_pending"
    return row, True, "queued"


def deliver_batch(session, *, owner: str, send, is_subscribed, commit=None,
                  legacy_marker=None, activated_at: datetime | None = None,
                  now: datetime | None = None, limit: int = 20) -> dict:
    """One worker pass: quarantine, expire, claim, recheck, dispatch, settle.

    `send(row) -> bool` is injected so the whole path can be exercised against a fake provider,
    including timeouts and crashes. `commit` is injected for the same reason: the crash test
    needs to stop committing at an exact point.

    THE ORDER IS THE DESIGN. `begin_dispatch` is committed BEFORE the provider call — an
    uncommitted marker records nothing, and the acceptance/crash gap is precisely the window
    where the process may stop between the two.
    """
    now = now or _ob.utcnow()
    commit = commit or session.commit
    out = {"quarantined": 0, "expired": 0, "claimed": 0, "accepted": 0, "failed": 0,
           "deferred": 0, "suppressed": 0, "expired_at_send": 0, "unknown": 0, "lost_lease": 0,
           "deferred_unknown_marker": 0, "pre_activation_suppressed": 0,
           "pre_activation_suppressed_evidenced": 0,
           "pre_activation_potentially_undelivered": 0}

    # NO WATERMARK, NO DELIVERY. Without it a shadow row — already delivered by the legacy
    # sender — cannot be told apart from one queued after the outbox took over. Delivering
    # anyway would re-send every notification accumulated during the shadow period, which is
    # the single worst thing a cutover can do.
    if activated_at is None:
        out["no_activation_watermark"] = True
        return out

    out["quarantined"] = _ob.quarantine_uncertain(session, now=now)
    out["expired"] = _ob.expire_stale(session, now=now)
    commit()

    rows = _ob.claim(session, owner=owner, limit=limit, now=now)
    commit()
    out["claimed"] = len(rows)

    for row in rows:
        # Rows queued BEFORE activation were queued while legacy still owned delivery, so they
        # have already been sent. Suppressed with a reason rather than dropped, so the cutover
        # leaves a record of exactly what it withheld and why.
        if row.created_at is not None and row.created_at < activated_at:
            # CUTOVER SUPPRESSION, which is NOT the same as confirmed prior delivery.
            #
            # An earlier version of this called it "the legacy sender already delivered this
            # event". That is too broad: the outbox has no acceptance record for a shadow row,
            # so it does not know whether legacy actually delivered it. What is being decided
            # is that the cutover will NOT send historical rows — a deliberate withholding.
            #
            # Where the legacy marker still exists, delivery IS evidenced; where it does not,
            # the row is POTENTIALLY UNDELIVERED and is counted separately so the cutover can
            # report what it may have dropped rather than claiming it was already handled.
            evidenced = legacy_marker(row) if legacy_marker is not None else None
            if evidenced is True:
                reason = ("cutover suppression: queued before activation, and the legacy "
                          "delivery marker for this event is present")
                out["pre_activation_suppressed_evidenced"] += 1
            else:
                reason = ("cutover suppression: queued before activation. NO legacy delivery "
                          "marker is present, so this event is POTENTIALLY UNDELIVERED and was "
                          "withheld by the cutover rather than confirmed as sent")
                out["pre_activation_potentially_undelivered"] += 1
            _ob.suppress(session, row, reason=reason, now=now)
            out["pre_activation_suppressed"] += 1
            commit(); continue

        # Re-check the legacy marker, for the same reason preferences are re-checked: the state
        # may have become readable (or become true) since enqueue. An unreadable marker DEFERS;
        # it never silently becomes "already delivered".
        if legacy_marker is not None:
            state = legacy_marker(row)
            if state is True:
                _ob.suppress(session, row,
                             reason="legacy path delivered this event before the outbox could",
                             now=now)
                out["suppressed"] += 1
                commit(); continue
            if state is None:
                _ob.defer(session, row, owner=owner,
                          reason="legacy marker unreadable; cannot rule out a duplicate",
                          now=now)
                out["deferred_unknown_marker"] += 1
                commit(); continue

        gate, reason = _ob.may_send(row, now=now, is_subscribed=is_subscribed)
        if gate is _ob.SendGate.SUPPRESS:
            _ob.suppress(session, row, reason=reason, now=now)
            out["suppressed"] += 1
            commit(); continue
        if gate is _ob.SendGate.EXPIRE:
            _ob.mark_expired(session, row, owner=owner, reason=reason, now=now)
            out["expired_at_send"] += 1
            commit(); continue
        if gate is _ob.SendGate.DEFER:
            _ob.defer(session, row, owner=owner, reason=reason, now=now)
            out["deferred"] += 1
            commit(); continue

        if not _ob.begin_dispatch(session, row, owner=owner, now=now):
            # Lease lost between claim and dispatch. Someone else owns this now; do not send.
            out["lost_lease"] += 1
            commit(); continue
        commit()                      # durable BEFORE the provider is touched

        try:
            accepted = send(row)
        except TimeoutError as exc:
            # The provider may already have accepted. Neither success nor failure is known.
            _ob.mark_unknown(session, row, owner=owner, detail=str(exc) or "timeout", now=now)
            out["unknown"] += 1
            commit(); continue
        except Exception as exc:                      # noqa: BLE001
            _ob.mark_failed(session, row, owner=owner, error=str(exc), now=now)
            out["failed"] += 1
            commit(); continue

        if accepted:
            _ob.mark_accepted(session, row, owner=owner, now=now)
            out["accepted"] += 1
        else:
            _ob.mark_failed(session, row, owner=owner, error="provider rejected", now=now)
            out["failed"] += 1
        commit()
    return out
