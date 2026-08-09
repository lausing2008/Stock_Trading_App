"""Tests for AUD263-TUNEALL-STALE-GUARD-NOT-WEEKLY — POST /ml/tune_all was fire-and-forget:
market-data's scheduler.py recorded that the POST was DISPATCHED ("tune_all_sent"), never that
the ~2-4h background run in ml-prediction actually FINISHED. A container recreate mid-run
silently killed tuning under a green job status, and _record_tune_history() hardcoded
triggered_by="scheduled" on every call regardless of whether it came from the weekly path or
the 21-day stale-guard rescue, making the two indistinguishable in the audit trail.

Fix: tune_all() writes a real completion marker (stockai:tune_all_completed) unconditionally
at the end of its background _run_all() closure — even on a zero-tuned run — and triggered_by
is threaded through as a real query param instead of a hardcoded literal.

routes.py can't be imported directly in this test environment (needs common.jwt_auth/FastAPI
Depends/db) — source-text regression checks, matching test_tuner_ev_gate_wiring.py's
established convention for this exact constraint.
"""
import pathlib

_ROUTES_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "routes.py"
_ROUTES_SOURCE = _ROUTES_PATH.read_text()


def _tune_all_body() -> str:
    start = _ROUTES_SOURCE.index('@router.post("/tune_all")')
    end = _ROUTES_SOURCE.index("\n\n\n", start)
    return _ROUTES_SOURCE[start:end]


def test_tune_all_accepts_a_triggered_by_query_param_defaulting_to_weekly():
    body = _tune_all_body()
    assert "triggered_by: str = \"weekly\"" in body


def test_completion_marker_is_written_unconditionally_not_only_when_tuned_count_positive():
    """The marker must be written BEFORE the `if tuned_count > 0:` branch — a run that
    genuinely finished with zero symbols tuned (every symbol legitimately failed) must still
    be distinguishable from a run that never finished at all."""
    body = _tune_all_body()
    marker_idx = body.index('"stockai:tune_all_completed"')
    branch_idx = body.index("if tuned_count > 0:")
    assert marker_idx < branch_idx


def test_completion_marker_records_triggered_by_tuned_and_total():
    body = _tune_all_body()
    marker_call = body[body.index('"stockai:tune_all_completed"'):body.index('"stockai:tune_all_completed"') + 600]
    assert '"tuned": tuned_count' in marker_call
    assert '"total": len(symbols)' in marker_call
    assert '"triggered_by": triggered_by' in marker_call
    assert '"completed_at":' in marker_call


def test_completion_marker_write_is_wrapped_in_try_except():
    """A Redis hiccup while writing the marker must never mask that tuning itself genuinely
    completed — the marker write is a diagnostic add-on, not something that should be able to
    make a real, finished run look like it crashed."""
    body = _tune_all_body()
    marker_idx = body.index('"stockai:tune_all_completed"')
    # Walk backward from the marker call to find its enclosing try block.
    try_idx = body.rindex("try:", 0, marker_idx)
    except_idx = body.index("except Exception as _mark_exc:", marker_idx)
    assert try_idx < marker_idx < except_idx


def test_run_all_threads_triggered_by_into_each_tune_symbol_call():
    body = _tune_all_body()
    assert "tune_symbol(sym, n_trials=n_trials, horizon=horizon, style=style, triggered_by=triggered_by)" in body
