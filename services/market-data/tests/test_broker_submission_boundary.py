"""M25-BROKER-COMMIT-BOUNDARY — a broker order must not be placed from an uncommitted transaction.

THE DEFECT, measured 2026-10-01. `_place_broker_entry` is called from inside
`_open_paper_trade`, which contains no `session.commit()` — the caller commits afterwards. So a
real order was submitted from within an uncommitted transaction, and a crash between the broker
accepting and that commit left an accepted order with **no local trade row at all**, plus an
exposure reservation that reverts and expires: capacity released for exposure that exists at the
broker.

Pre-existing. The exposure-reservation work did not cause it; asking what happens to capacity
across that crash is what exposed it.

These run against a real database in a subprocess (`_concentration_probe.py`) because every
claim is about what survives a commit boundary.

THE ROLLOUT FLAG DEFAULTS OFF. Switching it changes WHERE a real order is placed relative to the
commit — a live execution-ordering change, which belongs to a person rather than to a deploy.
"""
import json
import pathlib
import subprocess
import sys

import pytest

_PROBE = pathlib.Path(__file__).resolve().parent / "_concentration_probe.py"


@pytest.fixture(scope="module")
def probe():
    proc = subprocess.run([sys.executable, str(_PROBE)], capture_output=True, text=True,
                          timeout=300)
    if proc.returncode != 0:
        pytest.fail(f"probe failed:\n{proc.stdout[-3000:]}\n{proc.stderr[-3000:]}")
    return json.loads(proc.stdout)


# ── The flag ──────────────────────────────────────────────────────────────────────────────────

def test_the_new_ordering_is_off_unless_explicitly_enabled(probe):
    """Absent, or no client at all, reads as OFF — the historical inline behaviour. Deploying
    this module therefore changes nothing about how orders are placed."""
    f = probe["broker_flag"]
    assert f["absent"] is False
    assert f["no_client"] is False
    assert f["set"] is True


# ── Durable intent before anything is sent ────────────────────────────────────────────────────

def test_the_intent_is_recorded_and_committed_before_the_broker_is_contacted(probe):
    """The whole fix in one assertion: the local row exists, says `pending`, and carries no
    order id — so if the process dies here, nothing was sent and nothing is lost."""
    i = probe["broker_intent"]
    assert i["state"] == "pending"
    assert i["order_id"] is None
    assert i["claimable"] == 1


def test_a_successful_submission_records_the_order_exactly_once(probe):
    s = probe["broker_submit_ok"]
    assert s["state"] == "submitted" and s["order_id"] is not None
    assert s["calls"] == 1 and s["attempts"] == 1
    assert s["batch"]["submitted"] == 1


# ── The ambiguous outcome ─────────────────────────────────────────────────────────────────────

def test_a_timeout_after_possible_acceptance_is_unknown_and_never_auto_retried(probe):
    """A blind retry is precisely how a DUPLICATE REAL ORDER gets placed. The call count must
    stay at one across a second pass."""
    t = probe["broker_timeout"]
    assert t["batch"]["unknown"] == 1
    assert t["retried"] == 0, "an unknown outcome must not be re-claimed"
    assert t["calls"] == 1, "the broker must not be contacted a second time"
    assert t["awaiting_reconciliation"] >= 1


def test_a_worker_that_died_mid_call_is_reconciled_not_resubmitted(probe):
    """A row stuck in `submitting` may already correspond to a real order. Only the broker's own
    record can say, so it is surfaced rather than guessed at."""
    st = probe["broker_stuck_submitting"]
    assert st["reclaimed"] is False
    assert st["surfaced_for_reconciliation"] is True


# ── Definite failures stay retryable ──────────────────────────────────────────────────────────

def test_a_definite_rejection_is_retryable_and_bounded(probe):
    """A rejection is known not to have been accepted, so retrying it cannot duplicate anything
    — but the attempt count still bounds it."""
    r = probe["broker_reject"]
    assert r["batch"]["failed"] == 1
    assert r["failed_rows"] == 1
    assert r["attempts"] == 1


def test_a_call_that_returns_without_an_order_id_is_a_FAILURE_not_a_success(probe):
    """THE SUBTLE ONE. The historical `_place_broker_entry` swallows its own errors and falls
    back to the simulated entry, so returning cleanly does NOT mean an order was placed.
    Treating that as success would mark a trade `submitted` with no order behind it."""
    s = probe["broker_silent_fallback"]
    assert s["state"] == "failed"
    assert s["order_id"] is None
    assert "without an order id" in s["error"]


# ── What is deliberately not claimed ──────────────────────────────────────────────────────────

def test_the_module_does_not_promise_duplicate_free_submission():
    """A crash between the broker accepting and this process recording it is locally
    indistinguishable from a crash before the call. The window is narrowed, made detectable and
    auditable — not eliminated — and the docstring has to keep saying so."""
    src = (pathlib.Path(__file__).resolve().parents[1] / "src" / "services"
           / "broker_submission.py").read_text()
    assert "WHAT IS NOT PROMISED" in src
    assert "never retried automatically" in src
