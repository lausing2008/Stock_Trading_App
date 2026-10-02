"""M24 — capability readiness, checked against real schemas including broken ones.

WHY NOT A MIGRATION LEDGER. `migration_applied(name)` answers "did this statement run", which is
a different question from "can the outbox enqueue". EC-03 lived in that gap: a service started,
passed its healthcheck, and ran every job whose premise was a migration that had failed.

A healthy database proves almost nothing here. The cases that matter are the broken ones, and
one in particular: a table present WITHOUT its uniqueness constraint. Every insert still works,
the ledger entry still says the migration ran, and the idempotency the whole design rests on is
silently gone.
"""
import json
import pathlib
import subprocess
import sys

import pytest

_PROBE = pathlib.Path(__file__).resolve().parent / "_capability_probe.py"


@pytest.fixture(scope="module")
def probe():
    proc = subprocess.run([sys.executable, str(_PROBE)], capture_output=True, text=True,
                          timeout=300)
    if proc.returncode != 0:
        pytest.fail(f"capability probe failed:\n{proc.stdout[-3000:]}\n{proc.stderr[-3000:]}")
    return json.loads(proc.stdout)


# ── The three states ──────────────────────────────────────────────────────────────────────────

def test_an_empty_database_reports_every_capability_not_ready(probe):
    e = probe["empty_db"]
    assert e["all_ready"] is False
    assert set(e["states"].values()) == {"not_ready"}


def test_a_healthy_database_reports_every_capability_ready(probe):
    assert probe["healthy_db"]["all_ready"] is True
    assert probe["healthy_db"]["degraded"] == []


def test_an_outage_is_UNKNOWN_not_not_ready(probe):
    """A database that cannot be reached has NOT told us the capability is absent. Collapsing
    unknown to not_ready would block work on a transient outage."""
    assert set(probe["outage"]["states"].values()) == {"unknown"}
    assert "could not connect to server" in probe["outage"]["evidence"]


def test_a_permission_error_is_UNKNOWN_not_absence(probe):
    """"We were not allowed to look" and "we looked and it is absent" are different facts."""
    assert probe["permission_error"]["state"] == "unknown"
    assert "PermissionError" in probe["permission_error"]["evidence"]


def test_a_check_that_raises_is_UNKNOWN_not_a_missing_capability(probe):
    """FOUND BY A SABOTAGE THAT SURVIVED. Every check in capability_checks.py guards itself, so
    the handler inside `Requirement.evaluate` was never reached — untested defence-in-depth for
    any future check that forgets to.

    An erroring check has not established absence. Reporting it as `not_ready` would block work
    on a bug in the probe rather than on a real missing prerequisite."""
    c = probe["check_raises"]
    assert c["state"] == "unknown"
    assert "RuntimeError" in c["evidence"]


def test_a_capability_with_no_check_is_UNKNOWN_not_ready(probe):
    """An unimplemented check must never read as a satisfied prerequisite."""
    assert probe["check_missing"]["state"] == "unknown"


# ── The case a migration ledger gets wrong ────────────────────────────────────────────────────

def test_a_table_without_its_uniqueness_constraint_is_NOT_ready(probe):
    """THE WHOLE REASON THIS IS NOT A LEDGER CHECK. The table exists, every insert succeeds, and
    the migration is recorded as applied — but duplicate enqueues are accepted, so the
    idempotency guarantee is gone while everything reports healthy."""
    c = probe["constraint_missing"]
    assert c["requirements"]["notification_outbox table"] == "ready"
    assert c["requirements"]["event_id uniqueness"] == "not_ready"
    assert c["state"] == "not_ready"
    assert "idempotency is not enforced" in c["evidence"]


def test_capabilities_degrade_independently(probe):
    """The drain needs only the columns, which exist — so it stays ready while enqueue does not.
    A single fleet-wide 'ready' flag could not express that."""
    assert probe["constraint_missing"]["drain_state"] == "ready"


# ── What a check actually establishes ────────────────────────────────────────────────────────

def test_a_green_report_names_the_levels_it_did_NOT_test(probe):
    """A capability whose only level is `schema` is structurally present, not ready to operate.
    Saying so is what stops the first being read as the second."""
    fleet = probe["levels"]["fleet_not_checked"]
    assert "sandbox_write" in fleet
    assert "consumer_enforcement" in fleet


def test_sandbox_write_is_never_part_of_a_routine_probe(probe):
    """It performs a REAL write. A health endpoint that writes on every scrape is a liability,
    not a check — it belongs to an explicitly scoped lifecycle test."""
    assert probe["levels"]["sandbox_write_is_passive"] is False
    assert "sandbox_write" not in probe["levels"]["passive_levels"]


def test_write_readiness_is_not_inferred_from_a_successful_read(probe):
    """The connectivity check says so in its own evidence, because the inference is tempting
    and wrong: a readable table says nothing about who may write to it."""
    pc = probe["privilege_and_connectivity"]
    assert pc["connectivity_level"] == "connectivity"
    assert "NOT evidence of write readiness" in pc["connectivity_evidence"]


def test_privileges_are_their_own_level_separate_from_schema(probe):
    """A present table and a usable table are different facts, and the gap between them
    surfaces at the worst moment — with an entry or a notification already in flight."""
    pc = probe["privilege_and_connectivity"]
    assert pc["privilege_level"] == "privilege"
    assert probe["levels"]["per_capability"]["outbox_enqueue"]["checked"] == [
        "connectivity", "privilege", "schema"]


def test_a_dialect_with_no_privilege_layer_is_NOT_APPLICABLE_not_unverified(probe):
    """SQLite has no GRANT system, so there is nothing to check — different from failing to
    check something that exists. Reporting unknown would permanently degrade every SQLite
    deployment for a property that cannot exist."""
    pc = probe["privilege_and_connectivity"]
    assert pc["privilege_state"] == "ready"
    assert "no role-privilege layer" in pc["privilege_evidence"]


# ── Entry readiness is not exit readiness ─────────────────────────────────────────────────────

def test_unknown_blocks_a_risk_INCREASING_action(probe):
    """Proceeding on an unverified prerequisite is how an unapplied migration silently stamped
    every pending alert as delivered."""
    ok, why = probe["gate"]["unknown_blocks_entry"]
    assert ok is False and why.startswith("unknown")


def test_unknown_does_NOT_block_a_risk_REDUCING_action(probe):
    """Refusing to close a position because a database could not be reached turns a degraded
    state into a trapped one."""
    ok, why = probe["gate"]["unknown_allows_exit"]
    assert ok is True and "REDUCES risk" in why


def test_reconciliation_is_not_gated_by_an_entry_prerequisite(probe):
    """Establishing what actually happened must keep working while entries are blocked —
    otherwise a degraded entry path also blinds the thing that cleans up after it."""
    ok, _ = probe["gate"]["unknown_allows_reconciliation"]
    assert ok is True


def test_a_definite_absence_blocks_an_entry(probe):
    ok, why = probe["gate"]["not_ready_blocks_entry"]
    assert ok is False and why.startswith("not_ready")


def test_a_ready_capability_allows_the_action(probe):
    ok, _ = probe["gate"]["healthy_allows_entry"]
    assert ok is True


def test_exits_and_reconciliation_are_reported_separately_from_entries(probe):
    """An exit or reconciliation blocked by a prerequisite is a far more serious condition than
    a blocked entry, and must not be averaged in with one."""
    e = probe["empty_db"]
    assert "exposure_reservation" in e["entry_blocked"]
    assert "submission_reconciliation" in e["exit_or_reconciliation_blocked"]
    assert "exposure_reservation" not in e["exit_or_reconciliation_blocked"]


def test_degradation_is_reported_in_operator_language(probe):
    """EC-03's lesson: a monitor reading `{"ok": false}` learns that something is wrong but not
    what it costs. Every degraded line must name the consequence."""
    for line in probe["empty_db"]["degraded_in_operator_language"]:
        assert ":" in line and len(line.split(":", 1)[1].strip()) > 20, line


# ── Repair ────────────────────────────────────────────────────────────────────────────────────

def test_pending_work_is_preserved_while_a_prerequisite_is_missing(probe):
    r = probe["repair"]
    assert r["before"] == "deferred"
    assert r["pending_preserved_during_outage"] is True


def test_work_resumes_EXACTLY_ONCE_when_the_prerequisite_returns(probe):
    """The repair path's real risk is not failing to resume — it is resuming twice. A deferral
    that replays its backlog on every subsequent cycle would duplicate every item."""
    r = probe["repair"]
    assert r["after"] == "delivered"
    assert r["delivered"] == ["evt-a", "evt-b"]
    assert r["delivered_exactly_once"] is True
    assert r["pending_left"] == []
