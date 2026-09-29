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


def _run(scenario: str) -> dict:
    proc = subprocess.run(
        [sys.executable, str(_PROBE), scenario],
        capture_output=True, text=True, timeout=180,
    )
    assert proc.returncode == 0, f"probe failed:\n{proc.stdout}\n{proc.stderr}"
    return json.loads(proc.stdout)


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
