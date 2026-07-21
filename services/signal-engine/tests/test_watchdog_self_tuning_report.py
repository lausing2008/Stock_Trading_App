"""Tests for SELFIMPROVE-WATCHDOG-SELF-TUNING's watchdog_self_tuning_report()/_watchdog_action_kind().

signal_watchdog()'s own meta-parameters (38% win-rate floor, +0.03/-0.02 step size, 15
min-samples, 3x max-tighten) were exactly as hardcoded and never-revisited as any of the base
trading parameters the watchdog exists to correct. This report reads TuneHistory rows the
watchdog itself wrote (triggered_by="watchdog") whose realized_ev_pct_after has since been
populated by backfill_realized_ev() — the retro-feedback loop this item depended on — and
reports, per style, whether tighten actions' realized EV differs from relax actions', and how
often a tighten action's OWN realized EV was still negative (a signal the step size may be too
small). Deliberately READ-ONLY diagnostic output, not an auto-tuning job.

routes.py can't be imported directly in this environment (conftest.py stubs the `common`
package wholesale) — _watchdog_action_kind()'s source is extracted directly from the real file
and exec()'d, matching test_backfill_realized_ev.py's established source-text-extraction
technique for this exact import constraint. watchdog_self_tuning_report() itself (the FastAPI
route) is covered by source-text regression checks instead of a full functional exercise,
since it needs a real DB session with TuneHistory rows — the underlying grouping/classification
logic (_watchdog_action_kind) is what's behaviorally tested here.
"""
import pathlib

_ROUTES_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "routes.py"
_ROUTES_SOURCE = _ROUTES_PATH.read_text()


def _extract_watchdog_action_kind():
    """Pulls _watchdog_action_kind()'s real source out of routes.py and exec()s it — a pure
    function with no DB/session dependency, so no model/engine setup is needed for this one."""
    start = _ROUTES_SOURCE.index("def _watchdog_action_kind(")
    end = _ROUTES_SOURCE.index('@router.get("/watchdog_self_tuning_report")', start)
    func_source = _ROUTES_SOURCE[start:end]
    namespace: dict = {}
    exec(func_source, namespace)  # noqa: S102 — isolated eval of one pure function's real source
    return namespace["_watchdog_action_kind"]


_watchdog_action_kind = _extract_watchdog_action_kind()


# ── source-text checks: the endpoint and its safety properties really exist ───

def test_endpoint_is_registered_as_get_and_read_only():
    assert '@router.get("/watchdog_self_tuning_report")' in _ROUTES_SOURCE
    assert "def watchdog_self_tuning_report(" in _ROUTES_SOURCE


def test_report_only_reads_watchdog_rows_with_realized_ev_already_populated():
    """Must filter to triggered_by="watchdog", promoted=True, and realized_ev_pct_after NOT
    NULL — a row without a realized verdict yet has nothing trustworthy to report on."""
    start = _ROUTES_SOURCE.index("def watchdog_self_tuning_report(")
    end = _ROUTES_SOURCE.index("\n\n\n# ── T223", start)
    body = _ROUTES_SOURCE[start:end]
    assert 'TuneHistory.triggered_by == "watchdog"' in body
    assert "TuneHistory.promoted.is_(True)" in body
    assert "TuneHistory.realized_ev_pct_after.is_not(None)" in body


def test_report_never_writes_to_the_database():
    """This is a diagnostic report, not an auto-tuning job — must never call session.add/
    commit/execute with an UPDATE, only SELECT via session.execute(select(...))."""
    start = _ROUTES_SOURCE.index("def watchdog_self_tuning_report(")
    end = _ROUTES_SOURCE.index("\n\n\n# ── T223", start)
    body = _ROUTES_SOURCE[start:end]
    assert "session.commit()" not in body
    assert "session.add(" not in body
    assert ".setex(" not in body  # must not touch Redis watchdog state either


# ── behavioral checks against the real, extracted _watchdog_action_kind() ─────

def test_classifies_a_higher_new_threshold_as_tighten():
    assert _watchdog_action_kind({"threshold": 0.65}, {"threshold": 0.68}) == "tighten"


def test_classifies_a_lower_new_threshold_as_relax():
    assert _watchdog_action_kind({"threshold": 0.68}, {"threshold": 0.65}) == "relax"


def test_classifies_an_unchanged_threshold_as_none():
    assert _watchdog_action_kind({"threshold": 0.65}, {"threshold": 0.65}) is None


def test_classifies_malformed_rows_as_none_without_raising():
    assert _watchdog_action_kind({}, {"threshold": 0.65}) is None
    assert _watchdog_action_kind({"threshold": 0.65}, {}) is None
    assert _watchdog_action_kind({"threshold": "not-a-number"}, {"threshold": 0.65}) is None
    assert _watchdog_action_kind(None, {"threshold": 0.65}) is None
