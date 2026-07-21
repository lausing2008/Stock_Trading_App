"""Tests for T249-MARKETMOVER-P4's Market Pulse card (GET /stocks/market/pulse).

news.py imports real feedparser/vaderSentiment (not stubbed by conftest.py — both are real
pinned requirements.txt dependencies, just missing from this local dev environment, matching
the same class of gap already documented for jose/requests_oauthlib elsewhere in this repo) so
it can be imported directly. httpx/redis ARE stubbed as MagicMock by conftest.py, so the tests
below monkeypatch news._get_redis, news._google_news, and news._claude_market_themes directly
at the call boundary rather than going through the stubbed httpx/redis clients.
"""
from unittest.mock import patch

from src.api import news


def _make_item(title: str, sentiment: float = 0.0) -> news.NewsItem:
    return news.NewsItem(
        title=title, url="https://example.com", source="Test Source",
        published_at=1_700_000_000, sentiment=sentiment,
        sentiment_label=news._label(sentiment),
    )


class _FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}

    def get(self, key):
        return self.store.get(key)

    def setex(self, key, ttl, value):
        self.store[key] = value


# ── get_market_pulse() — Claude path ─────────────────────────────────────────────────────

def test_market_pulse_uses_claude_when_available():
    fake_redis = _FakeRedis()
    headlines = [_make_item("Fed signals rate cut", 0.4), _make_item("Stocks rally on jobs data", 0.6)]
    with patch.object(news, "_get_redis", return_value=fake_redis), \
         patch.object(news, "_google_news", return_value=headlines), \
         patch.object(news, "_claude_market_themes", return_value={"score": 72.0, "themes": ["Fed rate-cut expectations"]}):
        result = news.get_market_pulse()
    assert result.score == 72.0
    assert result.label == "positive"
    assert result.source == "claude"
    assert result.themes == ["Fed rate-cut expectations"]
    assert len(result.headlines) == 2


def test_market_pulse_falls_back_to_vader_when_claude_unavailable():
    fake_redis = _FakeRedis()
    headlines = [_make_item("Stocks fall on inflation fears", -0.5), _make_item("Market drops", -0.3)]
    with patch.object(news, "_get_redis", return_value=fake_redis), \
         patch.object(news, "_google_news", return_value=headlines), \
         patch.object(news, "_claude_market_themes", return_value=None):
        result = news.get_market_pulse()
    assert result.source == "vader"
    assert result.label == "negative"
    assert result.themes == []  # no theme extraction without Claude
    assert result.score < 50


def test_market_pulse_neutral_when_no_headlines_found():
    fake_redis = _FakeRedis()
    with patch.object(news, "_get_redis", return_value=fake_redis), \
         patch.object(news, "_google_news", return_value=[]), \
         patch.object(news, "_claude_market_themes", return_value=None):
        result = news.get_market_pulse()
    assert result.score == 50.0
    assert result.label == "neutral"
    assert result.headlines == []


def test_market_pulse_queries_all_three_market_level_terms():
    fake_redis = _FakeRedis()
    seen_queries = []

    def _fake_google_news(query, limit=15):
        seen_queries.append(query)
        return []

    with patch.object(news, "_get_redis", return_value=fake_redis), \
         patch.object(news, "_google_news", side_effect=_fake_google_news), \
         patch.object(news, "_claude_market_themes", return_value=None):
        news.get_market_pulse()

    assert seen_queries == news._PULSE_QUERIES
    assert seen_queries == ["stock market", "S&P 500", "Federal Reserve"]


def test_market_pulse_caches_result_in_redis():
    fake_redis = _FakeRedis()
    headlines = [_make_item("Stocks rise", 0.2)]
    with patch.object(news, "_get_redis", return_value=fake_redis), \
         patch.object(news, "_google_news", return_value=headlines), \
         patch.object(news, "_claude_market_themes", return_value=None):
        news.get_market_pulse()
    assert news._PULSE_CACHE_KEY in fake_redis.store


def test_market_pulse_reads_from_warm_cache_without_refetching():
    fake_redis = _FakeRedis()
    cached_json = news.MarketPulseResponse(
        score=88.0, label="positive", source="claude", themes=["Cached theme"],
        headlines=[], generated_at=1_700_000_000,
    ).model_dump_json()
    fake_redis.store[news._PULSE_CACHE_KEY] = cached_json

    call_count = {"n": 0}

    def _fake_google_news(query, limit=15):
        call_count["n"] += 1
        return []

    with patch.object(news, "_get_redis", return_value=fake_redis), \
         patch.object(news, "_google_news", side_effect=_fake_google_news):
        result = news.get_market_pulse()

    assert call_count["n"] == 0  # never re-fetched — served from cache
    assert result["score"] == 88.0
    assert result["themes"] == ["Cached theme"]


def test_market_pulse_caps_themes_at_three():
    """_claude_market_themes' own cap — a runaway/malformed Claude response with more than 3
    themes must not leak extras into the response (the prompt asks for <=3 but nothing stops a
    model from ignoring that)."""
    raw_themes = ["Theme A", "Theme B", "Theme C", "Theme D", "Theme E"]

    class _FakeResp:
        status_code = 200
        def json(self):
            return {"content": [{"text": '{"score": 55, "themes": ' + str(raw_themes).replace("'", '"') + '}'}]}

    class _FakeClient:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def post(self, *a, **kw):
            return _FakeResp()

    with patch.object(news, "_get_claude_key", return_value="fake-key"), \
         patch.object(news.httpx, "Client", return_value=_FakeClient()):
        result = news._claude_market_themes(["headline 1", "headline 2"])

    assert result is not None
    assert len(result["themes"]) == 3
    assert result["themes"] == ["Theme A", "Theme B", "Theme C"]


def test_claude_market_themes_strips_markdown_fence_before_parsing():
    """Real production bug (2026-07-18): Claude sometimes wraps its JSON response in
    ```json ... ``` despite the system prompt saying not to. json.loads() on the raw fenced
    text raises, which was silently swallowed and produced a None result — the deployed
    Market Pulse card always fell back to VADER with no themes, even with a valid, correctly
    configured API key, because every real response happened to come back fenced."""
    class _FakeResp:
        status_code = 200
        def json(self):
            return {"content": [{"text": '```json\n{"score": 72, "themes": ["Fed rate-cut hopes"]}\n```'}]}

    class _FakeClient:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def post(self, *a, **kw):
            return _FakeResp()

    with patch.object(news, "_get_claude_key", return_value="fake-key"), \
         patch.object(news.httpx, "Client", return_value=_FakeClient()):
        result = news._claude_market_themes(["headline 1"])

    assert result is not None
    assert result["score"] == 72.0
    assert result["themes"] == ["Fed rate-cut hopes"]


def test_strip_markdown_fence_handles_plain_json_unchanged():
    assert news._strip_markdown_fence('{"score": 50}') == '{"score": 50}'


def test_strip_markdown_fence_strips_json_language_tag():
    assert news._strip_markdown_fence('```json\n{"score": 50}\n```') == '{"score": 50}'


def test_strip_markdown_fence_strips_bare_fence_without_language_tag():
    assert news._strip_markdown_fence('```\n{"score": 50}\n```') == '{"score": 50}'


def test_claude_market_themes_returns_none_without_api_key():
    with patch.object(news, "_get_claude_key", return_value=""), \
         patch.object(news.httpx, "Client") as mock_client:
        result = news._claude_market_themes(["some headline"])
    assert result is None
    mock_client.assert_not_called()  # must short-circuit before any HTTP call


def test_claude_market_themes_returns_none_on_non_200():
    class _FakeResp:
        status_code = 500
        def json(self):
            return {}

    class _FakeClient:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def post(self, *a, **kw):
            return _FakeResp()

    with patch.object(news, "_get_claude_key", return_value="fake-key"), \
         patch.object(news.httpx, "Client", return_value=_FakeClient()):
        result = news._claude_market_themes(["some headline"])
    assert result is None


def test_claude_market_themes_returns_none_on_malformed_json():
    class _FakeResp:
        status_code = 200
        def json(self):
            return {"content": [{"text": "not json at all"}]}

    class _FakeClient:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def post(self, *a, **kw):
            return _FakeResp()

    with patch.object(news, "_get_claude_key", return_value="fake-key"), \
         patch.object(news.httpx, "Client", return_value=_FakeClient()):
        result = news._claude_market_themes(["some headline"])
    assert result is None


def test_market_pulse_label_boundaries():
    fake_redis = _FakeRedis()
    for score, expected_label in [(60.0, "positive"), (59.9, "neutral"), (40.1, "neutral"), (40.0, "negative")]:
        fake_redis.store.clear()
        with patch.object(news, "_get_redis", return_value=fake_redis), \
             patch.object(news, "_google_news", return_value=[_make_item("headline")]), \
             patch.object(news, "_claude_market_themes", return_value={"score": score, "themes": []}):
            result = news.get_market_pulse()
        assert result.label == expected_label, f"score={score} expected {expected_label}, got {result.label}"


# ── _get_claude_key() — Redis-first, settings-fallback (matches llm_scorer.py's pattern) ──
# AUD-REDISAUDIT-CLAUDEKEY-FALLBACK: the fallback used to be a bare os.getenv("ANTHROPIC_API_KEY")
# — the only site in the repo referencing that env var — now matches every sibling service's
# own convention of falling back to a settings attribute (getattr(_settings, "claude_api_key",
# "")) instead. Tests patch news._settings.claude_api_key rather than a removed _ANTHROPIC_KEY
# module constant.

def test_get_claude_key_prefers_redis_over_settings_fallback():
    fake_redis = _FakeRedis()
    fake_redis.store[news._REDIS_CLAUDE_KEY] = "redis-key"
    with patch.object(news, "_get_redis", return_value=fake_redis), \
         patch.object(news._settings, "claude_api_key", "settings-key", create=True):
        assert news._get_claude_key() == "redis-key"


def test_get_claude_key_falls_back_to_settings_when_redis_empty():
    fake_redis = _FakeRedis()  # no key set
    with patch.object(news, "_get_redis", return_value=fake_redis), \
         patch.object(news._settings, "claude_api_key", "settings-key", create=True):
        assert news._get_claude_key() == "settings-key"


def test_get_claude_key_falls_back_to_settings_on_redis_error():
    class _BrokenRedis:
        def get(self, key):
            raise ConnectionError("redis unavailable")
    with patch.object(news, "_get_redis", return_value=_BrokenRedis()), \
         patch.object(news._settings, "claude_api_key", "settings-key", create=True):
        assert news._get_claude_key() == "settings-key"


def test_get_claude_key_ignores_whitespace_only_redis_value():
    fake_redis = _FakeRedis()
    fake_redis.store[news._REDIS_CLAUDE_KEY] = "   "
    with patch.object(news, "_get_redis", return_value=fake_redis), \
         patch.object(news._settings, "claude_api_key", "settings-key", create=True):
        assert news._get_claude_key() == "settings-key"
