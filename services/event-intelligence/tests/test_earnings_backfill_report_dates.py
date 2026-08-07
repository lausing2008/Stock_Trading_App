"""Tests for AUD264-EARNINGS-FISCAL-QUARTER-FROM-ANNOUNCEMENT-MONTH's one-time backfill —
_backfill_report_dates_for_symbol() corrects already-stored, already-reported earnings_events
rows whose report_date is still the pre-fix fiscal PERIOD-END date, not the real announcement
date. The normal daily sync (_fetch_earnings_for_symbol) does NOT self-heal these rows (its
existing_pending lookup only matches eps_actual IS NULL rows), so this is a genuinely separate
code path with its own test coverage.

Uses the same real-in-memory-SQLite-plus-real-EarningsEvent-model technique already established
in news-intelligence's test_storage_dedup.py (db/sqlalchemy/pandas are stubbed wholesale by
conftest.py for Docker-only dependencies — this pops those stubs, builds a real engine with
real pandas/yfinance available, and restores the stubs immediately after import).
"""
import sys
from unittest.mock import MagicMock, patch

_STUBBED_MODULES = (
    "common", "common.config", "common.logging",
    "db", "db.session",
    "sqlalchemy", "sqlalchemy.orm", "sqlalchemy.dialects", "sqlalchemy.dialects.postgresql",
    "psycopg2", "pandas",
)
_saved_stubs = {_mod: sys.modules.pop(_mod, None) for _mod in _STUBBED_MODULES}

import importlib.util
import pathlib
from datetime import date

from sqlalchemy import Integer, create_engine, select
from sqlalchemy.orm import sessionmaker

_models_path = pathlib.Path(__file__).resolve().parents[3] / "shared" / "db" / "models.py"
_spec = importlib.util.spec_from_file_location("db_models_under_test_earnings_backfill", _models_path)
_models = importlib.util.module_from_spec(_spec)
sys.modules["db_models_under_test_earnings_backfill"] = _models
_spec.loader.exec_module(_models)

# EarningsEvent.id/Stock.id are BigInteger PKs (SQLite only auto-increments a plain INTEGER
# PK) — matching test_storage_dedup.py's own established column-type swap for this exact
# SQLite limitation.
_models.EarningsEvent.__table__.c.id.type = Integer()
_models.Stock.__table__.c.id.type = Integer()

_engine = create_engine("sqlite:///:memory:")
# create_all() against the FULL registry (not a filtered table subset) — Stock has ORM
# relationships to other models (e.g. Stock.prices -> Price) that SQLAlchemy's mapper
# configuration step resolves lazily on first real instantiation; creating only Stock's own
# table left those relationships unresolvable and raised a real ArgumentError the moment a
# Stock row was actually inserted (caught silently by _backfill_report_dates_for_symbol's own
# broad except Exception, making every test wrongly report corrected=0 instead of failing
# loudly — this was a real, confusing failure mode hit while writing these tests).
_models.Base.metadata.create_all(_engine)
_SessionLocal = sessionmaker(bind=_engine)

for _mod, _val in _saved_stubs.items():
    if _val is not None:
        sys.modules[_mod] = _val
    else:
        sys.modules.pop(_mod, None)

sys.modules.setdefault("common", MagicMock())
sys.modules.setdefault("common.config", MagicMock())
sys.modules.setdefault("common.logging", MagicMock())
sys.modules.setdefault("db", MagicMock())

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from src.services import earnings  # noqa: E402


def _make_stock(session, symbol="AAPL"):
    stock = _models.Stock(symbol=symbol, name="Test Co", market="US", exchange="NASDAQ", active=True)
    session.add(stock)
    session.commit()
    session.refresh(stock)
    return stock


def _make_reported_row(session, stock_id, report_date, eps_actual, fiscal_year=None, fiscal_quarter=None):
    ev = _models.EarningsEvent(
        stock_id=stock_id,
        report_date=report_date,
        eps_actual=eps_actual,
        fiscal_year=fiscal_year or report_date.year,
        fiscal_quarter=fiscal_quarter or ((report_date.month - 1) // 3 + 1),
        period="placeholder",
    )
    session.add(ev)
    session.commit()
    session.refresh(ev)
    return ev


def _make_pending_row(session, stock_id, report_date):
    """A calendar-path row for an UPCOMING (not yet reported) event — eps_actual is NULL."""
    ev = _models.EarningsEvent(
        stock_id=stock_id,
        report_date=report_date,
        eps_actual=None,
        fiscal_year=report_date.year,
        fiscal_quarter=(report_date.month - 1) // 3 + 1,
        period="placeholder",
    )
    session.add(ev)
    session.commit()
    session.refresh(ev)
    return ev


class _FakeYFFrame:
    """Minimal stand-in for a yfinance DataFrame — supports .empty and .iterrows()."""
    def __init__(self, rows):
        self._rows = rows  # list of (index_obj, row_dict)

    @property
    def empty(self):
        return len(self._rows) == 0

    def iterrows(self):
        return iter(self._rows)


class _FakeIdx:
    def __init__(self, d: date):
        self._d = d

    def date(self):
        return self._d


class _FakeTicker:
    def __init__(self, hist_rows, announce_rows):
        self.earnings_history = _FakeYFFrame([(_FakeIdx(r["period_end"]), {"epsActual": r["eps_actual"]}) for r in hist_rows])
        self.earnings_dates = _FakeYFFrame([(_FakeIdx(r["announce_date"]), {"Reported EPS": r["eps_actual"]}) for r in announce_rows])


def _patch_yfinance(hist_rows, announce_rows):
    fake_yf = MagicMock()
    fake_yf.Ticker = MagicMock(return_value=_FakeTicker(hist_rows, announce_rows))
    return patch.dict(sys.modules, {"yfinance": fake_yf})


class TestBackfillReportDatesForSymbol:
    def test_corrects_a_stale_period_end_report_date_to_the_real_announcement_date(self):
        with patch.object(earnings, "SessionLocal", _SessionLocal), \
             patch.object(earnings, "EarningsEvent", _models.EarningsEvent), \
             patch.object(earnings, "select", select):
            with _SessionLocal() as session:
                stock = _make_stock(session)
                stock_id = stock.id
                # Pre-fix stale row: report_date is the PERIOD-END date (2025-09-30), the
                # real announcement was 2025-10-30 — exactly the AUD264 bug this corrects.
                _make_reported_row(session, stock_id, date(2025, 9, 30), eps_actual=1.85)

            with _patch_yfinance(
                hist_rows=[{"period_end": date(2025, 9, 30), "eps_actual": 1.85}],
                announce_rows=[{"announce_date": date(2025, 10, 30), "eps_actual": 1.85}],
            ):
                corrected = earnings._backfill_report_dates_for_symbol("AAPL", stock_id)

            assert corrected == 1
            with _SessionLocal() as session:
                ev = session.query(_models.EarningsEvent).filter_by(stock_id=stock_id).one()
                assert ev.report_date == date(2025, 10, 30)
                assert ev.fiscal_year == 2025
                assert ev.fiscal_quarter == 4  # October -> Q4, matching the real announce month
                assert ev.period == "Q4 2025"

    def test_row_already_at_the_correct_date_is_left_untouched_and_not_counted(self):
        with patch.object(earnings, "SessionLocal", _SessionLocal), \
             patch.object(earnings, "EarningsEvent", _models.EarningsEvent), \
             patch.object(earnings, "select", select):
            with _SessionLocal() as session:
                stock = _make_stock(session, symbol="MSFT")
                stock_id = stock.id
                _make_reported_row(session, stock_id, date(2025, 10, 30), eps_actual=1.85)

            with _patch_yfinance(
                hist_rows=[{"period_end": date(2025, 9, 30), "eps_actual": 1.85}],
                announce_rows=[{"announce_date": date(2025, 10, 30), "eps_actual": 1.85}],
            ):
                corrected = earnings._backfill_report_dates_for_symbol("MSFT", stock_id)

            assert corrected == 0

    def test_row_with_no_matching_announce_data_is_left_untouched(self):
        """A data gap on the announce side (e.g. yfinance's earnings_dates window doesn't
        reach this far back) must leave the stale row alone rather than crash or fabricate
        a date — matching _fetch_earnings_for_symbol's own fail-open convention."""
        with patch.object(earnings, "SessionLocal", _SessionLocal), \
             patch.object(earnings, "EarningsEvent", _models.EarningsEvent), \
             patch.object(earnings, "select", select):
            with _SessionLocal() as session:
                stock = _make_stock(session, symbol="GOOGL")
                stock_id = stock.id
                _make_reported_row(session, stock_id, date(2020, 6, 30), eps_actual=9.99)

            with _patch_yfinance(
                hist_rows=[{"period_end": date(2025, 9, 30), "eps_actual": 1.85}],
                announce_rows=[{"announce_date": date(2025, 10, 30), "eps_actual": 1.85}],
            ):
                corrected = earnings._backfill_report_dates_for_symbol("GOOGL", stock_id)

            assert corrected == 0
            with _SessionLocal() as session:
                ev = session.query(_models.EarningsEvent).filter_by(stock_id=stock_id).one()
                assert ev.report_date == date(2020, 6, 30)  # unchanged

    def test_multiple_stale_rows_for_the_same_symbol_all_corrected_independently(self):
        with patch.object(earnings, "SessionLocal", _SessionLocal), \
             patch.object(earnings, "EarningsEvent", _models.EarningsEvent), \
             patch.object(earnings, "select", select):
            with _SessionLocal() as session:
                stock = _make_stock(session, symbol="NVDA")
                stock_id = stock.id
                _make_reported_row(session, stock_id, date(2025, 9, 30), eps_actual=1.85)
                _make_reported_row(session, stock_id, date(2025, 12, 31), eps_actual=2.84)

            with _patch_yfinance(
                hist_rows=[
                    {"period_end": date(2025, 9, 30), "eps_actual": 1.85},
                    {"period_end": date(2025, 12, 31), "eps_actual": 2.84},
                ],
                announce_rows=[
                    {"announce_date": date(2025, 10, 30), "eps_actual": 1.85},
                    {"announce_date": date(2026, 1, 29), "eps_actual": 2.84},
                ],
            ):
                corrected = earnings._backfill_report_dates_for_symbol("NVDA", stock_id)

            assert corrected == 2
            with _SessionLocal() as session:
                dates = sorted(
                    ev.report_date for ev in
                    session.query(_models.EarningsEvent).filter_by(stock_id=stock_id).all()
                )
                assert dates == [date(2025, 10, 30), date(2026, 1, 29)]

    def test_empty_earnings_history_returns_zero_without_crashing(self):
        with patch.object(earnings, "SessionLocal", _SessionLocal), \
             patch.object(earnings, "EarningsEvent", _models.EarningsEvent), \
             patch.object(earnings, "select", select):
            with _SessionLocal() as session:
                stock = _make_stock(session, symbol="EMPTY")
                stock_id = stock.id

            with _patch_yfinance(hist_rows=[], announce_rows=[]):
                corrected = earnings._backfill_report_dates_for_symbol("EMPTY", stock_id)

            assert corrected == 0

    def test_yfinance_exception_fails_open_returns_zero(self):
        with patch.object(earnings, "SessionLocal", _SessionLocal), \
             patch.object(earnings, "EarningsEvent", _models.EarningsEvent), \
             patch.object(earnings, "select", select):
            with _SessionLocal() as session:
                stock = _make_stock(session, symbol="CRASH")
                stock_id = stock.id

            fake_yf = MagicMock()
            fake_yf.Ticker.side_effect = RuntimeError("yfinance down")
            with patch.dict(sys.modules, {"yfinance": fake_yf}):
                corrected = earnings._backfill_report_dates_for_symbol("CRASH", stock_id)

            assert corrected == 0

    def test_only_touches_rows_for_the_given_stock_id_not_other_stocks(self):
        with patch.object(earnings, "SessionLocal", _SessionLocal), \
             patch.object(earnings, "EarningsEvent", _models.EarningsEvent), \
             patch.object(earnings, "select", select):
            with _SessionLocal() as session:
                stock_a = _make_stock(session, symbol="AMD")
                stock_b = _make_stock(session, symbol="INTC")
                stock_a_id, stock_b_id = stock_a.id, stock_b.id
                _make_reported_row(session, stock_a_id, date(2025, 9, 30), eps_actual=1.85)
                _make_reported_row(session, stock_b_id, date(2025, 9, 30), eps_actual=1.85)

            with _patch_yfinance(
                hist_rows=[{"period_end": date(2025, 9, 30), "eps_actual": 1.85}],
                announce_rows=[{"announce_date": date(2025, 10, 30), "eps_actual": 1.85}],
            ):
                corrected = earnings._backfill_report_dates_for_symbol("AMD", stock_a_id)

            assert corrected == 1
            with _SessionLocal() as session:
                ev_a = session.query(_models.EarningsEvent).filter_by(stock_id=stock_a_id).one()
                ev_b = session.query(_models.EarningsEvent).filter_by(stock_id=stock_b_id).one()
                assert ev_a.report_date == date(2025, 10, 30)  # corrected
                assert ev_b.report_date == date(2025, 9, 30)   # untouched (different stock_id)

    # ── AUD264-BACKFILL-PENDING-ROW-COLLISION: found live against real production AAPL data ──

    def test_deletes_a_redundant_pending_row_already_sitting_at_the_real_date(self):
        """The exact scenario found live in production: the normal daily sync ran AFTER this
        fix shipped but BEFORE the one-time backfill, so its own existing_pending logic had
        already inserted a pending row (eps_actual NULL) at the real announcement date for
        this same event. Without handling this, moving the stale reported row onto that same
        date raises a real UniqueViolation (confirmed live) — the fix must instead delete the
        now-redundant pending duplicate and move the reported row (which carries the real
        data) onto its correct date."""
        with patch.object(earnings, "SessionLocal", _SessionLocal), \
             patch.object(earnings, "EarningsEvent", _models.EarningsEvent), \
             patch.object(earnings, "select", select):
            with _SessionLocal() as session:
                stock = _make_stock(session, symbol="COLLIDE")
                stock_id = stock.id
                _make_reported_row(session, stock_id, date(2025, 9, 30), eps_actual=1.85)
                _make_pending_row(session, stock_id, date(2025, 10, 30))

            with _patch_yfinance(
                hist_rows=[{"period_end": date(2025, 9, 30), "eps_actual": 1.85}],
                announce_rows=[{"announce_date": date(2025, 10, 30), "eps_actual": 1.85}],
            ):
                corrected = earnings._backfill_report_dates_for_symbol("COLLIDE", stock_id)

            assert corrected == 1
            with _SessionLocal() as session:
                rows = session.query(_models.EarningsEvent).filter_by(stock_id=stock_id).all()
                assert len(rows) == 1  # the redundant pending row was deleted, not duplicated
                assert rows[0].report_date == date(2025, 10, 30)
                assert rows[0].eps_actual == 1.85  # the real, reported data survived

    def test_does_not_clobber_a_different_already_reported_row_at_the_same_date(self):
        """A genuinely different, already-reported row (eps_actual IS NOT NULL) sitting at the
        target date is NOT a redundant pending duplicate — it's real, independent data. The
        backfill must skip this row rather than silently delete or overwrite it, leaving it
        for manual review."""
        with patch.object(earnings, "SessionLocal", _SessionLocal), \
             patch.object(earnings, "EarningsEvent", _models.EarningsEvent), \
             patch.object(earnings, "select", select):
            with _SessionLocal() as session:
                stock = _make_stock(session, symbol="REALCONFLICT")
                stock_id = stock.id
                _make_reported_row(session, stock_id, date(2025, 9, 30), eps_actual=1.85)
                _make_reported_row(session, stock_id, date(2025, 10, 30), eps_actual=9.99)

            with _patch_yfinance(
                hist_rows=[{"period_end": date(2025, 9, 30), "eps_actual": 1.85}],
                announce_rows=[{"announce_date": date(2025, 10, 30), "eps_actual": 1.85}],
            ):
                corrected = earnings._backfill_report_dates_for_symbol("REALCONFLICT", stock_id)

            assert corrected == 0
            with _SessionLocal() as session:
                rows = session.query(_models.EarningsEvent).filter_by(stock_id=stock_id).all()
                assert len(rows) == 2  # both rows survive, untouched
                dates = sorted(r.report_date for r in rows)
                assert dates == [date(2025, 9, 30), date(2025, 10, 30)]

    def test_no_collision_when_target_date_is_genuinely_free(self):
        """The common case (re-confirmed here alongside the two collision tests above) — no
        row occupies the target date at all, so the update proceeds with no delete/skip
        branch involved."""
        with patch.object(earnings, "SessionLocal", _SessionLocal), \
             patch.object(earnings, "EarningsEvent", _models.EarningsEvent), \
             patch.object(earnings, "select", select):
            with _SessionLocal() as session:
                stock = _make_stock(session, symbol="NOCOLLIDE")
                stock_id = stock.id
                _make_reported_row(session, stock_id, date(2025, 9, 30), eps_actual=1.85)

            with _patch_yfinance(
                hist_rows=[{"period_end": date(2025, 9, 30), "eps_actual": 1.85}],
                announce_rows=[{"announce_date": date(2025, 10, 30), "eps_actual": 1.85}],
            ):
                corrected = earnings._backfill_report_dates_for_symbol("NOCOLLIDE", stock_id)

            assert corrected == 1
            with _SessionLocal() as session:
                rows = session.query(_models.EarningsEvent).filter_by(stock_id=stock_id).all()
                assert len(rows) == 1
                assert rows[0].report_date == date(2025, 10, 30)
