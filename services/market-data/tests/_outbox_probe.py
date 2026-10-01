"""Child process for test_outbox_delivery.py — exercises the REAL outbox against a REAL database.

WHY A SUBPROCESS. market-data's conftest stubs `sqlalchemy` outright, so inside the suite no
statement reaches a database. Every claim this module makes is about what the DATABASE does —
a UNIQUE constraint rejecting a duplicate, a compare-and-set UPDATE reporting rowcount 0 when it
loses a race — and a mock cannot answer any of them. Testing a claim protocol against a mock
tests the mock.

Real SQLAlchemy, real SQLite on disk, the real `shared/common/outbox.py` and the real
`NotificationOutbox` model. Prints one JSON object describing every scenario.
"""
import json
import pathlib
import sys
import tempfile
import types
from datetime import datetime, timedelta

_ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT / "shared"))

from sqlalchemy import create_engine                      # the REAL sqlalchemy
from sqlalchemy.orm import sessionmaker

# db.models imports common.config transitively through the package __init__; stub only what is
# needed to avoid building a real engine at import time.
_cfg = types.ModuleType("common.config")
_cfg.get_settings = lambda: types.SimpleNamespace(database_url="sqlite://", admin_password=None)
sys.modules.setdefault("common", types.ModuleType("common"))
sys.modules["common.config"] = _cfg

# Load models.py DIRECTLY by path under a stub `db` package. Importing the `db` package itself
# would run its __init__, which builds a real engine with PostgreSQL pool arguments that SQLite
# rejects — and this probe needs the model definitions, not a production engine.
import importlib.util as _ilu                             # noqa: E402

_db_pkg = types.ModuleType("db")
_db_pkg.__path__ = [str(_ROOT / "shared" / "db")]
sys.modules["db"] = _db_pkg
_mspec = _ilu.spec_from_file_location("db.models", _ROOT / "shared" / "db" / "models.py")
_models = _ilu.module_from_spec(_mspec)
sys.modules["db.models"] = _models
_mspec.loader.exec_module(_models)
Base, NotificationOutbox = _models.Base, _models.NotificationOutbox

_spec = _ilu.spec_from_file_location("common.outbox", _ROOT / "shared" / "common" / "outbox.py")
outbox = _ilu.module_from_spec(_spec)
sys.modules["common.outbox"] = outbox
_spec.loader.exec_module(outbox)

_DB = pathlib.Path(tempfile.mkdtemp()) / "outbox.db"
_engine = create_engine(f"sqlite:///{_DB}")
Base.metadata.create_all(_engine)
Session = sessionmaker(bind=_engine)

T0 = datetime(2026, 10, 1, 12, 0, 0)


def _fresh():
    """Each scenario starts from an empty table so results cannot leak between them."""
    with Session() as s:
        s.query(NotificationOutbox).delete()
        s.commit()


def _enq(s, event_id, **kw):
    kw.setdefault("recipient", "a@example.com")
    kw.setdefault("subject", "Subject")
    kw.setdefault("body_html", "<p>body</p>")
    kw.setdefault("body_text", "body")
    return outbox.enqueue(s, event_id=event_id, **kw)


results = {}

# ── 1. The UNIQUE constraint is the idempotency, not a prior read ─────────────────────────────
_fresh()
with Session() as s:
    r1, c1 = _enq(s, "evt-1")
    r2, c2 = _enq(s, "evt-1", subject="DIFFERENT")
    s.commit()
    results["idempotent_enqueue"] = {
        "created_first": c1, "created_second": c2,
        "rows": s.query(NotificationOutbox).count(),
        "subject_kept": r2.subject,          # the winner's content, not the loser's
        "same_row": r1.id == r2.id,
    }

# ── 2. Two workers race for the same row; exactly one wins ────────────────────────────────────
_fresh()
with Session() as s:
    _enq(s, "evt-race")
    s.commit()
with Session() as a, Session() as b:
    got_a = outbox.claim(a, owner="worker-a", now=T0)
    a.commit()
    got_b = outbox.claim(b, owner="worker-b", now=T0)
    b.commit()
    results["concurrent_claim"] = {"a": len(got_a), "b": len(got_b)}

# ── 3. A live lease is not stealable; an EXPIRED one is reclaimable ───────────────────────────
_fresh()
with Session() as s:
    _enq(s, "evt-lease")
    s.commit()
with Session() as s:
    outbox.claim(s, owner="worker-a", lease_seconds=120, now=T0)
    s.commit()
with Session() as s:
    during = outbox.claim(s, owner="worker-b", now=T0 + timedelta(seconds=60))
    s.commit()
with Session() as s:
    after = outbox.claim(s, owner="worker-b", now=T0 + timedelta(seconds=300))
    s.commit()
with Session() as s:
    row = s.query(NotificationOutbox).one()
    results["lease"] = {"stolen_during": len(during), "reclaimed_after": len(after),
                        "attempts": row.attempts, "owner": row.lease_owner}

# ── 4. Attempts exhaust into dead_letter, and the row is KEPT ─────────────────────────────────
_fresh()
with Session() as s:
    _enq(s, "evt-dead", max_attempts=2)
    s.commit()
states = []
now = T0
for _ in range(4):
    with Session() as s:
        got = outbox.claim(s, owner="w", now=now)
        if got:
            states.append(outbox.mark_failed(s, got[0], owner="w", error="smtp down", now=now))
        s.commit()
    now += timedelta(hours=2)        # past any backoff
with Session() as s:
    row = s.query(NotificationOutbox).one()
    results["dead_letter"] = {"transitions": states, "state": row.state,
                              "attempts": row.attempts, "kept": True,
                              "reason": row.terminal_reason, "error": row.last_error}

# ── 5. Backoff defers the next attempt ────────────────────────────────────────────────────────
_fresh()
with Session() as s:
    _enq(s, "evt-backoff")
    s.commit()
with Session() as s:
    got = outbox.claim(s, owner="w", now=T0)
    outbox.mark_failed(s, got[0], owner="w", error="transient", now=T0)
    s.commit()
with Session() as s:
    immediate = outbox.claim(s, owner="w", now=T0 + timedelta(seconds=5))
    s.commit()
with Session() as s:
    later = outbox.claim(s, owner="w", now=T0 + timedelta(seconds=120))
    s.commit()
    results["backoff"] = {"immediate": len(immediate), "later": len(later)}

# ── 6. A stale notification expires and is never sent ─────────────────────────────────────────
_fresh()
with Session() as s:
    _enq(s, "evt-stale", expires_at=T0 - timedelta(hours=1))
    _enq(s, "evt-live", expires_at=T0 + timedelta(hours=1))
    s.commit()
with Session() as s:
    claimed_before_expiry_sweep = [r.event_id for r in outbox.claim(s, owner="w", now=T0)]
    s.commit()
with Session() as s:
    n = outbox.expire_stale(s, now=T0)
    s.commit()
with Session() as s:
    stale = s.query(NotificationOutbox).filter_by(event_id="evt-stale").one()
    results["expiry"] = {
        "expired_count": n, "state": stale.state, "reason": stale.terminal_reason,
        "attempts": stale.attempts,              # never leased, so never attempted
        "claimed": claimed_before_expiry_sweep,  # the stale one must be absent
    }

# ── 7. Accepted is terminal and never re-claimed ──────────────────────────────────────────────
_fresh()
with Session() as s:
    _enq(s, "evt-ok")
    s.commit()
with Session() as s:
    got = outbox.claim(s, owner="w", now=T0)
    outbox.mark_accepted(s, got[0], owner="w", now=T0)
    s.commit()
with Session() as s:
    again = outbox.claim(s, owner="w", now=T0 + timedelta(days=1))
    s.commit()
with Session() as s:
    row = s.query(NotificationOutbox).one()
    results["accepted"] = {
        "state": row.state, "reclaimed": len(again),
        "accepted_at_set": row.accepted_at is not None,
        # There is deliberately no delivered_at column to be misread as proof of arrival.
        "has_delivered_at": hasattr(row, "delivered_at"),
    }

# ── 8. Retry content is frozen at enqueue ─────────────────────────────────────────────────────
_fresh()
with Session() as s:
    _enq(s, "evt-frozen", subject="Price 12.34", body_text="Price 12.34")
    s.commit()
with Session() as s:
    got = outbox.claim(s, owner="w", now=T0)
    outbox.mark_failed(s, got[0], owner="w", error="boom", now=T0)
    s.commit()
with Session() as s:
    got = outbox.claim(s, owner="w", now=T0 + timedelta(seconds=120))
    results["frozen_content"] = {"subject": got[0].subject, "body_text": got[0].body_text}
    s.commit()

# ── 9. Suppressed is a THIRD terminal fact, distinct from expired and dead_letter ─────────────
_fresh()
with Session() as s:
    r, _ = _enq(s, "evt-optout")
    outbox.suppress(s, r, reason="recipient opted out of alert_type", now=T0)
    s.commit()
with Session() as s:
    row = s.query(NotificationOutbox).one()
    again = outbox.claim(s, owner="w", now=T0 + timedelta(days=1))
    results["suppressed"] = {"state": row.state, "reason": row.terminal_reason,
                             "reclaimed": len(again)}

# ── 10. Release returns the lease without refunding the attempt ───────────────────────────────
_fresh()
with Session() as s:
    _enq(s, "evt-release")
    s.commit()
with Session() as s:
    got = outbox.claim(s, owner="w", now=T0)
    attempts_after_claim = got[0].attempts
    outbox.release(s, got[0], owner="w", now=T0)
    s.commit()
with Session() as s:
    row = s.query(NotificationOutbox).one()
    results["release"] = {"state": row.state, "attempts_after_claim": attempts_after_claim,
                          "attempts_after_release": row.attempts, "owner": row.lease_owner}

# ── 11. Enqueue requires an idempotency key ───────────────────────────────────────────────────
_fresh()
with Session() as s:
    try:
        _enq(s, "   ")
        results["blank_event_id"] = "accepted"
    except ValueError as exc:
        results["blank_event_id"] = str(exc)


# ── 12. ATOMIC EVENT/OUTBOX PERSISTENCE ───────────────────────────────────────────────────────
# A crash between committing the event and queueing its notification must lose BOTH or NEITHER.
from sqlalchemy import text as _text                       # noqa: E402

with Session() as s:
    s.execute(_text("CREATE TABLE IF NOT EXISTS probe_events (id INTEGER PRIMARY KEY, name TEXT)"))
    s.commit()
_fresh()
with Session() as s:
    s.execute(_text("DELETE FROM probe_events"))
    s.commit()

# The crash case: the domain write and the enqueue share one transaction, which is rolled back.
with Session() as s:
    s.execute(_text("INSERT INTO probe_events (id, name) VALUES (1, 'earnings')"))
    _enq(s, "evt-atomic")
    s.rollback()                                  # simulated crash before commit
with Session() as s:
    results["atomicity_crash"] = {
        "events": s.execute(_text("SELECT COUNT(*) FROM probe_events")).scalar(),
        "notifications": s.query(NotificationOutbox).count(),
    }
# The success case: both land together.
with Session() as s:
    s.execute(_text("INSERT INTO probe_events (id, name) VALUES (2, 'earnings')"))
    _enq(s, "evt-atomic-ok")
    s.commit()
with Session() as s:
    results["atomicity_commit"] = {
        "events": s.execute(_text("SELECT COUNT(*) FROM probe_events")).scalar(),
        "notifications": s.query(NotificationOutbox).count(),
    }

# ── 13. STABLE IDENTITY ACROSS RESTARTS AND REPEATED SCANS ────────────────────────────────────
_fresh()
key = "early_earnings:7:MU:2026-09-30:results"
for _ in range(3):                                 # repeated scan cycles
    with Session() as s:                           # a new session each time == a restart
        _enq(s, key, recipient="u7@example.com")
        s.commit()
with Session() as s:
    results["stable_identity"] = {"rows": s.query(NotificationOutbox).count()}

# ── 14. WORKER OWNERSHIP: an expired worker cannot acknowledge another worker's lease ─────────
_fresh()
with Session() as s:
    _enq(s, "evt-own")
    s.commit()
with Session() as s:
    stale_worker_row = outbox.claim(s, owner="worker-a", lease_seconds=60, now=T0)[0]
    s.commit()
LATER = T0 + timedelta(seconds=600)                # worker-a's lease has long lapsed
with Session() as s:
    outbox.claim(s, owner="worker-b", lease_seconds=60, now=LATER)
    s.commit()
with Session() as s:
    row = s.query(NotificationOutbox).filter_by(event_id="evt-own").one()
    a_wins = outbox.mark_accepted(s, row, owner="worker-a", now=LATER)   # the lapsed worker
    s.commit()
with Session() as s:
    row = s.query(NotificationOutbox).filter_by(event_id="evt-own").one()
    b_wins = outbox.mark_accepted(s, row, owner="worker-b", now=LATER)   # the real holder
    s.commit()
with Session() as s:
    row = s.query(NotificationOutbox).filter_by(event_id="evt-own").one()
    results["ownership"] = {"lapsed_worker_settled": a_wins, "lease_holder_settled": b_wins,
                            "state": row.state, "terminal_reason": row.terminal_reason}

# ── 15. AMBIGUOUS SEND OUTCOME: timeout after possible acceptance ─────────────────────────────
_fresh()
with Session() as s:
    _enq(s, "evt-ambiguous")
    s.commit()
with Session() as s:
    got = outbox.claim(s, owner="w", now=T0)
    outbox.mark_unknown(s, got[0], owner="w", detail="read timeout after SMTP DATA", now=T0)
    s.commit()
with Session() as s:
    retried = outbox.claim(s, owner="w", now=T0 + timedelta(days=1))
    row = s.query(NotificationOutbox).one()
    results["ambiguous"] = {
        "state": row.state, "auto_retried": len(retried),
        "reason": row.terminal_reason,
        "claims_failure": row.state == "dead_letter",
        "claims_success": row.state == "accepted",
        "exactly_once_promised": outbox.EXACTLY_ONCE,
    }

# ── 16. PREFERENCES AND EXPIRY RECHECKED IMMEDIATELY BEFORE SENDING ───────────────────────────
_fresh()
with Session() as s:
    _enq(s, "evt-pref")
    _enq(s, "evt-pref-expiring", expires_at=T0 + timedelta(seconds=30))
    s.commit()
with Session() as s:
    rows = {r.event_id: r for r in outbox.claim(s, owner="w", now=T0)}
    opted_out = outbox.may_send(rows["evt-pref"], now=T0, is_subscribed=lambda r: False)
    subscribed = outbox.may_send(rows["evt-pref"], now=T0, is_subscribed=lambda r: True)
    unchecked = outbox.may_send(rows["evt-pref"], now=T0)
    errored = outbox.may_send(rows["evt-pref"], now=T0,
                              is_subscribed=lambda r: (_ for _ in ()).throw(RuntimeError("redis down")))
    # Valid at enqueue, stale by the time the worker reaches the provider call.
    went_stale = outbox.may_send(rows["evt-pref-expiring"], now=T0 + timedelta(seconds=60),
                                 is_subscribed=lambda r: True)
    s.commit()
    results["presend_recheck"] = {
        "opted_out": opted_out, "subscribed": subscribed, "unchecked": unchecked,
        "preference_error_fails_open": errored, "expired_between_enqueue_and_send": went_stale}

# ── 17. HONEST DELIVERY STATES: accepted != delivered ─────────────────────────────────────────
_fresh()
with Session() as s:
    _enq(s, "evt-deliv")
    s.commit()
with Session() as s:
    got = outbox.claim(s, owner="w", now=T0)
    outbox.mark_accepted(s, got[0], owner="w", now=T0, provider_message_id="ses-abc123")
    s.commit()
with Session() as s:
    row = s.query(NotificationOutbox).one()
    before = {"state": row.state, "delivery_status": row.delivery_status}
    outbox.record_delivery(s, row, status="bounced", now=T0 + timedelta(minutes=5))
    s.commit()
with Session() as s:
    row = s.query(NotificationOutbox).one()
    results["delivery_states"] = {
        "accepted_leaves_delivery_unobserved": before,
        "after_callback": {"state": row.state, "delivery_status": row.delivery_status},
        "provider_message_id": row.provider_message_id,
    }

# ── 18. SAFE MIGRATION: historical events must not burst ──────────────────────────────────────
_fresh()
CUTOVER = T0
with Session() as s:
    outbox.backfill(s, event_id="hist-1", recipient="a@example.com", subject="old",
                    body_html="<p>old</p>", body_text="old",
                    event_time=T0 - timedelta(days=4), cutover_at=CUTOVER)
    outbox.backfill(s, event_id="hist-2", recipient="a@example.com", subject="old2",
                    body_html="<p>old2</p>", body_text="old2",
                    event_time=T0 - timedelta(days=1), cutover_at=CUTOVER)
    outbox.backfill(s, event_id="live-1", recipient="a@example.com", subject="new",
                    body_html="<p>new</p>", body_text="new",
                    event_time=T0 + timedelta(minutes=1), cutover_at=CUTOVER)
    s.commit()
with Session() as s:
    sendable = [r.event_id for r in outbox.claim(s, owner="w", limit=50,
                                                 now=T0 + timedelta(minutes=2))]
    s.commit()
with Session() as s:
    hist = s.query(NotificationOutbox).filter_by(event_id="hist-1").one()
    results["safe_migration"] = {
        "sendable": sendable,
        "historical_state": hist.state, "reconstructed": hist.reconstructed,
        "historical_accepted_at": hist.accepted_at,
        "rows_kept": s.query(NotificationOutbox).count(),
    }
# A historical key is HELD, so the live path cannot later enqueue a duplicate of it.
with Session() as s:
    _, created = _enq(s, "hist-1")
    s.commit()
    results["safe_migration"]["historical_key_blocks_reenqueue"] = not created

# ── 19. OPERATIONAL VISIBILITY ────────────────────────────────────────────────────────────────
_fresh()
with Session() as s:
    _enq(s, "vis-pending")
    r2, _ = _enq(s, "vis-old")
    r2.created_at = T0 - timedelta(hours=6)
    s.commit()
with Session() as s:
    # limit=1 so one row stays PENDING and queue AGE is actually exercised — a stat that is
    # always null proves nothing.
    got = outbox.claim(s, owner="w", limit=1, now=T0)
    outbox.mark_unknown(s, got[0], owner="w", detail="timeout", now=T0)
    s.commit()
with Session() as s:
    results["visibility"] = outbox.stats(s, now=T0)

print(json.dumps(results, indent=2, default=str))
