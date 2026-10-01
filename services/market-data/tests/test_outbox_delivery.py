"""M20 — durable notification delivery, verified against a real database.

Each test maps to one acceptance requirement. The behaviours all run in `_outbox_probe.py`
(a subprocess, because market-data's conftest stubs `sqlalchemy` and every claim here is about
what the DATABASE does — a UNIQUE constraint rejecting a duplicate, a compare-and-set reporting
rowcount 0 when it loses a race). Asserting those against a mock would assert on the mock.

WHAT IS DELIBERATELY NOT CLAIMED: exactly-once delivery. A crash between the provider accepting
a message and this process recording that fact is indistinguishable, locally, from a crash
before acceptance. The design marks those `unknown` rather than guessing, and
`test_the_system_does_not_claim_exactly_once_delivery` pins that honesty.
"""
import json
import pathlib
import subprocess
import sys

import pytest

_PROBE = pathlib.Path(__file__).resolve().parent / "_outbox_probe.py"


@pytest.fixture(scope="module")
def probe():
    proc = subprocess.run([sys.executable, str(_PROBE)], capture_output=True, text=True,
                          timeout=180)
    if proc.returncode != 0:
        pytest.fail(f"outbox probe failed:\n{proc.stdout}\n{proc.stderr}")
    return json.loads(proc.stdout)


# ── Atomic event/outbox persistence ───────────────────────────────────────────────────────────

def test_a_crash_cannot_commit_an_event_while_losing_its_notification(probe):
    """The outbox's reason for existing. If the domain write could commit while the notification
    did not, the alert is lost with no record that it was ever owed."""
    assert probe["atomicity_crash"] == {"events": 0, "notifications": 0}


def test_a_commit_lands_the_event_and_its_notification_together(probe):
    assert probe["atomicity_commit"] == {"events": 1, "notifications": 1}


# ── Stable notification identity ──────────────────────────────────────────────────────────────

def test_repeated_scans_and_restarts_create_one_logical_notification(probe):
    """Three scan cycles in three separate sessions. A TTL-based dedup answers "have I seen this
    key lately"; a UNIQUE constraint answers "has this been enqueued", and only the second
    survives a restart or an eviction."""
    assert probe["stable_identity"]["rows"] == 1


def test_a_duplicate_enqueue_keeps_the_first_content_and_reports_not_created(probe):
    e = probe["idempotent_enqueue"]
    assert e["created_first"] is True and e["created_second"] is False
    assert e["rows"] == 1 and e["same_row"] is True
    assert e["subject_kept"] == "Subject"


def test_enqueue_requires_an_idempotency_key(probe):
    assert "event_id is required" in probe["blank_event_id"]


# ── Worker ownership ──────────────────────────────────────────────────────────────────────────

def test_an_expired_worker_cannot_acknowledge_another_workers_lease(probe):
    """THE DEFECT THIS CAUGHT. The first version of `mark_accepted` mutated the row's attributes
    unconditionally, so a worker paused past its lease — GC, a slow provider, a loaded host —
    could overwrite the outcome recorded by the worker that legitimately reclaimed and sent it."""
    o = probe["ownership"]
    assert o["lapsed_worker_settled"] is False
    assert o["lease_holder_settled"] is True
    assert o["state"] == "accepted"


def test_only_one_worker_wins_a_contended_claim(probe):
    assert probe["concurrent_claim"] == {"a": 1, "b": 0}


def test_a_live_lease_is_not_stealable_but_an_expired_one_is_reclaimed(probe):
    lease = probe["lease"]
    assert lease["stolen_during"] == 0
    assert lease["reclaimed_after"] == 1
    assert lease["owner"] == "worker-b"
    assert lease["attempts"] == 2, "the reclaim must consume an attempt, not reset the count"


# ── Ambiguous send outcome ────────────────────────────────────────────────────────────────────

def test_a_timeout_after_possible_acceptance_claims_neither_success_nor_failure(probe):
    """Calling it failed invites a duplicate; calling it sent loses the alert. Neither is known,
    so neither is asserted."""
    a = probe["ambiguous"]
    assert a["state"] == "unknown"
    assert a["claims_failure"] is False and a["claims_success"] is False


def test_an_ambiguous_outcome_is_not_retried_automatically(probe):
    """A blind retry would be an unjustified claim that no duplicate can result."""
    assert probe["ambiguous"]["auto_retried"] == 0


def test_the_system_does_not_claim_exactly_once_delivery(probe):
    assert probe["ambiguous"]["exactly_once_promised"] is False
    assert probe["visibility"]["exactly_once_guaranteed"] is False


# ── Preferences and expiry rechecked before sending ───────────────────────────────────────────

def test_preferences_are_rechecked_immediately_before_the_provider_call(probe):
    r = probe["presend_recheck"]
    assert r["opted_out"] == [False, "recipient opted out"]
    assert r["subscribed"] == [True, "subscribed"]


def test_an_unchecked_preference_says_so_rather_than_implying_consent(probe):
    assert probe["presend_recheck"]["unchecked"] == [True, "preference not checked"]


def test_a_failed_preference_lookup_fails_open_and_records_why(probe):
    """Matches this repo's established alert-preference rule: absence of a preference is
    subscribed, and a lookup error must not silently suppress every alert."""
    ok, reason = probe["presend_recheck"]["preference_error_fails_open"]
    assert ok is True and "redis down" in reason


def test_a_notification_that_goes_stale_while_queued_is_not_sent(probe):
    """Valid at enqueue, expired by the time the worker reaches the provider. The gap between
    the two is the whole point of an outbox and is long enough to matter."""
    assert probe["presend_recheck"]["expired_between_enqueue_and_send"] == \
        [False, "expired before send"]


def test_expired_rows_are_terminal_auditable_and_never_attempted(probe):
    e = probe["expiry"]
    assert e["state"] == "expired" and e["expired_count"] == 1
    assert e["attempts"] == 0, "an expired row must not burn an attempt"
    assert "evt-stale" not in e["claimed"] and e["claimed"] == ["evt-live"]
    assert "stale" in e["reason"]


def test_dead_lettered_rows_are_kept_with_their_reason(probe):
    d = probe["dead_letter"]
    assert d["transitions"] == ["pending", "dead_letter"]
    assert d["state"] == "dead_letter" and d["attempts"] == 2
    assert "attempts exhausted" in d["reason"] and d["error"] == "smtp down"


def test_suppressed_is_a_distinct_fact_from_expired_and_dead_letter(probe):
    """"We chose not to send" is neither "too late" nor "could not". Collapsing the three
    misreports every delivery rate computed from them."""
    s = probe["suppressed"]
    assert s["state"] == "suppressed" and s["reclaimed"] == 0
    assert "opted out" in s["reason"]


# ── Honest delivery states ────────────────────────────────────────────────────────────────────

def test_provider_acceptance_leaves_delivery_unobserved(probe):
    """Acceptance is the last thing the sending process can observe. There is deliberately no
    `delivered_at` column to be quietly misread as arrival."""
    d = probe["delivery_states"]
    assert d["accepted_leaves_delivery_unobserved"] == \
        {"state": "accepted", "delivery_status": None}
    assert probe["accepted"]["has_delivered_at"] is False


def test_a_bounce_callback_is_recorded_without_rewriting_acceptance(probe):
    """A bounced message WAS accepted. Both facts are true and both are kept."""
    d = probe["delivery_states"]
    assert d["after_callback"] == {"state": "accepted", "delivery_status": "bounced"}
    assert d["provider_message_id"] == "ses-abc123"


def test_accepted_is_terminal_and_never_reclaimed(probe):
    a = probe["accepted"]
    assert a["state"] == "accepted" and a["reclaimed"] == 0 and a["accepted_at_set"] is True


# ── Safe migration ────────────────────────────────────────────────────────────────────────────

def test_pre_cutover_events_are_recorded_but_never_sent(probe):
    """Redis dedup markers expire on a TTL, so at cutover a backlog of historical events looks
    brand new. Enqueuing them as pending would send a mass of stale email in one sweep."""
    m = probe["safe_migration"]
    assert m["sendable"] == ["live-1"], "only post-cutover events may be delivered"
    assert m["historical_state"] == "suppressed"
    assert m["rows_kept"] == 3, "historical rows are withheld, not discarded"


def test_backfilled_rows_are_labelled_reconstructed_with_no_invented_timestamps(probe):
    m = probe["safe_migration"]
    assert m["reconstructed"] is True
    assert m["historical_accepted_at"] is None


def test_a_historical_key_blocks_a_later_duplicate_enqueue(probe):
    """Holding the event_id is what stops the live path re-queueing the same notification after
    cutover."""
    assert probe["safe_migration"]["historical_key_blocks_reenqueue"] is True


# ── Retry content and backoff ─────────────────────────────────────────────────────────────────

def test_retry_content_is_frozen_at_enqueue(probe):
    """EF-01: a retry that re-rendered its content shipped the threshold in place of the price,
    because the value it read had moved between attempts. A retry is transport, not re-compute."""
    assert probe["frozen_content"] == {"subject": "Price 12.34", "body_text": "Price 12.34"}


def test_backoff_defers_the_next_attempt(probe):
    assert probe["backoff"] == {"immediate": 0, "later": 1}


def test_releasing_a_lease_does_not_refund_the_attempt(probe):
    """Refunding it would let a message that reliably kills its worker at claim time retry
    forever."""
    r = probe["release"]
    assert r["state"] == "pending" and r["owner"] is None
    assert r["attempts_after_claim"] == 1 and r["attempts_after_release"] == 1


# ── Operational visibility ────────────────────────────────────────────────────────────────────

def test_state_counts_reconcile_against_the_table_total(probe):
    """If they do not, a state was written that the module does not know about, and every rate
    derived from these counts is suspect — which the dashboard must show, not average away."""
    v = probe["visibility"]
    assert v["reconciles"] is True
    assert sum(v["by_state"].values()) == v["total"]


def test_queue_age_is_measurable_not_just_queue_depth(probe):
    """A shallow queue whose oldest item is six hours old is worse than a deep one that drains."""
    assert probe["visibility"]["oldest_pending_age_seconds"] == pytest.approx(21600.0)


def test_ambiguous_outcomes_are_counted_separately_from_both_sides(probe):
    v = probe["visibility"]
    assert v["ambiguous_unknown"] == 1
    assert v["provider_accepted"] == 0


def test_unobserved_delivery_is_reported_as_unobserved_not_delivered(probe):
    v = probe["visibility"]
    assert v["delivery_observed"] == 0
    assert v["delivery_unobserved"] == v["total"]
