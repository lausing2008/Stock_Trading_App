"""Tests for T230-PORTFOLIO-BROKER-SYNC's sync_broker_positions().

paper_trading_engine.py can't be imported directly in this test environment — conftest.py
stubs `sqlalchemy` itself as a MagicMock (needed so ingestion.py-adjacent modules don't need a
real Postgres driver at import time), which breaks any real ORM query construction. This test
pops the stubbed sqlalchemy/db modules from sys.modules BEFORE importing anything else, so the
REAL sqlalchemy + the REAL shared/db/models.py load for this file specifically — matching the
"load the real implementation instead of the blanket stub" technique conftest.py itself
already uses for common.indicators, just applied one level up. sync_broker_positions()'s and
_handle_broker_error_if_token_rejected()'s actual source is then extracted and exec()'d against
this real session, so these tests exercise the real logic, not a hand-copied duplicate.
"""
import sys

# conftest.py stubs BOTH `sqlalchemy` and `db` as MagicMock() before this file is collected —
# but OTHER test files collected in the same pytest run (e.g. test_macro_events_from_db.py)
# import modules from src/ that themselves do `from sqlalchemy import select` at import time,
# and rely on getting conftest.py's permissive MagicMock `select`, not the real one (their own
# mocks are built assuming an untyped, anything-goes query-construction API). Pytest collects
# every test file's module-level code before running any test, so ANY global sys.modules
# mutation here is visible to every other file in the same run — both `sqlalchemy` and `db`
# must be fully restored to their stubbed state immediately after this file's own real imports
# resolve, not left swapped for the rest of the session. (A prior version of this fix only
# restored `db`, reasoning the real sqlalchemy was "a strict superset" — that reasoning was
# wrong: other test files' *mocks*, not real sqlalchemy behavior, depend on `select` itself
# being a MagicMock.)
_STUBBED_MODULES = ("sqlalchemy", "sqlalchemy.orm", "sqlalchemy.dialects", "sqlalchemy.dialects.postgresql", "db")
_saved_stubs = {_mod: sys.modules.pop(_mod, None) for _mod in _STUBBED_MODULES}

import importlib.util
import pathlib
from datetime import datetime, timezone
from unittest.mock import MagicMock

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

_models_path = pathlib.Path(__file__).resolve().parents[3] / "shared" / "db" / "models.py"
_spec = importlib.util.spec_from_file_location("db_models_under_test", _models_path)
_models = importlib.util.module_from_spec(_spec)
sys.modules["db_models_under_test"] = _models
_spec.loader.exec_module(_models)

# Build the ONE shared in-memory engine now, while the real sqlalchemy is still active —
# sqlalchemy's create_engine() does its own dynamic dialect-plugin lookup (importlib-based) at
# CALL time, not just at import time, so calling it lazily inside a test function (after the
# stub restoration below) raises `NoSuchModuleError: Can't load plugin: sqlalchemy.dialects:
# sqlite` once sys.modules["sqlalchemy.dialects"] has been swapped back to a MagicMock. Every
# test in this file shares this one engine + its tables; _make_session() below just wraps a
# fresh Session around it and each test cleans up its own rows (see _make_session's docstring).
_ENGINE = create_engine("sqlite:///:memory:")
_models.Base.metadata.create_all(
    _ENGINE,
    tables=[_models.User.__table__, _models.BrokerConnection.__table__,
            _models.UserPosition.__table__, _models.PositionTrade.__table__],
)

# Restore every stub now — this file's own module-level names (create_engine, select, Session,
# _models, _ENGINE) already hold real, working references; later-collected test files must see
# the ORIGINAL stubbed sys.modules state.
for _mod, _stub in _saved_stubs.items():
    if _stub is not None:
        sys.modules[_mod] = _stub
    else:
        sys.modules.pop(_mod, None)

BrokerConnection = _models.BrokerConnection
UserPosition = _models.UserPosition
PositionTrade = _models.PositionTrade
User = _models.User
Base = _models.Base

_ENGINE_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "paper_trading_engine.py"
_ENGINE_SOURCE = _ENGINE_PATH.read_text()


class _FakeBroker:
    def __init__(self, positions, raise_exc=None):
        self._positions = positions
        self._raise_exc = raise_exc

    def get_account(self, account_id=None):
        if self._raise_exc:
            raise self._raise_exc
        acct = MagicMock()
        acct.open_positions = self._positions
        return acct


def _make_bp(symbol, qty, avg_cost):
    bp = MagicMock()
    bp.symbol = symbol
    bp.qty = qty
    bp.avg_cost = avg_cost
    return bp


def _extract_sync_broker_positions(broker_factory, is_token_rejected_error=lambda exc: False):
    """Pulls sync_broker_positions()'s (and its small helper class's) real source out of
    paper_trading_engine.py and exec()s it against real sqlalchemy/models, with only the
    broker factory / token-rejection detector stubbed (both are external-service boundaries,
    not logic under test here)."""
    start = _ENGINE_SOURCE.index("def sync_broker_positions(")
    end = _ENGINE_SOURCE.index("\n\n# ── PT-3:", _ENGINE_SOURCE.index("class _FakePortfolioForConn"))
    func_and_class_source = _ENGINE_SOURCE[start:end]

    def _handle_broker_error_if_token_rejected_stub(session, portfolio_like, exc):
        return is_token_rejected_error(exc)

    namespace = {
        "select": select,
        "BrokerConnection": BrokerConnection,
        "UserPosition": UserPosition,
        "SessionLocal": None,  # never called — sync_broker_positions always receives a session in these tests
        "datetime": datetime,
        "timezone": timezone,
        "log": MagicMock(),
        "_handle_broker_error_if_token_rejected": _handle_broker_error_if_token_rejected_stub,
    }
    exec(func_and_class_source, namespace)  # noqa: S102 — isolated eval of real source

    # Patch in a fake broker factory at the exact import points the function uses internally
    # (`from src.api.broker import _decrypt_config` / `from src.services.broker import get_broker`)
    # by pre-registering fake modules under those dotted paths.
    import types
    fake_broker_api = types.ModuleType("src.api.broker")
    fake_broker_api._decrypt_config = lambda cfg: cfg
    fake_broker_svc = types.ModuleType("src.services.broker")
    fake_broker_svc.get_broker = broker_factory
    sys.modules["src.api.broker"] = fake_broker_api
    sys.modules["src.services.broker"] = fake_broker_svc
    sys.modules.setdefault("src", types.ModuleType("src"))
    sys.modules.setdefault("src.api", types.ModuleType("src.api"))
    sys.modules.setdefault("src.services", types.ModuleType("src.services"))

    return namespace["sync_broker_positions"]


def _make_session():
    """Fresh Session bound to the ONE shared _ENGINE (see the module-level comment above for
    why a new engine per test can't be created lazily here) — cleans all rows first so each
    test starts from an empty slate despite sharing the underlying SQLite database."""
    session = Session(_ENGINE)
    for table in (PositionTrade.__table__, UserPosition.__table__, BrokerConnection.__table__, User.__table__):
        session.execute(table.delete())
    session.commit()
    return session


def _make_conn(session, user_id=1, broker_type="etrade_sandbox", active=True, authorized=True):
    conn = BrokerConnection(
        user_id=user_id, name="Test", broker_type=broker_type, account_id="ACCT1",
        config={}, is_active=active, is_authorized=authorized,
    )
    session.add(conn)
    session.commit()
    return conn


# ── source-text checks ─────────────────────────────────────────────────────────

def test_wired_into_the_scheduler_next_to_the_order_fill_poll():
    _scheduler_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
    scheduler_source = _scheduler_path.read_text()
    assert "sync_broker_positions" in scheduler_source
    idx = scheduler_source.index("poll_broker_order_fills()")
    assert "sync_broker_positions()" in scheduler_source[idx:idx + 500]


def test_never_overwrites_a_manual_position_for_the_same_symbol():
    start = _ENGINE_SOURCE.index("def sync_broker_positions(")
    end = _ENGINE_SOURCE.index("\n\n# ── PT-3:", start)
    body = _ENGINE_SOURCE[start:end]
    assert "row.broker_connection_id is None" in body
    assert "conflicts += 1" in body


# ── behavioral checks against the real, extracted sync_broker_positions() ─────

def test_creates_a_new_synced_position_when_none_existed():
    session = _make_session()
    conn = _make_conn(session)
    broker = _FakeBroker([_make_bp("AAPL", 10, 150.0)])
    func = _extract_sync_broker_positions(lambda bt, cfg: broker)
    func(session=session)
    rows = session.execute(select(UserPosition)).scalars().all()
    assert len(rows) == 1
    assert rows[0].symbol == "AAPL"
    assert rows[0].shares == 10
    assert rows[0].avg_cost == 150.0
    assert rows[0].broker_connection_id == conn.id
    assert rows[0].broker_synced_at is not None


def test_updates_an_existing_synced_position_from_the_same_connection():
    session = _make_session()
    conn = _make_conn(session)
    session.add(UserPosition(
        user_id=1, symbol="AAPL", shares=5, avg_cost=100.0, currency="USD",
        broker_connection_id=conn.id,
    ))
    session.commit()
    broker = _FakeBroker([_make_bp("AAPL", 12, 145.0)])
    func = _extract_sync_broker_positions(lambda bt, cfg: broker)
    func(session=session)
    rows = session.execute(select(UserPosition)).scalars().all()
    assert len(rows) == 1
    assert rows[0].shares == 12
    assert rows[0].avg_cost == 145.0


def test_never_overwrites_a_manual_position_behaviorally():
    """The core safety property: a manually-entered position (broker_connection_id IS NULL)
    for a symbol the broker also happens to report must be left completely untouched."""
    session = _make_session()
    conn = _make_conn(session)
    session.add(UserPosition(
        user_id=1, symbol="AAPL", shares=999, avg_cost=1.23, currency="USD",
        broker_connection_id=None,  # manual entry
    ))
    session.commit()
    broker = _FakeBroker([_make_bp("AAPL", 10, 150.0)])
    func = _extract_sync_broker_positions(lambda bt, cfg: broker)
    func(session=session)
    rows = session.execute(select(UserPosition)).scalars().all()
    assert len(rows) == 1
    assert rows[0].shares == 999  # untouched
    assert rows[0].avg_cost == 1.23
    assert rows[0].broker_connection_id is None


def test_never_overwrites_a_position_owned_by_a_different_broker_connection():
    session = _make_session()
    conn1 = _make_conn(session, broker_type="etrade_sandbox")
    conn2 = BrokerConnection(
        user_id=1, name="Other", broker_type="etrade", account_id="ACCT2",
        config={}, is_active=True, is_authorized=True,
    )
    session.add(conn2)
    session.commit()
    session.add(UserPosition(
        user_id=1, symbol="AAPL", shares=7, avg_cost=200.0, currency="USD",
        broker_connection_id=conn1.id,
    ))
    session.commit()

    # Simulate conn2's own sync pass reporting the same symbol.
    broker = _FakeBroker([_make_bp("AAPL", 3, 50.0)])

    def _factory(bt, cfg):
        return broker
    func = _extract_sync_broker_positions(_factory)

    # Only conn2 is active+authorized for this call — pretend conn1 got deactivated so we
    # isolate exactly conn2's pass without conn1 also running and re-asserting its own row.
    conn1.is_active = False
    session.commit()
    func(session=session)
    rows = session.execute(select(UserPosition)).scalars().all()
    assert len(rows) == 1
    assert rows[0].shares == 7  # conn1's row, untouched by conn2's sync
    assert rows[0].broker_connection_id == conn1.id


def test_removes_a_synced_position_no_longer_reported_by_the_broker():
    session = _make_session()
    conn = _make_conn(session)
    session.add(UserPosition(
        user_id=1, symbol="OLDSYM", shares=4, avg_cost=10.0, currency="USD",
        broker_connection_id=conn.id,
    ))
    session.commit()
    broker = _FakeBroker([])  # broker now reports zero positions — OLDSYM was sold externally
    func = _extract_sync_broker_positions(lambda bt, cfg: broker)
    func(session=session)
    rows = session.execute(select(UserPosition)).scalars().all()
    assert rows == []


def test_does_not_remove_a_manual_position_even_if_broker_reports_nothing():
    session = _make_session()
    _make_conn(session)
    session.add(UserPosition(
        user_id=1, symbol="MANUAL", shares=1, avg_cost=1.0, currency="USD",
        broker_connection_id=None,
    ))
    session.commit()
    broker = _FakeBroker([])
    func = _extract_sync_broker_positions(lambda bt, cfg: broker)
    func(session=session)
    rows = session.execute(select(UserPosition)).scalars().all()
    assert len(rows) == 1
    assert rows[0].symbol == "MANUAL"


def test_skips_unauthorized_or_inactive_connections():
    session = _make_session()
    _make_conn(session, authorized=False)
    broker = _FakeBroker([_make_bp("AAPL", 10, 150.0)])
    func = _extract_sync_broker_positions(lambda bt, cfg: broker)
    func(session=session)
    rows = session.execute(select(UserPosition)).scalars().all()
    assert rows == []


def test_fetch_failure_for_one_connection_does_not_abort_the_whole_sync():
    session = _make_session()
    conn1 = _make_conn(session, broker_type="etrade_sandbox")
    conn2 = BrokerConnection(
        user_id=1, name="Other", broker_type="etrade", account_id="ACCT2",
        config={}, is_active=True, is_authorized=True,
    )
    session.add(conn2)
    session.commit()

    broken_broker = _FakeBroker([], raise_exc=RuntimeError("network error"))
    working_broker = _FakeBroker([_make_bp("MSFT", 5, 300.0)])

    def _factory(bt, cfg):
        return broken_broker if bt == "etrade_sandbox" else working_broker
    func = _extract_sync_broker_positions(_factory)
    func(session=session)
    rows = session.execute(select(UserPosition)).scalars().all()
    assert len(rows) == 1
    assert rows[0].symbol == "MSFT"
