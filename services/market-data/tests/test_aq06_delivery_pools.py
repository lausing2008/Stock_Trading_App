"""AQ06 — a candidate is not a delivered alert.

THE DEFECT. `_record_options_flow_alert_outcome` writes a row for EVERY candidate, before the
per-recipient loop applies cooldown, ranking and the email cap. That loop already knows what
happened — it computes deferred/queued/omitted/accepted and logs them — but nothing persisted.
So `options_flow_alert_outcomes` is a table of CANDIDATES wearing the name of alerts, and every
win rate over it answers "how did everything we considered behave" while the reader hears "how
did the alerts I got behave".

Both populations matter, which is why the fix records a state per candidate rather than
deleting the unsent ones: the candidate set is the baseline a delivered subset must beat to be
worth sending at all.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "shared"))

from services.alert_delivery_pool import (  # noqa: E402
    fold_recipient, merge, summarise, CANDIDATE, SUPPRESSED_BY_COOLDOWN, OMITTED_BY_CAP,
    QUEUED_NOT_SENT, SENDER_ACCEPTED, ORDER, STATE_MEANING, DELIVERED_POOL)


# ---- the five states stay distinct --------------------------------------------------------------

def test_a_candidate_nobody_reached_is_not_a_delivered_alert():
    got = summarise(["A"], {})
    assert got["per_chain"]["A"] == CANDIDATE
    assert got["delivered_total"] == 0
    assert got["candidate_total"] == 1


def test_a_contract_held_by_cooldown_is_not_omitted_by_the_cap():
    """Two different reasons it did not go out, and they lead different places: one is a
    deliberate rate limit, the other is losing a ranking."""
    st = fold_recipient({}, deferred={"A"}, queued=set(), omitted=set(), accepted=set(),
                        send_attempted=False, send_ok=False)
    assert st["A"] == SUPPRESSED_BY_COOLDOWN
    st2 = fold_recipient({}, deferred=set(), queued={"B"}, omitted={"B"}, accepted=set(),
                         send_attempted=True, send_ok=True)
    assert st2["B"] == OMITTED_BY_CAP


def test_a_queued_contract_whose_send_failed_is_not_omitted():
    """Nothing ranked it out — the send failed. Calling that 'omitted' would blame the cap for
    a provider error."""
    st = fold_recipient({}, deferred=set(), queued={"C"}, omitted=set(), accepted={"C"},
                        send_attempted=True, send_ok=False)
    assert st["C"] == QUEUED_NOT_SENT


def test_acceptance_requires_an_attempted_and_successful_send():
    st = fold_recipient({}, deferred=set(), queued={"D"}, omitted=set(), accepted={"D"},
                        send_attempted=True, send_ok=True)
    assert st["D"] == SENDER_ACCEPTED


# ---- across recipients, the strongest state wins --------------------------------------------------

def test_a_contract_deferred_for_one_recipient_and_sent_to_another_counts_as_delivered():
    """The question each state answers is "did this reach anyone", so one recipient's cooldown
    cannot mark a contract undelivered when another was sent it."""
    st = {}
    fold_recipient(st, deferred={"A"}, queued=set(), omitted=set(), accepted=set(),
                   send_attempted=False, send_ok=False)
    fold_recipient(st, deferred=set(), queued={"A"}, omitted=set(), accepted={"A"},
                   send_attempted=True, send_ok=True)
    assert st["A"] == SENDER_ACCEPTED


def test_the_merge_order_is_total_and_never_downgrades():
    for i, weaker in enumerate(ORDER):
        for stronger in ORDER[i:]:
            assert merge(stronger, weaker) == stronger
            assert merge(weaker, stronger) == stronger


# ---- provider acceptance is not inbox delivery ------------------------------------------------------

def test_the_strongest_state_does_not_claim_the_message_was_received():
    assert "NOT proof of inbox delivery" in STATE_MEANING[SENDER_ACCEPTED]
    assert DELIVERED_POOL == (SENDER_ACCEPTED,)
    assert "candidate set is the baseline" in summarise([], {})["note"]


def test_every_state_is_explained_in_words():
    for state in ORDER:
        assert state in STATE_MEANING and len(STATE_MEANING[state]) > 25


# ---- the scheduler persists it ------------------------------------------------------------------------

def _sched():
    return (Path(__file__).resolve().parents[1] / "src/services/scheduler.py").read_text()


def _fn(name):
    import ast
    src = _sched()
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == name)
    return ast.unparse(fn)


def test_the_send_loop_folds_every_recipient_into_one_map():
    body = _fn("check_options_flow_alerts")
    assert "_fold_delivery(" in body
    for kw in ("deferred=deferred_chains", "queued=cooldown_ok_chains",
               "omitted=omitted_chains", "accepted=accepted_chains"):
        assert kw in body, f"{kw} must reach the fold"


def test_the_decision_is_written_to_the_outcome_rows():
    import ast
    src = _sched()
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "check_options_flow_alerts")
    updates = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
               and isinstance(n.func, ast.Name) and n.func.id == "update"]
    assert updates, "the delivery state must be persisted, not only logged"
    body = ast.unparse(fn)
    assert "delivery_state=_state" in body
    assert "delivered_recipient_count=" in body


def test_a_persistence_failure_does_not_lose_the_send():
    body = _fn("check_options_flow_alerts")
    seg = body[body.index("delivery_state=_state"):]
    assert "rollback()" in seg and "delivery_state_persist_failed" in seg


# ---- the API reports the two pools apart --------------------------------------------------------------

def _admin():
    return (Path(__file__).resolve().parents[1] / "src/api/admin.py").read_text()


def test_the_delivered_pool_sits_beside_the_candidate_figure_not_instead_of_it():
    import ast
    src = _admin()
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "options_flow_alert_performance")
    body = ast.unparse(fn)
    assert "'delivered': _delivered_pool(elig)" in body
    # the candidate figures are still there
    assert "'win_rate'" in body and "'original_n'" in body
    assert "'delivery_state_unknown'" in body, \
        "rows predating AQ06 must be counted, not guessed into a pool"


def test_the_helper_lives_in_the_endpoint_that_uses_it():
    """It was first written into `squeeze_alert_performance` by a bad anchor match, where the
    flow endpoint could not see it."""
    import ast
    src = _admin()
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.FunctionDef) and n.name == "options_flow_alert_performance":
            inner = {c.name for c in ast.walk(n) if isinstance(c, ast.FunctionDef)}
            assert "_delivered_pool" in inner
            return
    pytest.fail("options_flow_alert_performance not found")


def test_the_delivered_pool_carries_its_own_denominator_and_status():
    import ast
    src = _admin()
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "_delivered_pool")
    body = ast.unparse(fn)
    assert "'population': 'sender_accepted'" in body
    assert "_cohort_status(" in body, "the same publication floors as every other cohort"
    assert "not inbox delivery" in body
