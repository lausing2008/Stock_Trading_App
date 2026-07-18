"""Tests for T258-WHATCOULDGOWRONG-AGENT's check_risks().

Mirrors llm_scorer.py's established fail-open/opt-in/Redis-cache conventions exactly (this
module was built by copying that pattern). httpx/redis are real installed packages here — no
stub needed for them; only `common`/`common.config` (Docker-only) are stubbed, matching every
other decision-engine test file's convention. The httpx call itself is mocked via monkeypatch
on httpx.AsyncClient, not the LLM's semantic output — consistent with this repo's established
"mock the network boundary, not the content" testing discipline for LLM-calling code.
"""
import asyncio
import json
import sys
from unittest.mock import AsyncMock, MagicMock, patch

sys.modules.setdefault("common", MagicMock())
sys.modules.setdefault("common.config", MagicMock())

import src.api.risk_agent as risk_agent  # noqa: E402


def _neutral_kwargs(**overrides):
    kwargs = dict(
        symbol="AAPL", style="SWING", sig_direction="BUY", confidence=75.0,
        game_plan={"entry2": 100.0, "stop": 95.0, "take_profit": 115.0},
        regime_state="bull", regime={"state": "bull", "vix": 15.0},
        is_pre_choppy=False, is_pre_risk_off=False,
        research_rec="BUY", research_score=80.0,
        days_to_earnings=None, volume_z=None, reasons={},
        sig_ts="2026-07-18T10:00:00Z",
        cfg={"risk_check_enabled": True, "claude_api_key": "test-key"},
    )
    kwargs.update(overrides)
    return kwargs


def _run(coro):
    return asyncio.run(coro)


def _make_response(status_code=200, risks=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = "error body"
    if risks is not None:
        resp.json.return_value = {
            "content": [{"text": json.dumps({"risks": risks})}]
        }
    return resp


class _FakeAsyncClient:
    def __init__(self, response, exc=None):
        self._response = response
        self._exc = exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, *a, **kw):
        if self._exc:
            raise self._exc
        return self._response


def _patch_redis_no_cache(monkeypatch):
    """Redis client whose get() always misses and whose setex() is a no-op — isolates the
    HTTP-call path from any real/leftover Redis state."""
    fake = MagicMock()
    fake.get.return_value = None
    monkeypatch.setattr(risk_agent, "_redis_client", lambda: fake)
    return fake


# ── Opt-in / fail-open gating ──────────────────────────────────────────────────

def test_returns_none_when_risk_check_disabled(monkeypatch):
    """Isolates the opt-in gate specifically — supplies a valid API key so a disabled-gate
    bug can't hide behind the separate no-api-key early return (both currently return None,
    so a naive test using cfg={"risk_check_enabled": False} alone doesn't actually prove this
    gate fires; it could pass even with the opt-in check completely removed)."""
    _patch_redis_no_cache(monkeypatch)
    monkeypatch.setattr(risk_agent, "_get_api_key", lambda cfg: "test-key")
    api_was_called = {"value": False}

    class _TrackingClient(_FakeAsyncClient):
        async def post(self, *a, **kw):
            api_was_called["value"] = True
            return await super().post(*a, **kw)

    monkeypatch.setattr(risk_agent.httpx, "AsyncClient", lambda **kw: _TrackingClient(_make_response(200, [])))
    kwargs = _neutral_kwargs(cfg={"risk_check_enabled": False})
    result = _run(risk_agent.check_risks(**kwargs))
    assert result is None
    assert api_was_called["value"] is False, "the API must never be called when risk_check_enabled is False"


def test_returns_none_when_no_api_key_available(monkeypatch):
    _patch_redis_no_cache(monkeypatch)
    monkeypatch.setattr(risk_agent, "_get_api_key", lambda cfg: "")
    kwargs = _neutral_kwargs()
    result = _run(risk_agent.check_risks(**kwargs))
    assert result is None


# ── HTTP call + parsing ─────────────────────────────────────────────────────────

def test_successful_call_returns_parsed_risks(monkeypatch):
    _patch_redis_no_cache(monkeypatch)
    risks = [
        {"category": "macro", "severity": "high", "note": "FOMC meeting in 2 days"},
        {"category": "technical", "severity": "medium", "note": "RSI overbought at 78"},
        {"category": "sector", "severity": "low", "note": "Sector rotation fading"},
    ]
    fake_client = _FakeAsyncClient(_make_response(200, risks))
    monkeypatch.setattr(risk_agent.httpx, "AsyncClient", lambda **kw: fake_client)
    result = _run(risk_agent.check_risks(**_neutral_kwargs()))
    assert result == risks


def test_returns_none_on_non_200_status(monkeypatch):
    _patch_redis_no_cache(monkeypatch)
    fake_client = _FakeAsyncClient(_make_response(500))
    monkeypatch.setattr(risk_agent.httpx, "AsyncClient", lambda **kw: fake_client)
    result = _run(risk_agent.check_risks(**_neutral_kwargs()))
    assert result is None


def test_returns_none_on_network_exception(monkeypatch):
    _patch_redis_no_cache(monkeypatch)
    fake_client = _FakeAsyncClient(None, exc=ConnectionError("network down"))
    monkeypatch.setattr(risk_agent.httpx, "AsyncClient", lambda **kw: fake_client)
    result = _run(risk_agent.check_risks(**_neutral_kwargs()))
    assert result is None


def test_returns_none_on_malformed_json(monkeypatch):
    _patch_redis_no_cache(monkeypatch)
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"content": [{"text": "not valid json{{{"}]}
    fake_client = _FakeAsyncClient(resp)
    monkeypatch.setattr(risk_agent.httpx, "AsyncClient", lambda **kw: fake_client)
    result = _run(risk_agent.check_risks(**_neutral_kwargs()))
    assert result is None


def test_strips_markdown_code_fences(monkeypatch):
    _patch_redis_no_cache(monkeypatch)
    risks = [{"category": "company", "severity": "medium", "note": "Guidance cut last quarter"}]
    resp = MagicMock(status_code=200)
    resp.json.return_value = {
        "content": [{"text": f"```json\n{json.dumps({'risks': risks})}\n```"}]
    }
    fake_client = _FakeAsyncClient(resp)
    monkeypatch.setattr(risk_agent.httpx, "AsyncClient", lambda **kw: fake_client)
    result = _run(risk_agent.check_risks(**_neutral_kwargs()))
    assert result == risks


# ── Validation of individual risk items ─────────────────────────────────────────

def test_filters_out_risks_with_invalid_category(monkeypatch):
    _patch_redis_no_cache(monkeypatch)
    risks = [
        {"category": "macro", "severity": "high", "note": "Valid risk"},
        {"category": "not_a_real_category", "severity": "high", "note": "Should be dropped"},
    ]
    fake_client = _FakeAsyncClient(_make_response(200, risks))
    monkeypatch.setattr(risk_agent.httpx, "AsyncClient", lambda **kw: fake_client)
    result = _run(risk_agent.check_risks(**_neutral_kwargs()))
    assert result == [{"category": "macro", "severity": "high", "note": "Valid risk"}]


def test_filters_out_risks_with_invalid_severity(monkeypatch):
    _patch_redis_no_cache(monkeypatch)
    risks = [
        {"category": "macro", "severity": "extreme", "note": "Invalid severity"},
        {"category": "technical", "severity": "low", "note": "Valid risk"},
    ]
    fake_client = _FakeAsyncClient(_make_response(200, risks))
    monkeypatch.setattr(risk_agent.httpx, "AsyncClient", lambda **kw: fake_client)
    result = _run(risk_agent.check_risks(**_neutral_kwargs()))
    assert result == [{"category": "technical", "severity": "low", "note": "Valid risk"}]


def test_returns_none_when_zero_risks_pass_validation(monkeypatch):
    """All-invalid risks must degrade to None, not an empty list — check_risks() never
    reports 'no risks found' as a real finding (see module docstring)."""
    _patch_redis_no_cache(monkeypatch)
    risks = [{"category": "bogus", "severity": "bogus", "note": "x"}]
    fake_client = _FakeAsyncClient(_make_response(200, risks))
    monkeypatch.setattr(risk_agent.httpx, "AsyncClient", lambda **kw: fake_client)
    result = _run(risk_agent.check_risks(**_neutral_kwargs()))
    assert result is None


def test_returns_none_when_risks_is_not_a_list(monkeypatch):
    _patch_redis_no_cache(monkeypatch)
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"content": [{"text": json.dumps({"risks": "not a list"})}]}
    fake_client = _FakeAsyncClient(resp)
    monkeypatch.setattr(risk_agent.httpx, "AsyncClient", lambda **kw: fake_client)
    result = _run(risk_agent.check_risks(**_neutral_kwargs()))
    assert result is None


# ── Redis cache behavior ─────────────────────────────────────────────────────────

def test_cache_hit_returns_cached_risks_without_calling_the_api(monkeypatch):
    cached_risks = [{"category": "macro", "severity": "high", "note": "cached risk"}]
    fake_redis = MagicMock()
    fake_redis.get.return_value = json.dumps({"risks": cached_risks})
    monkeypatch.setattr(risk_agent, "_redis_client", lambda: fake_redis)

    api_was_called = {"value": False}

    class _TrackingClient(_FakeAsyncClient):
        async def post(self, *a, **kw):
            api_was_called["value"] = True
            return await super().post(*a, **kw)

    monkeypatch.setattr(risk_agent.httpx, "AsyncClient", lambda **kw: _TrackingClient(_make_response(200, [])))
    result = _run(risk_agent.check_risks(**_neutral_kwargs()))
    assert result == cached_risks
    assert api_was_called["value"] is False


def test_successful_call_writes_to_cache(monkeypatch):
    fake_redis = _patch_redis_no_cache(monkeypatch)
    risks = [{"category": "technical", "severity": "low", "note": "Minor concern"}]
    fake_client = _FakeAsyncClient(_make_response(200, risks))
    monkeypatch.setattr(risk_agent.httpx, "AsyncClient", lambda **kw: fake_client)
    _run(risk_agent.check_risks(**_neutral_kwargs()))
    assert fake_redis.setex.called
    cache_call_args = fake_redis.setex.call_args
    assert cache_call_args[0][1] == risk_agent._CACHE_TTL_SECONDS


# ── Prompt construction (pure, no network) ──────────────────────────────────────

def test_prompt_includes_pre_regime_flags_when_set():
    prompt = risk_agent._build_prompt(
        symbol="AAPL", style="SWING", sig_direction="BUY", confidence=0.75,
        game_plan={"entry2": 100.0, "stop": 95.0, "take_profit": 115.0},
        regime_state="neutral", vix=18.0,
        is_pre_choppy=True, is_pre_risk_off=False,
        research_rec=None, research_score=None,
        days_to_earnings=3, volume_z=2.1, reasons={"sector_momentum": "weak"},
    )
    assert "pre-choppy" in prompt.lower()
    assert "pre-risk-off" not in prompt.lower()
    assert "Days to next earnings: 3" in prompt
    assert "Sector momentum: weak" in prompt


def test_prompt_omits_optional_fields_when_absent():
    prompt = risk_agent._build_prompt(
        symbol="AAPL", style="SWING", sig_direction="BUY", confidence=0.75,
        game_plan={"entry2": 100.0, "stop": 95.0, "take_profit": 115.0},
        regime_state="neutral", vix=None,
        is_pre_choppy=False, is_pre_risk_off=False,
        research_rec=None, research_score=None,
        days_to_earnings=None, volume_z=None, reasons={},
    )
    assert "Days to next earnings" not in prompt
    assert "VIX" not in prompt
    assert "Sector momentum" not in prompt
