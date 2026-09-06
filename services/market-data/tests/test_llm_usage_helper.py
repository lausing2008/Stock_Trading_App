"""Tests for shared/common/llm_usage.py — the single shared helper that logs every real
Anthropic API call across all 9 call sites, added specifically because none of them logged
real token usage before, and that blind spot let BUG-NEWSCLASSIFY-REPEATCOST's undetected
deploy-drift run for six weeks (5.44M unlogged Haiku tokens in one day, discovered only by
chance on the external Claude Console billing page).

Loaded via exec()-from-source since `log_llm_call()` does a deferred `from db import
LlmCallLog, SessionLocal` INSIDE the function body specifically so a DB/import failure there
can never prevent the module from loading — matching this repo's established technique for
shared/common modules (see test_ai_keys.py).
"""
import pathlib

_path = pathlib.Path(__file__).resolve().parents[3] / "shared" / "common" / "llm_usage.py"
_source = _path.read_text()

_namespace: dict = {}
exec(compile(_source, str(_path), "exec"), _namespace)
log_llm_call = _namespace["log_llm_call"]


class _FakeLlmCallLog:
    """Plain stand-in for the real (stubbed-out-as-MagicMock) `db.LlmCallLog` model — a bare
    MagicMock()'s attributes don't retain assigned values the way this repo's actual test
    conftest.py stubs `db` wholesale, so a real dataclass-shaped object is needed to assert on
    what log_llm_call() actually passed through, matching test_correlation_preentry.py's own
    "the stub doesn't behave like the real thing, so stand in a real object" reasoning."""
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


class _FakeSession:
    def __init__(self, store):
        self.store = store
        self.added = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def add(self, obj):
        self.added = obj
        self.store.append(obj)

    def commit(self):
        pass


class _RaisingSession:
    def __enter__(self):
        raise RuntimeError("db unreachable")

    def __exit__(self, *a):
        return False


def _patch_db(monkeypatch, session_factory):
    """log_llm_call() does `from db import LlmCallLog, SessionLocal` inside the function body.
    `db` is stubbed as a bare MagicMock() by this repo's own tests/conftest.py, so both names
    are patched here — LlmCallLog to a real, attribute-retaining stand-in, SessionLocal to the
    fake session factory under test."""
    import db as _db_module
    monkeypatch.setattr(_db_module, "SessionLocal", session_factory)
    monkeypatch.setattr(_db_module, "LlmCallLog", _FakeLlmCallLog)


def test_call_site_constants_are_all_distinct():
    slugs = [v for k, v in _namespace.items() if k.startswith("CALL_SITE_")]
    assert len(slugs) == len(set(slugs)), "two call sites must never share a slug"


def test_nine_real_call_sites_all_have_a_constant():
    """One per real Anthropic call site this session wired: research(x2), decision-engine(x2),
    market-data(x3), event-intelligence(x2), news-intelligence(x1) = 10 constants (2 of
    event-intelligence's earnings sites share the module but are distinct slugs)."""
    expected = {
        "CALL_SITE_RESEARCH_REPORT", "CALL_SITE_RESEARCH_CHAT",
        "CALL_SITE_DECIDE_LLM_SCORER", "CALL_SITE_DECIDE_RISK_AGENT",
        "CALL_SITE_NEWS_SENTIMENT", "CALL_SITE_MARKET_PULSE", "CALL_SITE_TRADE_COACH",
        "CALL_SITE_THEME_SIGNALS", "CALL_SITE_MACRO_REACTION",
        "CALL_SITE_EARNINGS_IMPACT", "CALL_SITE_EARNINGS_FORECAST",
        "CALL_SITE_NEWS_CLASSIFY",
    }
    assert expected.issubset(_namespace.keys())


def test_logs_a_successful_call_with_real_usage(monkeypatch):
    store: list = []
    _patch_db(monkeypatch, lambda: _FakeSession(store))
    log_llm_call(
        service="news-intelligence", call_site="news_classify", model="claude-haiku-4-5-20251001",
        usage={"input_tokens": 120, "output_tokens": 340}, duration_ms=850, status="ok",
        context={"headline_count": 8},
    )
    assert len(store) == 1
    row = store[0]
    assert row.service == "news-intelligence"
    assert row.call_site == "news_classify"
    assert row.input_tokens == 120
    assert row.output_tokens == 340
    assert row.status == "ok"


def test_missing_usage_stores_null_tokens_not_zero(monkeypatch):
    """A failed call (http_error/exception path) must store NULL token counts, not a
    fabricated 0 — a real zero-token success must never be confused with a failed call that
    never got a response body to read usage from."""
    store: list = []
    _patch_db(monkeypatch, lambda: _FakeSession(store))
    log_llm_call(
        service="decision-engine", call_site="decide_risk_agent", model="claude-haiku-4-5-20251001",
        usage=None, duration_ms=200, status="http_error", http_status=429,
    )
    row = store[0]
    assert row.input_tokens is None
    assert row.output_tokens is None
    assert row.http_status == 429


def test_error_message_is_truncated_to_2000_chars(monkeypatch):
    store: list = []
    _patch_db(monkeypatch, lambda: _FakeSession(store))
    long_error = "x" * 5000
    log_llm_call(
        service="research-engine", call_site="research_report", model="claude-sonnet-4-6",
        status="error", error=long_error,
    )
    assert len(store[0].error) == 2000


def test_a_db_failure_never_raises_out_of_log_llm_call(monkeypatch):
    """The whole point: a logging failure must never affect the caller's real LLM-call path.
    This must not raise, even though the session factory itself blows up."""
    _patch_db(monkeypatch, _RaisingSession)
    log_llm_call(
        service="market-data", call_site="trade_coach", model="claude-haiku-4-5-20251001",
        status="ok", usage={"input_tokens": 1, "output_tokens": 1},
    )  # must not raise


def test_context_dict_is_stored_verbatim(monkeypatch):
    store: list = []
    _patch_db(monkeypatch, lambda: _FakeSession(store))
    log_llm_call(
        service="research-engine", call_site="research_chat", model="claude-sonnet-4-6",
        status="ok", usage={"input_tokens": 5, "output_tokens": 5},
        context={"symbol": "AAPL"},
    )
    assert store[0].context == {"symbol": "AAPL"}
