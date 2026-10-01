"""Outbox concurrency and crash recovery on PostgreSQL — the engine production actually runs.

WHY THIS IS SEPARATE FROM THE SQLITE SUITE. SQLite establishes the state machine but serialises
writers, so it structurally cannot exercise what matters here: genuinely simultaneous claims
from separate connections, two workers racing to settle one row, BIGSERIAL identity, and
transaction conflicts under real MVCC. A claim protocol verified only on SQLite has been
verified on the one engine that cannot contend.

SKIPS unless `OUTBOX_PG_URL` points at a disposable PostgreSQL, so the suite stays runnable
without Docker. To run it:

    docker run -d --name stockai-outbox-pgtest -e POSTGRES_PASSWORD=probe \\
        -e POSTGRES_DB=outboxprobe -p 55432:5432 postgres:16-alpine
    OUTBOX_PG_URL=postgresql+psycopg2://postgres:probe@localhost:55432/outboxprobe \\
        python -m pytest tests/test_outbox_postgres_concurrency.py

The probe DROPS AND RECREATES the public schema, so point it only at a throwaway database.
"""
import json
import os
import pathlib
import subprocess
import sys

import pytest

_PROBE = pathlib.Path(__file__).resolve().parent / "_outbox_pg_probe.py"
_URL = os.environ.get("OUTBOX_PG_URL")

#: Set by the CI job. A SKIPPED concurrency test is indistinguishable from a passing one in a
#: summary line, so in any context that gates a release the absence of a database must FAIL
#: rather than quietly skip — otherwise a broken service container produces a green job that
#: exercised nothing.
_REQUIRED = os.environ.get("OUTBOX_PG_REQUIRED", "").strip().lower() in ("1", "true", "yes")

if _REQUIRED and not _URL:
    raise RuntimeError(
        "OUTBOX_PG_REQUIRED is set but OUTBOX_PG_URL is not. These tests gate a release: "
        "refusing to pass without having exercised PostgreSQL concurrency.")

pytestmark = pytest.mark.skipif(
    not _URL, reason="OUTBOX_PG_URL not set; see this module's docstring to run it")


@pytest.fixture(scope="module")
def pg():
    proc = subprocess.run([sys.executable, str(_PROBE)], capture_output=True, text=True,
                          timeout=300, env={**os.environ, "OUTBOX_PG_URL": _URL})
    if proc.returncode != 0:
        pytest.fail(f"postgres probe failed:\n{proc.stdout}\n{proc.stderr}")
    # The probe imports the real trading engine for the mixed-writer scenario, whose structlog
    # writes to stdout. Parse from the delimiter rather than assuming stdout is only JSON.
    marker = "===PROBE_JSON==="
    if marker not in proc.stdout:
        pytest.fail(f"probe produced no result block:\n{proc.stdout[-3000:]}\n{proc.stderr[-2000:]}")
    data = json.loads(proc.stdout.split(marker, 1)[1])
    if data.get("skipped"):
        if _REQUIRED:
            pytest.fail(f"probe skipped while required: {data.get('reason')}")
        pytest.skip(data.get("reason", "probe skipped"))
    return data


def test_bigserial_identity_is_assigned(pg):
    """SQLite needed an Integer variant for the BigInteger key. This confirms the production
    type works on the production engine, rather than inferring it from the variant."""
    assert pg["bigint_identity"]["id_assigned"] is True


def test_simultaneous_workers_never_double_claim_a_row(pg):
    """Eight threads on eight connections against twenty rows. The compare-and-set must hold
    under real contention, not just under SQLite's serialised writers."""
    c = pg["concurrent_claims"]
    assert c["double_claimed"] == 0
    assert c["distinct_claimed"] == c["total_claimed"] == c["rows"]
    assert c["transaction_errors"] == []


def test_a_contended_claim_consumes_exactly_one_attempt_per_row(pg):
    """A losing claim must not increment attempts — otherwise contention alone would burn a
    row's retry budget."""
    assert pg["concurrent_claims"]["max_attempts_on_any_row"] == 1


def test_only_the_lease_holder_can_settle_under_contention(pg):
    """Three impostors racing the holder. Exactly one write may land."""
    s = pg["concurrent_settle"]
    assert s["winners"] == ["holder"]
    assert s["final_state"] == "accepted"
    assert all("error" not in r for r in s["results"]), "contention must not raise"


def test_lease_expiry_is_respected_on_postgres(pg):
    lease = pg["lease_expiry"]
    assert lease["stolen_during"] == 0
    assert lease["reclaimed_after"] == 1 and lease["owner"] == "second"


def test_concurrent_enqueue_of_one_event_yields_exactly_one_row(pg):
    """The UNIQUE constraint is the idempotency. Eight simultaneous enqueues, one winner, seven
    readers of the winner's row, and no unhandled integrity error."""
    e = pg["concurrent_enqueue"]
    assert e["rows"] == 1
    assert e["created_true"] == 1 and e["created_false"] == 7
    assert e["errors"] == []


def test_crash_recovery_quarantines_instead_of_resending(pg):
    c = pg["crash_recovery"]
    assert c["reclaimed_without_sweep"] == 0
    assert c["quarantined"] == 1 and c["state"] == "unknown"


def test_stats_reconcile_on_postgres(pg):
    assert pg["stats"]["reconciles"] is True


# ── M15 exposure reservation — the guarantees SQLite cannot establish ─────────────────────────

def test_an_aborted_transaction_claims_no_exposure(pg):
    """On SQLite this cannot be tested at all: pysqlite does not open a transaction for DML, so
    a released SAVEPOINT is already durable. Here the rollback is real."""
    r = pg["reservation_rollback"]
    assert r["reserve_reason"] == "reserved"
    assert r["rows_after_rollback"] == 0
    assert r["reserved_after_rollback"] == 0.0


def test_concurrent_reservations_cannot_jointly_exceed_the_cap(pg):
    """THE GUARANTEE THE WHOLE RESERVATION EXISTS FOR, under real contention. Eight threads on
    eight connections each claim 4% of equity against a 15% sector cap. At most three can fit.

    SQLite serialises writers, so it would pass this by accident; PostgreSQL does not, which is
    why the portfolio row lock has to be real."""
    c = pg["concurrent_reservation"]
    assert c["errors"] == []
    assert c["granted"] == 3 and c["refused"] == 5
    assert c["within_cap"] is True
    assert c["total_reserved_value"] <= c["cap_value"]


def test_the_same_intent_reserved_concurrently_claims_exposure_once(pg):
    """A retry of one proposed entry must not reserve twice. Six simultaneous attempts, one
    winner, five told it is already reserved, and a single row's worth of exposure claimed."""
    d = pg["idempotent_intent"]
    assert d["rows"] == 1
    assert d["outcomes"].count("reserved") == 1
    assert d["outcomes"].count("already_reserved") == 5
    assert d["reserved_value"] == 1000.0


# ── Mixed-writer integration: the two PRODUCTION entry paths against each other ───────────────

def test_organic_and_conditional_entries_contend_correctly_at_the_cap(pg):
    """Racing two `reserve()` calls shows the PRIMITIVE works. It does not show the two
    production writers are protected: `_scan_for_entries` and `conditional_orders._execute_buy`
    are different modules that build their own snapshots and could reach the cap by different
    routes.

    This races the real organic entry against the real `_execute_buy` — each sized at ~10,010
    against a 15,000 cap, so only one can fit — and requires the surviving positions to respect
    the cap."""
    m = pg["mixed_writer"]
    assert all("error" not in r for r in m["results"]), m["results"]
    paths = {r["path"]: r for r in m["results"]}
    assert paths["organic"]["opened"] is True
    assert paths["conditional"]["opened"] is False
    assert "sector_cap" in paths["conditional"]["reason"]
    assert m["within_cap"] is True
    assert m["combined_value"] <= m["cap_value"]


def test_both_paths_carry_the_reservation_through_trade_creation(pg):
    """The handover must leave neither an accounting gap nor a double count: every reservation
    ends terminal, every consumed one points at a real open trade, and none is left `reserved`
    holding capacity nothing will use."""
    m = pg["mixed_writer"]
    assert m["none_left_reserved"] is True
    assert m["consumed_point_at_open_trades"] is True
    assert m["reconcile"]["reconciles"] is True
    assert m["reconcile"]["consumed"] == m["positions_opened"]
    assert m["reconcile"]["consumed_without_open_trade"] == []


def test_crash_reclamation_cannot_free_capacity_mid_commit(pg):
    """A reservation marked `committing` — the trade row is being written, or its broker outcome
    is unknown — must NEVER be reclaimed by the expiry sweep. Releasing capacity there is how
    the cap gets exceeded by the mechanism meant to enforce it.

    Swept an hour past its TTL: still held, a new entry is still refused, and it is surfaced for
    review rather than silently released."""
    c = pg["committing_not_reclaimed"]
    assert c["swept_by_expiry"] == 0
    assert c["state"] == "committing"
    assert c["capacity_still_held"] == 14000.0
    assert c["new_entry_blocked"] == "sector_cap"
    assert "pg-commit" in c["surfaced_for_review"]
