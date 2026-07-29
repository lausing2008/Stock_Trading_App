"""Tests for research report DB persistence — fixes reports vanishing on every research-engine
restart.

Root cause found live 2026-07-28/29: routes.py's `_cache` was a plain in-memory Python dict
with ZERO database persistence. Every generated report (manual or auto-triggered) was lost
completely the moment the container restarted for any reason (a routine fix deploy, a crash,
an EC2 reboot) — confirmed live: RXT/SMTC/MU/UNH all had real reports generated earlier in the
day, then all four returned a real 404 from research-engine directly after this container was
restarted to deploy an unrelated fix, with the frontend silently falling back to "Generate
Report" with no indication a report had ever existed.

Fix: a new ResearchReportCache DB table (one row per symbol, upserted on every generation),
with generate_research() writing through on every save and every read site (GET /{symbol},
/summary, /batch, /chat, trigger_research's cooldown check, generate_research's own fast-path
check) falling back to the DB via _get_cached_report() when the in-memory _cache misses.
clear_research() (the "Regenerate" button) also deletes the DB row, not just the in-memory
entry — otherwise Regenerate would still serve the stale row straight back out via the DB
fallback, defeating the button's whole purpose.

`db` is stubbed as a bare MagicMock() in conftest.py (Docker-only dependency) — _report_ttl()
is pure and DB-independent, tested directly with a real import. The DB-touching functions
(_db_save_report/_db_load_report/_db_clear_report/_get_cached_report) are tested via direct
monkeypatching of the DB helpers themselves (mocking the DB boundary, not the SQL), plus
source-text regression checks confirming every read/write site is actually wired to the new
persistence layer, matching this repo's established technique for functions with heavy
DB/session dependencies that would need a disproportionately large fixture harness.
"""
import pathlib
from datetime import datetime, timezone

from src.api.routes import _report_ttl, _get_cached_report, _cache

_routes_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "routes.py"
_routes_source = _routes_path.read_text()


# ── _report_ttl() — pure function, real import ──────────────────────────────

def test_report_ttl_full_quality():
    assert _report_ttl({"report_quality": "full"}) == 86_400


def test_report_ttl_partial_quality():
    assert _report_ttl({"report_quality": "partial"}) == 1_800


def test_report_ttl_fallback_quality():
    assert _report_ttl({"report_quality": "fallback"}) == 300


def test_report_ttl_missing_quality_key_defaults_to_full():
    assert _report_ttl({}) == 86_400


# ── _get_cached_report() — in-memory first, DB fallback second ─────────────

def test_in_memory_hit_never_touches_the_db(monkeypatch):
    """The fast path: if _cache already has the symbol, _db_load_report must not even be
    called — no DB round-trip on the common case."""
    sym = "TESTMEM"
    _cache[sym] = ({"report_quality": "full"}, datetime.now(timezone.utc))
    called = {"n": 0}

    def _fail_if_called(_sym):
        called["n"] += 1
        raise AssertionError("should not reach the DB when _cache already has the symbol")

    monkeypatch.setattr("src.api.routes._db_load_report", _fail_if_called)
    try:
        entry = _get_cached_report(sym)
        assert entry is not None
        assert called["n"] == 0
    finally:
        _cache.pop(sym, None)


def test_in_memory_miss_falls_back_to_db(monkeypatch):
    """The exact fix: a symbol NOT in _cache (e.g. right after a restart) must still be
    returned if the DB has a durable row for it."""
    sym = "TESTDBFALLBACK"
    _cache.pop(sym, None)
    fake_report = {"report_quality": "full", "symbol": sym}
    fake_ts = datetime.now(timezone.utc)

    monkeypatch.setattr("src.api.routes._db_load_report", lambda s: (fake_report, fake_ts) if s == sym else None)
    try:
        entry = _get_cached_report(sym)
        assert entry is not None
        assert entry[0] == fake_report
    finally:
        _cache.pop(sym, None)


def test_db_fallback_hit_is_written_back_into_memory_cache(monkeypatch):
    """A DB hit should populate _cache so a SECOND request in the same process doesn't hit
    the DB again — confirms the write-back, not just the read."""
    sym = "TESTWRITEBACK"
    _cache.pop(sym, None)
    fake_report = {"report_quality": "full"}
    fake_ts = datetime.now(timezone.utc)
    monkeypatch.setattr("src.api.routes._db_load_report", lambda s: (fake_report, fake_ts))
    try:
        _get_cached_report(sym)
        assert sym in _cache
        assert _cache[sym][0] == fake_report
    finally:
        _cache.pop(sym, None)


def test_neither_cache_nor_db_returns_none(monkeypatch):
    sym = "TESTNEITHER"
    _cache.pop(sym, None)
    monkeypatch.setattr("src.api.routes._db_load_report", lambda s: None)
    assert _get_cached_report(sym) is None


# ── Source-text regression checks: every read/write site is wired ─────────

def _get_research_body() -> str:
    start = _routes_source.index("async def get_research(symbol")
    end = _routes_source.index("\n@router.delete", start)
    return _routes_source[start:end]


def _generate_research_body() -> str:
    start = _routes_source.index("async def generate_research(")
    end = _routes_source.index("\n\n\n# ── Chat endpoint", start)
    return _routes_source[start:end]


def test_get_research_uses_the_db_fallback():
    assert "_get_cached_report(sym)" in _get_research_body()


def test_get_research_summary_uses_the_db_fallback():
    start = _routes_source.index("async def get_research_summary(")
    end = _routes_source.index("\n\n\n@router.get(\"/{symbol}\")", start)
    body = _routes_source[start:end]
    assert "_get_cached_report(sym)" in body


def test_get_research_batch_uses_the_db_fallback():
    start = _routes_source.index("async def get_research_batch(")
    end = _routes_source.index("\n\n\n@router.get(\"/{symbol}/summary\")", start)
    body = _routes_source[start:end]
    assert "_get_cached_report(sym)" in body


def test_trigger_research_cooldown_uses_the_db_fallback():
    start = _routes_source.index("async def trigger_research(")
    end = _routes_source.index("\n\n\nasync def _generate_with_service_token", start)
    body = _routes_source[start:end]
    assert "_get_cached_report(sym)" in body


def test_chat_research_uses_the_db_fallback():
    start = _routes_source.index("async def chat_research(")
    end = _routes_source.index("\n\n\n@router.get", start) if "\n\n\n@router.get" in _routes_source[start:] else len(_routes_source)
    body = _routes_source[start:start + 500]
    assert "_get_cached_report(sym)" in body


def test_generate_research_fast_path_uses_the_db_fallback():
    body = _generate_research_body()
    assert "_get_cached_report(sym)" in body
    # the fast path specifically — must appear before the in-flight dedup section
    fast_path_idx = body.index("_get_cached_report(sym)")
    inflight_idx = body.index("_inflight_research")
    assert fast_path_idx < inflight_idx


def test_generate_research_writes_through_to_the_db():
    """The exact fix for durability: every real generation must persist to the DB, not just
    the in-memory _cache — otherwise nothing survives a restart at all."""
    body = _generate_research_body()
    cache_write_idx = body.index('_cache[sym] = (report, datetime.now(timezone.utc))')
    db_write_idx = body.index("_db_save_report(sym, report, req)")
    assert cache_write_idx < db_write_idx  # DB write happens right after the in-memory write


def test_clear_research_also_clears_the_db_row():
    """The exact regression this guards against: without this, clicking Regenerate would
    still serve the stale row straight back out of the DB via _get_cached_report()'s own
    fallback the very next read, defeating the button's whole purpose."""
    start = _routes_source.index("async def clear_research(")
    end = _routes_source.index("\n\n\n@router.post(\"/{symbol}/trigger\"", start)
    body = _routes_source[start:end]
    assert "_db_clear_report(sym)" in body


def test_db_save_report_is_best_effort_never_raises_on_failure():
    """A DB write failure must never break the actual report generation the caller is
    waiting on — _db_save_report's own try/except must swallow the exception, not propagate."""
    start = _routes_source.index("def _db_save_report(")
    end = _routes_source.index("\n\ndef _db_load_report(", start)
    body = _routes_source[start:end]
    assert "except Exception" in body
    # the except branch must not re-raise
    except_idx = body.index("except Exception")
    tail = body[except_idx:]
    assert "raise" not in tail


def test_db_load_report_is_fail_open_on_failure():
    start = _routes_source.index("def _db_load_report(")
    end = _routes_source.index("\n\ndef _get_cached_report(", start)
    body = _routes_source[start:end]
    assert "except Exception" in body
    except_idx = body.index("except Exception")
    tail = body[except_idx:]
    assert "return None" in tail
