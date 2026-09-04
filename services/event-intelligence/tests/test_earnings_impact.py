"""Tests for T249-EARNINGS-LLM-IMPACT — the earnings-side mirror of macro_reaction.py's
generate_reaction()/check_release_day_fast_poll(), built after a direct user request:
"add the LLM feature to earning report as well same as Marco to get the impact report and all
the details."

generate_earnings_impact() calls Claude Haiku with the same fail-open contract, same sector-
impact structure ({"impact_text": ..., "sectors_helped": [...], "sectors_hurt": [...]}), and
the same _clean_sector_list() validation as macro_reaction.py's generate_reaction() — deliberate
parity, not just a similar shape. check_earnings_impact_poll() is the detection half (delivery
is market-data's check_earnings_impact_alerts(), same detect/deliver split already established
for check_release_day_fast_poll()/check_macro_reaction_alerts()), gated behind the
earnings_llm_impact_enabled admin flag (default OFF).
"""
import json as _json
from unittest.mock import MagicMock

from src.services.earnings import _clean_sector_list, _select_transcript_excerpts, generate_earnings_impact


def _run(coro):
    import asyncio
    return asyncio.run(coro)


# ── _clean_sector_list() — identical validation contract to macro_reaction.py's own ────

def test_clean_sector_list_valid_list():
    assert _clean_sector_list(["Technology", "Healthcare"]) == ["Technology", "Healthcare"]


def test_clean_sector_list_non_list_returns_empty():
    assert _clean_sector_list("Technology") == []
    assert _clean_sector_list(None) == []
    assert _clean_sector_list(42) == []


def test_clean_sector_list_filters_non_string_entries():
    assert _clean_sector_list(["Technology", 42, None, "Energy"]) == ["Technology", "Energy"]


def test_clean_sector_list_strips_whitespace_and_drops_empty_strings():
    assert _clean_sector_list(["  Technology  ", "", "   "]) == ["Technology"]


def test_clean_sector_list_caps_at_six():
    raw = [f"Sector{i}" for i in range(10)]
    assert len(_clean_sector_list(raw)) == 6


# ── _select_transcript_excerpts() (AUD-TRANSCRIPT) ──────────────────────────────────

def test_select_excerpts_ranks_by_absolute_sentiment_descending():
    statements = [
        {"speaker": "A", "title": "CFO", "content": "Neutral remark.", "sentiment": 0.05},
        {"speaker": "B", "title": "CEO", "content": "Very confident guidance.", "sentiment": 0.9},
        {"speaker": "C", "title": "Analyst", "content": "Sharp concern raised.", "sentiment": -0.85},
    ]
    result = _select_transcript_excerpts(statements)
    assert [r["speaker"] for r in result] == ["B", "C", "A"]


def test_select_excerpts_drops_statements_with_no_content_or_no_sentiment():
    statements = [
        {"speaker": "A", "title": "CEO", "content": None, "sentiment": 0.5},
        {"speaker": "B", "title": "CFO", "content": "Real content.", "sentiment": None},
        {"speaker": "C", "title": "CEO", "content": "Real content too.", "sentiment": 0.3},
    ]
    result = _select_transcript_excerpts(statements)
    assert len(result) == 1
    assert result[0]["speaker"] == "C"


def test_select_excerpts_caps_at_max_statements():
    import src.services.earnings as e
    statements = [
        {"speaker": f"S{i}", "title": "CEO", "content": f"Statement {i}.", "sentiment": 0.01 * i}
        for i in range(50)
    ]
    result = _select_transcript_excerpts(statements)
    assert len(result) == e._TRANSCRIPT_EXCERPT_MAX_STATEMENTS


def test_select_excerpts_caps_at_max_total_chars():
    import src.services.earnings as e
    long_content = "x" * 400  # each statement's content is itself capped at 400 chars
    statements = [
        {"speaker": f"S{i}", "title": "CEO", "content": long_content, "sentiment": 1.0 - i * 0.01}
        for i in range(20)
    ]
    result = _select_transcript_excerpts(statements)
    total_chars = sum(len(r["content"]) for r in result)
    assert total_chars <= e._TRANSCRIPT_EXCERPT_MAX_CHARS


def test_select_excerpts_truncates_each_statement_to_400_chars():
    statements = [{"speaker": "A", "title": "CEO", "content": "x" * 1000, "sentiment": 0.5}]
    result = _select_transcript_excerpts(statements)
    assert len(result[0]["content"]) == 400


def test_select_excerpts_handles_missing_speaker_and_title_gracefully():
    statements = [{"speaker": None, "title": None, "content": "Real content.", "sentiment": 0.5}]
    result = _select_transcript_excerpts(statements)
    assert result[0]["speaker"] == "Unknown"
    assert result[0]["title"] is None


def test_select_excerpts_empty_input_returns_empty_list():
    assert _select_transcript_excerpts([]) == []


def test_select_excerpts_skips_non_dict_rows_without_crashing():
    statements = [
        {"speaker": "A", "title": "CEO", "content": "Real.", "sentiment": 0.5},
        "not a dict",
        None,
    ]
    result = _select_transcript_excerpts(statements)
    assert len(result) == 1


# ── generate_earnings_impact() with transcript excerpts (AUD-TRANSCRIPT) ────────────

def test_transcript_statements_omitted_produces_byte_identical_behavior(monkeypatch):
    """The core backward-compatibility guarantee: every pre-existing caller (which never
    passes transcript_statements) must see identical behavior to before this feature."""
    import src.services.earnings as e
    monkeypatch.setattr(e, "_api_key", lambda: "test-key")
    fake_client = _FakeAsyncClient(_mock_anthropic_response())
    monkeypatch.setattr(e.httpx, "AsyncClient", lambda **kw: fake_client)

    result = _run(generate_earnings_impact("AAPL", "Technology", 1.5, 1.4, 7.1, 90e9, 88e9, 2.3, 72.0))
    assert result["management_tone"] is None


def test_transcript_statements_provided_are_folded_into_the_prompt(monkeypatch):
    import src.services.earnings as e
    monkeypatch.setattr(e, "_api_key", lambda: "test-key")
    captured = {}

    class _CapturingClient(_FakeAsyncClient):
        async def post(self, url, headers, json):
            captured["body"] = json
            return self._response

    fake_client = _CapturingClient(_mock_anthropic_response(management_tone="Confident on guidance."))
    monkeypatch.setattr(e.httpx, "AsyncClient", lambda **kw: fake_client)

    statements = [{"speaker": "Tim Cook", "title": "CEO", "content": "We feel great about next quarter.", "sentiment": 0.8}]
    result = _run(generate_earnings_impact(
        "AAPL", "Technology", 1.5, 1.4, 7.1, 90e9, 88e9, 2.3, 72.0, statements,
    ))
    assert "Tim Cook" in captured["body"]["messages"][0]["content"]
    assert "We feel great about next quarter." in captured["body"]["messages"][0]["content"]
    assert result["management_tone"] == "Confident on guidance."


def test_empty_transcript_list_produces_no_transcript_block_in_the_prompt(monkeypatch):
    import src.services.earnings as e
    monkeypatch.setattr(e, "_api_key", lambda: "test-key")
    captured = {}

    class _CapturingClient(_FakeAsyncClient):
        async def post(self, url, headers, json):
            captured["body"] = json
            return self._response

    fake_client = _CapturingClient(_mock_anthropic_response())
    monkeypatch.setattr(e.httpx, "AsyncClient", lambda **kw: fake_client)

    result = _run(generate_earnings_impact(
        "AAPL", "Technology", 1.5, 1.4, 7.1, 90e9, 88e9, 2.3, 72.0, [],
    ))
    assert "transcript" not in captured["body"]["messages"][0]["content"].lower()
    assert result["management_tone"] is None


# ── generate_earnings_impact() ──────────────────────────────────────────────────────

def _mock_anthropic_response(sectors_helped=None, sectors_hurt=None, one_paragraph="Test impact.", management_tone=""):
    payload = {
        "one_paragraph": one_paragraph,
        "sectors_helped": sectors_helped if sectors_helped is not None else [],
        "sectors_hurt": sectors_hurt if sectors_hurt is not None else [],
        "management_tone": management_tone,
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


def test_returns_none_when_no_api_key(monkeypatch):
    import src.services.earnings as e
    monkeypatch.setattr(e, "_api_key", lambda: "")
    result = _run(generate_earnings_impact("AAPL", "Technology", 1.5, 1.4, 7.1, None, None, None, 72.0))
    assert result is None


def test_returns_dict_with_sector_lists_on_success(monkeypatch):
    import src.services.earnings as e
    monkeypatch.setattr(e, "_api_key", lambda: "test-key")
    fake_client = _FakeAsyncClient(_mock_anthropic_response(
        sectors_helped=["Technology"], sectors_hurt=["Utilities"],
    ))
    monkeypatch.setattr(e.httpx, "AsyncClient", lambda **kw: fake_client)

    result = _run(generate_earnings_impact("AAPL", "Technology", 1.5, 1.4, 7.1, 90e9, 88e9, 2.3, 72.0))
    assert result == {
        "impact_text": "Test impact.",
        "sectors_helped": ["Technology"],
        "sectors_hurt": ["Utilities"],
        "management_tone": None,
    }


def test_returns_empty_sector_lists_when_llm_provides_none(monkeypatch):
    import src.services.earnings as e
    monkeypatch.setattr(e, "_api_key", lambda: "test-key")
    fake_client = _FakeAsyncClient(_mock_anthropic_response(sectors_helped=[], sectors_hurt=[]))
    monkeypatch.setattr(e.httpx, "AsyncClient", lambda **kw: fake_client)

    result = _run(generate_earnings_impact("XYZ", None, 0.5, 0.6, -16.7, None, None, None, None))
    assert result["sectors_helped"] == []
    assert result["sectors_hurt"] == []


def test_returns_none_when_impact_text_is_missing(monkeypatch):
    """A response with sector lists but no usable one_paragraph must still degrade to None —
    the sector fields are additive to the impact text, never a substitute for it (matching
    macro_reaction.py's generate_reaction() exactly)."""
    import src.services.earnings as e
    monkeypatch.setattr(e, "_api_key", lambda: "test-key")
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"content": [{"text": _json.dumps({
        "one_paragraph": "", "sectors_helped": ["Technology"], "sectors_hurt": [],
    })}]}
    fake_client = _FakeAsyncClient(resp)
    monkeypatch.setattr(e.httpx, "AsyncClient", lambda **kw: fake_client)

    result = _run(generate_earnings_impact("AAPL", "Technology", 1.5, 1.4, 7.1, None, None, None, None))
    assert result is None


def test_returns_none_on_non_200_response(monkeypatch):
    import src.services.earnings as e
    monkeypatch.setattr(e, "_api_key", lambda: "test-key")
    resp = MagicMock(status_code=500, text="Internal Server Error")
    fake_client = _FakeAsyncClient(resp)
    monkeypatch.setattr(e.httpx, "AsyncClient", lambda **kw: fake_client)

    result = _run(generate_earnings_impact("AAPL", "Technology", 1.5, 1.4, 7.1, None, None, None, None))
    assert result is None


def test_returns_none_on_network_exception(monkeypatch):
    import src.services.earnings as e
    monkeypatch.setattr(e, "_api_key", lambda: "test-key")
    fake_client = _FakeAsyncClient(None, exc=ConnectionError("refused"))
    monkeypatch.setattr(e.httpx, "AsyncClient", lambda **kw: fake_client)

    result = _run(generate_earnings_impact("AAPL", "Technology", 1.5, 1.4, 7.1, None, None, None, None))
    assert result is None


def test_returns_none_on_malformed_json(monkeypatch):
    import src.services.earnings as e
    monkeypatch.setattr(e, "_api_key", lambda: "test-key")
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"content": [{"text": "not valid json{{{"}]}
    fake_client = _FakeAsyncClient(resp)
    monkeypatch.setattr(e.httpx, "AsyncClient", lambda **kw: fake_client)

    result = _run(generate_earnings_impact("AAPL", "Technology", 1.5, 1.4, 7.1, None, None, None, None))
    assert result is None


def test_strips_markdown_fence_before_parsing(monkeypatch):
    """Claude sometimes wraps its JSON in ```json fences despite the system prompt saying not
    to — must be stripped before json.loads(), matching risk_agent.py's/news.py's established
    fix for this exact recurring issue."""
    import src.services.earnings as e
    monkeypatch.setattr(e, "_api_key", lambda: "test-key")
    payload = _json.dumps({"one_paragraph": "Fenced.", "sectors_helped": [], "sectors_hurt": []})
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"content": [{"text": f"```json\n{payload}\n```"}]}
    fake_client = _FakeAsyncClient(resp)
    monkeypatch.setattr(e.httpx, "AsyncClient", lambda **kw: fake_client)

    result = _run(generate_earnings_impact("AAPL", "Technology", 1.5, 1.4, 7.1, None, None, None, None))
    assert result is not None
    assert result["impact_text"] == "Fenced."


def test_impact_text_truncated_to_500_chars(monkeypatch):
    import src.services.earnings as e
    monkeypatch.setattr(e, "_api_key", lambda: "test-key")
    long_text = "x" * 600
    fake_client = _FakeAsyncClient(_mock_anthropic_response(one_paragraph=long_text))
    monkeypatch.setattr(e.httpx, "AsyncClient", lambda **kw: fake_client)

    result = _run(generate_earnings_impact("AAPL", "Technology", 1.5, 1.4, 7.1, None, None, None, None))
    assert len(result["impact_text"]) == 500


# ── check_earnings_impact_poll() — feature-flag gate ────────────────────────────────

def test_poll_is_a_noop_when_feature_flag_unset(monkeypatch):
    """Default-OFF discipline: an unset admin flag must skip ALL work (no DB query, no LLM
    call) — matches auto_research_enabled's own fail-closed convention (CLAUDE-API-COST-AUDIT),
    since this is a brand-new Claude-calling feature."""
    from unittest.mock import MagicMock
    import src.services.earnings as e

    fake_redis = MagicMock()
    fake_redis.get.return_value = None
    monkeypatch.setattr("common.redis_client.get_redis", lambda: fake_redis)

    result = _run(e.check_earnings_impact_poll())
    assert result == {"checked": 0, "generated": 0, "skipped": "feature_disabled"}


def test_poll_is_a_noop_when_feature_flag_explicitly_off(monkeypatch):
    from unittest.mock import MagicMock
    import src.services.earnings as e

    fake_redis = MagicMock()
    fake_redis.get.return_value = "0"
    monkeypatch.setattr("common.redis_client.get_redis", lambda: fake_redis)

    result = _run(e.check_earnings_impact_poll())
    assert result["skipped"] == "feature_disabled"


def test_poll_fails_closed_on_redis_error(monkeypatch):
    """An unreachable admin-flag store must not silently enable this expensive feature —
    a Redis exception during the flag check must skip, not fall through to the DB query."""
    import src.services.earnings as e

    def _raise(*a, **kw):
        raise ConnectionError("redis down")

    monkeypatch.setattr("common.redis_client.get_redis", _raise)

    result = _run(e.check_earnings_impact_poll())
    assert result["skipped"] == "feature_disabled"


def test_poll_flag_check_happens_before_any_db_query():
    """Source-text check: the Redis flag guard must be the FIRST thing the function does,
    before SessionLocal is ever touched — a disabled flag must cost nothing."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "earnings.py").read_text()
    start = src.index("async def check_earnings_impact_poll(")
    end = src.index("\ndef ", start) if "\ndef " in src[start:] else len(src)
    body = src[start:end]
    flag_idx = body.index("_REDIS_EARNINGS_LLM_ENABLED")
    session_idx = body.index("with SessionLocal() as s:")
    assert flag_idx < session_idx


def _poll_body() -> str:
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "earnings.py").read_text()
    start = src.index("async def check_earnings_impact_poll(")
    end = src.index("\ndef ", start) if "\ndef " in src[start:] else len(src)
    return src[start:end]


def test_poll_fetches_the_transcript_before_generating_impact():
    body = _poll_body()
    fetch_idx = body.index("_executor, _fetch_transcript_statements_sync")
    call_idx = body.index("impact = await generate_earnings_impact(")
    assert fetch_idx < call_idx


def test_poll_passes_the_fetched_transcript_into_generate_earnings_impact():
    body = _poll_body()
    call_idx = body.index("impact = await generate_earnings_impact(")
    segment = body[call_idx:call_idx + 300]
    assert "transcript_statements" in segment


def test_poll_writes_management_tone_to_the_event():
    body = _poll_body()
    assert 'ev.management_tone = impact.get("management_tone")' in body
