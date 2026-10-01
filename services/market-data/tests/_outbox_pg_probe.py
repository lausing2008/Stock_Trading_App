"""PostgreSQL concurrency and crash-recovery probe for the outbox.

WHY THIS EXISTS SEPARATELY FROM THE SQLITE PROBE. SQLite establishes the state machine, but it
serialises writers, so it cannot exercise the cases that actually matter in production:
genuinely simultaneous claims from separate connections, two workers racing to settle one row,
`BigInteger` identity, and transaction conflicts under real MVCC. A claim protocol verified only
on SQLite is a claim protocol verified on the one engine that cannot contend.

Needs a disposable PostgreSQL at $OUTBOX_PG_URL. Skips (exit 0, "skipped": true) when absent, so
the suite stays runnable without Docker.
"""
import json
import os
import pathlib
import sys
import types
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

_URL = os.environ.get("OUTBOX_PG_URL")
if not _URL:
    print(json.dumps({"skipped": True, "reason": "OUTBOX_PG_URL not set"}))
    sys.exit(0)

_ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT / "shared"))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
import importlib.util as _ilu

_cfg = types.ModuleType("common.config")
_cfg.get_settings = lambda: types.SimpleNamespace(database_url=_URL, admin_password=None)
sys.modules.setdefault("common", types.ModuleType("common"))
sys.modules["common.config"] = _cfg

_db_pkg = types.ModuleType("db"); _db_pkg.__path__ = [str(_ROOT / "shared" / "db")]
sys.modules["db"] = _db_pkg
_mspec = _ilu.spec_from_file_location("db.models", _ROOT / "shared" / "db" / "models.py")
_models = _ilu.module_from_spec(_mspec); sys.modules["db.models"] = _models
_mspec.loader.exec_module(_models)
NotificationOutbox, Base = _models.NotificationOutbox, _models.Base

_ospec = _ilu.spec_from_file_location("common.outbox", _ROOT / "shared" / "common" / "outbox.py")
ob = _ilu.module_from_spec(_ospec); sys.modules["common.outbox"] = ob
_ospec.loader.exec_module(ob)

engine = create_engine(_URL, pool_size=20, max_overflow=20)
# The outbox carries an FK to users, so the whole schema is created. This is a disposable
# database; nothing here touches any other container's data.
with engine.begin() as c:
    c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)

T0 = datetime(2026, 10, 1, 12, 0, 0)
R = {"skipped": False}


def _fresh():
    with Session() as s:
        s.query(NotificationOutbox).delete(); s.commit()


def _enq(s, eid, **kw):
    kw.setdefault("recipient", "a@example.com"); kw.setdefault("subject", "s")
    kw.setdefault("body_html", "<p>b</p>"); kw.setdefault("body_text", "b")
    return ob.enqueue(s, event_id=eid, **kw)


# ── 1. BIGINT identity actually works on the production engine ────────────────────────────────
_fresh()
with Session() as s:
    r, _ = _enq(s, "pg-id"); s.commit()
    R["bigint_identity"] = {"id_assigned": r.id is not None, "id": r.id}

# ── 2. SIMULTANEOUS CLAIMS from separate connections ──────────────────────────────────────────
# 8 threads, 8 connections, 20 rows. Each row must be claimed exactly once.
_fresh()
N_ROWS, N_WORKERS = 20, 8
with Session() as s:
    for i in range(N_ROWS):
        _enq(s, f"pg-race-{i}")
    s.commit()


def _worker(name):
    with Session() as s:
        try:
            got = ob.claim(s, owner=name, limit=N_ROWS, now=T0)
            ids = [r.id for r in got]
            s.commit()
            return ids
        except Exception as exc:                      # noqa: BLE001
            s.rollback()
            return {"error": type(exc).__name__}


with ThreadPoolExecutor(max_workers=N_WORKERS) as ex:
    batches = list(ex.map(_worker, [f"w{i}" for i in range(N_WORKERS)]))

errors = [b for b in batches if isinstance(b, dict)]
claimed = [i for b in batches if isinstance(b, list) for i in b]
with Session() as s:
    leased = s.query(NotificationOutbox).filter(NotificationOutbox.state == "leased").count()
    attempts = [a for (a,) in s.query(NotificationOutbox.attempts).all()]
R["concurrent_claims"] = {
    "rows": N_ROWS, "workers": N_WORKERS,
    "total_claimed": len(claimed),
    "distinct_claimed": len(set(claimed)),
    "double_claimed": len(claimed) - len(set(claimed)),
    "leased_in_db": leased,
    "max_attempts_on_any_row": max(attempts) if attempts else None,
    "transaction_errors": errors,
}

# ── 3. TWO WORKERS SETTLING THE SAME ROW — only the lease holder may win ──────────────────────
_fresh()
with Session() as s:
    _enq(s, "pg-settle"); s.commit()
with Session() as s:
    ob.claim(s, owner="holder", lease_seconds=300, now=T0); s.commit()


def _settle(owner):
    with Session() as s:
        row = s.query(NotificationOutbox).filter_by(event_id="pg-settle").one()
        try:
            won = ob.mark_accepted(s, row, owner=owner, now=T0)
            s.commit()
            return {"owner": owner, "won": won}
        except Exception as exc:                      # noqa: BLE001
            s.rollback()
            return {"owner": owner, "error": type(exc).__name__}


with ThreadPoolExecutor(max_workers=4) as ex:
    settles = list(ex.map(_settle, ["holder", "impostor-a", "impostor-b", "impostor-c"]))
with Session() as s:
    row = s.query(NotificationOutbox).filter_by(event_id="pg-settle").one()
R["concurrent_settle"] = {
    "results": settles,
    "winners": [r["owner"] for r in settles if r.get("won")],
    "final_state": row.state,
}

# ── 4. LEASE EXPIRY under a real clock-independent comparison ─────────────────────────────────
_fresh()
with Session() as s:
    _enq(s, "pg-lease"); s.commit()
with Session() as s:
    ob.claim(s, owner="first", lease_seconds=60, now=T0); s.commit()
with Session() as s:
    during = ob.claim(s, owner="second", now=T0 + timedelta(seconds=30)); s.commit()
with Session() as s:
    after = ob.claim(s, owner="second", now=T0 + timedelta(seconds=120)); s.commit()
with Session() as s:
    row = s.query(NotificationOutbox).one()
R["lease_expiry"] = {"stolen_during": len(during), "reclaimed_after": len(after),
                     "owner": row.lease_owner, "attempts": row.attempts}

# ── 5. IDEMPOTENCY under concurrent enqueue of the SAME event ─────────────────────────────────
_fresh()


def _enqueue_same(n):
    with Session() as s:
        try:
            row, created = _enq(s, "pg-dup")
            s.commit()
            return {"created": created}
        except Exception as exc:                      # noqa: BLE001
            s.rollback()
            return {"error": type(exc).__name__}


with ThreadPoolExecutor(max_workers=8) as ex:
    dups = list(ex.map(_enqueue_same, range(8)))
with Session() as s:
    R["concurrent_enqueue"] = {
        "rows": s.query(NotificationOutbox).filter_by(event_id="pg-dup").count(),
        "created_true": sum(1 for d in dups if d.get("created") is True),
        "created_false": sum(1 for d in dups if d.get("created") is False),
        "errors": [d for d in dups if "error" in d],
    }

# ── 6. CRASH RECOVERY: an abandoned in-flight row is quarantined, not resent ──────────────────
_fresh()
with Session() as s:
    _enq(s, "pg-crash"); s.commit()
with Session() as s:
    got = ob.claim(s, owner="doomed", lease_seconds=60, now=T0)
    ob.begin_dispatch(s, got[0], owner="doomed", now=T0)
    s.commit()                                        # in flight; the process then dies
LATER = T0 + timedelta(seconds=600)
with Session() as s:
    reclaim = ob.claim(s, owner="next", now=LATER); s.commit()
with Session() as s:
    n = ob.quarantine_uncertain(s, now=LATER); s.commit()
with Session() as s:
    row = s.query(NotificationOutbox).one()
R["crash_recovery"] = {"reclaimed_without_sweep": len(reclaim), "quarantined": n,
                       "state": row.state, "reason": row.terminal_reason}

# ── 7. STATS reconcile on the production engine ───────────────────────────────────────────────
with Session() as s:
    st = ob.stats(s, now=LATER)
R["stats"] = {"reconciles": st["reconciles"], "awaiting_review": st["awaiting_review"],
              "by_state": st["by_state"]}

print(json.dumps(R, indent=2, default=str))
