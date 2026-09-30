"""EC-01 (2026-09-29 email-fix closure review): a row-mutating migration must run exactly once,
not on every service startup.

THE DEFECT, AND WHY IT WAS WORSE THAN NOT MIGRATING AT ALL. The EF-03 remediation added a legacy
price-alert closeout to `_apply_isolated_ddl()`'s statement list:

    UPDATE price_alerts SET last_sent_at = triggered_at
    WHERE triggered IS TRUE AND last_sent_at IS NULL AND triggered_at IS NOT NULL

carrying a comment that called it "one-shot: after this runs there are no NULL-timestamped
triggered rows left to match". That is a claim about data, and data changes. `init_db()` calls
`_apply_isolated_ddl()` on EVERY startup of every one of the twelve backend services, and a
NULL-timestamped triggered row is exactly what a FAILED SEND looks like — producing them is the
entire purpose of the retry mechanism the same remediation introduced.

So the next restart of any service would have stamped every genuinely-pending alert as
delivered, with zero transport calls, and the retry query (which requires `last_sent_at IS NULL`)
would never see it again. The retry feature would have looked implemented and been inert after
the first restart, with no error anywhere.

Measured against production on 2026-09-29, before this fix: 0 rows were in that pending state
and 60 carried the legacy closeout stamp, so the latent defect had not yet destroyed anything.

These tests run the REAL `_apply_one_shot_migrations()` against a REAL database in a subprocess
(`_ec01_migration_probe.py`) — see that file for why the suite's own process cannot.
"""
import json
import pathlib
import subprocess
import sys

import pytest

_PROBE = pathlib.Path(__file__).resolve().parent / "_ec01_migration_probe.py"
_MIGRATION = "2026-09-28-legacy-price-alert-delivery-closeout"


# ── EC-03: migration readiness ──────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def readiness():
    return _run("readiness")


def test_a_clean_process_reports_its_migrations_ok(readiness):
    assert readiness["state_after_success"]["ok"] is True
    assert readiness["state_after_success"]["failed"] == []


def test_an_applied_migration_can_be_verified_by_a_dependent_job(readiness):
    """The question `check_price_alerts` asks before running its retry. Its premise is that the
    legacy closeout ran; if it did not, every pre-cutoff legacy row is indistinguishable from a
    failed send and the job would re-deliver alerts that already went out."""
    assert readiness["applied_true"] is True


def test_an_unknown_migration_is_reported_as_not_applied(readiness):
    assert readiness["applied_unknown_name"] is False


def test_a_failure_survives_the_function_that_produced_it(readiness):
    """THE READINESS GAP. `_apply_once` used to print and return, and /health reported "ok"
    unconditionally — so a service whose migration never applied started, passed its healthcheck,
    and ran every job that depended on it. Process startup is not proof of prerequisites."""
    state = readiness["state_after_failure"]
    assert state["ok"] is False
    assert "probe-broken" in state["failed"]
    assert state["detail"]["probe-broken"]


def test_a_failed_migration_reports_as_not_applied(readiness):
    assert readiness["applied_failed"] is False


def test_an_unanswerable_question_returns_None_not_a_guess(readiness):
    """THE DISTINCTION THE GATE DEPENDS ON. With no ledger table the answer is unknown, and
    `check_price_alerts` treats unknown exactly like "not applied" — it skips the retry. Returning
    True here would let the retry run on an unverified prerequisite and re-deliver old alerts;
    returning False would be a definite claim the code cannot support.

    An earlier version of this file had no test for the unreachable case at all, and a sabotage
    that returned True walked straight through it."""
    assert readiness["applied_unknown_no_ledger"] is None


def test_a_recorded_failure_answers_the_question_even_with_no_ledger(readiness):
    """The recorded failure is why `migration_applied` checks it BEFORE querying. Without that
    branch this falls through to a query that cannot run, and a definite "it failed" degrades
    into "unknown"."""
    assert readiness["applied_recorded_failure_no_ledger"] is False


def test_a_repaired_migration_clears_the_recorded_failure(readiness):
    """A process must not stay permanently marked broken once the migration actually applies —
    otherwise the readiness signal is unusable after any transient database problem."""
    assert readiness["state_after_repair"]["ok"] is True
    assert readiness["state_after_repair"]["failed"] == []


def _run(scenario: str) -> dict:
    proc = subprocess.run(
        [sys.executable, str(_PROBE), scenario],
        capture_output=True, text=True, timeout=180,
    )
    assert proc.returncode == 0, f"probe failed:\n{proc.stdout}\n{proc.stderr}"
    # The probe emits a marker before its JSON: a scenario that deliberately fails a migration
    # also produces `_apply_once`'s own stdout warning ahead of it.
    marker = "---PROBE-JSON---"
    assert marker in proc.stdout, f"probe produced no payload:\n{proc.stdout}\n{proc.stderr}"
    return json.loads(proc.stdout.split(marker, 1)[1])


def _by_symbol(rows):
    return {r["symbol"]: r for r in rows}


@pytest.fixture(scope="module")
def restart():
    return _run("restart")


def test_the_legacy_row_is_still_closed_out(restart):
    """The migration's actual job, unchanged. A pre-watermark triggered row with no send
    timestamp is stamped from its own trigger time, which marks it not-retryable."""
    legacy = _by_symbol(restart["after_first_init"])["LEGACY"]
    assert legacy["last_sent_at"] == legacy["triggered_at"]


def test_a_pending_alert_survives_a_restart(restart):
    """THE REGRESSION TEST. A send that failed AFTER the fix must still be pending after another
    service starts up. Before this change the restart consumed it, with no transport call and no
    log line — the notification simply stopped existing."""
    pending = _by_symbol(restart["after_restart"])["PENDING"]
    assert pending["last_sent_at"] is None, \
        "a restart consumed a genuinely undelivered notification"


def test_the_restart_does_not_disturb_the_already_closed_legacy_row(restart):
    legacy = _by_symbol(restart["after_restart"])["LEGACY"]
    assert legacy["last_sent_at"] == legacy["triggered_at"]


def test_the_ledger_records_the_migration_exactly_once(restart):
    assert restart["ledger_after_first"] == [_MIGRATION]
    assert restart["ledger_after_restart"] == [_MIGRATION]


def test_repeated_restarts_never_consume_the_pending_row():
    """One restart is the reported counterexample; a service fleet restarts far more often than
    that, and a guard that only holds for the second run is not a guard."""
    out = _run("many_restarts")
    assert _by_symbol(out["after_restart"])["PENDING"]["last_sent_at"] is None
    assert out["ledger_after_restart"] == [_MIGRATION]


def test_the_statement_does_not_run_a_second_time_at_all():
    """Isolates the LEDGER from the watermark, which otherwise masks it.

    Every other test here passes with the ledger guard deleted, because the watermark alone
    already excludes the realistic pending row. That makes those tests a check on the watermark,
    not on the once-only claim. Here a second PRE-watermark row appears after the migration has
    run: the watermark clause would match it happily, so only the ledger can leave it alone —
    which is the actual contract, that a row-mutating statement executes once per database and
    never again.
    """
    out = _run("late_legacy")
    late = _by_symbol(out["after_restart"])["LATE_LEGACY"]
    assert late["last_sent_at"] is None, \
        "the migration ran a second time; the ledger is not holding"


def test_a_failed_migration_does_not_leave_its_name_claimed():
    """A failure must be retryable, or it becomes permanent.

    If the ledger kept the claim after the statement raised, the ledger would report "already
    applied" forever and the statement would never get a second chance — a migration that
    silently never ran, which is the same failure shape as EC-01 itself one level along.

    This holds only because the INSERT and the statement share ONE transaction. That is easy to
    lose in a later edit — claim in one `with engine.begin()`, run in the next — and nothing else
    in this suite would notice, which is why it gets its own test.

    Raised by the independent EC closure verification, which tested it before I did.
    """
    out = _run("failed_then_retry")
    assert "probe-failing-migration" not in (out["ledger_after_failure"] or []), \
        "a failed statement left its name claimed; the migration can never run again"


def test_a_migration_that_failed_once_still_runs_on_the_retry():
    """The other half: rolling the claim back is only useful if the retry actually applies."""
    out = _run("failed_then_retry")
    assert "probe-failing-migration" in (out["ledger_after_retry"] or [])
    target = _by_symbol(out["after_restart"])["RETRY_TARGET"]
    assert target["last_sent_at"] == target["triggered_at"], \
        "the retry claimed the ledger but its statement did not run"


def test_the_watermark_protects_the_row_even_if_the_ledger_is_lost():
    """Defence in depth, and the reason the fix is not the ledger alone. The two guards are
    independent: the ledger stops the statement running twice, the watermark stops it touching a
    post-fix row even if it somehow does. A restored-from-backup or hand-repaired database is a
    real way to lose a ledger table, and losing it must not cost a notification."""
    out = _run("ledger_lost")
    assert _by_symbol(out["after_restart"])["PENDING"]["last_sent_at"] is None, \
        "with the ledger gone, the watermark must still exclude the post-fix pending row"


def test_the_legacy_stamp_stays_distinguishable_from_a_real_send(restart):
    """The closure review asked for legacy uncertainty to remain visible rather than being
    indistinguishable from delivery evidence. `last_sent_at == triggered_at` exactly is that
    marker: a real send stamps strictly later than the trigger, so equality identifies the
    closed-out legacy set without a schema change."""
    legacy = _by_symbol(restart["after_restart"])["LEGACY"]
    assert legacy["last_sent_at"] == legacy["triggered_at"]
