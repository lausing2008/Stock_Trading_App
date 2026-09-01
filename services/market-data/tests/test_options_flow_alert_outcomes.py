"""Tests for MPE-OPTIONS-FLOW-ALERT's DB-level machinery — _record_options_flow_alert_outcome(),
evaluate_options_flow_alert_outcomes(), and options_flow_alert_performance() in admin.py.

scheduler.py/admin.py can't be imported directly in this test environment (conftest.py stubs
sqlalchemy itself as a MagicMock) — this test pops the stubbed sqlalchemy/db modules from
sys.modules BEFORE importing anything else, so the REAL sqlalchemy + the REAL shared/db/models.py
load for this file specifically, then extracts and exec()'s the real source of each function
under test — matching test_squeeze_alert_outcomes.py's/test_broker_position_sync.py's own
established technique for this exact constraint, scoped here to OptionsFlowAlertOutcome's own
table (a genuinely separate mechanism from SqueezeAlertOutcome/PreBreakoutAlertOutcome, matching
those tables' own established "separate table, separate test file" convention).
"""
import sys

_STUBBED_MODULES = ("sqlalchemy", "sqlalchemy.orm", "sqlalchemy.dialects", "sqlalchemy.dialects.postgresql", "db")
_saved_stubs = {_mod: sys.modules.pop(_mod, None) for _mod in _STUBBED_MODULES}

import importlib.util
import pathlib
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

_models_path = pathlib.Path(__file__).resolve().parents[3] / "shared" / "db" / "models.py"
_spec = importlib.util.spec_from_file_location("db_models_under_test_ofao", _models_path)
_models = importlib.util.module_from_spec(_spec)
sys.modules["db_models_under_test_ofao"] = _models
_spec.loader.exec_module(_models)

_ENGINE = create_engine("sqlite:///:memory:")
_models.Base.metadata.create_all(
    _ENGINE,
    tables=[_models.Stock.__table__, _models.Price.__table__, _models.OptionsFlowAlertOutcome.__table__],
)

_autoincrement_counter = [0]


@event.listens_for(_models.OptionsFlowAlertOutcome, "before_insert")
def _assign_test_id(mapper, connection, target):
    if target.id is None:
        _autoincrement_counter[0] += 1
        target.id = _autoincrement_counter[0]

for _mod, _stub in _saved_stubs.items():
    if _stub is not None:
        sys.modules[_mod] = _stub
    else:
        sys.modules.pop(_mod, None)

Stock = _models.Stock
Price = _models.Price
OptionsFlowAlertOutcome = _models.OptionsFlowAlertOutcome
Market = _models.Market
TimeFrame = _models.TimeFrame

_SCHEDULER_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
_SCHEDULER_SOURCE = _SCHEDULER_PATH.read_text()
_ADMIN_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "admin.py"
_ADMIN_SOURCE = _ADMIN_PATH.read_text()

_next_id = [1000]


def _new_id() -> int:
    _next_id[0] += 1
    return _next_id[0]


def _make_session():
    session = Session(_ENGINE)
    for table in (OptionsFlowAlertOutcome.__table__, Price.__table__, Stock.__table__):
        session.execute(table.delete())
    session.commit()
    return session


def _make_stock(session, symbol="AAPL"):
    st = Stock(id=_new_id(), symbol=symbol, name=symbol, market=Market.US, exchange="NASDAQ", sector="Tech", active=True)
    session.add(st)
    session.commit()
    return st


def _make_price(session, stock_id, ts_date: date, close: float):
    session.add(Price(
        id=_new_id(), stock_id=stock_id, ts=datetime.combine(ts_date, datetime.min.time()),
        timeframe=TimeFrame.D1, open=close, high=close, low=close, close=close, volume=1000.0,
    ))
    session.commit()


def _extract_record_options_flow_alert_outcome():
    start = _SCHEDULER_SOURCE.index("def _record_options_flow_alert_outcome(")
    end = _SCHEDULER_SOURCE.index("\n\n\n_OPTIONS_FLOW_ALERT_CAL_MIN_COUNT", start)
    body = _SCHEDULER_SOURCE[start:end]
    fake_log = MagicMock()
    namespace = {"select": select, "OptionsFlowAlertOutcome": OptionsFlowAlertOutcome, "date": date, "log": fake_log}
    exec(body, namespace)  # noqa: S102 — isolated eval of real source
    return namespace["_record_options_flow_alert_outcome"], fake_log


class _CtxSession:
    """evaluate_options_flow_alert_outcomes() opens its own `with SessionLocal() as session:`
    block — wraps the ONE shared _make_session() so the function's real commit()/rollback()
    calls land on the same in-memory DB the test set up rows in."""
    def __init__(self, session):
        self._session = session

    def __enter__(self):
        return self._session

    def __exit__(self, *a):
        return False


def _extract_evaluate_options_flow_alert_outcomes(session):
    start = _SCHEDULER_SOURCE.index(
        '_OPTIONS_FLOW_ALERT_OUTCOME_EVAL_LOCK_KEY = "stockai:lock:evaluate_options_flow_alert_outcomes"'
    )
    end = _SCHEDULER_SOURCE.index("\n\n\n_VALUE_AREA_COMPUTE_LOCK_KEY", start)
    body = _SCHEDULER_SOURCE[start:end]

    # Pull the shared constants this function reuses directly out of the real source, matching
    # test_squeeze_alert_outcomes.py's own established pattern for this exact dependency shape.
    win_hurdle_start = _SCHEDULER_SOURCE.index("_SQUEEZE_OUTCOME_WIN_HURDLE_PCT = ")
    win_hurdle_end = _SCHEDULER_SOURCE.index("\n", win_hurdle_start)
    windows_start = _SCHEDULER_SOURCE.index("_SQUEEZE_OUTCOME_WINDOWS = ")
    windows_end = _SCHEDULER_SOURCE.index("\n", windows_start)
    grace_start = _SCHEDULER_SOURCE.index("_SQUEEZE_OUTCOME_CENSOR_GRACE_DAYS = ")
    grace_end = _SCHEDULER_SOURCE.index("\n", grace_start)
    const_namespace: dict = {"bisect": __import__("bisect"), "date": date, "timedelta": timedelta}
    exec(_SCHEDULER_SOURCE[win_hurdle_start:win_hurdle_end], const_namespace)  # noqa: S102
    exec(_SCHEDULER_SOURCE[windows_start:windows_end], const_namespace)  # noqa: S102
    exec(_SCHEDULER_SOURCE[grace_start:grace_end], const_namespace)  # noqa: S102

    lookup_start = _SCHEDULER_SOURCE.index("def _squeeze_outcome_lookup_price(")
    lookup_end = _SCHEDULER_SOURCE.index("\n\n\ndef evaluate_squeeze_alert_outcomes(", lookup_start)
    exec(_SCHEDULER_SOURCE[lookup_start:lookup_end], const_namespace)  # noqa: S102

    _fake_redis = MagicMock()
    _fake_redis.set.return_value = True
    namespace = {
        "select": select, "bisect": __import__("bisect"),
        "Price": Price, "TimeFrame": TimeFrame, "OptionsFlowAlertOutcome": OptionsFlowAlertOutcome,
        "SessionLocal": lambda: _CtxSession(session),
        "date": date, "datetime": datetime, "timedelta": timedelta, "timezone": timezone,
        "time": __import__("time"),
        "log": MagicMock(),
        "_get_redis": lambda: _fake_redis,
        "_record_job_status": MagicMock(),
        "_squeeze_outcome_lookup_price": const_namespace["_squeeze_outcome_lookup_price"],
        "_SQUEEZE_OUTCOME_WINDOWS": const_namespace["_SQUEEZE_OUTCOME_WINDOWS"],
        "_SQUEEZE_OUTCOME_WIN_HURDLE_PCT": const_namespace["_SQUEEZE_OUTCOME_WIN_HURDLE_PCT"],
    }
    exec(body, namespace)  # noqa: S102 — isolated eval of real source
    return namespace["evaluate_options_flow_alert_outcomes"]


# ── _record_options_flow_alert_outcome() ────────────────────────────────────────────────────

def test_record_creates_a_new_row_on_first_fire():
    session = _make_session()
    stock = _make_stock(session, "MSFT")
    record, _ = _extract_record_options_flow_alert_outcome()

    record(session, stock.id, "MSFT", 372.99, {
        "option_chain": "MSFT231222C00375000", "option_type": "call", "direction": "bullish",
        "strike": 375.0, "expiry": "2023-12-22", "total_premium": 186705.0,
        "ask_side_dominant": True, "volume_oi_ratio": 0.31, "has_sweep": True, "alert_rule": "RepeatedHits",
    })

    rows = session.execute(select(OptionsFlowAlertOutcome)).scalars().all()
    assert len(rows) == 1
    assert rows[0].symbol == "MSFT"
    assert rows[0].option_chain == "MSFT231222C00375000"
    assert rows[0].direction == "bullish"
    assert rows[0].expiry == date(2023, 12, 22)
    assert rows[0].fired_date == date.today()


def test_record_is_a_noop_on_a_second_call_same_contract_same_day():
    session = _make_session()
    stock = _make_stock(session, "MSFT")
    record, fake_log = _extract_record_options_flow_alert_outcome()
    cand = {
        "option_chain": "MSFT231222C00375000", "option_type": "call", "direction": "bullish",
        "strike": 375.0, "expiry": "2023-12-22", "total_premium": 100000.0,
        "ask_side_dominant": True, "volume_oi_ratio": 0.5, "has_sweep": True, "alert_rule": "RepeatedHits",
    }

    record(session, stock.id, "MSFT", 372.99, cand)
    record(session, stock.id, "MSFT", 380.00, {**cand, "total_premium": 250000.0})  # later cycle, same day

    rows = session.execute(select(OptionsFlowAlertOutcome)).scalars().all()
    assert len(rows) == 1
    assert rows[0].alert_price == 372.99  # the FIRST fire's price, not overwritten
    assert rows[0].total_premium == 100000.0
    fake_log.warning.assert_not_called()


def test_record_creates_a_separate_row_for_a_genuinely_different_contract_same_symbol_same_day():
    """The whole reason this table is keyed per-CONTRACT, not per-symbol: a single underlying
    can legitimately have two real, distinct flow alerts on the same day."""
    session = _make_session()
    stock = _make_stock(session, "MSFT")
    record, _ = _extract_record_options_flow_alert_outcome()

    record(session, stock.id, "MSFT", 372.99, {
        "option_chain": "MSFT231222C00375000", "option_type": "call", "direction": "bullish",
        "strike": 375.0, "expiry": "2023-12-22", "total_premium": 100000.0,
        "ask_side_dominant": True, "volume_oi_ratio": 0.5, "has_sweep": True, "alert_rule": "RepeatedHits",
    })
    record(session, stock.id, "MSFT", 372.99, {
        "option_chain": "MSFT240119P00360000", "option_type": "put", "direction": "bearish",
        "strike": 360.0, "expiry": "2024-01-19", "total_premium": 80000.0,
        "ask_side_dominant": True, "volume_oi_ratio": 0.8, "has_sweep": True, "alert_rule": "RepeatedHitsDescendingFill",
    })

    rows = session.execute(select(OptionsFlowAlertOutcome)).scalars().all()
    assert len(rows) == 2
    assert {r.option_chain for r in rows} == {"MSFT231222C00375000", "MSFT240119P00360000"}
    assert {r.direction for r in rows} == {"bullish", "bearish"}


def test_record_is_fail_open_when_expiry_is_missing():
    """A candidate with no real expiry string must still record (expiry is nullable on the
    model), never crash the whole recording attempt."""
    session = _make_session()
    stock = _make_stock(session, "XYZ")
    record, _ = _extract_record_options_flow_alert_outcome()

    record(session, stock.id, "XYZ", 50.0, {
        "option_chain": "XYZ1", "option_type": "call", "direction": "bullish",
        "strike": None, "expiry": None, "total_premium": None,
        "ask_side_dominant": True, "volume_oi_ratio": None, "has_sweep": False, "alert_rule": None,
    })

    rows = session.execute(select(OptionsFlowAlertOutcome)).scalars().all()
    assert len(rows) == 1
    assert rows[0].expiry is None


# ── evaluate_options_flow_alert_outcomes() ──────────────────────────────────────────────────

def test_evaluate_scores_a_bullish_row_as_a_win_when_price_rises():
    session = _make_session()
    stock = _make_stock(session, "AAPL")
    fired = date(2026, 1, 5)
    session.add(OptionsFlowAlertOutcome(
        id=_new_id(), stock_id=stock.id, symbol="AAPL", option_chain="AAPL1", option_type="call",
        direction="bullish", strike=100.0, fired_date=fired, alert_price=100.0, ask_side_dominant=True,
    ))
    session.commit()
    _make_price(session, stock.id, date(2026, 1, 6), 101.0)  # T+1 entry
    _make_price(session, stock.id, date(2026, 1, 16), 115.0)  # 10d later, real rise

    evaluate = _extract_evaluate_options_flow_alert_outcomes(session)
    evaluate()

    row = session.execute(select(OptionsFlowAlertOutcome)).scalar_one()
    assert row.entry_price == 101.0
    assert row.is_correct_10d is True
    assert row.return_10d > 0


def test_evaluate_scores_a_bearish_row_as_a_win_when_price_falls():
    session = _make_session()
    stock = _make_stock(session, "AAPL")
    fired = date(2026, 1, 5)
    session.add(OptionsFlowAlertOutcome(
        id=_new_id(), stock_id=stock.id, symbol="AAPL", option_chain="AAPL2", option_type="put",
        direction="bearish", strike=100.0, fired_date=fired, alert_price=100.0, ask_side_dominant=True,
    ))
    session.commit()
    _make_price(session, stock.id, date(2026, 1, 6), 100.0)
    _make_price(session, stock.id, date(2026, 1, 16), 85.0)  # real fall

    evaluate = _extract_evaluate_options_flow_alert_outcomes(session)
    evaluate()

    row = session.execute(select(OptionsFlowAlertOutcome)).scalar_one()
    assert row.is_correct_10d is True
    assert row.return_10d < 0


def test_evaluate_scores_a_bullish_row_as_a_loss_when_price_falls():
    session = _make_session()
    stock = _make_stock(session, "AAPL")
    fired = date(2026, 1, 5)
    session.add(OptionsFlowAlertOutcome(
        id=_new_id(), stock_id=stock.id, symbol="AAPL", option_chain="AAPL3", option_type="call",
        direction="bullish", strike=100.0, fired_date=fired, alert_price=100.0, ask_side_dominant=True,
    ))
    session.commit()
    _make_price(session, stock.id, date(2026, 1, 6), 100.0)
    _make_price(session, stock.id, date(2026, 1, 16), 90.0)  # a real drop, not a rise

    evaluate = _extract_evaluate_options_flow_alert_outcomes(session)
    evaluate()

    row = session.execute(select(OptionsFlowAlertOutcome)).scalar_one()
    assert row.is_correct_10d is False


def test_evaluate_leaves_a_window_open_when_it_hasnt_closed_yet():
    session = _make_session()
    stock = _make_stock(session, "AAPL")
    fired = date.today() - timedelta(days=2)  # far too recent for the 10d window to have closed
    session.add(OptionsFlowAlertOutcome(
        id=_new_id(), stock_id=stock.id, symbol="AAPL", option_chain="AAPL4", option_type="call",
        direction="bullish", strike=100.0, fired_date=fired, alert_price=100.0, ask_side_dominant=True,
    ))
    session.commit()
    _make_price(session, stock.id, fired + timedelta(days=1), 101.0)

    evaluate = _extract_evaluate_options_flow_alert_outcomes(session)
    evaluate()

    row = session.execute(select(OptionsFlowAlertOutcome)).scalar_one()
    assert row.entry_price == 101.0  # entry itself resolves right away
    assert row.return_10d is None  # but the 10d forward window correctly stays open


def test_evaluate_never_re_evaluates_an_already_filled_window():
    session = _make_session()
    stock = _make_stock(session, "AAPL")
    fired = date(2026, 1, 5)
    session.add(OptionsFlowAlertOutcome(
        id=_new_id(), stock_id=stock.id, symbol="AAPL", option_chain="AAPL5", option_type="call",
        direction="bullish", strike=100.0, fired_date=fired, alert_price=100.0, ask_side_dominant=True,
        entry_date=date(2026, 1, 6), entry_price=101.0,
        price_1d=102.0, return_1d=0.0099, is_correct_1d=True,
    ))
    session.commit()
    # If the price bucket below were consulted for the 1d window again, it would resolve to a
    # DIFFERENT value than the pre-filled one — proving the "already filled" guard is real.
    _make_price(session, stock.id, date(2026, 1, 7), 999.0)

    evaluate = _extract_evaluate_options_flow_alert_outcomes(session)
    evaluate()

    row = session.execute(select(OptionsFlowAlertOutcome)).scalar_one()
    assert row.price_1d == 102.0  # untouched — never re-evaluated
