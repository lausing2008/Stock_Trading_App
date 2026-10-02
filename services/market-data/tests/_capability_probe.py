"""M24 capability matrix against a REAL database, including repair.

A healthy database proves almost nothing here. The cases that matter are the broken ones:
a missing table, a table present WITHOUT its uniqueness constraint (idempotency silently gone
while every insert still works), a permission error, an outage, and the repair path — does
pending work resume exactly once when the prerequisite returns?
"""
import json
import pathlib
import sys
import tempfile
import types

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "shared"))

from sqlalchemy import create_engine, text
import importlib.util as _ilu

_cfg = types.ModuleType("common.config")
_cfg.get_settings = lambda: types.SimpleNamespace(database_url="sqlite://", admin_password=None)
sys.modules.setdefault("common", types.ModuleType("common"))
sys.modules["common.config"] = _cfg

_cspec = _ilu.spec_from_file_location("common.capabilities", ROOT / "shared" / "common" / "capabilities.py")
cap = _ilu.module_from_spec(_cspec); sys.modules["common.capabilities"] = cap
_cspec.loader.exec_module(cap)

_db_pkg = types.ModuleType("db"); _db_pkg.__path__ = [str(ROOT / "shared" / "db")]
sys.modules["db"] = _db_pkg
_mspec = _ilu.spec_from_file_location("db.models", ROOT / "shared" / "db" / "models.py")
_models = _ilu.module_from_spec(_mspec); sys.modules["db.models"] = _models
_mspec.loader.exec_module(_models)

_kspec = _ilu.spec_from_file_location("db.capability_checks", ROOT / "shared" / "db" / "capability_checks.py")
checks = _ilu.module_from_spec(_kspec); _kspec.loader.exec_module(checks)

R = {}
_DB = pathlib.Path(tempfile.mkdtemp()) / "cap.db"
engine = create_engine(f"sqlite:///{_DB}")


def _state(matrix, name):
    return next(e for e in cap.report(matrix)["capabilities"] if e["capability"] == name)


# ── 1. EMPTY database: everything NOT_READY, each naming what it blocks ───────────────────────
matrix = checks.build_matrix(engine)
rep = cap.report(matrix)
R["empty_db"] = {
    "all_ready": rep["all_ready"],
    "states": {e["capability"]: e["state"] for e in rep["capabilities"]},
    "degraded_in_operator_language": rep["degraded"],
    "entry_blocked": rep["entry_blocked"],
    "exit_or_reconciliation_blocked": rep["exit_or_reconciliation_blocked"],
}

# ── 2. HEALTHY database: everything ready ─────────────────────────────────────────────────────
_models.Base.metadata.create_all(engine)
rep = cap.report(matrix)
R["healthy_db"] = {"all_ready": rep["all_ready"],
                   "states": {e["capability"]: e["state"] for e in rep["capabilities"]},
                   "degraded": rep["degraded"]}

# ── 3. TABLE PRESENT, CONSTRAINT MISSING — the case a ledger entry reports as success ─────────
_DB2 = pathlib.Path(tempfile.mkdtemp()) / "noconstraint.db"
engine2 = create_engine(f"sqlite:///{_DB2}")
with engine2.begin() as c:
    # Same table, no UNIQUE on event_id. Every insert still works; idempotency is gone.
    c.execute(text("CREATE TABLE notification_outbox (id INTEGER PRIMARY KEY, event_id TEXT, "
                   "state TEXT, lease_owner TEXT, lease_expires_at TEXT, "
                   "dispatch_started_at TEXT, attempts INTEGER)"))
m2 = checks.build_matrix(engine2)
enq = _state(m2, "outbox_enqueue")
R["constraint_missing"] = {
    "state": enq["state"],
    "requirements": {r["requirement"]: r["state"] for r in enq["requirements"]},
    "evidence": next(r["evidence"] for r in enq["requirements"]
                     if r["requirement"] == "event_id uniqueness"),
    # The drain only needs the columns, which DO exist — so it stays ready while enqueue does not.
    "drain_state": _state(m2, "outbox_drain")["state"],
}

# ── 4. OUTAGE / PERMISSION ERROR: UNKNOWN, never NOT_READY ────────────────────────────────────
class _Unreachable:
    def __getattr__(self, name):
        raise OSError("could not connect to server: Connection refused")


m3 = checks.build_matrix(_Unreachable())
rep3 = cap.report(m3)
R["outage"] = {"states": {e["capability"]: e["state"] for e in rep3["capabilities"]},
               "evidence": rep3["capabilities"][0]["requirements"][0]["evidence"][:80]}


class _Forbidden:
    def __getattr__(self, name):
        raise PermissionError("permission denied for table notification_outbox")


m4 = checks.build_matrix(_Forbidden())
R["permission_error"] = {
    "state": _state(m4, "outbox_enqueue")["state"],
    "evidence": _state(m4, "outbox_enqueue")["requirements"][0]["evidence"][:80],
}

# ── 4b. A CHECK THAT RAISES OUTRIGHT — the Requirement-level safety net ───────────────────────
# Every check in capability_checks.py catches its own exceptions, so the handler inside
# `Requirement.evaluate` is never reached by them. That makes it untested defence-in-depth for
# any FUTURE check that forgets to. Exercised directly here.
def _raises():
    raise RuntimeError("check itself blew up")


_raw = cap.Capability(
    name="raw", blocks="nothing real", impact=cap.Impact.ENTRY,
    requirements=[cap.Requirement("boom", "a check that does not guard itself", _raises)])
_raw_eval = _raw.evaluate()
R["check_raises"] = {
    "state": _raw_eval["state"],
    "evidence": _raw_eval["requirements"][0]["evidence"][:60],
}

_nocheck = cap.Capability(
    name="nocheck", blocks="nothing real", impact=cap.Impact.ENTRY,
    requirements=[cap.Requirement("unimplemented", "no check supplied")])
R["check_missing"] = {"state": _nocheck.evaluate()["state"]}

# ── 5. THE GATE: unknown blocks risk-INCREASING, never risk-REDUCING ──────────────────────────
unknown_eval = _state(m3, "exposure_reservation")
notready_eval = _state(checks.build_matrix(engine2), "exposure_reservation")
recon_unknown = _state(m3, "submission_reconciliation")
R["gate"] = {
    "unknown_blocks_entry": cap.gate(unknown_eval, risk_increasing=True),
    "unknown_allows_exit": cap.gate(unknown_eval, risk_increasing=False),
    "not_ready_blocks_entry": cap.gate(notready_eval, risk_increasing=True),
    "unknown_allows_reconciliation": cap.gate(recon_unknown, risk_increasing=False),
    "healthy_allows_entry": cap.gate(_state(matrix, "exposure_reservation"),
                                     risk_increasing=True),
}

# ── 6. REPAIR: unavailable -> deferred -> restored -> resumes ONCE ────────────────────────────
_DB3 = pathlib.Path(tempfile.mkdtemp()) / "repair.db"
engine3 = create_engine(f"sqlite:///{_DB3}")
m5 = checks.build_matrix(engine3)

pending_work = ["evt-a", "evt-b"]
delivered: list[str] = []


def _attempt_delivery(matrix_):
    """Deliver only when the capability is ready; otherwise PRESERVE the work."""
    ok, _why = cap.gate(_state(matrix_, "outbox_enqueue"), risk_increasing=True)
    if not ok:
        return "deferred"
    while pending_work:
        delivered.append(pending_work.pop(0))
    return "delivered"


before = _attempt_delivery(m5)                 # table absent -> deferred, nothing lost
_models.Base.metadata.create_all(engine3)      # the repair
after = _attempt_delivery(m5)                  # ready -> delivers
again = _attempt_delivery(m5)                  # and must NOT deliver a second time
R["repair"] = {
    "before": before, "after": after, "again": again,
    "pending_preserved_during_outage": before == "deferred",
    "delivered": delivered,
    "delivered_exactly_once": len(delivered) == len(set(delivered)) == 2,
    "pending_left": pending_work,
}

print(json.dumps(R, indent=2, default=str))
