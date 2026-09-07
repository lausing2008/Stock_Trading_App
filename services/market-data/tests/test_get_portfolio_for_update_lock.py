"""Regression tests for the cash-corruption race found in the 2026-09-06 deep audit
(priority item #6): with_for_update() appeared nowhere in the codebase, and current_cash was
mutated read-modify-write in HTTP handlers (manual_exit_trade, liquidate_portfolio,
reset_portfolio, set_capital) that no lock covered — the scheduler's own _PAPER_TRADING_LOCK
only ever serialized scheduler-vs-scheduler runs, never a concurrent HTTP request.

Concrete failure this closes: two concurrent exits on the same portfolio both read
current_cash=10000; both compute independently (+500 and +300); whichever commits second
silently overwrites the first's credited proceeds, with no error logged.

Fix: _get_portfolio() gained a for_update: bool = False parameter that adds
.with_for_update() to the SELECT, taking a real Postgres row lock held until the caller's
transaction commits. Every cash-mutating call site (manual_exit_trade, liquidate_portfolio,
reset_portfolio, set_capital) now passes for_update=True; every read-only caller (summaries,
listings) is unaffected by leaving the default False.

conftest.py stubs sqlalchemy itself as a MagicMock (needed so other test files can import
scheduler.py/ingestion.py without their real DB deps) — matches test_liquidate_portfolio.py's/
test_broker_position_sync.py's established technique: pop the stub, use REAL sqlalchemy to
extract and exec() _get_portfolio()'s actual source, then restore the stub. This lets us
compile the emitted SELECT against the POSTGRES dialect (what production runs) and assert
"FOR UPDATE" appears — precise, and doesn't need a real Postgres server.
"""
import sys

_STUBBED_MODULES = ("sqlalchemy", "sqlalchemy.orm", "sqlalchemy.dialects", "sqlalchemy.dialects.postgresql")
_saved_stubs = {_mod: sys.modules.pop(_mod, None) for _mod in _STUBBED_MODULES}

import importlib.util
import pathlib

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

# Load the REAL PaperPortfolio ORM model (matching test_liquidate_portfolio.py's established
# technique) — select(PaperPortfolio) requires a genuine mapped class, not a plain sentinel,
# for SQLAlchemy to compile it as a valid FROM-clause element.
_models_path = pathlib.Path(__file__).resolve().parents[3] / "shared" / "db" / "models.py"
_spec = importlib.util.spec_from_file_location("db_models_under_test_get_portfolio_lock", _models_path)
_models = importlib.util.module_from_spec(_spec)
sys.modules["db_models_under_test_get_portfolio_lock"] = _models
_spec.loader.exec_module(_models)
PaperPortfolio = _models.PaperPortfolio

for _mod, _stub in _saved_stubs.items():
    if _stub is not None:
        sys.modules[_mod] = _stub
    else:
        sys.modules.pop(_mod, None)

_ROUTES_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "paper_portfolio.py"
_ROUTES_SOURCE = _ROUTES_PATH.read_text()


class _FakeHTTPException(Exception):
    def __init__(self, status_code, detail):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


def _load_get_portfolio():
    """Extract _get_portfolio()'s real source and exec() it against real (unstubbed)
    sqlalchemy, matching this test suite's own established isolation technique."""
    start = _ROUTES_SOURCE.index("def _get_portfolio(")
    end = _ROUTES_SOURCE.index("\n\n\n", start)
    ns = {
        "select": select, "HTTPException": _FakeHTTPException, "Session": Session,
        "PaperPortfolio": PaperPortfolio,
    }
    _saved = {_m: sys.modules.pop(_m, None) for _m in _STUBBED_MODULES}
    try:
        exec(_ROUTES_SOURCE[start:end], ns)  # noqa: S102 — isolated eval of one pure function's source
    finally:
        for _m, _s in _saved.items():
            if _s is not None:
                sys.modules[_m] = _s
            else:
                sys.modules.pop(_m, None)
    return ns["_get_portfolio"]


_get_portfolio = _load_get_portfolio()


def _compiled_pg_sql(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


class _CapturingSession:
    """A fake Session whose .execute() just records the statement it was given, so we can
    inspect exactly what _get_portfolio() built without needing a real database connection."""

    def __init__(self):
        self.last_stmt = None

    def execute(self, stmt):
        self.last_stmt = stmt

        class _Result:
            def scalar_one_or_none(_self):
                return "not-none-so-no-404"  # any truthy value short-circuits the 404 path

        return _Result()


def test_get_portfolio_default_does_not_lock():
    """Read-only callers (summaries, listings) must NOT take a row lock — that would add
    contention with no benefit, since they never write current_cash."""
    session = _CapturingSession()
    _get_portfolio(session, portfolio_id=1)
    sql = _compiled_pg_sql(session.last_stmt)
    assert "FOR UPDATE" not in sql


def test_get_portfolio_for_update_true_takes_a_real_row_lock():
    """The core fix: for_update=True must compile to a real `FOR UPDATE` clause — this is
    what actually serializes concurrent writers against the same portfolio row in Postgres."""
    session = _CapturingSession()
    _get_portfolio(session, portfolio_id=1, for_update=True)
    sql = _compiled_pg_sql(session.last_stmt)
    assert "FOR UPDATE" in sql


def test_get_portfolio_for_update_true_with_no_explicit_id_still_locks():
    """The default-portfolio path (portfolio_id=None) must also lock when for_update=True —
    this is the path manual_exit_trade/liquidate_portfolio/etc. take when no portfolio_id
    query param is supplied."""
    session = _CapturingSession()
    _get_portfolio(session, for_update=True)
    sql = _compiled_pg_sql(session.last_stmt)
    assert "FOR UPDATE" in sql


# ── Source-level guard: every cash-mutating handler must pass for_update=True ────────────────

@pytest.mark.parametrize("fn_name", [
    "manual_exit_trade",
    "liquidate_portfolio",
    "reset_portfolio",
    "set_capital",
])
def test_cash_mutating_handlers_request_the_lock(fn_name):
    """Source-level regression guard: these 4 handlers mutate current_cash via a
    read-modify-write and MUST call _get_portfolio(..., for_update=True), not the unlocked
    default. This is exactly the class of regression a future refactor could silently
    reintroduce (e.g. someone adding a 5th cash-mutating endpoint that copies an existing
    call site's for_update=False default instead of True)."""
    def_start = _ROUTES_SOURCE.index(f"def {fn_name}(")
    next_def = _ROUTES_SOURCE.index("\ndef ", def_start + 10)
    body = _ROUTES_SOURCE[def_start:next_def]

    assert "for_update=True)" in body, (
        f"{fn_name}() mutates current_cash and must fetch its portfolio with "
        f"for_update=True to avoid the AUD-CASHRACE lost-update race — got a call site that "
        f"doesn't request the lock."
    )
