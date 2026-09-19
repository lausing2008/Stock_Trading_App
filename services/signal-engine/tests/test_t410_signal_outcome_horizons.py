"""T410-AUD-C02-C03 — independent per-window signal outcome resolution.

evaluate_signal_outcomes() can't be driven end-to-end in this test environment (its top-level
`from common.jwt_auth import get_current_username` isn't importable here) — following
test_delisted_loss_scoring.py's own established convention exactly: the REAL closures
(_lookup_outcome_price, _window_return) and the REAL Phase 3 code block are extracted via
source-text slicing and exec'd together against a REAL in-memory-SQLite session, so these tests
exercise the actual production price-lookup and resolution logic, not a reimplementation of it
(the same discipline options_income_backtest.py's own docstring names: "a backtest of a
parallel implementation measures the parallel implementation").
"""
import importlib.util
import pathlib
import sys
import textwrap
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

_models_path = pathlib.Path(__file__).resolve().parents[3] / "shared" / "db" / "models.py"
_spec = importlib.util.spec_from_file_location("db_models_under_test_t410", _models_path)
_models = importlib.util.module_from_spec(_spec)
sys.modules["db_models_under_test_t410"] = _models
_spec.loader.exec_module(_models)

Signal = _models.Signal
SignalOutcomeHorizon = _models.SignalOutcomeHorizon
SignalHorizon = _models.SignalHorizon
SignalType = _models.SignalType
Stock = _models.Stock
Market = _models.Market
Exchange = _models.Exchange
Base = _models.Base

_ROUTES_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "outcomes.py"
_ROUTES_SOURCE = _ROUTES_PATH.read_text()

_OUTCOME_HOLD_DAYS = {"SHORT": 7, "SWING": 14, "LONG": 28, "GROWTH": 14}
_SELL_OUTCOME_HOLD_DAYS = {"SHORT": 5, "SWING": 7, "LONG": 10, "GROWTH": 7}
_OUTCOME_WIN_HURDLE_PCT = 0.005
_OUTCOME_CENSOR_GRACE_DAYS = 10


def _extract(start_marker: str, end_marker: str) -> str:
    start = _ROUTES_SOURCE.index(start_marker)
    end = _ROUTES_SOURCE.index(end_marker, start)
    return _ROUTES_SOURCE[start:end]


# The two closures Phase 3 depends on, verbatim from production. Already indented 4 spaces in
# the source (nested inside evaluate_signal_outcomes), which is exactly what "if True:\n" below
# needs as its body — no re-indenting, which is what corrupted the nested docstrings/if-blocks
# on the first attempt.
_PRICE_HELPERS_SRC = "if True:\n" + _extract(
    '    def _lookup_outcome_price(stock_id: int, on_or_after: "date")',
    '\n    def _fetch_research(',
)
_PHASE3_SRC = "if True:\n" + _extract(
    "    # ── Phase 3 (T410-AUD-C02-C03): independent per-window resolution",
    "\n    # AUD232-003: confidence-calibration's Redis cache",
)


_next_horizon_id = [0]


@event.listens_for(SignalOutcomeHorizon, "before_insert")
def _assign_sqlite_bigint_pk(mapper, connection, target):
    """SQLite quirk, not a production concern: a column only auto-increments when declared
    EXACTLY `INTEGER PRIMARY KEY` (the ROWID alias). SQLAlchemy's BigInteger compiles to
    SQLite's `BIGINT`, which does NOT get that treatment, so production's own code — which
    never sets `id` explicitly, since real Postgres handles the sequence — hits a NOT NULL
    failure here. Assigning it in a before_insert hook keeps production untouched."""
    if target.id is None:
        _next_horizon_id[0] += 1
        target.id = _next_horizon_id[0]


class _NullLog:
    def info(self, *a, **k):
        pass


def _make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[Stock.__table__, Signal.__table__, SignalOutcomeHorizon.__table__])
    return Session(engine)


def _add_stock(session, stock_id, symbol, delisted=False):
    session.add(Stock(id=stock_id, symbol=symbol, name=symbol, market=Market.US,
                       exchange=Exchange.NASDAQ, active=True, delisted=delisted))


_next_sig_id = [0]


def _add_signal(session, *, stock_id, horizon, direction, first_buy_sell_at, confidence=60.0):
    _next_sig_id[0] += 1
    sid = _next_sig_id[0]
    session.add(Signal(
        id=sid, stock_id=stock_id, horizon=horizon,
        signal=direction, first_buy_sell_signal=direction, first_buy_sell_at=first_buy_sell_at,
        confidence=confidence, bullish_probability=0.6,
        first_buy_sell_confidence=confidence, first_buy_sell_bullish_probability=0.6,
        ts=first_buy_sell_at,
    ))
    return sid


def _run_phase3(session, pending_signals, price_map: dict, today: date):
    """`price_map`: {stock_id: [(date, close), ...]} — the exact shape _outcome_price_map
    already uses in production. `pending_signals`: [(Signal, symbol, is_delisted), ...]."""
    from collections import defaultdict
    import bisect as _bisect
    namespace = {
        "bisect": _bisect, "date": date, "datetime": datetime, "timezone": timezone,
        "timedelta": timedelta, "select": select,
        "_outcome_price_map": defaultdict(list, price_map),
        "_OUTCOME_CENSOR_GRACE_DAYS": _OUTCOME_CENSOR_GRACE_DAYS,
        "_OUTCOME_WIN_HURDLE_PCT": _OUTCOME_WIN_HURDLE_PCT,
        "_OUTCOME_HOLD_DAYS": _OUTCOME_HOLD_DAYS,
        "_SELL_OUTCOME_HOLD_DAYS": _SELL_OUTCOME_HOLD_DAYS,
        "SignalOutcomeHorizon": SignalOutcomeHorizon,
        "session": session, "pending_signals": pending_signals, "today": today,
        "log": _NullLog(),
    }
    exec(_PRICE_HELPERS_SRC, namespace)  # noqa: S102 — isolated eval of real production source
    namespace["_window_return"] = namespace["_window_return"]  # defined by the block above
    exec(_PHASE3_SRC, namespace)  # noqa: S102 — isolated eval of real production source
    return {k: namespace[k] for k in ("horizon_created", "horizon_resolved", "horizon_missing")}


def _rows(session):
    return session.execute(select(SignalOutcomeHorizon)).scalars().all()


# ── The central acceptance criterion ────────────────────────────────────────────────────

def test_a_long_buy_gets_a_resolved_5day_outcome_while_its_28day_primary_stays_pending():
    """THE finding this table exists to fix. LONG's primary is 28 calendar days; its 5-day
    window is fully determined after 5. Entry T+1 on 2026-01-02, today = 2026-01-08 -> 6 days
    held -> the 5-day window (target 2026-01-07) has a real close; the 28-day primary
    (target 2026-01-30) has not remotely arrived."""
    session = _make_session()
    _add_stock(session, 1, "LONGCO")
    sid = _add_signal(session, stock_id=1, horizon=SignalHorizon.LONG, direction=SignalType.BUY,
                       first_buy_sell_at=datetime(2026, 1, 1, 20, 0))
    session.commit()

    price_map = {1: [(date(2026, 1, 2), 100.0), (date(2026, 1, 7), 106.0), (date(2026, 1, 8), 107.0)]}
    sig = session.get(Signal, sid)
    counts = _run_phase3(session, [(sig, "LONGCO", False)], price_map, today=date(2026, 1, 8))
    session.commit()

    rows = {r.window_days: r for r in _rows(session)}
    assert rows[5].status == "resolved"
    assert rows[5].is_correct is True   # (106-100)/100 = 6% > hurdle
    assert rows[5].pct_return == pytest.approx(0.06)
    assert rows[28].status == "pending"
    assert rows[28].exit_price is None and rows[28].is_correct is None
    assert rows[28].is_primary_window is True
    assert rows[5].is_primary_window is False
    assert counts["horizon_resolved"] == 1 and counts["horizon_created"] >= 1


def test_swing_long_growth_all_get_early_5day_coverage_independent_of_their_own_primary():
    """Directly answers the measured gap: SWING/LONG/GROWTH had ZERO resolved 5-day outcomes
    at any given time because no row existed at all until their (14/28/14-day) primary closed."""
    session = _make_session()
    for i, h in enumerate([SignalHorizon.SWING, SignalHorizon.LONG, SignalHorizon.GROWTH], start=1):
        _add_stock(session, i, f"S{i}")
    session.commit()
    entry_dt = datetime(2026, 1, 1, 20, 0)
    sigs = []
    for i, h in enumerate([SignalHorizon.SWING, SignalHorizon.LONG, SignalHorizon.GROWTH], start=1):
        sid = _add_signal(session, stock_id=i, horizon=h, direction=SignalType.BUY, first_buy_sell_at=entry_dt)
        session.commit()
        sigs.append((session.get(Signal, sid), f"S{i}", False))

    price_map = {i: [(date(2026, 1, 2), 50.0), (date(2026, 1, 7), 53.0)] for i in (1, 2, 3)}
    counts = _run_phase3(session, sigs, price_map, today=date(2026, 1, 8))
    session.commit()

    fiveday = [r for r in _rows(session) if r.window_days == 5]
    assert len(fiveday) == 3
    assert all(r.status == "resolved" for r in fiveday)
    assert counts["horizon_resolved"] == 3


# ── Idempotency (scoping doc requirement 4) ─────────────────────────────────────────────

def test_a_resolved_row_is_never_rewritten_on_a_second_run():
    session = _make_session()
    _add_stock(session, 1, "AAA")
    sid = _add_signal(session, stock_id=1, horizon=SignalHorizon.SHORT, direction=SignalType.BUY,
                       first_buy_sell_at=datetime(2026, 1, 1, 20, 0))
    session.commit()
    price_map = {1: [(date(2026, 1, 2), 100.0), (date(2026, 1, 7), 110.0), (date(2026, 1, 8), 90.0)]}
    sig = session.get(Signal, sid)

    _run_phase3(session, [(sig, "AAA", False)], price_map, today=date(2026, 1, 8))
    session.commit()
    resolved_at_1 = {r.window_days: (r.exit_price, r.resolved_at) for r in _rows(session)}

    # Re-run with DIFFERENT (wrong) prices — if the guard failed, this would overwrite reality.
    bad_price_map = {1: [(date(2026, 1, 2), 100.0), (date(2026, 1, 7), 999.0), (date(2026, 1, 8), 999.0)]}
    _run_phase3(session, [(sig, "AAA", False)], bad_price_map, today=date(2026, 1, 9))
    session.commit()
    resolved_at_2 = {r.window_days: (r.exit_price, r.resolved_at) for r in _rows(session)}

    assert resolved_at_1 == resolved_at_2, "a resolved row must never be rewritten by a later run"


def test_a_pending_row_becomes_resolved_once_its_target_date_arrives():
    session = _make_session()
    _add_stock(session, 1, "BBB")
    sid = _add_signal(session, stock_id=1, horizon=SignalHorizon.LONG, direction=SignalType.BUY,
                       first_buy_sell_at=datetime(2026, 1, 1, 20, 0))
    session.commit()
    sig = session.get(Signal, sid)

    price_map_day6 = {1: [(date(2026, 1, 2), 100.0), (date(2026, 1, 7), 106.0)]}
    _run_phase3(session, [(sig, "BBB", False)], price_map_day6, today=date(2026, 1, 8))
    session.commit()
    assert {r.window_days: r.status for r in _rows(session)}[10] == "pending"

    price_map_day12 = dict(price_map_day6)
    price_map_day12[1] = price_map_day6[1] + [(date(2026, 1, 12), 108.0)]
    counts = _run_phase3(session, [(sig, "BBB", False)], price_map_day12, today=date(2026, 1, 13))
    session.commit()
    row10 = {r.window_days: r for r in _rows(session)}[10]
    assert row10.status == "resolved" and row10.exit_price == 108.0
    assert counts["horizon_resolved"] == 1


# ── Missing price / grace window (mirrors the primary's own censoring semantics) ────────

def test_missing_price_within_grace_window_stays_pending_not_missing():
    session = _make_session()
    _add_stock(session, 1, "CCC")
    sid = _add_signal(session, stock_id=1, horizon=SignalHorizon.SHORT, direction=SignalType.BUY,
                       first_buy_sell_at=datetime(2026, 1, 1, 20, 0))
    session.commit()
    sig = session.get(Signal, sid)
    price_map = {1: [(date(2026, 1, 2), 100.0)]}  # nothing at/after the 5d target
    # target = 2026-01-07; today = 2026-01-10 -> 3 days late, inside the 10-day grace window
    _run_phase3(session, [(sig, "CCC", False)], price_map, today=date(2026, 1, 10))
    session.commit()
    row = {r.window_days: r for r in _rows(session)}[5]
    assert row.status == "pending"


def test_missing_price_past_grace_window_becomes_missing_price():
    session = _make_session()
    _add_stock(session, 1, "DDD")
    sid = _add_signal(session, stock_id=1, horizon=SignalHorizon.SHORT, direction=SignalType.BUY,
                       first_buy_sell_at=datetime(2026, 1, 1, 20, 0))
    session.commit()
    sig = session.get(Signal, sid)
    price_map = {1: [(date(2026, 1, 2), 100.0)]}
    # target = 2026-01-07; today = 2026-01-20 -> 13 days late, past the 10-day grace window
    counts = _run_phase3(session, [(sig, "DDD", False)], price_map, today=date(2026, 1, 20))
    session.commit()
    row = {r.window_days: r for r in _rows(session)}[5]
    assert row.status == "missing_price"
    assert row.exit_price is None and row.is_correct is None
    assert counts["horizon_missing"] >= 1


def test_delisted_stock_is_not_scored_as_a_loss_matching_the_auxiliary_window_precedent():
    """The PRIMARY row's own 5/10/20d auxiliary columns never applied the delisting-loss rule
    (_window_return takes no is_delisted parameter) — this table stays consistent with that,
    rather than inventing a stricter rule for auxiliary windows than the existing precedent."""
    session = _make_session()
    _add_stock(session, 1, "DELISTED", delisted=True)
    sid = _add_signal(session, stock_id=1, horizon=SignalHorizon.SHORT, direction=SignalType.BUY,
                       first_buy_sell_at=datetime(2026, 1, 1, 20, 0))
    session.commit()
    sig = session.get(Signal, sid)
    price_map = {1: [(date(2026, 1, 2), 100.0)]}
    _run_phase3(session, [(sig, "DELISTED", True)], price_map, today=date(2026, 1, 20))
    session.commit()
    row = {r.window_days: r for r in _rows(session)}[5]
    assert row.status == "missing_price"
    assert row.is_correct is None, "must stay NULL/censored, never manufactured as a loss"


# ── SELL SHORT's collapsed window (hold_days == 5) ──────────────────────────────────────

def test_sell_short_primary_and_5day_window_collapse_into_one_row():
    """SELL SHORT's own hold_days is 5 — the SAME value as the standard 5-day auxiliary
    window. Must produce exactly ONE row (not a unique-constraint collision), correctly
    flagged as the primary."""
    session = _make_session()
    _add_stock(session, 1, "EEE")
    sid = _add_signal(session, stock_id=1, horizon=SignalHorizon.SHORT, direction=SignalType.SELL,
                       first_buy_sell_at=datetime(2026, 1, 1, 20, 0))
    session.commit()
    sig = session.get(Signal, sid)
    price_map = {1: [(date(2026, 1, 2), 100.0), (date(2026, 1, 7), 94.0)]}
    _run_phase3(session, [(sig, "EEE", False)], price_map, today=date(2026, 1, 8))
    session.commit()

    rows = _rows(session)
    assert len(rows) == 3  # {5, 10, 20} — hold_days=5 collapses into the window_days=5 row
    five = {r.window_days: r for r in rows}[5]
    assert five.is_primary_window is True
    assert five.is_correct is True  # SELL wins on a DOWN move: (94-100)/100 = -6% < -hurdle


# ── No entry price yet — nothing is written, matching the primary's own precedent ───────

def test_no_entry_price_writes_nothing_and_can_be_retried_later():
    session = _make_session()
    _add_stock(session, 1, "FFF")
    sid = _add_signal(session, stock_id=1, horizon=SignalHorizon.SHORT, direction=SignalType.BUY,
                       first_buy_sell_at=datetime(2026, 1, 1, 20, 0))
    session.commit()
    sig = session.get(Signal, sid)
    counts = _run_phase3(session, [(sig, "FFF", False)], {1: []}, today=date(2026, 1, 8))
    session.commit()
    assert _rows(session) == []
    assert counts == {"horizon_created": 0, "horizon_resolved": 0, "horizon_missing": 0}

# ── The candidate-cutoff floor (source-text, since this governs the SQL query outside
# what _run_phase3's unit tests can exercise — they call it with a pre-supplied candidate
# list, bypassing the real pending_signals query entirely) ─────────────────────────────

def test_candidate_cutoff_has_an_explicit_5day_floor_not_just_the_hold_day_minimums():
    """Without this floor, if _SELL_OUTCOME_HOLD_DAYS["SHORT"] (currently 5, today's only
    5-day primary) were ever raised, the candidate query's min_hold would silently rise past
    5 and a signal could age past the point its OWN 5-day auxiliary window is resolvable
    before ever becoming a candidate — reproducing exactly the gap this table exists to
    close, just shifted from 28 days to whatever the new minimum became."""
    src = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "outcomes.py"
    body = src.read_text()
    assert "min_hold = min(min(_OUTCOME_HOLD_DAYS.values()), min(_SELL_OUTCOME_HOLD_DAYS.values()), 5)" in body


# ── GET /signals/horizon_coverage (source-text, matching the read-only-route convention
# this file's siblings already use for endpoints that need real FastAPI/DB wiring) ────────

def test_horizon_coverage_endpoint_is_registered_and_reads_the_new_table():
    src = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "analytics.py"
    body = src.read_text()
    assert '@router.get("/horizon_coverage")' in body
    assert "SignalOutcomeHorizon" in body
    start = body.index("def get_horizon_coverage(")
    fn = body[start:body.index("\n@router.get", start) if "\n@router.get" in body[start:] else len(body)]
    # Must not read/write signal_outcomes at all — this is a NEW, independent consumer of the
    # NEW table only, never touching the 201-reference surface the scoping audit protected.
    assert "SignalOutcome." not in fn.replace("SignalOutcomeHorizon.", "")


def test_horizon_coverage_default_lookback_uses_et_aware_today_not_naive():
    """A fresh instance of the exact AUD-T409 bug was caught and fixed while writing this
    endpoint — asserted here so it cannot silently regress back to date.today()."""
    src = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "analytics.py"
    body = src.read_text()
    start = body.index("def get_horizon_coverage(")
    fn = body[start:start + 1500]
    assert "_today_et()" in fn
    assert "date.today()" not in fn
