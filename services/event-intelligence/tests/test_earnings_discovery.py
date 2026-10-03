"""Discovery finds what the pending-event loop structurally cannot.

`sync_todays_earnings()` selects EXISTING pending rows, so an event absent from the table can
never enter its candidate set — polling faster can never find what was never created. These
tests drive the real comparison with a stubbed provider, using MU's actual stored dates.
"""
import importlib.util
import pathlib
import sys
from datetime import date
from unittest.mock import MagicMock

import pytest

_SRC = pathlib.Path(__file__).resolve().parents[1] / "src"
_ROOT = pathlib.Path(__file__).resolve().parents[3]
for _m in ["redis", "httpx", "structlog", "yfinance", "pandas"]:
    sys.modules.setdefault(_m, MagicMock())
sys.path.insert(0, str(_ROOT / "shared"))


#: This suite's conftest stubs `sqlalchemy` itself, so there are no real entities to build:
#: `select()` and the comparison operators are already mocks, and the stub session below ignores
#: the statement entirely. Plain mocks are what the surrounding tests use and all this needs.
def _entity():
    return MagicMock()


def _load():
    spec = importlib.util.spec_from_file_location(
        "earnings_discovery_under_test", _SRC / "services" / "earnings_discovery.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


D = _load()

#: MU's real stored report dates, read from production on 2026-10-03. The gap is the fiscal Q4
#: period ending 2026-09-03, announced 2026-09-30 and never ingested.
MU_STORED = [date(2025, 5, 31), date(2025, 9, 23), date(2025, 12, 17),
             date(2026, 3, 18), date(2026, 6, 24),
             # The FUTURE scheduled event. Omitting it from this fixture is why the open-ended
             # upper bound went unnoticed until discovery ran against production: the newest
             # provider period matched this, three months away, and reported a genuinely
             # missing quarter as present.
             date(2026, 12, 23)]

MU_PROVIDER = [
    {"period_end": date(2025, 5, 29), "eps_actual": 1.91, "eps_estimate": 1.59,
     "outcome": None, "reason": None},
    {"period_end": date(2025, 8, 28), "eps_actual": 3.03, "eps_estimate": 2.86,
     "outcome": None, "reason": None},
    {"period_end": date(2025, 11, 27), "eps_actual": 4.78, "eps_estimate": 3.96,
     "outcome": None, "reason": None},
    {"period_end": date(2026, 2, 26), "eps_actual": 12.2, "eps_estimate": 9.16,
     "outcome": None, "reason": None},
    {"period_end": date(2026, 5, 28), "eps_actual": 25.11, "eps_estimate": 20.71,
     "outcome": None, "reason": None},
    # The missing quarter: fiscal Q4 ending 2026-09-03, announced 2026-09-30.
    {"period_end": date(2026, 9, 3), "eps_actual": 33.42, "eps_estimate": 31.10,
     "outcome": None, "reason": None},
]


class _Session:
    """A session that is never actually queried — the accessors are patched instead."""
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _patch_db(monkeypatch, dates):
    """Replace the two data accessors.

    NOT the ORM or the session: this suite's conftest stubs `sqlalchemy` when the file runs
    alone while the full-suite run has it REAL, so anything built on `select()` passes one way
    and fails the other. Patching the accessors removes the dependency entirely and leaves the
    comparison — the part with the defect potential — exercised directly.
    """
    monkeypatch.setattr(D, "_db", lambda: (_entity(), _Session, _entity()))
    monkeypatch.setattr(D, "_resolve_stock",
                        lambda session, symbol: type("S", (), {"id": 1, "symbol": symbol})())
    monkeypatch.setattr(D, "_stored_events", lambda session, stock_id: [
        d if isinstance(d, dict)
        else {"id": 1000 + i, "report_date": d, "has_result": True,
              "report_date_source": None, "period_end": None}
        for i, d in enumerate(dates)])


@pytest.fixture
def mu(monkeypatch):
    _patch_db(monkeypatch, MU_STORED)
    monkeypatch.setattr(D, "_provider_rows", lambda sym: ([dict(r) for r in MU_PROVIDER], None))
    return D.discover("MU")


def test_discovery_finds_the_quarter_the_polling_loop_cannot(mu):
    """MU's 2026-09-03 period is upstream and absent locally. No amount of polling the pending
    set would surface it, because no pending row was ever created for it."""
    absent = mu["absent"]
    assert len(absent) == 1
    assert absent[0]["period_end"] == "2026-09-03"
    assert absent[0]["eps_actual"] == 33.42


def test_legacy_rows_without_a_period_end_are_ambiguous_not_present(mu):
    """Position inside a date interval establishes nothing about WHICH period a row reports.
    MU's stored rows carry no period_end, so none of them can be CONFIRMED to report the
    quarter the provider is describing — and the honest answer is ambiguous, not present."""
    assert mu["present"] == 0
    assert mu["rows_returned"] == 6
    assert len(mu["ambiguous"]) == 5
    assert all("cannot be shown to report THIS period" in a["reason"] for a in mu["ambiguous"])


def test_a_long_announcement_lag_is_still_not_called_absent(mu):
    """A row DOES sit in that window, so the quarter is not absent — it is unverified. The
    distinction matters: absent invites a write, ambiguous forbids one."""
    assert "2026-05-28" not in {r["period_end"] for r in mu["absent"]}
    assert "2026-05-28" in {r["period_end"] for r in mu["ambiguous"]}


def test_a_very_long_lag_does_not_create_a_false_absence(monkeypatch):
    """A 55-day lag is ordinary. Matching is bounded by the NEXT period, not by a day count."""
    _patch_db(monkeypatch, [{"id": 1, "report_date": date(2026, 5, 25), "has_result": True,
                             "report_date_source": None, "period_end": date(2026, 3, 31)}])
    monkeypatch.setattr(D, "_provider_rows",
                        lambda sym: ([{"period_end": date(2026, 3, 31), "eps_actual": 1.1,
                                       "eps_estimate": 1.0, "outcome": None, "reason": None}], None))
    out = D.discover("X")
    assert out["absent"] == []
    assert out["present"] == 1, "a matching period_end verifies it despite the 55-day lag"


def test_a_retrieval_failure_is_reported_as_retrieval_not_absence(monkeypatch):
    """A provider that cannot be reached has not told us anything is missing."""
    _patch_db(monkeypatch, MU_STORED)
    monkeypatch.setattr(D, "_provider_rows", lambda sym: ([], "TimeoutError: no answer"))
    out = D.discover("MU")
    assert out["stage"] == "retrieval"
    assert out["rows_returned"] is None, "NULL is not measured; 0 would claim an empty response"
    assert out["absent"] == []


def test_repair_previews_by_default_and_writes_nothing(monkeypatch):
    _patch_db(monkeypatch, MU_STORED)
    monkeypatch.setattr(D, "_provider_rows", lambda sym: ([dict(r) for r in MU_PROVIDER], None))
    plan = D.repair("MU")
    assert plan["committed"] is False
    assert [r["period_end"] for r in plan["would_insert"]] == ["2026-09-03"]


def test_repair_skips_a_scheduled_period_with_no_reported_result(monkeypatch):
    """A provider row with no reported EPS is a scheduled period, not a released result."""
    _patch_db(monkeypatch, [])
    monkeypatch.setattr(D, "_provider_rows",
                        lambda sym: ([{"period_end": date(2026, 12, 1), "eps_actual": None,
                                       "eps_estimate": 1.0, "outcome": None, "reason": None}], None))
    plan = D.repair("Z")
    assert plan["would_insert"] == []
    assert len(plan["skipped_no_actual"]) == 1


def test_the_plan_states_that_the_report_date_is_substituted(monkeypatch):
    """The provider gives a PERIOD END; the announcement date is a different fact. A repaired
    row must not be mistakable for a sourced announcement date."""
    _patch_db(monkeypatch, MU_STORED)
    monkeypatch.setattr(D, "_provider_rows", lambda sym: ([dict(r) for r in MU_PROVIDER], None))
    plan = D.repair("MU")
    assert "not an announcement date" in plan["report_date_substitution"]
    assert "report_date_source" in plan["report_date_substitution"], \
        "the plan must point at the marker that is persisted, not just warn in prose"


def test_an_unusable_provider_index_is_reported_not_dropped(monkeypatch):
    _patch_db(monkeypatch, [])
    monkeypatch.setattr(D, "_provider_rows",
                        lambda sym: ([{"period_end": None, "raw_index": "not-a-date",
                                       "outcome": D.UNMAPPABLE,
                                       "reason": "the provider's index is not a usable date"}], None))
    out = D.discover("Y")
    assert len(out["unmappable"]) == 1
    assert out["absent"] == [], "an unreadable row is not evidence that an event is missing"


# ── The coverage watermark must record proven coverage, not processing time ────────────────

def _earnings_helpers():
    import ast
    src = (_SRC / "services" / "earnings.py").read_text()
    tree = ast.parse(src)
    ns = {"date": date}
    for name in ("_history_watermark", "_coverage_outcome", "_calendar_outcome"):
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
        exec(compile(ast.Module(body=[fn], type_ignores=[]), "<x>", "exec"), ns)
    return ns


def test_the_watermark_is_the_newest_period_written_not_today():
    """It defaulted to `date.today()`, recording when the job RAN rather than what it covered —
    so a pass that wrote nothing newer than last quarter still marked coverage current, and a
    discovery step trusting it would skip the periods nobody had fetched."""
    ns = _earnings_helpers()
    wm = ns["_history_watermark"]
    assert wm({"history_newest_written": date(2026, 6, 24)},
              mode="history", outcome="ok") == date(2026, 6, 24)
    assert wm({"history_newest_written": date(2026, 6, 24)},
              mode="history", outcome="ok") != date.today()


def test_an_ok_pass_that_wrote_nothing_advances_no_watermark():
    ns = _earnings_helpers()
    assert ns["_history_watermark"]({}, mode="history", outcome="ok") is None


@pytest.mark.parametrize("mode,outcome", [
    ("history", "partial"), ("history", "mapping_failed"), ("history", "ok_empty"),
    ("history", "retrieval_failed"), ("calendar", "ok"),
])
def test_only_a_fully_mapped_history_pass_advances_the_watermark(mode, outcome):
    ns = _earnings_helpers()
    assert ns["_history_watermark"]({"history_newest_written": date(2026, 6, 24)},
                                    mode=mode, outcome=outcome) is None



def test_a_released_result_cannot_be_matched_to_a_future_scheduled_event(mu):
    """MU's newest provider period has no next period, so its upper bound is open-ended. Without
    a further constraint it matched the December scheduled event and reported the missing
    September quarter as present — the exact false NEGATIVE that hid the gap."""
    absent = {r["period_end"] for r in mu["absent"]}
    assert "2026-09-03" in absent, "a released period must not match a future scheduled event"


def test_a_scheduled_period_may_still_match_a_future_event(monkeypatch):
    """The constraint applies only to RELEASED rows. A provider row with no reported EPS is a
    scheduled period, and a future event is exactly what should match it."""
    _patch_db(monkeypatch, [{"id": 3, "report_date": date(2026, 12, 23), "has_result": True,
                             "report_date_source": None, "period_end": date(2026, 12, 1)}])
    monkeypatch.setattr(D, "_provider_rows",
                        lambda sym: ([{"period_end": date(2026, 12, 1), "eps_actual": None,
                                       "eps_estimate": 1.0, "outcome": None, "reason": None}], None))
    out = D.discover("MU")
    assert out["absent"] == []
    assert out["present"] == 1



# ── The semantic round: period end is not an announcement date ─────────────────────────────

def test_the_substitution_is_persisted_on_the_row_not_only_in_the_plan(monkeypatch):
    """The warning existed only in the returned plan. Every consumer reads `report_date` as the
    announcement date, and the next reader is a SQL query — so the marker has to be on the row.
    Without it the return window anchors on a period end and publishes pre-release prices as
    the post-earnings reaction."""
    import ast
    src = (_SRC / "services" / "earnings_discovery.py").read_text()
    tree = ast.parse(src)
    fn = next(n for n in tree.walk(tree) if False) if False else None
    body = src[src.index("def repair("):]
    assert "report_date_source=SUBSTITUTED_PERIOD_END" in body, \
        "the stand-in must be marked on the stored row"
    assert "period_end=pe" in body, "the period end must be stored where it belongs"


def test_the_repair_suppresses_notification_replay_explicitly(monkeypatch):
    """"The window happens not to reach it" is a property of today's constants, not a
    guarantee."""
    body = (_SRC / "services" / "earnings_discovery.py").read_text()
    body = body[body.index("def repair("):]
    assert "notification_suppressed_at=now" in body


def test_a_pending_placeholder_does_not_mark_a_result_present(monkeypatch):
    """A date range alone let a scheduled row with no figures mark a released quarter covered."""
    _patch_db(monkeypatch, [{"id": 7, "report_date": date(2026, 9, 20), "has_result": False,
                             "report_date_source": None, "period_end": date(2026, 9, 3)}])
    monkeypatch.setattr(D, "_provider_rows",
                        lambda sym: ([{"period_end": date(2026, 9, 3), "eps_actual": 33.42,
                                       "eps_estimate": 31.82, "outcome": None,
                                       "reason": None}], None))
    out = D.discover("MU")
    assert out["present"] == 0
    assert len(out["pending_placeholder"]) == 1
    assert out["pending_placeholder"][0]["matched_event_id"] == 7


def test_a_placeholder_is_filled_rather_than_duplicated(monkeypatch):
    """Inserting a second event for the same quarter creates exactly the ambiguity discovery
    exists to remove."""
    _patch_db(monkeypatch, [{"id": 7, "report_date": date(2026, 9, 20), "has_result": False,
                             "report_date_source": None, "period_end": date(2026, 9, 3)}])
    monkeypatch.setattr(D, "_provider_rows",
                        lambda sym: ([{"period_end": date(2026, 9, 3), "eps_actual": 33.42,
                                       "eps_estimate": 31.82, "outcome": None,
                                       "reason": None}], None))
    plan = D.repair("MU")
    assert plan["would_insert"] == []
    assert [r["matched_event_id"] for r in plan["would_fill_placeholder"]] == [7]


def test_an_event_recording_a_result_still_counts_as_present(monkeypatch):
    _patch_db(monkeypatch, [{"id": 9, "report_date": date(2026, 9, 20), "has_result": True,
                             "report_date_source": None, "period_end": date(2026, 9, 3)}])
    monkeypatch.setattr(D, "_provider_rows",
                        lambda sym: ([{"period_end": date(2026, 9, 3), "eps_actual": 33.42,
                                       "eps_estimate": 31.82, "outcome": None,
                                       "reason": None}], None))
    out = D.discover("MU")
    assert out["present"] == 1
    assert out["absent"] == []
    assert out["pending_placeholder"] == []


def test_the_return_backfill_refuses_a_substituted_anchor():
    """The calculation anchors its whole window on `report_date`. A substituted period end
    would measure the wrong weeks and publish them as the reaction."""
    import ast
    src = (_SRC / "services" / "earnings.py").read_text()
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
              and n.name == "backfill_post_earnings_returns")
    body = ast.get_source_segment(src, fn)
    assert "report_date_source" in body, "the anchor's provenance must be part of the selection"
    assert "substituted_period_end" in body



# ── Identity, and suppression that is not delivery ─────────────────────────────────────────

def test_an_unverified_row_in_the_window_is_never_filled(monkeypatch):
    """Filling on position alone writes EPS into whichever event happens to sit in the range —
    which could be a different quarter entirely."""
    _patch_db(monkeypatch, [{"id": 7, "report_date": date(2026, 9, 20), "has_result": False,
                             "report_date_source": None, "period_end": None}])
    monkeypatch.setattr(D, "_provider_rows",
                        lambda sym: ([{"period_end": date(2026, 9, 3), "eps_actual": 33.42,
                                       "eps_estimate": 31.82, "outcome": None,
                                       "reason": None}], None))
    out = D.discover("MU")
    assert out["present"] == 0
    assert out["pending_placeholder"] == []
    assert len(out["ambiguous"]) == 1

    plan = D.repair("MU")
    assert plan["would_insert"] == [], "an ambiguous match must not become a new event either"
    assert plan["would_fill_placeholder"] == []
    assert len(plan["ambiguous_not_written"]) == 1


def test_a_mismatched_period_end_is_ambiguous_not_a_match(monkeypatch):
    """A stored period end that differs from the provider's is a DIFFERENT quarter, however
    close the two dates are."""
    _patch_db(monkeypatch, [{"id": 7, "report_date": date(2026, 9, 20), "has_result": False,
                             "report_date_source": None, "period_end": date(2026, 8, 31)}])
    monkeypatch.setattr(D, "_provider_rows",
                        lambda sym: ([{"period_end": date(2026, 9, 3), "eps_actual": 33.42,
                                       "eps_estimate": 31.82, "outcome": None,
                                       "reason": None}], None))
    out = D.discover("MU")
    assert out["pending_placeholder"] == []
    assert len(out["ambiguous"]) == 1


def test_suppression_is_recorded_as_suppression_not_as_delivery():
    """Stamping `impact_sent_at` recorded a delivery that never happened; any later audit of
    what was actually sent would have counted it."""
    body = (_SRC / "services" / "earnings_discovery.py").read_text()
    body = body[body.index("def repair("):]
    assert "notification_suppressed_at=now" in body
    assert "notification_suppressed_reason=_SUPPRESSION_REASON" in body
    assert "impact_sent_at=now" not in body, "delivery evidence must not be forged"


def test_the_delivery_paths_check_suppression_explicitly():
    """Relying on a lookback window happening not to reach a historical row is a property of
    today's constants, not a guarantee."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[3]
    sched = (root / "services/market-data/src/services/scheduler.py").read_text()
    earn = (_SRC / "services" / "earnings.py").read_text()
    assert sched.count("notification_suppressed_at.is_(None)") >= 2
    assert "notification_suppressed_at.is_(None)" in earn
