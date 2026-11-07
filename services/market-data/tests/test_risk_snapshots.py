"""Tests for IF-01: risk_snapshots.py — persisted VaR/CVaR + stress-test snapshots for a
user's real position book, closing the "never persisted" half of Tier 289's IF-01 finding.

_user_symbols_and_weights() needs a real DB session — matching test_correlation_preentry.py's
established technique exactly: pop the sqlalchemy/db stubs, build ONE shared in-memory engine +
real Stock/UserPosition/PortfolioRiskMetric/StressTestResult models, then restore the stubs
immediately so later-collected test files aren't affected.

The route handlers (save_var_snapshot/save_stress_test/etc.) are mostly HTTP-call
orchestration to portfolio-optimizer — tested with a fake httpx.Client, matching
test_market_pulse.py's own established pattern for mocking outbound httpx calls in this repo.
"""
import sys

_STUBBED_MODULES = ("sqlalchemy", "sqlalchemy.orm", "sqlalchemy.dialects", "sqlalchemy.dialects.postgresql", "db")
_saved_stubs = {_mod: sys.modules.pop(_mod, None) for _mod in _STUBBED_MODULES}

import importlib.util
import json
import pathlib

import pytest
from unittest.mock import MagicMock

from sqlalchemy import create_engine, select as _REAL_SELECT
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert as _REAL_PG_INSERT

_models_path = pathlib.Path(__file__).resolve().parents[3] / "shared" / "db" / "models.py"
_spec = importlib.util.spec_from_file_location("db_models_under_test_risk_snap", _models_path)
_models = importlib.util.module_from_spec(_spec)
sys.modules["db_models_under_test_risk_snap"] = _models
_spec.loader.exec_module(_models)

# PortfolioRiskMetric/StressTestResult's real id column is BigInteger — SQLite only grants
# implicit ROWID-alias autoincrement to a plain Integer primary key, not BigInteger (the same
# gotcha already documented for Price.id/SignalOutcome.id elsewhere in this test suite). Since
# these rows are written by the CODE UNDER TEST's own pg_insert() call (not explicitly assigned
# an id by this test file, unlike test_correlation_preentry.py's direct Price inserts), the
# simplest fix is overriding just these two ephemeral in-memory test tables' id column type to
# a plain Integer before create_all() runs — the real Postgres schema is untouched.
_models.PortfolioRiskMetric.__table__.c.id.type = _models.Integer()
_models.StressTestResult.__table__.c.id.type = _models.Integer()

_ENGINE = create_engine("sqlite:///:memory:")
_models.Base.metadata.create_all(
    _ENGINE,
    tables=[_models.User.__table__, _models.UserPosition.__table__,
            _models.PortfolioRiskMetric.__table__, _models.StressTestResult.__table__],
)

for _mod, _stub in _saved_stubs.items():
    if _stub is not None:
        sys.modules[_mod] = _stub
    else:
        sys.modules.pop(_mod, None)

User = _models.User
UserPosition = _models.UserPosition
PortfolioRiskMetric = _models.PortfolioRiskMetric
StressTestResult = _models.StressTestResult

# CRITICAL: once the stubs above are restored, ANY LATER `from sqlalchemy import select`
# (even from inside a function body called after this point) resolves to the STUBBED mock
# again, not the real sqlalchemy — the exact gotcha documented in CLAUDE.md's Redis-connection-
# pooling audit history, generalized to sqlalchemy itself. _REAL_SELECT/_REAL_PG_INSERT/Session
# were captured ABOVE, before the restore, and must be reused directly everywhere below rather
# than re-imported.


def _session():
    return Session(_ENGINE)


def _add_position(session, user_id, symbol, shares, avg_cost):
    session.add(UserPosition(user_id=user_id, symbol=symbol, shares=shares, avg_cost=avg_cost, currency="USD"))
    session.commit()


# ── _user_symbols_and_weights() — source-extracted, real DB session ─────────────────────────

_RISK_SNAP_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "risk_snapshots.py"
_RISK_SNAP_SOURCE = _RISK_SNAP_PATH.read_text()


def _extract_user_symbols_and_weights():
    start = _RISK_SNAP_SOURCE.index("def _user_symbols_and_weights(")
    end = _RISK_SNAP_SOURCE.index("\n\n\n@router.post(\"/var\")", start)
    func_source = _RISK_SNAP_SOURCE[start:end]
    namespace = {"select": _REAL_SELECT, "UserPosition": UserPosition, "Session": Session}
    exec(func_source, namespace)  # noqa: S102 — isolated eval of real source
    return namespace["_user_symbols_and_weights"]


_user_symbols_and_weights = _extract_user_symbols_and_weights()


def test_builds_symbols_and_weights_from_real_positions():
    with _session() as s:
        _add_position(s, 9201, "AAPL", 10, 150.0)
        _add_position(s, 9201, "MSFT", 5, 300.0)
        symbols, weights = _user_symbols_and_weights(s, 9201)
    assert set(symbols) == {"AAPL", "MSFT"}
    # AAPL: 10*150=1500, MSFT: 5*300=1500 — equal weight
    assert len(weights) == 2


def test_a_zero_value_position_is_excluded():
    """A position with zero shares or zero avg_cost has no real market value to weight by —
    it must not silently contribute a spurious zero-weight entry."""
    with _session() as s:
        _add_position(s, 9202, "REAL", 10, 100.0)
        _add_position(s, 9202, "ZEROSHARES", 0, 50.0)
        symbols, weights = _user_symbols_and_weights(s, 9202)
    assert symbols == ["REAL"]


def test_isolated_per_user():
    with _session() as s:
        _add_position(s, 9203, "AAA", 10, 100.0)
        _add_position(s, 9204, "BBB", 10, 100.0)
        symbols_a, _ = _user_symbols_and_weights(s, 9203)
        symbols_b, _ = _user_symbols_and_weights(s, 9204)
    assert symbols_a == ["AAA"]
    assert symbols_b == ["BBB"]


def test_no_positions_returns_empty_lists():
    with _session() as s:
        symbols, weights = _user_symbols_and_weights(s, 9999)
    assert symbols == []
    assert weights == []


# ── Route handlers — httpx mocked, matching test_market_pulse.py's established pattern ──────
# risk_snapshots.py has a relative import (`from .auth import get_current_user`), so it can't
# be loaded via a bare spec_from_file_location (no parent package) — each route function's real
# source is instead extracted and exec()'d directly (matching test_correlation_preentry.py's
# own established technique), with FastAPI's Depends()/HTTPException/httpx supplied as real
# objects in the exec namespace and get_session/get_current_user as harmless no-op stand-ins
# (these tests call the route functions directly with explicit session=/user= kwargs, the same
# way test_portfolio_risk.py calls portfolio_risk() directly, bypassing FastAPI's own DI).

class _FakeHttpxModule:
    """A tiny stand-in exposing only .Client, monkeypatchable per-test exactly like the real
    httpx module attribute risk_snapshots.py's own code references (`httpx.Client(...)`)."""
    def __init__(self):
        self.Client = None


def _extract_route_function(name: str, end_marker: str):
    from fastapi import Depends as _Depends, HTTPException as _HTTPException

    start = _RISK_SNAP_SOURCE.index(f"def {name}(")
    end = _RISK_SNAP_SOURCE.index(end_marker, start) if end_marker else len(_RISK_SNAP_SOURCE)
    func_source = _RISK_SNAP_SOURCE[start:end]
    fake_httpx = _FakeHttpxModule()
    namespace = {
        "Depends": _Depends, "HTTPException": _HTTPException,
        "select": _REAL_SELECT, "pg_insert": _REAL_PG_INSERT, "Session": Session,
        "httpx": fake_httpx, "json": json,
        "PortfolioRiskMetric": PortfolioRiskMetric, "StressTestResult": StressTestResult,
        "User": User, "UserPosition": UserPosition,
        "get_session": lambda: None, "get_current_user": lambda: None,
        "_user_symbols_and_weights": _user_symbols_and_weights,
        "_service_token": lambda: "fake-service-token",
        "_settings": MagicMock(portfolio_optimizer_url="http://portfolio-optimizer:8007"),
        "date": __import__("datetime").date,
        "router": MagicMock(),
    }
    import math
    namespace["math"] = math
    helpers = _RISK_SNAP_SOURCE[_RISK_SNAP_SOURCE.index("def _select_snapshot_book("):
                                _RISK_SNAP_SOURCE.index("def _user_symbols_and_weights(")]
    exec(helpers, namespace)
    exec(func_source, namespace)  # noqa: S102 — isolated eval of real source
    fn = namespace[name]
    fn.__httpx_module__ = fake_httpx  # expose so tests can monkeypatch .Client on it
    return fn


class _FakeResp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeClient:
    def __init__(self, resp):
        self._resp = resp
        self.last_kwargs = None  # captures headers/params for assertions

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, *a, **kw):
        self.last_kwargs = kw
        return self._resp


save_var_snapshot = _extract_route_function("save_var_snapshot", '\n\n\n@router.get("/var/history")')
save_stress_test = _extract_route_function("save_stress_test", '\n\n\n@router.get("/stress-test/history")')


def test_save_var_snapshot_rejects_fewer_than_two_symbols():
    from fastapi import HTTPException
    with _session() as s:
        _add_position(s, 9301, "ONLY_ONE", 10, 100.0)
        user = MagicMock(id=9301)
        try:
            save_var_snapshot(session=s, user=user)
            assert False, "expected HTTPException"
        except HTTPException as exc:
            assert exc.status_code == 400


def test_save_var_snapshot_persists_a_real_row(monkeypatch):
    with _session() as s:
        _add_position(s, 9302, "AAPL", 10, 150.0)
        _add_position(s, 9302, "MSFT", 5, 300.0)
        user = MagicMock(id=9302)

        fake_payload = {
            "symbols": ["AAPL", "MSFT"], "portfolio_beta": 1.1, "var_95_pct": 3.5,
            "historical_var": {
                "var_95_1d_pct": 3.2, "var_99_1d_pct": 4.8,
                "var_95_10d_pct": 10.1, "var_99_10d_pct": 15.2,
                "cvar_95_1d_pct": 4.0, "cvar_99_1d_pct": 5.5,
                "cvar_95_10d_pct": 12.6, "cvar_99_10d_pct": 17.4,
                "sample_size": 60, "insufficient_data": False,
            },
        }
        fake_client = _FakeClient(_FakeResp(200, fake_payload))
        monkeypatch.setattr(save_var_snapshot.__httpx_module__, "Client", lambda *a, **kw: fake_client)

        result = save_var_snapshot(session=s, user=user)
        assert result["saved"] is True
        assert result["var_95_1d_pct"] == 3.2

        row = s.query(PortfolioRiskMetric).filter_by(user_id=9302).one()
        assert row.var_95_1d_pct == 3.2
        assert json.loads(row.symbols_json) == ["AAPL", "MSFT"]


def test_save_var_snapshot_upserts_the_same_day_not_duplicating_rows(monkeypatch):
    """Running the snapshot twice on the same day must update the ONE row for that
    (user_id, as_of), never insert a second one — matching the unique constraint's own intent."""
    with _session() as s:
        _add_position(s, 9303, "AAPL", 10, 150.0)
        _add_position(s, 9303, "MSFT", 5, 300.0)
        user = MagicMock(id=9303)

        def _make_client(var_95):
            return _FakeClient(_FakeResp(200, {
                "symbols": ["AAPL", "MSFT"], "portfolio_beta": 1.0, "var_95_pct": var_95,
                "historical_var": {"var_95_1d_pct": var_95, "var_99_1d_pct": None,
                                   "var_95_10d_pct": None, "var_99_10d_pct": None,
                                   "cvar_95_1d_pct": None, "cvar_99_1d_pct": None,
                                   "cvar_95_10d_pct": None, "cvar_99_10d_pct": None,
                                   "sample_size": 60, "insufficient_data": False},
            }))

        monkeypatch.setattr(save_var_snapshot.__httpx_module__, "Client", lambda *a, **kw: _make_client(3.0))
        save_var_snapshot(session=s, user=user)
        monkeypatch.setattr(save_var_snapshot.__httpx_module__, "Client", lambda *a, **kw: _make_client(9.9))
        save_var_snapshot(session=s, user=user)

        rows = s.query(PortfolioRiskMetric).filter_by(user_id=9303).all()
        assert len(rows) == 1
        assert rows[0].var_95_1d_pct == 9.9  # the SECOND call's value, updated in place


def test_save_stress_test_persists_a_real_row(monkeypatch):
    with _session() as s:
        _add_position(s, 9304, "AAPL", 10, 150.0)
        _add_position(s, 9304, "MSFT", 5, 300.0)
        user = MagicMock(id=9304)

        fake_payload = {
            "scenario": "covid_2020", "label": "COVID-19 Crash (Feb 19 - Mar 23, 2020)",
            "symbols": ["AAPL", "MSFT"], "benchmark_move_pct": -34.0,
            "portfolio_impact_pct": -30.5,
            "per_position_impact_pct": {"AAPL": -30.0, "MSFT": -31.0},
        }
        fake_client = _FakeClient(_FakeResp(200, fake_payload))
        monkeypatch.setattr(save_stress_test.__httpx_module__, "Client", lambda *a, **kw: fake_client)

        result = save_stress_test(scenario="covid_2020", session=s, user=user)
        assert result["saved"] is True
        assert result["portfolio_impact_pct"] == -30.5

        row = s.query(StressTestResult).filter_by(user_id=9304, scenario="covid_2020").one()
        assert row.portfolio_impact_pct == -30.5
        assert json.loads(row.per_position_impact_json) == {"AAPL": -30.0, "MSFT": -31.0}


def test_save_stress_test_raises_400_on_an_invalid_scenario(monkeypatch):
    from fastapi import HTTPException
    with _session() as s:
        _add_position(s, 9305, "AAPL", 10, 150.0)
        _add_position(s, 9305, "MSFT", 5, 300.0)
        user = MagicMock(id=9305)

        fake_client = _FakeClient(_FakeResp(400, {"detail": "Unknown scenario 'bad_key'"}))
        monkeypatch.setattr(save_stress_test.__httpx_module__, "Client", lambda *a, **kw: fake_client)

        try:
            save_stress_test(scenario="bad_key", session=s, user=user)
            assert False, "expected HTTPException"
        except HTTPException as exc:
            assert exc.status_code == 400


# ── BUG-RISKSNAP-NOSERVICETOKEN regression guard ────────────────────────────────────────────
# Real bug caught via live-verification against real production data immediately after this
# feature's first deploy: portfolio-optimizer's risk endpoints require auth, and the outbound
# httpx calls originally sent no Authorization header at all, 401ing every real call. Both
# tests below assert the real Authorization: Bearer <token> header is actually present —
# neither test above (which only checks the RESULT, not the request that produced it) would
# have caught this class of bug on its own.

def test_save_var_snapshot_sends_a_real_authorization_header(monkeypatch):
    with _session() as s:
        _add_position(s, 9306, "AAPL", 10, 150.0)
        _add_position(s, 9306, "MSFT", 5, 300.0)
        user = MagicMock(id=9306)

        fake_client = _FakeClient(_FakeResp(200, {
            "symbols": ["AAPL", "MSFT"], "portfolio_beta": 1.0, "var_95_pct": 3.0,
            "historical_var": {"var_95_1d_pct": 3.0, "var_99_1d_pct": None,
                               "var_95_10d_pct": None, "var_99_10d_pct": None,
                               "cvar_95_1d_pct": None, "cvar_99_1d_pct": None,
                               "cvar_95_10d_pct": None, "cvar_99_10d_pct": None,
                               "sample_size": 60, "insufficient_data": False},
        }))
        monkeypatch.setattr(save_var_snapshot.__httpx_module__, "Client", lambda *a, **kw: fake_client)

        save_var_snapshot(session=s, user=user)

        assert fake_client.last_kwargs is not None
        headers = fake_client.last_kwargs.get("headers", {})
        assert "Authorization" in headers
        assert headers["Authorization"].startswith("Bearer ")
        assert headers["Authorization"] != "Bearer "  # a real, non-empty token


def test_save_stress_test_sends_a_real_authorization_header(monkeypatch):
    with _session() as s:
        _add_position(s, 9307, "AAPL", 10, 150.0)
        _add_position(s, 9307, "MSFT", 5, 300.0)
        user = MagicMock(id=9307)

        fake_client = _FakeClient(_FakeResp(200, {
            "scenario": "covid_2020", "label": "COVID-19 Crash",
            "symbols": ["AAPL", "MSFT"], "benchmark_move_pct": -34.0,
            "portfolio_impact_pct": -30.0,
            "per_position_impact_pct": {"AAPL": -30.0, "MSFT": -30.0},
        }))
        monkeypatch.setattr(save_stress_test.__httpx_module__, "Client", lambda *a, **kw: fake_client)

        save_stress_test(scenario="covid_2020", session=s, user=user)

        assert fake_client.last_kwargs is not None
        headers = fake_client.last_kwargs.get("headers", {})
        assert "Authorization" in headers
        assert headers["Authorization"].startswith("Bearer ")
        assert headers["Authorization"] != "Bearer "


@pytest.mark.parametrize("route", [save_var_snapshot, save_stress_test])
@pytest.mark.parametrize("status", [400, 401, 403, 422, 429, 500])
def test_upstream_error_status_and_reason(route, status, monkeypatch):
    from fastapi import HTTPException
    client = _FakeClient(_FakeResp(status, {"detail": "Only 0 shared trading days"}))
    monkeypatch.setattr(route.__httpx_module__, "Client", lambda **kw: client)
    monkeypatch.setitem(route.__globals__, "_user_symbols_and_weights", lambda *a: (["A", "B"], [1, 1]))
    session = MagicMock()
    kwargs = {"scenario": "covid_2020"} if route is save_stress_test else {}
    with pytest.raises(HTTPException) as error:
        route(session=session, user=MagicMock(id=1), **kwargs)
    assert error.value.status_code == (status if status < 500 else 502)
    if status < 500:
        assert error.value.detail == "Only 0 shared trading days"
    session.execute.assert_not_called()
    session.commit.assert_not_called()


@pytest.mark.parametrize("route", [save_var_snapshot, save_stress_test])
def test_connection_failure_is_502(route, monkeypatch):
    from fastapi import HTTPException
    monkeypatch.setitem(route.__globals__, "_user_symbols_and_weights", lambda *a: (["A", "B"], [1, 1]))
    def fail(**kwargs):
        raise ConnectionError("unavailable")
    monkeypatch.setattr(route.__httpx_module__, "Client", fail)
    with pytest.raises(HTTPException) as error:
        route(session=MagicMock(), user=MagicMock(id=1), **({"scenario": "x"} if route is save_stress_test else {}))
    assert error.value.status_code == 502


@pytest.mark.parametrize("route,model,uid", [(save_var_snapshot, PortfolioRiskMetric, 9401),
                                            (save_stress_test, StressTestResult, 9402)])
def test_top_ten_and_full_book_coverage_persist(route, model, uid, monkeypatch):
    with _session() as session:
        for i in range(1, 13):
            _add_position(session, uid, f"S{i:02}", 1, i)
        selected = [f"S{i:02}" for i in range(12, 2, -1)]
        payload = {"symbols": selected, "coverage": {"covered_weight_pct": 80, "observations": 25},
                   "scenario": "covid_2020", "label": "COVID", "benchmark_move_pct": -30,
                   "portfolio_impact_pct": -25, "per_position_impact_pct": {},
                   "warnings": ["Missing history"]}
        client = _FakeClient(_FakeResp(200, payload))
        monkeypatch.setattr(route.__httpx_module__, "Client", lambda **kw: client)
        result = route(session=session, user=MagicMock(id=uid), **({"scenario": "covid_2020"} if route is save_stress_test else {}))
        assert client.last_kwargs["params"]["symbols"].split(",") == selected
        assert client.last_kwargs["params"]["weights"] == ",".join(str(float(i)) for i in range(12, 2, -1))
        coverage = result["coverage"]
        assert coverage["selected_weight_pct"] == pytest.approx(75 / 78 * 100)
        assert coverage["covered_weight_pct"] == pytest.approx(75 / 78 * 80)
        assert [p["symbol"] for p in coverage["excluded_positions"]] == ["S02", "S01"]
        row = session.query(model).filter_by(user_id=uid).one()
        assert row.coverage == coverage
        assert "S02, S01" in coverage["warnings"][-1]
        history_name = "var_snapshot_history" if route is save_var_snapshot else "stress_test_history"
        end = '\n\n\n@router.post("/stress-test")' if route is save_var_snapshot else None
        history = _extract_route_function(history_name, end)
        assert history(session=session, user=MagicMock(id=uid))[0]["coverage"] == coverage
        row.coverage = None
        session.commit()
        assert history(session=session, user=MagicMock(id=uid))[0]["coverage"] is None


def test_equal_weights_deterministic_and_large_weights_safe():
    select_book = save_var_snapshot.__globals__["_select_snapshot_book"]
    symbols, weights, coverage = select_book(["B", "A"], [1e308, 1e308])
    assert symbols == ["A", "B"]
    assert coverage["selected_weight_pct"] == 100
    assert coverage["excluded_positions"] == []


def test_non_json_4xx_keeps_status():
    from fastapi import HTTPException
    response = MagicMock(status_code=422, text="Insufficient shared history")
    response.json.side_effect = ValueError("not JSON")
    with pytest.raises(HTTPException) as error:
        save_var_snapshot.__globals__["_upstream_data"](response)
    assert error.value.status_code == 422
    assert error.value.detail == response.text


@pytest.mark.parametrize("weight", [float("inf"), float("nan")])
def test_non_finite_selection_rejected(weight):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as error:
        save_var_snapshot.__globals__["_select_snapshot_book"](["A", "B"], [weight, 1])
    assert error.value.status_code == 400


def test_cutoff_ties_use_symbol_order_and_unknown_upstream_coverage_stays_unknown():
    ns = save_var_snapshot.__globals__
    symbols, _, selection = ns["_select_snapshot_book"](list("LKJIHGFEDCBA"), [1] * 12)
    assert symbols == list("ABCDEFGHIJ")
    assert [p["symbol"] for p in selection["excluded_positions"]] == ["K", "L"]
    assert ns["_snapshot_coverage"](selection, {})["covered_weight_pct"] is None
