"""End-to-end probe for M20's earnings-phase integration, against a REAL database.

Covers the full path with a FAKE provider: release -> event -> outbox -> claim -> pre-send
recheck -> dispatch -> provider -> recorded acceptance, plus the failure and restart cases.
"""
import json
import pathlib
import sys
import tempfile
import types
from datetime import datetime, timedelta

_ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT / "shared"))
sys.path.insert(0, str(_ROOT / "services" / "market-data"))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import importlib.util as _ilu

_cfg = types.ModuleType("common.config")
_cfg.get_settings = lambda: types.SimpleNamespace(database_url="sqlite://", admin_password=None)
_common = types.ModuleType("common")
sys.modules["common"] = _common
sys.modules["common.config"] = _cfg

_db_pkg = types.ModuleType("db")
_db_pkg.__path__ = [str(_ROOT / "shared" / "db")]
sys.modules["db"] = _db_pkg
_mspec = _ilu.spec_from_file_location("db.models", _ROOT / "shared" / "db" / "models.py")
_models = _ilu.module_from_spec(_mspec)
sys.modules["db.models"] = _models
_mspec.loader.exec_module(_models)
Base, NotificationOutbox = _models.Base, _models.NotificationOutbox

_ospec = _ilu.spec_from_file_location("common.outbox", _ROOT / "shared" / "common" / "outbox.py")
ob = _ilu.module_from_spec(_ospec)
sys.modules["common.outbox"] = ob
_ospec.loader.exec_module(ob)
_common.outbox = ob

_espec = _ilu.spec_from_file_location(
    "earnings_outbox", _ROOT / "services" / "market-data" / "src" / "services" / "earnings_outbox.py")
eo = _ilu.module_from_spec(_espec)
_espec.loader.exec_module(eo)

_DB = pathlib.Path(tempfile.mkdtemp()) / "eo.db"
_engine = create_engine(f"sqlite:///{_DB}")
Base.metadata.create_all(_engine)
Session = sessionmaker(bind=_engine)

# DERIVED FROM THE CLOCK, NOT PINNED TO A LITERAL. `enqueue` stamps timestamps from the real
# clock, so a fixture instant frozen in the past stops matching its own rows once wall-clock time
# passes it — the failure that turned this suite red in CI while it had passed locally hours
# earlier. Every offset below is relative to this.
T0 = ob.utcnow().replace(microsecond=0)
R = {}


def _fresh():
    with Session() as s:
        s.query(NotificationOutbox).delete(); s.commit()


class FakeRedis:
    def __init__(self, keys=(), broken=False):
        self.keys = set(keys); self.broken = broken
        self.store = {}

    def exists(self, k):
        if self.broken:
            raise ConnectionError("redis down")
        return 1 if k in self.keys else 0

    def get(self, k):
        if self.broken:
            raise ConnectionError("redis down")
        return self.store.get(k)


class FakeProvider:
    """Records what it was actually asked to send, so duplicates are detectable."""
    def __init__(self, behaviour="accept"):
        self.behaviour = behaviour
        self.sent = []

    def __call__(self, row):
        self.sent.append(row.event_id)
        if self.behaviour == "timeout":
            raise TimeoutError("read timeout after DATA")
        if self.behaviour == "reject":
            return False
        if self.behaviour == "raise":
            raise RuntimeError("smtp 550")
        return True


def _enqueue(s, phase="results", uid=7, symbol="MU", redis_client=None, cutover_at=None,
             event_time=None, ttl_hours=48):
    return eo.enqueue_phase_alert(
        s, user_id=uid, recipient="u7@example.com", symbol=symbol,
        event_date="2026-09-30", phase=phase, subject=f"{symbol} {phase}",
        body_html=f"<p>{phase}</p>", body_text=phase, redis_client=redis_client,
        cutover_at=cutover_at, event_time=event_time or T0, ttl_hours=ttl_hours)


# ── 1. ROLLOUT MODES ARE MUTUALLY EXCLUSIVE ───────────────────────────────────────────────────
R["rollout"] = {
    "default_when_absent": eo.rollout_mode(FakeRedis()),
    "unknown_value": (lambda r: (r.store.__setitem__(eo.ROLLOUT_KEY, "yes"),
                                 eo.rollout_mode(r))[1])(FakeRedis()),
    "redis_down": eo.rollout_mode(FakeRedis(broken=True)),
    "none_client": eo.rollout_mode(None),
    "exclusive": {m: {"legacy": eo.legacy_should_send(m), "outbox": eo.outbox_should_send(m)}
                  for m in eo.MODES},
}

# ── 2. FULL HAPPY PATH ────────────────────────────────────────────────────────────────────────
_fresh()
prov = FakeProvider("accept")
with Session() as s:
    row, created, disp = _enqueue(s, redis_client=FakeRedis()); s.commit()
with Session() as s:
    res = eo.deliver_batch(s, activated_at=T0 - timedelta(days=1), owner="w1", send=prov, is_subscribed=lambda r: True, now=T0)
with Session() as s:
    row = s.query(NotificationOutbox).one()
    R["happy_path"] = {
        "disposition": disp, "created": created, "batch": res,
        "state": row.state, "accepted_at_set": row.accepted_at is not None,
        "delivery_status": row.delivery_status, "provider_calls": list(prov.sent),
        "event_id": row.event_id, "dispatch_recorded": row.dispatch_started_at is not None,
    }

# ── 3. PREFERENCE LOOKUP ERROR DEFERS — it does NOT send ──────────────────────────────────────
_fresh()
prov = FakeProvider("accept")
def _broken_prefs(row):
    raise ConnectionError("preference store unreachable")
with Session() as s:
    _enqueue(s, redis_client=FakeRedis()); s.commit()
with Session() as s:
    res = eo.deliver_batch(s, activated_at=T0 - timedelta(days=1), owner="w1", send=prov, is_subscribed=_broken_prefs, now=T0)
with Session() as s:
    row = s.query(NotificationOutbox).one()
    R["pref_error_defers"] = {
        "batch": res, "provider_calls": list(prov.sent), "state": row.state,
        "attempts": row.attempts, "defers": row.defers,
        "available_at_moved": row.available_at > T0, "last_error": row.last_error,
    }
# ... and recovers once the preference source returns
with Session() as s:
    res2 = eo.deliver_batch(s, activated_at=T0 - timedelta(days=1), owner="w1", send=prov, is_subscribed=lambda r: True,
                            now=T0 + timedelta(hours=1))
with Session() as s:
    row = s.query(NotificationOutbox).one()
    R["pref_error_defers"]["after_recovery"] = {
        "batch": res2, "state": row.state, "provider_calls": list(prov.sent)}

# ── 4. DEFER CAP dead-letters rather than deferring forever in silence ────────────────────────
_fresh()
prov = FakeProvider("accept")
with Session() as s:
    row, _, _ = _enqueue(s, redis_client=FakeRedis())
    row.max_defers = 3
    s.commit()
now = T0
for _ in range(5):
    with Session() as s:
        eo.deliver_batch(s, activated_at=T0 - timedelta(days=1), owner="w1", send=prov, is_subscribed=_broken_prefs, now=now)
    now += timedelta(hours=4)
with Session() as s:
    row = s.query(NotificationOutbox).one()
    R["defer_cap"] = {"state": row.state, "defers": row.defers, "attempts": row.attempts,
                      "reason": row.terminal_reason, "provider_calls": list(prov.sent)}

# ── 5. OPT-OUT SUPPRESSES; EXPIRY AT SEND TIME EXPIRES ────────────────────────────────────────
_fresh()
prov = FakeProvider("accept")
with Session() as s:
    _enqueue(s, phase="results", redis_client=FakeRedis())
    _enqueue(s, phase="guidance", redis_client=FakeRedis(), ttl_hours=1)
    s.commit()
with Session() as s:
    # 90 minutes later the guidance row's 1h TTL has lapsed; results is still live.
    res = eo.deliver_batch(s, activated_at=T0 - timedelta(days=1), owner="w1", send=prov,
                           is_subscribed=lambda r: r.event_id.endswith("guidance"),
                           now=T0 + timedelta(minutes=90))
with Session() as s:
    rows = {r.event_id.rsplit(":", 1)[1]: r for r in s.query(NotificationOutbox).all()}
    R["gating"] = {
        "batch": res, "provider_calls": list(prov.sent),
        "results_optedout": rows["results"].state,
        "guidance_expired": rows["guidance"].state,
        "guidance_reason": rows["guidance"].terminal_reason,
    }

# ── 6. THE ACCEPTANCE/CRASH GAP ───────────────────────────────────────────────────────────────
# The provider ACCEPTED; the process died before recording it. A later worker must not resend.
_fresh()
prov = FakeProvider("accept")
with Session() as s:
    _enqueue(s, redis_client=FakeRedis()); s.commit()
with Session() as s:
    got = ob.claim(s, owner="w-doomed", lease_seconds=60, now=T0)
    ob.begin_dispatch(s, got[0], owner="w-doomed", now=T0)
    s.commit()                         # durable marker, BEFORE the provider call
    prov(got[0])                       # provider accepts...
    # ...and the process dies here. Nothing settles the row.
CRASH_LATER = T0 + timedelta(seconds=600)
with Session() as s:
    res = eo.deliver_batch(s, activated_at=T0 - timedelta(days=1), owner="w2", send=prov, is_subscribed=lambda r: True,
                           now=CRASH_LATER)
with Session() as s:
    row = s.query(NotificationOutbox).one()
    R["crash_gap"] = {
        "batch": res, "state": row.state, "reason": row.terminal_reason,
        "provider_calls": list(prov.sent),       # must still be ONE: no blind resend
        "awaiting_review": len(ob.needs_review(s)),
    }

# ── 7. REVIEW QUEUE AND RECONCILIATION ────────────────────────────────────────────────────────
with Session() as s:
    pending_review = ob.needs_review(s)
    before = {"count": len(pending_review),
              "stats_awaiting": ob.stats(s, now=CRASH_LATER)["awaiting_review"],
              "age": ob.stats(s, now=CRASH_LATER)["oldest_unknown_age_seconds"]}
    try:
        ob.reconcile_unknown(s, pending_review[0], resolution="accepted", evidence="",
                             actor="ops:sing")
        no_evidence = "accepted"
    except ValueError as exc:
        no_evidence = str(exc)
    try:
        ob.reconcile_unknown(s, pending_review[0], resolution="accepted",
                             evidence="ses log", actor="")
        no_actor = "accepted"
    except ValueError as exc:
        no_actor = str(exc)
    try:
        ob.reconcile_unknown(s, pending_review[0], resolution="pending",
                             evidence="let us just retry it", actor="ops:sing")
        retry_allowed = True
    except ValueError as exc:
        retry_allowed = str(exc)
    ob.reconcile_unknown(s, pending_review[0], resolution="accepted",
                         evidence="SES delivery log shows message-id ses-xyz accepted 12:00:03",
                         actor="ops:sing", now=CRASH_LATER)
    s.commit()
with Session() as s:
    row = s.query(NotificationOutbox).one()
    R["review"] = {
        "before": before, "evidence_required": no_evidence, "state": row.state,
        "reconciled": row.reconciled_at is not None, "reconstructed": row.reconstructed,
        "reason": row.terminal_reason,
        "still_awaiting": ob.stats(s, now=CRASH_LATER)["awaiting_review"],
        "actor_required": no_actor, "retry_resolution_rejected": retry_allowed,
        "actor": row.reconciled_by, "from_state": row.reconciled_from_state,
        "evidence_stored": row.reconciliation_evidence,
    }
# A second reconciliation — same verdict or conflicting — must be refused, not silently applied.
with Session() as s:
    row = s.query(NotificationOutbox).one()
    for label, res in (("repeat", "accepted"), ("conflicting", "dead_letter")):
        try:
            ob.reconcile_unknown(s, row, resolution=res, evidence="second opinion",
                                 actor="ops:other", now=CRASH_LATER)
            R["review"][f"{label}_allowed"] = True
        except ValueError as exc:
            R["review"][f"{label}_allowed"] = str(exc)[:80]
    R["review"]["state_after_repeat_attempts"] = row.state
    R["review"]["actor_unchanged"] = row.reconciled_by

# ── 8. CUTOVER AGAINST EXISTING PHASE MARKERS ─────────────────────────────────────────────────
_fresh()
prov = FakeProvider("accept")
existing = eo.event_id_for(7, "MU", "2026-09-30", "results")
with Session() as s:
    _, _, d_legacy = _enqueue(s, phase="results", redis_client=FakeRedis(keys=[existing]))
    _, _, d_unknown = _enqueue(s, phase="guidance", redis_client=FakeRedis(broken=True))
    _, _, d_old = _enqueue(s, phase="preview", redis_client=FakeRedis(),
                           cutover_at=T0, event_time=T0 - timedelta(days=3))
    _, _, d_new = _enqueue(s, phase="call", redis_client=FakeRedis(), cutover_at=T0,
                           event_time=T0 + timedelta(minutes=1))
    s.commit()
def _marker_unreadable(row):
    return None if row.event_id.endswith("guidance") else False


with Session() as s:
    res = eo.deliver_batch(s, activated_at=T0 - timedelta(days=1), owner="w1", send=prov, is_subscribed=lambda r: True,
                           legacy_marker=_marker_unreadable, now=T0 + timedelta(minutes=5))
with Session() as s:
    rows = {r.event_id.rsplit(":", 1)[1]: r for r in s.query(NotificationOutbox).all()}
    R["cutover"] = {
        "batch": res,
        "unknown_marker_state": rows["guidance"].state,
        "unknown_marker_defers": rows["guidance"].defers,
        "unknown_marker_error": rows["guidance"].last_error,
        "dispositions": {"legacy_marker": d_legacy, "marker_unknown": d_unknown,
                         "pre_cutover": d_old, "post_cutover": d_new},
        "states": {k: v.state for k, v in rows.items()},
        "provider_calls": list(prov.sent),
        "rows_kept": len(rows),
        "pre_cutover_reconstructed": rows["preview"].reconstructed,
    }
# The held keys block any later duplicate enqueue.
with Session() as s:
    _, created_again, disp_again = _enqueue(s, phase="results", redis_client=FakeRedis())
    s.commit()
    R["cutover"]["reenqueue_blocked"] = {"created": created_again, "disposition": disp_again}

# ── 9. RESTART MID-BATCH: a new worker resumes without duplicating ────────────────────────────
_fresh()
prov = FakeProvider("accept")
with Session() as s:
    for ph in ("preview", "results", "guidance"):
        _enqueue(s, phase=ph, redis_client=FakeRedis())
    s.commit()
# Worker 1 claims all three, settles one, then dies.
with Session() as s:
    got = ob.claim(s, owner="w1", lease_seconds=60, now=T0)
    ob.begin_dispatch(s, got[0], owner="w1", now=T0); s.commit()
    prov(got[0])
    ob.mark_accepted(s, got[0], owner="w1", now=T0); s.commit()
    # got[1] and got[2] remain leased by the dead worker, never dispatched.
RESUME = T0 + timedelta(seconds=600)
with Session() as s:
    res = eo.deliver_batch(s, activated_at=T0 - timedelta(days=1), owner="w2", send=prov, is_subscribed=lambda r: True, now=RESUME)
with Session() as s:
    states = sorted((r.event_id.rsplit(":", 1)[1], r.state) for r in s.query(NotificationOutbox))
    R["restart"] = {"batch": res, "states": states, "provider_calls": sorted(prov.sent),
                    "unique_sends": len(set(prov.sent)), "total_sends": len(prov.sent)}

# ── 10. PROVIDER REJECTION AND TIMEOUT ────────────────────────────────────────────────────────
_fresh()
with Session() as s:
    _enqueue(s, phase="results", redis_client=FakeRedis()); s.commit()
with Session() as s:
    res_t = eo.deliver_batch(s, activated_at=T0 - timedelta(days=1), owner="w1", send=FakeProvider("timeout"),
                             is_subscribed=lambda r: True, now=T0)
with Session() as s:
    timeout_row = s.query(NotificationOutbox).one()
_fresh()
with Session() as s:
    _enqueue(s, phase="results", redis_client=FakeRedis()); s.commit()
with Session() as s:
    res_r = eo.deliver_batch(s, activated_at=T0 - timedelta(days=1), owner="w1", send=FakeProvider("reject"),
                             is_subscribed=lambda r: True, now=T0)
with Session() as s:
    reject_row = s.query(NotificationOutbox).one()
R["provider_outcomes"] = {
    "timeout": {"batch": res_t, "state": timeout_row.state,
                "reason": timeout_row.terminal_reason},
    "reject": {"batch": res_r, "state": reject_row.state,
               "retryable": reject_row.state == "pending"},
}

# ── 11. ATTRIBUTION IS NOT CLAIMED ────────────────────────────────────────────────────────────
# `accepted` must mean "the provider took this message", never "the right release".
R["attribution"] = {
    "outbox_columns": sorted(c.name for c in NotificationOutbox.__table__.columns),
    "module_doc_disclaims": "makes no claim about whether the headline" in (eo.__doc__ or ""),
    "accepted_means": "the provider took this message",
}

# ── 12. ACTIVATION WATERMARK: shadow rows must not drain after the flip ───────────────────────
_fresh()
prov_w = FakeProvider("accept")
SHADOW_T = T0 - timedelta(hours=6)
ACTIVATED = T0 - timedelta(hours=1)
with Session() as s:
    row_shadow, _, _ = _enqueue(s, phase="preview", redis_client=FakeRedis(),
                                event_time=SHADOW_T)
    # `created_at` is when the ROW WAS WRITTEN, which is what the watermark compares against —
    # set explicitly, because both rows are written now in a test.
    row_shadow.created_at = SHADOW_T
    row_shadow.available_at = SHADOW_T
    _enqueue(s, phase="results", redis_client=FakeRedis(), event_time=T0)
    s.commit()
with Session() as s:
    # No legacy marker for either row -> both are POTENTIALLY UNDELIVERED, which the cutover
    # must report rather than claim as already sent.
    res_w = eo.deliver_batch(s, activated_at=ACTIVATED, owner="w", send=prov_w,
                             is_subscribed=lambda r: True,
                             legacy_marker=lambda r: False, now=T0)
with Session() as s:
    rows = {r.event_id.rsplit(":", 1)[1]: r for r in s.query(NotificationOutbox).all()}
    R["activation_watermark"] = {
        "batch": res_w,
        "shadow_row_state": rows["preview"].state,
        "shadow_row_reason": rows["preview"].terminal_reason,
        "live_row_state": rows["results"].state,
        "provider_calls": list(prov_w.sent),
    }

# Without a watermark the drain refuses to deliver at all.
_fresh()
prov_n = FakeProvider("accept")
with Session() as s:
    _enqueue(s, phase="results", redis_client=FakeRedis()); s.commit()
with Session() as s:
    res_n = eo.deliver_batch(s, activated_at=None, owner="w", send=prov_n,
                             is_subscribed=lambda r: True, now=T0)
R["no_watermark"] = {"batch": res_n, "provider_calls": list(prov_n.sent)}

print(json.dumps(R, indent=2, default=str))
