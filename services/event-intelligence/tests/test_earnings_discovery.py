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
from sqlalchemy import Column, Date, Integer, String
from sqlalchemy.orm import declarative_base

_SRC = pathlib.Path(__file__).resolve().parents[1] / "src"
_ROOT = pathlib.Path(__file__).resolve().parents[3]
for _m in ["redis", "httpx", "structlog", "yfinance", "pandas"]:
    sys.modules.setdefault(_m, MagicMock())
sys.path.insert(0, str(_ROOT / "shared"))


#: Minimal stand-ins with just the columns the comparison reads.
#:
#: NOT the shared models, and deliberately so: importing `db` here builds a real engine from a
#: mocked config and fails, which is exactly why another test in this suite stubs `db` out. The
#: comparison only needs entities SQLAlchemy can put in a `select()`, so the test supplies its
#: own rather than fighting over a shared import.
_Base = declarative_base()


class _Stock(_Base):
    __tablename__ = "t_stocks"
    id = Column(Integer, primary_key=True)
    symbol = Column(String(32))


class _Event(_Base):
    __tablename__ = "t_events"
    id = Column(Integer, primary_key=True)
    stock_id = Column(Integer)
    report_date = Column(Date)


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
             date(2026, 3, 18), date(2026, 6, 24)]

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
    """Minimal stand-in for the comparison, which only reads stored report dates."""
    def __init__(self, dates): self._dates = dates
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def execute(self, _stmt):
        dates = self._dates
        class _R:
            def scalars(self_inner):
                class _S:
                    def first(_s):
                        return type("S", (), {"id": 1, "symbol": "X"})() if dates is not None else None
                    def all(_s):
                        return [type("E", (), {"report_date": d})() for d in dates]
                return _S()
        return _R()


def _fake_db(dates):
    """(EarningsEvent, SessionLocal, Stock) with a session that only serves stored dates."""
    return _Event, (lambda: _Session(dates)), _Stock


@pytest.fixture
def mu(monkeypatch):
    monkeypatch.setattr(D, "_db", lambda: _fake_db(MU_STORED))
    monkeypatch.setattr(D, "_provider_rows", lambda sym: ([dict(r) for r in MU_PROVIDER], None))
    return D.discover("MU")


def test_discovery_finds_the_quarter_the_polling_loop_cannot(mu):
    """MU's 2026-09-03 period is upstream and absent locally. No amount of polling the pending
    set would surface it, because no pending row was ever created for it."""
    absent = mu["absent"]
    assert len(absent) == 1
    assert absent[0]["period_end"] == "2026-09-03"
    assert absent[0]["eps_actual"] == 33.42


def test_every_stored_quarter_is_recognised_as_present(mu):
    assert mu["present"] == 5
    assert mu["rows_returned"] == 6


def test_a_long_announcement_lag_still_matches(mu):
    """The provider indexes by PERIOD END and the table stores the ANNOUNCEMENT date; the two
    differ by the lag, which is exactly the quantity that must not be guessed. The 2026-05-28
    period matches the 2026-06-24 announcement — 27 days later — without a lag constant."""
    rows = {r["period_end"]: r for r in mu["absent"]}
    assert "2026-05-28" not in rows, "a 27-day lag must not read as a missing event"


def test_a_very_long_lag_does_not_create_a_false_absence(monkeypatch):
    """A 55-day lag is ordinary. Matching is bounded by the NEXT period, not by a day count."""
    monkeypatch.setattr(D, "_db", lambda: _fake_db([date(2026, 5, 25)]))
    monkeypatch.setattr(D, "_provider_rows",
                        lambda sym: ([{"period_end": date(2026, 3, 31), "eps_actual": 1.1,
                                       "eps_estimate": 1.0, "outcome": None, "reason": None}], None))
    out = D.discover("X")
    assert out["absent"] == []
    assert out["present"] == 1


def test_a_retrieval_failure_is_reported_as_retrieval_not_absence(monkeypatch):
    """A provider that cannot be reached has not told us anything is missing."""
    monkeypatch.setattr(D, "_db", lambda: _fake_db(MU_STORED))
    monkeypatch.setattr(D, "_provider_rows", lambda sym: ([], "TimeoutError: no answer"))
    out = D.discover("MU")
    assert out["stage"] == "retrieval"
    assert out["rows_returned"] is None, "NULL is not measured; 0 would claim an empty response"
    assert out["absent"] == []


def test_repair_previews_by_default_and_writes_nothing(monkeypatch):
    monkeypatch.setattr(D, "_db", lambda: _fake_db(MU_STORED))
    monkeypatch.setattr(D, "_provider_rows", lambda sym: ([dict(r) for r in MU_PROVIDER], None))
    plan = D.repair("MU")
    assert plan["committed"] is False
    assert [r["period_end"] for r in plan["would_write"]] == ["2026-09-03"]


def test_repair_skips_a_scheduled_period_with_no_reported_result(monkeypatch):
    """A provider row with no reported EPS is a scheduled period, not a released result."""
    monkeypatch.setattr(D, "_db", lambda: _fake_db([]))
    monkeypatch.setattr(D, "_provider_rows",
                        lambda sym: ([{"period_end": date(2026, 12, 1), "eps_actual": None,
                                       "eps_estimate": 1.0, "outcome": None, "reason": None}], None))
    plan = D.repair("Z")
    assert plan["would_write"] == []
    assert len(plan["skipped_no_actual"]) == 1


def test_the_plan_states_that_the_report_date_is_substituted(monkeypatch):
    """The provider gives a PERIOD END; the announcement date is a different fact. A repaired
    row must not be mistakable for a sourced announcement date."""
    monkeypatch.setattr(D, "_db", lambda: _fake_db(MU_STORED))
    monkeypatch.setattr(D, "_provider_rows", lambda sym: ([dict(r) for r in MU_PROVIDER], None))
    plan = D.repair("MU")
    assert "not a sourced announcement date" in plan["report_date_substitution"]


def test_an_unusable_provider_index_is_reported_not_dropped(monkeypatch):
    monkeypatch.setattr(D, "_db", lambda: _fake_db([]))
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
