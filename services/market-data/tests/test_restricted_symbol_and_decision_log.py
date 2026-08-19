"""Tests for IF-12: the restricted/no-trade symbol hard-reject in _scan_for_entries(), and the
append-only PaperTradeDecisionLog audit trail written by _write_decision_log().

_scan_for_entries() itself has heavy DB/session/scheduler dependencies disproportionate to a
full functional exercise (matching test_should_enter_de_parity.py's own established
proportionate-testing precedent for this file) — its restricted-symbol wiring is covered via
source-text regression checks. _write_decision_log() is small and DB-facing enough to exercise
directly against a real in-memory SQLite session, using the same real-sqlalchemy-via-stub-pop-
and-restore technique already established in test_trade_postmortem.py/
test_broker_position_sync.py/test_correlation_preentry.py for this exact file's Docker-only
dependency constraint (conftest.py stubs sqlalchemy itself as a MagicMock).
"""
import sys
import pathlib

_PTE_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "paper_trading_engine.py"
_PTE_SOURCE = _PTE_PATH.read_text()


def _func_body(func_name: str) -> str:
    start = _PTE_SOURCE.index(f"def {func_name}(")
    end = _PTE_SOURCE.index("\ndef ", start + 1)
    return _PTE_SOURCE[start:end]


# ── Source-text regression: the restricted-symbol gate inside _scan_for_entries() ────────────

def test_restricted_symbols_are_bulk_fetched_once_per_scan_cycle_not_per_candidate():
    """A per-candidate query for a small, rarely-changing no-trade list would be a real N+1
    cost on every scan cycle — confirm the fetch happens ONCE, before the candidate loop."""
    body = _func_body("_scan_for_entries")
    assert "select(RestrictedSymbol.symbol)" in body
    fetch_idx = body.index("select(RestrictedSymbol.symbol)")
    loop_idx = body.index("for sig, stock, ranking in buy_signals:")
    assert fetch_idx < loop_idx, "the restricted-symbol fetch must happen before the candidate loop, not inside it"


def test_restricted_symbol_check_is_the_first_check_in_the_candidate_loop():
    """A user-banned symbol must never even reach any other candidate-specific computation
    (stop-cooldown, cross-portfolio cap, scale-in, sizing, ...) — confirm the restricted-symbol
    check is the FIRST real check inside the loop, immediately after the max_positions break."""
    body = _func_body("_scan_for_entries")
    loop_start = body.index("for sig, stock, ranking in buy_signals:")
    restricted_idx = body.index("if stock.symbol in _restricted_symbols:", loop_start)
    cooldown_idx = body.index("if stock.symbol in _recently_stopped:", loop_start)
    global_cap_idx = body.index("_skip_tally[\"global_symbol_cap\"]", loop_start)
    assert restricted_idx < cooldown_idx
    assert restricted_idx < global_cap_idx


def test_a_restricted_symbol_is_skipped_not_hard_stopped():
    """The check must `continue` to the next candidate, never abort the whole scan cycle for
    every other symbol just because one is restricted."""
    body = _func_body("_scan_for_entries")
    restricted_block_start = body.index("if stock.symbol in _restricted_symbols:")
    restricted_block = body[restricted_block_start:restricted_block_start + 300]
    assert "continue" in restricted_block


def test_restricted_symbol_skips_are_tallied_for_visibility():
    """Matches the file's own established T232-WHYNOTRADE convention — every skip reason,
    including this new one, should be visible in the skip tally, not just a silent continue."""
    body = _func_body("_scan_for_entries")
    assert '_skip_tally["restricted_symbol"]' in body


# ── _write_decision_log() — direct behavioral tests against a real in-memory session ─────────

_STUBBED_MODULES = ("sqlalchemy", "sqlalchemy.orm", "sqlalchemy.dialects", "sqlalchemy.dialects.postgresql", "db")
_saved_stubs = {_mod: sys.modules.pop(_mod, None) for _mod in _STUBBED_MODULES}

import importlib.util  # noqa: E402
from sqlalchemy import create_engine, event, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

_models_path = pathlib.Path(__file__).resolve().parents[3] / "shared" / "db" / "models.py"
_spec = importlib.util.spec_from_file_location("db_models_under_test_restricted", _models_path)
_models = importlib.util.module_from_spec(_spec)
sys.modules["db_models_under_test_restricted"] = _models
_spec.loader.exec_module(_models)

_ENGINE = create_engine("sqlite:///:memory:")
_models.Base.metadata.create_all(
    _ENGINE,
    tables=[
        _models.PaperPortfolio.__table__, _models.PaperTrade.__table__,
        _models.PaperTradeDecisionLog.__table__, _models.RestrictedSymbol.__table__,
    ],
)

for _mod, _stub in _saved_stubs.items():
    if _stub is not None:
        sys.modules[_mod] = _stub
    else:
        sys.modules.pop(_mod, None)

PaperPortfolio = _models.PaperPortfolio
PaperTrade = _models.PaperTrade
PaperTradeDecisionLog = _models.PaperTradeDecisionLog
RestrictedSymbol = _models.RestrictedSymbol

# PaperTradeDecisionLog.id is a BigInteger PK, which doesn't get SQLite's implicit
# autoincrement (a real Postgres sequence handles this in production) — matching
# test_squeeze_alert_outcomes.py's own established workaround exactly: _write_decision_log()
# (the real code under test) constructs a PaperTradeDecisionLog with no id at all, just as it
# does in production, so a before_insert listener scoped to this test engine assigns one
# automatically rather than requiring a test-only change to the real function's source.
_autoincrement_counter = [0]


@event.listens_for(PaperTradeDecisionLog, "before_insert")
def _assign_test_id(mapper, connection, target):
    if target.id is None:
        _autoincrement_counter[0] += 1
        target.id = _autoincrement_counter[0]


def _extract_write_decision_log():
    start = _PTE_SOURCE.index("def _write_decision_log(")
    end = _PTE_SOURCE.index("\ndef ", start + 1)
    raw = _PTE_SOURCE[start:end]
    import json as _json
    import types
    _fake_log = types.SimpleNamespace(warning=lambda *a, **kw: None)
    namespace = {
        "json": _json, "PaperTradeDecisionLog": PaperTradeDecisionLog, "log": _fake_log,
    }
    exec(raw, namespace)  # noqa: S102 — real source, not a duplicate
    return namespace["_write_decision_log"]


def _make_session():
    session = Session(_ENGINE)
    for table in (PaperTradeDecisionLog.__table__, PaperTrade.__table__, PaperPortfolio.__table__):
        session.execute(table.delete())
    session.commit()
    return session


def _make_trade(session, portfolio_id=1, symbol="AAPL"):
    p = session.get(PaperPortfolio, portfolio_id)
    if p is None:
        p = PaperPortfolio(id=portfolio_id, name="Test", initial_capital=100_000.0,
                            current_cash=100_000.0, config={"trading_style": "SWING"}, is_active=True)
        session.add(p)
        session.flush()
    import datetime as _dt
    trade = PaperTrade(
        portfolio_id=p.id, symbol=symbol, trading_style="SWING",
        entry_date=_dt.date(2026, 1, 1), entry_time=_dt.datetime(2026, 1, 1, 14, 30),
        entry_price=100.0, shares=10.0,
        entry_shares=10.0, stop_loss=95.0, take_profit=115.0, current_stop=95.0, highest_price=100.0,
        current_price=100.0, stage="open", hold_days=0,
    )
    session.add(trade)
    session.flush()
    return trade


def test_writes_a_real_row_with_the_passed_fields():
    write_decision_log = _extract_write_decision_log()
    session = _make_session()
    trade = _make_trade(session)
    write_decision_log(session, trade, "entry", 100.5, 10.0, "high conviction", {"entry_score": 6})
    session.commit()
    rows = session.execute(select(PaperTradeDecisionLog).where(PaperTradeDecisionLog.trade_id == trade.id)).scalars().all()
    assert len(rows) == 1
    assert rows[0].action == "entry"
    assert rows[0].price == 100.5
    assert rows[0].symbol == "AAPL"
    assert rows[0].reason == "high conviction"
    assert '"entry_score": 6' in rows[0].details_json


def test_two_calls_for_the_same_trade_produce_two_rows_never_an_update():
    """The whole point of this table — a genuine INSERT every time, never mutating a prior
    row, unlike paper_trades itself."""
    write_decision_log = _extract_write_decision_log()
    session = _make_session()
    trade = _make_trade(session)
    write_decision_log(session, trade, "entry", 100.0, 10.0, "entry note", {})
    session.commit()
    write_decision_log(session, trade, "exit", 110.0, 10.0, "stop_hit", {})
    session.commit()
    rows = session.execute(
        select(PaperTradeDecisionLog).where(PaperTradeDecisionLog.trade_id == trade.id)
    ).scalars().all()
    assert len(rows) == 2
    actions = {r.action for r in rows}
    assert actions == {"entry", "exit"}


def test_fails_open_on_a_write_error_rather_than_raising():
    """A logging failure must never abort a real trade entry/exit — the caller (a real
    _open_paper_trade()/_close_one_paper_trade() call) should never see an exception from this
    function even if the DB write itself somehow fails."""
    write_decision_log = _extract_write_decision_log()
    session = _make_session()
    trade = _make_trade(session)

    class _BrokenSession:
        def add(self, obj):
            raise RuntimeError("simulated DB failure")

    # Must not raise.
    write_decision_log(_BrokenSession(), trade, "entry", 100.0, 10.0, None, {})


def test_details_json_is_never_a_bare_python_repr_but_real_json():
    """A downstream consumer (an admin audit page, a future report) needs to json.loads() this
    field — confirm it's real, valid JSON, not a Python str() of the dict."""
    import json
    write_decision_log = _extract_write_decision_log()
    session = _make_session()
    trade = _make_trade(session)
    write_decision_log(session, trade, "exit", 105.0, 10.0, "target_reached", {"pnl_pct": 12.5, "hold_days": 8})
    session.commit()
    row = session.execute(select(PaperTradeDecisionLog).where(PaperTradeDecisionLog.trade_id == trade.id)).scalar_one()
    parsed = json.loads(row.details_json)
    assert parsed == {"pnl_pct": 12.5, "hold_days": 8}


# ── Source-text regression: the IF-12 REST API routes in paper_portfolio.py ──────────────────
# paper_portfolio.py can't be imported directly in this test environment (its import chain
# needs the real conftest.py stub setup only pytest's own collection provides for db/
# db.models) — matching this repo's established pattern for this exact file (IF-10/IF-11's own
# tests in this same directory).

_PAPER_PORTFOLIO_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "paper_portfolio.py"
_PAPER_PORTFOLIO_SOURCE = _PAPER_PORTFOLIO_PATH.read_text()


def _route_body(func_name: str) -> str:
    start = _PAPER_PORTFOLIO_SOURCE.index(f"def {func_name}(")
    try:
        end = _PAPER_PORTFOLIO_SOURCE.index("\ndef ", start + 1)
    except ValueError:
        end = len(_PAPER_PORTFOLIO_SOURCE)  # this function is the last one in the file
    return _PAPER_PORTFOLIO_SOURCE[start:end]


def test_all_4_if12_routes_are_registered_at_the_documented_paths():
    assert '@router.get("/restricted-symbols")' in _PAPER_PORTFOLIO_SOURCE
    assert '@router.post("/restricted-symbols")' in _PAPER_PORTFOLIO_SOURCE
    assert '@router.delete("/restricted-symbols/{symbol}")' in _PAPER_PORTFOLIO_SOURCE
    assert '@router.get("/decision-log")' in _PAPER_PORTFOLIO_SOURCE


def test_mutating_restricted_symbol_routes_require_admin_not_just_any_user():
    """Only an admin should be able to add/remove a restricted symbol — a regular user can
    still READ the list (list_restricted_symbols uses get_current_user), but mutating it is
    admin-gated, matching this file's own established convention for every other mutating
    route."""
    add_body = _route_body("add_restricted_symbol")
    remove_body = _route_body("remove_restricted_symbol")
    assert "Depends(get_admin_user)" in add_body
    assert "Depends(get_admin_user)" in remove_body
    list_body = _route_body("list_restricted_symbols")
    assert "Depends(get_admin_user)" not in list_body


def test_adding_a_duplicate_restricted_symbol_is_rejected_not_silently_duplicated():
    body = _route_body("add_restricted_symbol")
    assert "status_code=409" in body


def test_decision_log_route_is_genuinely_read_only():
    """The audit-trail READ endpoint must never write anything — confirm no commit()/add()/
    delete() calls anywhere in its body."""
    body = _route_body("get_decision_log")
    assert "session.commit()" not in body
    assert "session.add(" not in body
    assert "session.delete(" not in body


def test_decision_log_route_parses_details_json_back_into_a_real_object():
    """The stored details_json column is a JSON string — confirm the route parses it back
    into a real object for the API response, rather than returning the raw string a client
    would have to json.loads() themselves."""
    body = _route_body("get_decision_log")
    assert "json.loads(r.details_json)" in body
