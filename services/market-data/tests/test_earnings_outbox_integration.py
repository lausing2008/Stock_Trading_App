"""M20's first bounded integration — earnings release-phase alerts end to end.

The full path with a FAKE provider: release -> event -> outbox -> claim -> pre-send recheck ->
dispatch -> provider -> recorded acceptance, plus failures, crashes and restarts. Runs in a
subprocess against a real database (`_earnings_outbox_probe.py`) because every claim here is
about what the database does under contention and restart.

ROLLOUT IS OFF BY DEFAULT. Nothing in this file activates a delivery path in production.
"""
import json
import pathlib
import subprocess
import sys

import pytest

_PROBE = pathlib.Path(__file__).resolve().parent / "_earnings_outbox_probe.py"


def _function_body(name: str) -> str:
    """One top-level function from scheduler.py, sliced on real boundaries.

    Slicing to the next `\ndef ` is wrong when a function runs long — it silently swallows the
    next one. That mistake produced a false finding earlier in this work.
    """
    src = (pathlib.Path(__file__).resolve().parents[1] / "src" / "services"
           / "scheduler.py").read_text()
    lines = src.split("\n")
    starts = {i: ln for i, ln in enumerate(lines) if ln.startswith("def ")}
    i = next(k for k, ln in starts.items() if ln.startswith(f"def {name}("))
    j = next((k for k in sorted(starts) if k > i), len(lines))
    return "\n".join(lines[i:j])


@pytest.fixture(scope="module")
def probe():
    proc = subprocess.run([sys.executable, str(_PROBE)], capture_output=True, text=True,
                          timeout=180)
    if proc.returncode != 0:
        pytest.fail(f"earnings outbox probe failed:\n{proc.stdout}\n{proc.stderr}")
    return json.loads(proc.stdout)


# ── Rollout flag ──────────────────────────────────────────────────────────────────────────────

def test_rollout_is_off_unless_explicitly_enabled(probe):
    """Absent, unrecognised, unreachable or no client — all OFF. An unknown configuration is
    never an implicit opt-in to a new delivery path."""
    r = probe["rollout"]
    assert r["default_when_absent"] == "off"
    assert r["unknown_value"] == "off"
    assert r["redis_down"] == "off"
    assert r["none_client"] == "off"


def test_legacy_and_outbox_delivery_are_mutually_exclusive(probe):
    """A cutover double-sends when both paths deliver. There is no mode in which they do."""
    for mode, flags in probe["rollout"]["exclusive"].items():
        assert not (flags["legacy"] and flags["outbox"]), f"{mode} would deliver twice"
        assert flags["legacy"] or flags["outbox"], f"{mode} would deliver nothing"


# ── Requirement 6: the complete path ──────────────────────────────────────────────────────────

def test_the_complete_path_runs_release_to_recorded_acceptance(probe):
    h = probe["happy_path"]
    assert h["disposition"] == "queued" and h["created"] is True
    assert h["batch"]["claimed"] == 1 and h["batch"]["accepted"] == 1
    assert h["state"] == "accepted" and h["accepted_at_set"] is True
    assert h["provider_calls"] == ["stockai:early_earnings_news:7:MU:2026-09-30:results"]


def test_acceptance_still_leaves_delivery_unobserved(probe):
    assert probe["happy_path"]["delivery_status"] is None


def test_the_event_id_matches_the_legacy_dedup_key_shape(probe):
    """One-to-one with the legacy marker, so "has the old path handled this" is answerable
    without a heuristic."""
    assert probe["happy_path"]["event_id"].startswith("stockai:early_earnings_news:")


# ── Requirement 1: preference errors DEFER ────────────────────────────────────────────────────

def test_a_preference_lookup_error_defers_and_sends_nothing(probe):
    """The provider is never called. Logging the error and sending anyway is an unenforced
    opt-out."""
    d = probe["pref_error_defers"]
    assert d["batch"]["deferred"] == 1 and d["batch"]["accepted"] == 0
    assert d["provider_calls"] == [], "nothing may reach the provider on unknown consent"
    assert d["state"] == "pending"


def test_a_deferral_does_not_consume_a_send_attempt(probe):
    """An hour of preference-source downtime must not exhaust max_attempts and dead-letter a
    backlog of perfectly deliverable alerts."""
    d = probe["pref_error_defers"]
    assert d["attempts"] == 0 and d["defers"] == 1
    assert d["available_at_moved"] is True


def test_delivery_resumes_once_the_preference_source_returns(probe):
    a = probe["pref_error_defers"]["after_recovery"]
    assert a["batch"]["accepted"] == 1 and a["state"] == "accepted"


def test_endless_deferral_dead_letters_with_the_real_cause(probe):
    """Deferring forever would strand the alert as surely as dropping it, and silently."""
    c = probe["defer_cap"]
    assert c["state"] == "dead_letter" and c["defers"] == 3
    assert "without establishing consent" in c["reason"]
    assert c["provider_calls"] == []


# ── Requirement 2: preferences and expiry rechecked at send time ──────────────────────────────

def test_an_opt_out_between_enqueue_and_send_suppresses(probe):
    g = probe["gating"]
    assert g["results_optedout"] == "suppressed"


def test_a_notification_that_goes_stale_while_queued_expires_instead_of_sending(probe):
    g = probe["gating"]
    assert g["guidance_expired"] == "expired"
    assert g["provider_calls"] == []


# ── Requirement 3: the acceptance/crash gap ───────────────────────────────────────────────────

def test_a_crash_after_provider_acceptance_is_not_blindly_resent(probe):
    """THE CASE THIS DESIGN EXISTS FOR. The provider accepted; the process died before
    recording it. A reclaiming worker sees only "leased, lease lapsed" — identical to a crash
    before the call — so without the committed dispatch marker it would resend a message the
    recipient already has."""
    c = probe["crash_gap"]
    assert c["batch"]["quarantined"] == 1
    assert c["batch"]["claimed"] == 0, "an in-flight row must not re-enter the claim path"
    assert c["state"] == "unknown"
    assert len(c["provider_calls"]) == 1, "the message must not be sent a second time"


def test_an_uncertain_attempt_names_why_it_is_uncertain(probe):
    assert "provider may have accepted" in probe["crash_gap"]["reason"]


# ── Requirement 5: unknown outcomes are reviewable, not stranded ──────────────────────────────

def test_uncertain_outcomes_enter_a_review_queue(probe):
    """Never auto-retrying avoids duplicates but strands the notification unless something
    surfaces it for a decision."""
    assert probe["crash_gap"]["awaiting_review"] == 1
    assert probe["review"]["before"]["stats_awaiting"] == 1
    assert probe["review"]["before"]["age"] is not None


def test_reconciliation_requires_external_evidence(probe):
    """A bare verdict is a guess wearing a decision's clothes."""
    assert "requires evidence" in probe["review"]["evidence_required"]


def test_reconciliation_requires_an_accountable_actor(probe):
    """It is a human overriding the system's own record. "The state changed" is not a trail."""
    assert "requires an actor" in probe["review"]["actor_required"]


def test_reconciliation_records_actor_prior_state_and_evidence(probe):
    r = probe["review"]
    assert r["actor"] == "ops:sing"
    assert r["from_state"] == "unknown"
    assert "SES delivery log" in r["evidence_stored"]


def test_reconciliation_cannot_requeue_and_resend_stale_content(probe):
    """There is deliberately no 'retry' resolution: the content may be expired, and re-queueing
    it would resend stale mail under cover of an audit action."""
    assert "no 'retry' resolution" in probe["review"]["retry_resolution_rejected"]


def test_a_repeated_or_conflicting_reconciliation_is_rejected(probe):
    """Idempotent by refusal, not by overwrite. The first decision and its evidence stand."""
    r = probe["review"]
    assert r["repeat_allowed"] is not True
    assert r["conflicting_allowed"] is not True
    assert r["state_after_repeat_attempts"] == "accepted"
    assert r["actor_unchanged"] == "ops:sing"


def test_a_reconciled_acceptance_is_marked_reconstructed_not_observed(probe):
    """The acceptance instant is unknown, so no send timestamp is invented for it."""
    r = probe["review"]
    assert r["state"] == "accepted" and r["reconciled"] is True
    assert r["reconstructed"] is True
    assert r["still_awaiting"] == 0


# ── Requirement 4: cutover against existing phase markers ─────────────────────────────────────

def test_an_event_the_legacy_path_already_handled_is_not_resent(probe):
    c = probe["cutover"]
    assert c["dispositions"]["legacy_marker"] == "legacy_already_sent"
    assert c["states"]["results"] == "suppressed"


def test_an_unreadable_legacy_marker_defers_rather_than_suppressing(probe):
    """CORRECTED 2026-10-01. The first version SUPPRESSED on an unreadable marker, reasoning
    that withholding beats a possible duplicate. That is right as a momentary choice and wrong
    as a permanent verdict: one second of Redis being unreachable would have cancelled a real
    alert forever, recorded as though delivery had been confirmed.

    "We could not tell" is not "already delivered". The row stays deliverable, is re-checked
    before every send attempt, and defers while the answer is unknown."""
    c = probe["cutover"]
    assert c["dispositions"]["marker_unknown"] == "legacy_state_unknown_pending"
    assert c["unknown_marker_state"] == "pending", "must remain deliverable"
    assert c["batch"]["deferred_unknown_marker"] == 1
    assert c["unknown_marker_defers"] == 1
    assert "cannot rule out a duplicate" in c["unknown_marker_error"]


def test_a_marker_that_never_becomes_readable_ends_in_the_review_queue_not_silence(probe):
    """Deferral is bounded by the same cap as any other: it cannot defer forever unnoticed."""
    assert probe["defer_cap"]["state"] == "dead_letter"


def test_pre_cutover_events_are_recorded_reconstructed_and_never_sent(probe):
    c = probe["cutover"]
    assert c["dispositions"]["pre_cutover"] == "pre_cutover"
    assert c["states"]["preview"] == "suppressed"
    assert c["pre_cutover_reconstructed"] is True


def test_only_the_post_cutover_event_reaches_the_provider(probe):
    c = probe["cutover"]
    assert c["provider_calls"] == ["stockai:early_earnings_news:7:MU:2026-09-30:call"]
    assert c["rows_kept"] == 4, "withheld events are kept, not discarded"


def test_a_withheld_event_still_blocks_a_later_duplicate_enqueue(probe):
    r = probe["cutover"]["reenqueue_blocked"]
    assert r["created"] is False and r["disposition"] == "already_enqueued"


# ── Restart and provider outcomes ─────────────────────────────────────────────────────────────

def test_a_worker_restart_resumes_without_duplicating(probe):
    r = probe["restart"]
    assert r["unique_sends"] == r["total_sends"] == 3
    assert [s[1] for s in r["states"]] == ["accepted"] * 3


def test_a_provider_timeout_becomes_unknown_not_failed(probe):
    t = probe["provider_outcomes"]["timeout"]
    assert t["state"] == "unknown" and t["batch"]["unknown"] == 1


def test_a_provider_rejection_is_a_definite_failure_and_stays_retryable(probe):
    r = probe["provider_outcomes"]["reject"]
    assert r["state"] == "pending" and r["batch"]["failed"] == 1


# ── Attribution stays separate ────────────────────────────────────────────────────────────────

def test_delivery_state_never_feeds_trading_eligibility():
    """THE ATTRIBUTION CONTRACT, stated as the thing that would actually go wrong.

    An earlier version of this test asserted the absence of a column named like "verified",
    which is near-worthless: nobody was going to add one, and a differently-named field would
    have passed. The meaningful contract is directional — **delivery acceptance must never
    promote classification confidence or trading eligibility** — so this asserts that the
    entry-decision path does not consult outbox or delivery state at all.

    If an engine ever reads `accepted_at`, `delivery_status` or the outbox to decide an entry,
    a mail-server outage becomes a trading signal.
    """
    src = pathlib.Path(__file__).resolve().parents[1] / "src" / "services"
    engine = (src / "paper_trading_engine.py").read_text()
    for forbidden in ("notification_outbox", "NotificationOutbox", "delivery_status",
                      "accepted_at", "earnings_outbox"):
        assert forbidden not in engine, (
            f"paper_trading_engine references {forbidden!r}: delivery state would be feeding "
            f"an entry decision, making a mail outage a trading input")


def test_the_outbox_producer_does_not_import_trading_or_decision_modules():
    """The dependency may only point one way. An alert path that can reach the trading engine
    is an alert path that can change a position."""
    mod = (pathlib.Path(__file__).resolve().parents[1] / "src" / "services"
           / "earnings_outbox.py").read_text()
    for forbidden in ("paper_trading_engine", "decision_engine", "hard_rejects",
                      "_should_enter", "signals"):
        assert forbidden not in mod, f"earnings_outbox imports/references {forbidden!r}"


def test_trading_eligibility_is_identical_whether_notifications_work_or_not():
    """THE BEHAVIOUR the import/source restrictions exist to guarantee.

    Those checks assert the entry path does not NAME outbox or delivery state; they could still
    be satisfied by an indirect call. This runs the real entry path twice with identical trading
    inputs — once normally, once with `send_email` raising and `common.outbox` replaced by an
    object whose every attribute access raises — and requires the eligibility decision, the
    share count and the fill price to match exactly.

    If they ever diverge, a mail-server outage has become a trading input.

    Runs inside `_concentration_probe.py` because it needs the real engine against a real
    database; market-data's conftest stubs sqlalchemy."""
    import json as _json
    import subprocess as _sp
    import sys as _sys
    probe_path = pathlib.Path(__file__).resolve().parent / "_concentration_probe.py"
    proc = _sp.run([_sys.executable, str(probe_path)], capture_output=True, text=True,
                   timeout=300)
    if proc.returncode != 0:
        pytest.fail(f"concentration probe failed:\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}")
    r = _json.loads(proc.stdout)["eligibility_parity_under_notification_outage"]
    assert r["identical_eligibility"] is True, (
        f"eligibility changed when notification infrastructure was unavailable: "
        f"{r['healthy']} vs {r['notifications_unavailable']}")
    assert r["healthy"]["opened"] is True, "the parity fixture must actually open a trade"


def test_classification_is_carried_as_a_label_not_a_verdict(probe):
    """`phase` travels through so delivery is idempotent per label. Acceptance records that the
    provider took the message — never that the right release was identified."""
    assert probe["attribution"]["module_doc_disclaims"] is True
    assert probe["attribution"]["accepted_means"] == "the provider took this message"


# ── M20 cutover wiring: the flag is the single switch ─────────────────────────────────────────

_SCHED = (pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py").read_text()


def _drain_job_kwargs() -> dict:
    """The drain job's registration, parsed as VALUES.

    Asserting `"minutes=1" in source` is the defect T401 exists to prevent: that substring
    survives the interval being changed to `minutes=10`. Parsing the call gives the integer, so
    the assertion is about the schedule rather than about the text that describes it."""
    import ast
    tree = ast.parse(_SCHED)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        kw = {k.arg: k.value for k in node.keywords if k.arg}
        tgt = kw.get("id")
        if isinstance(tgt, ast.Constant) and tgt.value == "earnings_outbox_drain":
            out = {k: v.value for k, v in kw.items() if isinstance(v, ast.Constant)}
            out["_func"] = (node.args[0].id if node.args and isinstance(node.args[0], ast.Name)
                            else None)
            out["_trigger"] = (node.args[1].value
                               if len(node.args) > 1 and isinstance(node.args[1], ast.Constant)
                               else None)
            return out
    raise AssertionError("the drain job is not registered in start_scheduler()")


def test_the_drain_job_is_registered_unconditionally():
    """Registered whatever the flag says, so the cutover is a FLAG CHANGE rather than a deploy.
    A job that only comes into existence once the flag moves cannot be observed working
    beforehand — which is the whole point of having a shadow mode."""
    kw = _drain_job_kwargs()
    assert kw["_func"] == "drain_earnings_outbox"
    assert kw["_trigger"] == "interval"
    assert kw["minutes"] == 1
    assert kw["max_instances"] == 1, "a second drain must not run alongside the first"


def test_the_drain_job_survives_a_brief_outage_instead_of_discarding_the_cycle():
    """Grace well above the usual 60s. Notifications are not market-hours work, and the
    scheduler-misfire incident is precisely a job being DISCARDED after a short delay. The
    outbox is durable, so a late drain still delivers; a discarded one leaves rows sitting.

    Asserted as an inequality against the default, not as the literal 300 — the requirement is
    'survives a brief outage', and pinning the exact number would fail on any reasonable tuning
    while passing if it were changed to 61."""
    assert _drain_job_kwargs()["misfire_grace_time"] >= 180


def test_the_drain_job_declines_to_dispatch_unless_the_flag_says_outbox():
    body = _function_body("drain_earnings_outbox")
    assert "if not _eo.outbox_should_send(mode):" in body
    assert '_record_job_status("drain_earnings_outbox", "skipped"' in body


def test_an_unknown_recipient_defers_rather_than_being_treated_as_subscribed():
    """`_subscribed` returns None when the user row is gone. `may_send` treats None as consent
    UNESTABLISHED and defers — it must never read as permission."""
    body = _function_body("drain_earnings_outbox")
    assert "if user is None:" in body
    assert "return None" in body


def test_the_producer_enqueues_in_shadow_as_well_as_outbox_mode():
    """Shadow accumulates real traffic in the outbox with zero delivery effect. Enqueuing only
    in `outbox` mode would mean the first rows ever written are also the first ones sent."""
    body = _function_body("_send_early_earnings_stage")
    assert "if _mode in (_eo.SHADOW, _eo.OUTBOX):" in body


def test_the_legacy_sender_is_skipped_in_outbox_mode_and_writes_no_marker():
    """The two paths are mutually exclusive. Writing the legacy marker in outbox mode would make
    the outbox's own cutover check believe the old path had already delivered the event."""
    body = _function_body("_send_early_earnings_stage")
    skip_idx = body.index("if not _legacy_sends:")
    send_idx = body.index("sent_ok = send_email(")
    assert skip_idx < send_idx, "the legacy send must be AFTER the outbox-mode bail-out"
    assert "continue" in body[skip_idx:send_idx]


def test_an_outbox_enqueue_failure_never_suppresses_the_legacy_send():
    """While legacy owns delivery, an outbox problem must not cost a real notification."""
    body = _function_body("_send_early_earnings_stage")
    enq = body.index("_eo.enqueue_phase_alert(")
    tail = body[enq:enq + 900]
    assert "except Exception as _enq_exc:" in tail
    assert "early_earnings_outbox_enqueue_failed" in tail


def test_the_rollout_flag_is_not_defaulted_on_anywhere_in_the_scheduler():
    """A cutover must be deliberate. Nothing in the scheduler may write the flag."""
    assert "earnings_outbox_rollout" not in _SCHED, \
        "the scheduler reads the mode through rollout_mode(); it must never set the flag"


# ── The activation watermark ──────────────────────────────────────────────────────────────────

def test_shadow_queued_rows_are_not_delivered_after_the_flip(probe):
    """THE WORST THING A CUTOVER CAN DO. Rows queued during `shadow` were queued while the
    LEGACY sender still owned delivery — it already sent them. Draining them after the flag
    moves would re-send every notification accumulated during the shadow period.

    They are suppressed with a reason rather than dropped, so the cutover leaves a record of
    exactly what it withheld and why."""
    w = probe["activation_watermark"]
    assert w["batch"]["pre_activation_suppressed"] == 1
    assert w["shadow_row_state"] == "suppressed"
    assert "already delivered" in w["shadow_row_reason"]
    assert [c.rsplit(":", 1)[1] for c in w["provider_calls"]] == ["results"]


def test_a_row_queued_after_activation_is_delivered_normally(probe):
    assert probe["activation_watermark"]["live_row_state"] == "accepted"


def test_without_a_watermark_the_drain_refuses_to_deliver_anything(probe):
    """Fail closed. Without the activation instant a shadow row cannot be told apart from a
    live one, and guessing wrong duplicates real mail. Refusing is recoverable; duplicating is
    not."""
    n = probe["no_watermark"]
    assert n["batch"]["no_activation_watermark"] is True
    assert n["provider_calls"] == []
    assert n["batch"]["accepted"] == 0
