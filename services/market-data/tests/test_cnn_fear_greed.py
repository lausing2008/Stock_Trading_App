"""Behavioral checks for CNN snapshots, cache migration, and provider failures."""
import ast
import importlib.util
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
spec = importlib.util.spec_from_file_location("cnn_fear_greed", SRC / "services/fear_greed.py")
cnn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cnn)


@pytest.fixture
def payload():
    return {"fear_and_greed": {
        "score": 31.25, "rating": "fear", "timestamp": "2026-10-16T13:35:57Z",
        "previous_close": 31.1, "previous_1_week": 46.2,
        "previous_1_month": 27.3, "previous_1_year": 23.4,
    }}


def test_preserves_published_values_and_timestamp(payload):
    result = cnn.parse_snapshot(payload)
    assert result == {
        "score": 31.25, "rating": "Fear", "source": "CNN",
        "updated_at": "2026-10-16T13:35:57+00:00",
        "previous_close": 31.1, "previous_1_week": 46.2,
        "previous_1_month": 27.3, "previous_1_year": 23.4,
    }


@pytest.mark.parametrize("field,value", [
    ("score", float("nan")), ("score", float("inf")), ("score", -1),
    ("score", 101), ("score", True), ("score", "31"),
    ("rating", "unknown"), ("timestamp", "2026-10-16T13:35:57"),
    ("timestamp", "invalid"), ("previous_close", 101),
])
def test_rejects_invalid_provider_values(payload, field, value):
    payload["fear_and_greed"][field] = value
    with pytest.raises(ValueError):
        cnn.parse_snapshot(payload)


def test_missing_history_is_not_fabricated(payload):
    del payload["fear_and_greed"]["previous_1_year"]
    assert cnn.parse_snapshot(payload)["previous_1_year"] is None


def test_fetch_uses_cnn_response(monkeypatch, payload):
    response = Mock()
    response.json.return_value = payload
    get = Mock(return_value=response)
    monkeypatch.setattr(cnn.httpx, "get", get)
    assert cnn.fetch_snapshot()["score"] == 31.25
    assert get.call_args.args == (cnn.CNN_URL,)
    assert get.call_args.kwargs["timeout"] == 4.0
    assert get.call_args.kwargs["headers"]["Origin"] == "https://www.cnn.com"
    assert "Mozilla/5.0" in get.call_args.kwargs["headers"]["User-Agent"]


def test_provider_block_is_not_replaced_with_an_estimate(monkeypatch):
    response = Mock()
    response.raise_for_status.side_effect = RuntimeError("HTTP 418")
    monkeypatch.setattr(cnn.httpx, "get", Mock(return_value=response))
    with pytest.raises(RuntimeError, match="HTTP 418"):
        cnn.fetch_snapshot()


def route_namespace(cached, fetch):
    # Execute the real route in isolation from unrelated DB and auth imports.
    tree = ast.parse((SRC / "api/routes.py").read_text())
    route = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "fear_greed")
    route.decorator_list = []
    cache = Mock()
    cache.get.return_value = json.dumps(cached) if cached else None

    class HTTPException(Exception):
        def __init__(self, status_code, detail):
            self.status_code = status_code

    ns = dict(json=json, _get_redis=lambda: cache, _compute_fear_greed=fetch,
              _FEAR_GREED_KEY="stockai:fear_greed", _FEAR_GREED_TTL=300,
              log=Mock(), HTTPException=HTTPException)
    exec(compile(ast.Module(body=[route], type_ignores=[]), "routes.py", "exec"), ns)
    return ns, cache


def test_old_custom_cache_is_replaced(payload):
    snapshot = cnn.parse_snapshot(payload)
    fetch = Mock(return_value=snapshot)
    ns, cache = route_namespace({"score": 76.3, "rating": "Extreme Greed"}, fetch)
    assert ns["fear_greed"]() == snapshot
    cache.setex.assert_called_once_with("stockai:fear_greed", 300, json.dumps(snapshot))


def test_cnn_cache_avoids_provider_request(payload):
    snapshot = cnn.parse_snapshot(payload)
    fetch = Mock()
    ns, cache = route_namespace(snapshot, fetch)
    assert ns["fear_greed"]() == snapshot
    fetch.assert_not_called()


def test_outage_returns_503_even_with_old_custom_cache():
    ns, cache = route_namespace({"score": 76.3}, Mock(side_effect=ValueError("blocked")))
    with pytest.raises(ns["HTTPException"]) as error:
        ns["fear_greed"]()
    assert error.value.status_code == 503
    cache.setex.assert_not_called()


def test_redis_outage_does_not_hide_cnn(payload):
    snapshot = cnn.parse_snapshot(payload)
    ns, cache = route_namespace(None, Mock(return_value=snapshot))
    cache.get.side_effect = ConnectionError()
    cache.setex.side_effect = ConnectionError()
    assert ns["fear_greed"]() == snapshot


def test_yahoo_outage_preserves_cnn_snapshot(payload):
    tree = ast.parse((SRC / "api/routes.py").read_text())
    compute = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_compute_fear_greed")
    compute.body = [n for n in compute.body if not isinstance(n, ast.ImportFrom)]
    snapshot = cnn.parse_snapshot(payload)
    yahoo = Mock()
    yahoo.download.side_effect = RuntimeError("Yahoo unavailable")
    ns = dict(fetch_snapshot=lambda: snapshot, yf=yahoo, log=Mock())
    exec(compile(ast.Module(body=[compute], type_ignores=[]), "routes.py", "exec"), ns)
    assert ns["_compute_fear_greed"]() == snapshot
    assert "sp500_regime" not in snapshot


@pytest.mark.parametrize("cached,expected", [
    ({"score": 76.3, "sp500_regime": "bull"}, "unknown"),
    ({"source": "CNN", "score": 31, "sp500_regime": "bull"}, "bull"),
    ({"source": "CNN", "score": 23, "sp500_regime": "bull"}, "high_vol"),
    ({"source": "CNN", "score": 23, "sp500_regime": "bear"}, "bear"),
])
def test_scheduler_uses_cnn_with_existing_thresholds(cached, expected):
    tree = ast.parse((SRC / "services/scheduler.py").read_text())
    func = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_get_current_regime")
    cache = Mock()
    cache.get.return_value = json.dumps(cached)
    ns = dict(json=json, _get_redis=lambda: cache)
    exec(compile(ast.Module(body=[func], type_ignores=[]), "scheduler.py", "exec"), ns)
    assert ns["_get_current_regime"]() == expected
