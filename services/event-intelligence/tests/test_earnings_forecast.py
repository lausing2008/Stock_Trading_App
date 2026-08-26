"""Tests for AUD-EARNINGSFORECAST — the PRE-report sibling of T249-EARNINGS-LLM-IMPACT's
generate_earnings_impact(). Built after a direct user request for a "whisper number"/forward-
guidance read on upcoming earnings, framed with an explicit cost-minimization constraint: one
combined Claude call producing both a narrative ("watching_for") and a fixed 3-row scenario
table ("scenarios"), triggered on-demand by a user click (not a scheduled poll), cached 24h
per symbol.

Mirrors test_earnings_impact.py's exact conventions: direct import from src.services.earnings,
the _run(coro) asyncio helper, a _FakeAsyncClient mock for httpx.AsyncClient, and
monkeypatch.setattr("common.redis_client.get_redis", ...) for the flag/cache checks — this
module's own lazy `from common.redis_client import get_redis` import happens INSIDE
generate_earnings_forecast(), so patching the string module path (not e.get_redis, which
doesn't exist as a module attribute) is what actually takes effect, matching the same gotcha
already documented elsewhere in this codebase for lazy imports against a MagicMock-stubbed
parent package.
"""
import json as _json
from unittest.mock import MagicMock

from src.services.earnings import (
    _clean_scenarios,
    _nearest_forecast_period,
    _fetch_fundamentals_sync,
    generate_earnings_forecast,
)


def _run(coro):
    import asyncio
    return asyncio.run(coro)


# ── _clean_scenarios() — whole-result-degrades-to-None validation, unlike _clean_sector_list ──

def _valid_scenarios():
    return [
        {"scenario": "Beat + Raise", "interpretation": "demand accelerating", "typical_reaction": "often a rally"},
        {"scenario": "In-Line", "interpretation": "growth matching expectations", "typical_reaction": "muted reaction"},
        {"scenario": "Miss or Cut", "interpretation": "guidance concerns validated", "typical_reaction": "can trigger a selloff"},
    ]


def test_clean_scenarios_valid_list_passes_through():
    result = _clean_scenarios(_valid_scenarios())
    assert result is not None
    assert len(result) == 3
    assert result[0]["scenario"] == "Beat + Raise"
    assert result[1]["scenario"] == "In-Line"
    assert result[2]["scenario"] == "Miss or Cut"


def test_clean_scenarios_non_list_returns_none():
    assert _clean_scenarios("not a list") is None
    assert _clean_scenarios(None) is None
    assert _clean_scenarios({"scenario": "x"}) is None


def test_clean_scenarios_wrong_length_returns_none():
    assert _clean_scenarios(_valid_scenarios()[:2]) is None
    assert _clean_scenarios(_valid_scenarios() + [_valid_scenarios()[0]]) is None
    assert _clean_scenarios([]) is None


def test_clean_scenarios_non_dict_row_returns_none():
    rows = _valid_scenarios()
    rows[1] = "not a dict"
    assert _clean_scenarios(rows) is None


def test_clean_scenarios_missing_field_returns_none():
    """A single incomplete row must degrade the WHOLE table to None — this feature's entire
    value is the tailored table, so a partial/broken table is worse than none at all."""
    rows = _valid_scenarios()
    del rows[2]["typical_reaction"]
    assert _clean_scenarios(rows) is None

    rows2 = _valid_scenarios()
    rows2[0]["scenario"] = ""
    assert _clean_scenarios(rows2) is None

    rows3 = _valid_scenarios()
    rows3[1]["interpretation"] = "   "  # whitespace-only must not pass the truthiness check
    assert _clean_scenarios(rows3) is None


def test_clean_scenarios_truncates_field_lengths():
    rows = _valid_scenarios()
    rows[0]["scenario"] = "x" * 100
    rows[0]["interpretation"] = "y" * 500
    rows[0]["typical_reaction"] = "z" * 500
    result = _clean_scenarios(rows)
    assert result is not None
    assert len(result[0]["scenario"]) == 40
    assert len(result[0]["interpretation"]) == 200
    assert len(result[0]["typical_reaction"]) == 300


def test_clean_scenarios_strips_whitespace():
    rows = _valid_scenarios()
    rows[0]["scenario"] = "  Beat + Raise  "
    result = _clean_scenarios(rows)
    assert result is not None
    assert result[0]["scenario"] == "Beat + Raise"


# ── _nearest_forecast_period() ──────────────────────────────────────────────────────────

def test_nearest_forecast_period_resolves_0q():
    consensus = {"0q": {"eps_avg": 1.5}, "+1q": {"eps_avg": 1.6}}
    result = _nearest_forecast_period(consensus)
    assert result == ("0q", {"eps_avg": 1.5})


def test_nearest_forecast_period_none_when_0q_missing():
    consensus = {"+1q": {"eps_avg": 1.6}, "0y": {"eps_avg": 6.0}}
    assert _nearest_forecast_period(consensus) is None


def test_nearest_forecast_period_none_on_falsy_input():
    assert _nearest_forecast_period(None) is None
    assert _nearest_forecast_period({}) is None


# ── _fetch_fundamentals_sync() ──────────────────────────────────────────────────────────

def test_fetch_fundamentals_sync_returns_json_on_200(monkeypatch):
    import src.services.earnings as e
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"earnings_consensus": {"0q": {"eps_avg": 1.5}}}
    monkeypatch.setattr(e.httpx, "get", lambda *a, **kw: resp)
    result = _fetch_fundamentals_sync("AAPL")
    assert result == {"earnings_consensus": {"0q": {"eps_avg": 1.5}}}


def test_fetch_fundamentals_sync_returns_none_on_non_200(monkeypatch):
    import src.services.earnings as e
    resp = MagicMock(status_code=404)
    monkeypatch.setattr(e.httpx, "get", lambda *a, **kw: resp)
    assert _fetch_fundamentals_sync("AAPL") is None


def test_fetch_fundamentals_sync_returns_none_on_exception(monkeypatch):
    import src.services.earnings as e

    def _raise(*a, **kw):
        raise ConnectionError("refused")

    monkeypatch.setattr(e.httpx, "get", _raise)
    assert _fetch_fundamentals_sync("AAPL") is None


# ── _fetch_past_reactions_sync() ────────────────────────────────────────────────────────
# Matches test_sync_todays_earnings.py's established fake-session convention exactly (a real
# SQLAlchemy expression tree can't be built against the stubbed sqlalchemy module, so these
# drive behavior via what SessionLocal().execute(...) returns, not by inspecting the built
# query). Unlike that file's single s.execute(...).all() call, this function calls s.execute()
# TWICE (a .scalar() stock_id lookup, then a .all() reactions-rows fetch) — side_effect is a
# list so each call returns its own distinct fake result in order.

def _install_fake_reactions_session(monkeypatch, stock_id, rows):
    """stock_id: the value the first s.execute(...).scalar() call should return (None for a
    genuinely unknown symbol). rows: the list of tuples the second s.execute(...).all() call
    should return."""
    import src.services.earnings as e
    scalar_result = MagicMock()
    scalar_result.scalar.return_value = stock_id
    all_result = MagicMock()
    all_result.all.return_value = rows
    fake_session = MagicMock()
    fake_session.execute.side_effect = [scalar_result, all_result]
    fake_session.__enter__.return_value = fake_session
    fake_session.__exit__.return_value = False
    monkeypatch.setattr(e, "SessionLocal", lambda: fake_session)
    return fake_session


def test_fetch_past_reactions_sync_returns_empty_list_for_unknown_symbol(monkeypatch):
    import src.services.earnings as e
    _install_fake_reactions_session(monkeypatch, None, [])
    assert e._fetch_past_reactions_sync("ZZZZ") == []


def test_fetch_past_reactions_sync_returns_real_rows_shaped_correctly(monkeypatch):
    import src.services.earnings as e
    from datetime import date
    rows = [
        (date(2026, 8, 6), 45.2, -0.1153, 0.0826),
        (date(2026, 5, 6), -12.0, -0.0421, -0.0198),
    ]
    _install_fake_reactions_session(monkeypatch, 42, rows)
    result = e._fetch_past_reactions_sync("OSCR")
    assert result == [
        {"report_date": "2026-08-06", "surprise_pct": 45.2, "return_1d": -0.1153, "return_5d": 0.0826},
        {"report_date": "2026-05-06", "surprise_pct": -12.0, "return_1d": -0.0421, "return_5d": -0.0198},
    ]


def test_fetch_past_reactions_sync_returns_empty_list_when_no_reactions_are_measured_yet(monkeypatch):
    """A symbol with real EarningsEvent rows but none with a measured post_earnings_return_1d
    yet (too recent, or the backfill job hasn't run) must return [] — never a partial/padded
    result guessing at unmeasured values."""
    import src.services.earnings as e
    _install_fake_reactions_session(monkeypatch, 42, [])
    assert e._fetch_past_reactions_sync("NVDA") == []


def test_fetch_past_reactions_sync_fails_open_on_a_db_exception(monkeypatch):
    import src.services.earnings as e

    def _raise():
        raise ConnectionError("db down")

    monkeypatch.setattr(e, "SessionLocal", _raise)
    assert e._fetch_past_reactions_sync("AAPL") == []


# ── generate_earnings_forecast() ────────────────────────────────────────────────────────

def _mock_anthropic_response(watching_for="Watch the guidance.", scenarios=None, bellwether_note=""):
    payload = {
        "watching_for": watching_for,
        "scenarios": scenarios if scenarios is not None else _valid_scenarios(),
        "bellwether_note": bellwether_note,
    }
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"content": [{"text": _json.dumps(payload)}]}
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


def _fake_redis(flag_value="1", cached_value=None):
    r = MagicMock()

    def _get(key):
        if key == "stockai:earnings_forecast:AAPL":
            return cached_value
        return flag_value

    r.get.side_effect = _get
    return r


def _patch_fundamentals(monkeypatch, fundamentals):
    """Bypasses the real executor/thread-pool hop — patches _fetch_fundamentals_sync directly
    on the module, matching how loop.run_in_executor(_executor, _fetch_fundamentals_sync, ...)
    calls it: by module-level name, so a monkeypatch on the module attribute takes effect."""
    import src.services.earnings as e
    monkeypatch.setattr(e, "_fetch_fundamentals_sync", lambda symbol: fundamentals)


def _patch_past_reactions(monkeypatch, reactions):
    """Same bypass shape as _patch_fundamentals above, for the sibling
    _fetch_past_reactions_sync() executor call."""
    import src.services.earnings as e
    monkeypatch.setattr(e, "_fetch_past_reactions_sync", lambda symbol, limit=4: reactions)


_REAL_FUNDAMENTALS = {
    "earnings_consensus": {
        "0q": {
            "eps_avg": 1.5, "eps_low": 1.4, "eps_high": 1.6, "number_of_analysts": 42,
            "eps_trend_current": 1.5, "eps_trend_7d_ago": 1.48, "eps_trend_30d_ago": 1.45,
            "eps_trend_90d_ago": 1.40, "revisions_up_30d": 12, "revisions_down_30d": 2,
            "revenue_avg": 90e9, "revenue_low": 88e9, "revenue_high": 92e9,
        },
    },
    "growth_vs_index": {"0q": {"stock_growth": 0.25, "index_growth": 0.08}},
    "eps_beat_rate": 0.85,
    "eps_avg_surprise_pct": 0.06,
}


def test_returns_none_when_feature_flag_unset(monkeypatch):
    monkeypatch.setattr("common.redis_client.get_redis", lambda: _fake_redis(flag_value=None))
    result = _run(generate_earnings_forecast("AAPL", "Technology", 1))
    assert result is None


def test_returns_none_when_feature_flag_explicitly_off(monkeypatch):
    monkeypatch.setattr("common.redis_client.get_redis", lambda: _fake_redis(flag_value="0"))
    result = _run(generate_earnings_forecast("AAPL", "Technology", 1))
    assert result is None


def test_fails_closed_on_redis_error(monkeypatch):
    """A Redis outage during the flag check must skip, never fall through to a real (costly)
    Claude call — matches check_earnings_impact_poll()'s own established fail-closed discipline."""
    def _raise():
        raise ConnectionError("redis down")

    monkeypatch.setattr("common.redis_client.get_redis", _raise)
    result = _run(generate_earnings_forecast("AAPL", "Technology", 1))
    assert result is None


def test_returns_none_when_no_api_key(monkeypatch):
    import src.services.earnings as e
    monkeypatch.setattr("common.redis_client.get_redis", lambda: _fake_redis())
    monkeypatch.setattr(e, "_api_key", lambda: "")
    result = _run(generate_earnings_forecast("AAPL", "Technology", 1))
    assert result is None


def test_returns_cached_result_without_a_fresh_call(monkeypatch):
    """A cache hit must short-circuit before ever fetching fundamentals or calling Claude —
    the whole point of the 24h TTL is to avoid a repeat-click regenerating the forecast."""
    import src.services.earnings as e
    cached = {"watching_for": "Cached.", "scenarios": _valid_scenarios(), "bellwether_note": None, "generated_at": "2026-08-25T00:00:00+00:00"}
    monkeypatch.setattr("common.redis_client.get_redis", lambda: _fake_redis(cached_value=_json.dumps(cached)))
    monkeypatch.setattr(e, "_api_key", lambda: "test-key")

    def _boom(symbol):
        raise AssertionError("must not fetch fundamentals on a cache hit")

    monkeypatch.setattr(e, "_fetch_fundamentals_sync", _boom)

    result = _run(generate_earnings_forecast("AAPL", "Technology", 1))
    assert result == cached


def test_a_corrupted_cache_entry_falls_through_to_a_fresh_regeneration(monkeypatch):
    import src.services.earnings as e
    monkeypatch.setattr("common.redis_client.get_redis", lambda: _fake_redis(cached_value="not valid json{{{"))
    monkeypatch.setattr(e, "_api_key", lambda: "test-key")
    _patch_fundamentals(monkeypatch, _REAL_FUNDAMENTALS)
    fake_client = _FakeAsyncClient(_mock_anthropic_response())
    monkeypatch.setattr(e.httpx, "AsyncClient", lambda **kw: fake_client)

    result = _run(generate_earnings_forecast("AAPL", "Technology", 1))
    assert result is not None
    assert result["watching_for"] == "Watch the guidance."


def test_returns_none_when_fundamentals_fetch_fails(monkeypatch):
    import src.services.earnings as e
    monkeypatch.setattr("common.redis_client.get_redis", lambda: _fake_redis())
    monkeypatch.setattr(e, "_api_key", lambda: "test-key")
    _patch_fundamentals(monkeypatch, None)
    result = _run(generate_earnings_forecast("AAPL", "Technology", 1))
    assert result is None


def test_returns_none_when_0q_consensus_period_is_missing(monkeypatch):
    """Thin/no analyst coverage for the actual upcoming report must never fabricate a forecast
    from a different period's data."""
    import src.services.earnings as e
    monkeypatch.setattr("common.redis_client.get_redis", lambda: _fake_redis())
    monkeypatch.setattr(e, "_api_key", lambda: "test-key")
    _patch_fundamentals(monkeypatch, {"earnings_consensus": {"+1q": {"eps_avg": 1.6}}})
    result = _run(generate_earnings_forecast("AAPL", "Technology", 1))
    assert result is None


def test_returns_full_result_shape_on_success(monkeypatch):
    import src.services.earnings as e
    fake_redis = _fake_redis()
    monkeypatch.setattr("common.redis_client.get_redis", lambda: fake_redis)
    monkeypatch.setattr(e, "_api_key", lambda: "test-key")
    _patch_fundamentals(monkeypatch, _REAL_FUNDAMENTALS)
    fake_client = _FakeAsyncClient(_mock_anthropic_response(
        watching_for="Analysts have raised estimates 12 times in 30 days.",
        bellwether_note="A strong print here would validate broader tech demand.",
    ))
    monkeypatch.setattr(e.httpx, "AsyncClient", lambda **kw: fake_client)

    result = _run(generate_earnings_forecast("AAPL", "Technology", 1))
    assert result is not None
    assert result["watching_for"] == "Analysts have raised estimates 12 times in 30 days."
    assert len(result["scenarios"]) == 3
    assert result["bellwether_note"] == "A strong print here would validate broader tech demand."
    assert "generated_at" in result
    # A symbol with no real past-reaction history yet must report an empty list, not omit the
    # key or fabricate placeholder rows.
    assert result["past_reactions"] == []
    # A successful generation must write back to the cache for next time.
    fake_redis.setex.assert_called_once()
    assert fake_redis.setex.call_args[0][0] == "stockai:earnings_forecast:AAPL"


def test_past_reactions_are_included_in_the_result_and_the_prompt(monkeypatch):
    """AUD-EARNINGSFORECAST-EXTEND: this stock's own real, measured past-reaction history must
    (a) be passed through verbatim into the final result (so the frontend can render it
    directly, independent of how the LLM chose to reference it) and (b) actually reach the
    Claude prompt (so the LLM's own typical_reaction framing can be grounded in it)."""
    import src.services.earnings as e
    monkeypatch.setattr("common.redis_client.get_redis", lambda: _fake_redis())
    monkeypatch.setattr(e, "_api_key", lambda: "test-key")
    _patch_fundamentals(monkeypatch, _REAL_FUNDAMENTALS)
    reactions = [
        {"report_date": "2026-08-06", "surprise_pct": 45.2, "return_1d": -0.1153, "return_5d": 0.0826},
    ]
    _patch_past_reactions(monkeypatch, reactions)

    captured_prompts = []

    class _CapturingClient(_FakeAsyncClient):
        async def post(self, *a, **kw):
            captured_prompts.append(kw["json"]["messages"][0]["content"])
            return await super().post(*a, **kw)

    monkeypatch.setattr(e.httpx, "AsyncClient", lambda **kw: _CapturingClient(_mock_anthropic_response()))

    result = _run(generate_earnings_forecast("AAPL", "Healthcare", 3))
    assert result is not None
    assert result["past_reactions"] == reactions
    assert len(captured_prompts) == 1
    assert "2026-08-06" in captured_prompts[0]
    assert "-11.5%" in captured_prompts[0]  # return_1d formatted as a percent
    assert "8.3%" in captured_prompts[0]    # return_5d formatted as a percent


def test_no_past_reactions_degrades_the_prompt_gracefully_not_a_crash(monkeypatch):
    import src.services.earnings as e
    monkeypatch.setattr("common.redis_client.get_redis", lambda: _fake_redis())
    monkeypatch.setattr(e, "_api_key", lambda: "test-key")
    _patch_fundamentals(monkeypatch, _REAL_FUNDAMENTALS)
    _patch_past_reactions(monkeypatch, [])

    captured_prompts = []

    class _CapturingClient(_FakeAsyncClient):
        async def post(self, *a, **kw):
            captured_prompts.append(kw["json"]["messages"][0]["content"])
            return await super().post(*a, **kw)

    monkeypatch.setattr(e.httpx, "AsyncClient", lambda **kw: _CapturingClient(_mock_anthropic_response()))

    result = _run(generate_earnings_forecast("AAPL", "Technology", 1))
    assert result is not None
    assert result["past_reactions"] == []
    assert "unavailable" in captured_prompts[0]


def test_empty_bellwether_note_becomes_none(monkeypatch):
    import src.services.earnings as e
    monkeypatch.setattr("common.redis_client.get_redis", lambda: _fake_redis())
    monkeypatch.setattr(e, "_api_key", lambda: "test-key")
    _patch_fundamentals(monkeypatch, _REAL_FUNDAMENTALS)
    fake_client = _FakeAsyncClient(_mock_anthropic_response(bellwether_note=""))
    monkeypatch.setattr(e.httpx, "AsyncClient", lambda **kw: fake_client)

    result = _run(generate_earnings_forecast("AAPL", "Technology", 1))
    assert result["bellwether_note"] is None


def test_returns_none_when_scenarios_are_invalid(monkeypatch):
    import src.services.earnings as e
    monkeypatch.setattr("common.redis_client.get_redis", lambda: _fake_redis())
    monkeypatch.setattr(e, "_api_key", lambda: "test-key")
    _patch_fundamentals(monkeypatch, _REAL_FUNDAMENTALS)
    fake_client = _FakeAsyncClient(_mock_anthropic_response(scenarios=[{"scenario": "only one"}]))
    monkeypatch.setattr(e.httpx, "AsyncClient", lambda **kw: fake_client)

    result = _run(generate_earnings_forecast("AAPL", "Technology", 1))
    assert result is None


def test_returns_none_when_watching_for_is_missing(monkeypatch):
    import src.services.earnings as e
    monkeypatch.setattr("common.redis_client.get_redis", lambda: _fake_redis())
    monkeypatch.setattr(e, "_api_key", lambda: "test-key")
    _patch_fundamentals(monkeypatch, _REAL_FUNDAMENTALS)
    fake_client = _FakeAsyncClient(_mock_anthropic_response(watching_for=""))
    monkeypatch.setattr(e.httpx, "AsyncClient", lambda **kw: fake_client)

    result = _run(generate_earnings_forecast("AAPL", "Technology", 1))
    assert result is None


def test_returns_none_on_non_200_response(monkeypatch):
    import src.services.earnings as e
    monkeypatch.setattr("common.redis_client.get_redis", lambda: _fake_redis())
    monkeypatch.setattr(e, "_api_key", lambda: "test-key")
    _patch_fundamentals(monkeypatch, _REAL_FUNDAMENTALS)
    resp = MagicMock(status_code=500, text="Internal Server Error")
    fake_client = _FakeAsyncClient(resp)
    monkeypatch.setattr(e.httpx, "AsyncClient", lambda **kw: fake_client)

    result = _run(generate_earnings_forecast("AAPL", "Technology", 1))
    assert result is None


def test_returns_none_on_network_exception(monkeypatch):
    import src.services.earnings as e
    monkeypatch.setattr("common.redis_client.get_redis", lambda: _fake_redis())
    monkeypatch.setattr(e, "_api_key", lambda: "test-key")
    _patch_fundamentals(monkeypatch, _REAL_FUNDAMENTALS)
    fake_client = _FakeAsyncClient(None, exc=ConnectionError("refused"))
    monkeypatch.setattr(e.httpx, "AsyncClient", lambda **kw: fake_client)

    result = _run(generate_earnings_forecast("AAPL", "Technology", 1))
    assert result is None


def test_returns_none_on_malformed_json(monkeypatch):
    import src.services.earnings as e
    monkeypatch.setattr("common.redis_client.get_redis", lambda: _fake_redis())
    monkeypatch.setattr(e, "_api_key", lambda: "test-key")
    _patch_fundamentals(monkeypatch, _REAL_FUNDAMENTALS)
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"content": [{"text": "not valid json{{{"}]}
    fake_client = _FakeAsyncClient(resp)
    monkeypatch.setattr(e.httpx, "AsyncClient", lambda **kw: fake_client)

    result = _run(generate_earnings_forecast("AAPL", "Technology", 1))
    assert result is None


def test_strips_markdown_fence_before_parsing(monkeypatch):
    """Claude sometimes wraps its JSON in ```json fences despite the system prompt saying not
    to — matches the identical fix already applied to generate_earnings_impact()/risk_agent.py/
    news.py for this exact recurring issue."""
    import src.services.earnings as e
    monkeypatch.setattr("common.redis_client.get_redis", lambda: _fake_redis())
    monkeypatch.setattr(e, "_api_key", lambda: "test-key")
    _patch_fundamentals(monkeypatch, _REAL_FUNDAMENTALS)
    payload = _json.dumps({"watching_for": "Fenced.", "scenarios": _valid_scenarios(), "bellwether_note": ""})
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"content": [{"text": f"```json\n{payload}\n```"}]}
    fake_client = _FakeAsyncClient(resp)
    monkeypatch.setattr(e.httpx, "AsyncClient", lambda **kw: fake_client)

    result = _run(generate_earnings_forecast("AAPL", "Technology", 1))
    assert result is not None
    assert result["watching_for"] == "Fenced."


def test_watching_for_truncated_to_500_chars(monkeypatch):
    import src.services.earnings as e
    monkeypatch.setattr("common.redis_client.get_redis", lambda: _fake_redis())
    monkeypatch.setattr(e, "_api_key", lambda: "test-key")
    _patch_fundamentals(monkeypatch, _REAL_FUNDAMENTALS)
    fake_client = _FakeAsyncClient(_mock_anthropic_response(watching_for="x" * 600))
    monkeypatch.setattr(e.httpx, "AsyncClient", lambda **kw: fake_client)

    result = _run(generate_earnings_forecast("AAPL", "Technology", 1))
    assert len(result["watching_for"]) == 500


def test_a_cache_write_failure_does_not_block_returning_the_real_result(monkeypatch):
    import src.services.earnings as e
    fake_redis = _fake_redis()
    fake_redis.setex.side_effect = ConnectionError("redis down")
    monkeypatch.setattr("common.redis_client.get_redis", lambda: fake_redis)
    monkeypatch.setattr(e, "_api_key", lambda: "test-key")
    _patch_fundamentals(monkeypatch, _REAL_FUNDAMENTALS)
    fake_client = _FakeAsyncClient(_mock_anthropic_response())
    monkeypatch.setattr(e.httpx, "AsyncClient", lambda **kw: fake_client)

    result = _run(generate_earnings_forecast("AAPL", "Technology", 1))
    assert result is not None
    assert result["watching_for"] == "Watch the guidance."


# ── source-text checks — flag ordering, cache-key isolation ────────────────────────────

def test_flag_check_happens_before_any_fundamentals_fetch():
    """Source-text check: the Redis flag guard must be the FIRST thing the function does,
    before the (real, blocking, executor-hopped) fundamentals fetch is ever scheduled — a
    disabled flag must cost nothing, matching check_earnings_impact_poll()'s own established
    discipline (test_poll_flag_check_happens_before_any_db_query)."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "earnings.py").read_text()
    start = src.index("async def generate_earnings_forecast(")
    end = src.index("\nasync def check_earnings_impact_poll(")
    body = src[start:end]
    flag_idx = body.index("_REDIS_EARNINGS_FORECAST_ENABLED")
    fetch_idx = body.index("run_in_executor(_executor, _fetch_fundamentals_sync")
    assert flag_idx < fetch_idx


def test_forecast_cache_key_is_distinct_from_the_admin_flag_key():
    """The per-symbol cache key and the global admin-flag key must never collide — this is a
    regression guard for a genuinely easy mistake (both are simple 'stockai:...:{symbol}'-
    shaped Redis keys read from the same lazily-imported get_redis())."""
    from src.services.earnings import _REDIS_EARNINGS_FORECAST_ENABLED
    assert _REDIS_EARNINGS_FORECAST_ENABLED != "stockai:earnings_forecast:AAPL"
    assert "admin:feature" in _REDIS_EARNINGS_FORECAST_ENABLED


def test_the_lazy_redis_client_import_lives_inside_the_flag_check_try_block():
    """Source-text check: `from common.redis_client import get_redis` must sit INSIDE the
    try block that also does the flag check, not before it — matching check_earnings_
    impact_poll()'s own established shape exactly. A genuinely broken/missing common.
    redis_client module in production must fail open the same way a real Redis connection
    error does (return None), not raise past this function's own try/except and crash the
    caller. Placing the import outside the try would silently defeat that guarantee — this
    was a real bug caught during development (the import originally sat one line above the
    try, letting a ModuleNotFoundError propagate uncaught)."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "earnings.py").read_text()
    start = src.index("async def generate_earnings_forecast(")
    end = src.index("\nasync def check_earnings_impact_poll(")
    body = src[start:end]
    try_idx = body.index("try:")
    import_idx = body.index("from common.redis_client import get_redis")
    except_idx = body.index("except Exception:\n        return None")
    assert try_idx < import_idx < except_idx
