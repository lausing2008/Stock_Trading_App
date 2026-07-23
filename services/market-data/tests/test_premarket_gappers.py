"""Tests for T257-OVERNIGHT-FLOW-BRIEF's premarket-gappers feature — _fetch_premarket_gappers()
in scheduler.py, plus the new _refresh_premarket_5m() ingest job wiring and the email section.

scheduler.py can't be imported directly in this test environment (conftest.py stubs sqlalchemy
itself as a MagicMock) — matches test_correlation_preentry.py's/test_broker_position_sync.py's
established technique exactly: pop the stub, build ONE shared in-memory engine + real models
while real sqlalchemy is active, then restore the stub immediately so later-collected test
files aren't affected. _fetch_premarket_gappers() is extracted from the real source via exec()
and run against this real session, so these tests exercise the actual query logic, not a
re-implementation.
"""
import sys

_STUBBED_MODULES = ("sqlalchemy", "sqlalchemy.orm", "sqlalchemy.dialects", "sqlalchemy.dialects.postgresql", "db")
_saved_stubs = {_mod: sys.modules.pop(_mod, None) for _mod in _STUBBED_MODULES}

import importlib.util
import pathlib
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from src.services.email_service import send_premarket_brief_email

_models_path = pathlib.Path(__file__).resolve().parents[3] / "shared" / "db" / "models.py"
_spec = importlib.util.spec_from_file_location("db_models_under_test_pmg", _models_path)
_models = importlib.util.module_from_spec(_spec)
sys.modules["db_models_under_test_pmg"] = _models
_spec.loader.exec_module(_models)

_ENGINE = create_engine("sqlite:///:memory:")
_models.Base.metadata.create_all(
    _ENGINE, tables=[_models.Stock.__table__, _models.Price.__table__],
)

for _mod, _stub in _saved_stubs.items():
    if _stub is not None:
        sys.modules[_mod] = _stub
    else:
        sys.modules.pop(_mod, None)

Stock = _models.Stock
Price = _models.Price
TimeFrame = _models.TimeFrame
Market = _models.Market
Exchange = _models.Exchange

_SCHEDULER_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
_SCHEDULER_SOURCE = _SCHEDULER_PATH.read_text()


class _FakeRedis:
    def __init__(self, cached=None):
        self._cached = cached
        self.writes: dict[str, tuple[int, str]] = {}

    def get(self, key):
        return self._cached

    def setex(self, key, ttl, value):
        self.writes[key] = (ttl, value)


def _extract_fetch_premarket_gappers():
    start = _SCHEDULER_SOURCE.index("_PREMARKET_GAPPERS_CACHE_KEY = ")
    end = _SCHEDULER_SOURCE.index("\ndef send_premarket_brief(")
    func_source = _SCHEDULER_SOURCE[start:end]
    namespace = {
        "select": select,
        "func": func,
        "Price": Price,
        "Stock": Stock,
        "TimeFrame": TimeFrame,
        "Market": Market,
        "Session": Session,
        "json": __import__("json"),
    }
    exec(func_source, namespace)  # noqa: S102 — isolated eval of real source, matching repo convention
    return namespace["_fetch_premarket_gappers"], namespace["_PREMARKET_GAPPERS_CACHE_KEY"]


_fetch_premarket_gappers, _PREMARKET_GAPPERS_CACHE_KEY = _extract_fetch_premarket_gappers()


def _make_session():
    session = Session(_ENGINE)
    session.execute(Price.__table__.delete())
    session.execute(Stock.__table__.delete())
    session.commit()
    return session


def _make_stock(session, stock_id, symbol, market=Market.US):
    stock = Stock(id=stock_id, symbol=symbol, market=market, exchange=Exchange.NASDAQ, name=symbol)
    session.add(stock)
    session.commit()
    return stock


_next_price_id = [1]


def _add_price(session, stock_id, timeframe, session_tag, close, ts):
    session.add(Price(
        id=_next_price_id[0], stock_id=stock_id, ts=ts, timeframe=timeframe,
        open=close, high=close, low=close, close=close, volume=1000, session=session_tag,
    ))
    _next_price_id[0] += 1
    session.commit()


def _run_with_fake_redis(session, redis_obj):
    """_fetch_premarket_gappers() does `_get_redis()` — inject a fake directly into the
    exec()'d function's own globals dict, matching test_overnight_futures_brief.py's
    established technique for this exact import-constraint class."""
    _fetch_premarket_gappers.__globals__["_get_redis"] = lambda: redis_obj
    return _fetch_premarket_gappers(session)


# ── _fetch_premarket_gappers() — real source, real in-memory DB ──────────────────────────────

def test_computes_gap_pct_from_prior_daily_close_and_latest_pre_bar():
    session = _make_session()
    _make_stock(session, 1, "GAPUP")
    now = datetime.now(timezone.utc)
    _add_price(session, 1, TimeFrame.D1, "REGULAR", 100.0, now - timedelta(days=1))
    _add_price(session, 1, TimeFrame.M5, "PRE", 108.0, now)
    results = _run_with_fake_redis(session, _FakeRedis())
    assert len(results) == 1
    assert results[0]["symbol"] == "GAPUP"
    assert results[0]["change_pct"] == 8.0
    session.close()


def test_ranks_by_absolute_change_pct_descending():
    session = _make_session()
    now = datetime.now(timezone.utc)
    _make_stock(session, 1, "SMALLGAP")
    _add_price(session, 1, TimeFrame.D1, "REGULAR", 100.0, now - timedelta(days=1))
    _add_price(session, 1, TimeFrame.M5, "PRE", 102.0, now)  # +2%
    _make_stock(session, 2, "BIGDROP")
    _add_price(session, 2, TimeFrame.D1, "REGULAR", 100.0, now - timedelta(days=1))
    _add_price(session, 2, TimeFrame.M5, "PRE", 85.0, now)  # -15%
    results = _run_with_fake_redis(session, _FakeRedis())
    assert [r["symbol"] for r in results] == ["BIGDROP", "SMALLGAP"]


def test_caps_results_at_top_n():
    session = _make_session()
    now = datetime.now(timezone.utc)
    for i in range(15):
        _make_stock(session, i + 1, f"SYM{i}")
        _add_price(session, i + 1, TimeFrame.D1, "REGULAR", 100.0, now - timedelta(days=1))
        _add_price(session, i + 1, TimeFrame.M5, "PRE", 100.0 + i, now)  # increasing gap
    results = _run_with_fake_redis(session, _FakeRedis())
    assert len(results) == 10  # _PREMARKET_GAPPERS_TOP_N


def test_only_includes_us_market_stocks():
    session = _make_session()
    now = datetime.now(timezone.utc)
    _make_stock(session, 1, "USSTOCK", market=Market.US)
    _add_price(session, 1, TimeFrame.D1, "REGULAR", 100.0, now - timedelta(days=1))
    _add_price(session, 1, TimeFrame.M5, "PRE", 110.0, now)
    _make_stock(session, 2, "0001.HK", market=Market.HK)
    _add_price(session, 2, TimeFrame.D1, "REGULAR", 100.0, now - timedelta(days=1))
    _add_price(session, 2, TimeFrame.M5, "PRE", 110.0, now)
    results = _run_with_fake_redis(session, _FakeRedis())
    assert [r["symbol"] for r in results] == ["USSTOCK"]


def test_stock_with_no_pre_session_bar_is_excluded():
    """A stock with only REGULAR daily bars (no premarket ingest job has run for it yet, or
    it genuinely had zero premarket trading) must not appear at all — not with a fabricated
    0% gap."""
    session = _make_session()
    now = datetime.now(timezone.utc)
    _make_stock(session, 1, "NOPRE")
    _add_price(session, 1, TimeFrame.D1, "REGULAR", 100.0, now - timedelta(days=1))
    results = _run_with_fake_redis(session, _FakeRedis())
    assert results == []


def test_regular_session_5m_bars_are_not_mistaken_for_premarket():
    """A regular-hours 5m bar (session="REGULAR") must not be picked up as if it were a PRE
    bar — the query filters on Price.session == "PRE" explicitly."""
    session = _make_session()
    now = datetime.now(timezone.utc)
    _make_stock(session, 1, "REGULARONLY")
    _add_price(session, 1, TimeFrame.D1, "REGULAR", 100.0, now - timedelta(days=1))
    _add_price(session, 1, TimeFrame.M5, "REGULAR", 150.0, now)  # a huge move, but NOT premarket
    results = _run_with_fake_redis(session, _FakeRedis())
    assert results == []


def test_returns_empty_list_when_cache_hit():
    session = _make_session()  # empty DB — would return [] anyway, but confirms no query runs
    cached_redis = _FakeRedis(cached='[{"symbol": "CACHED", "pre_close": 1.0, "prior_close": 1.0, "change_pct": 0.0, "as_of": null}]')
    results = _run_with_fake_redis(session, cached_redis)
    assert results == [{"symbol": "CACHED", "pre_close": 1.0, "prior_close": 1.0, "change_pct": 0.0, "as_of": None}]


def test_caches_the_result_after_a_real_fetch():
    session = _make_session()
    now = datetime.now(timezone.utc)
    _make_stock(session, 1, "CACHEME")
    _add_price(session, 1, TimeFrame.D1, "REGULAR", 100.0, now - timedelta(days=1))
    _add_price(session, 1, TimeFrame.M5, "PRE", 105.0, now)
    fresh_redis = _FakeRedis()
    _run_with_fake_redis(session, fresh_redis)
    assert _PREMARKET_GAPPERS_CACHE_KEY in fresh_redis.writes
    ttl, value = fresh_redis.writes[_PREMARKET_GAPPERS_CACHE_KEY]
    assert ttl == 300
    assert "CACHEME" in value


# ── send_premarket_brief() / _refresh_premarket_5m() wiring — source-text regression checks ──

def _premarket_brief_body() -> str:
    start = _SCHEDULER_SOURCE.index("def send_premarket_brief(")
    end = _SCHEDULER_SOURCE.index("\ndef ", start + 1)
    return _SCHEDULER_SOURCE[start:end]


def test_premarket_5m_job_is_registered_as_scheduled_jobs():
    assert 'id="us_premarket_5m_early"' in _SCHEDULER_SOURCE
    assert 'id="us_premarket_5m_9am"' in _SCHEDULER_SOURCE
    assert "_refresh_premarket_5m" in _SCHEDULER_SOURCE


def test_premarket_5m_early_window_stops_before_9am_hour():
    """The early-window job's own hour list must not include 9 — the 9am-hour job is a
    SEPARATE registration specifically so it can stop at :25 instead of :55, handing off
    cleanly to us_5m_intraday's own 9:30 start without a double-fire at 9:30."""
    start = _SCHEDULER_SOURCE.index('id="us_premarket_5m_early"')
    preceding = _SCHEDULER_SOURCE[max(0, start - 400):start]
    hour_line = next(line for line in preceding.splitlines() if "hour=" in line)
    assert '"4,5,6,7,8"' in hour_line


def test_premarket_5m_9am_window_stops_at_25_not_55():
    start = _SCHEDULER_SOURCE.index('id="us_premarket_5m_9am"')
    preceding = _SCHEDULER_SOURCE[max(0, start - 400):start]
    minute_line = next(line for line in preceding.splitlines() if "minute=" in line)
    assert '"0,5,10,15,20,25"' in minute_line
    assert "30" not in minute_line


def test_refresh_premarket_5m_does_not_call_paper_trading_step():
    """Deliberately does NOT reuse _refresh_5m() as-is — that function unconditionally runs
    _run_paper_trading_step()/_check_short_intraday_triggers() after every ingest, which are
    designed around regular-hours trading logic. The premarket ingest must be ingest-only.

    Checks only the function's real code (past its own closing docstring delimiter), not the
    docstring itself — the docstring legitimately mentions both function names in prose while
    explaining why they're deliberately NOT called, which would otherwise be a false positive.
    """
    start = _SCHEDULER_SOURCE.index("def _refresh_premarket_5m(")
    end = _SCHEDULER_SOURCE.index("\ndef _is_token_rejected_error(", start)
    full_body = _SCHEDULER_SOURCE[start:end]
    docstring_end = full_body.index('"""', full_body.index('"""') + 3) + 3
    code_only = full_body[docstring_end:]
    assert "_run_paper_trading_step" not in code_only
    assert "_check_short_intraday_triggers" not in code_only
    assert 'ingest_universe(symbols, "5m")' in code_only


def test_premarket_brief_gates_gappers_fetch_to_us_only():
    body = _premarket_brief_body()
    assert "_fetch_premarket_gappers(session)" in body
    fetch_idx = body.index("_fetch_premarket_gappers(session)")
    preceding = body[:fetch_idx]
    last_us_gate = preceding.rindex('if "US" in markets:')
    between = body[last_us_gate:fetch_idx]
    assert between.count("\n") <= 2


def test_premarket_brief_passes_movers_into_the_email_and_the_done_log():
    body = _premarket_brief_body()
    assert "premarket_movers=premarket_movers" in body
    done_log_idx = body.index('log.info("premarket_brief.done"')
    done_log_line = body[done_log_idx:body.index("\n", done_log_idx + 300)]
    assert "premarket_movers=len(premarket_movers)" in done_log_line


def test_premarket_brief_nothing_to_report_guard_includes_movers():
    body = _premarket_brief_body()
    guard_idx = body.index('log.info("premarket_brief.nothing_to_report"')
    guard_line_start = body.rindex("if not macro_today", 0, guard_idx)
    guard_line = body[guard_line_start:guard_idx]
    assert "not premarket_movers" in guard_line


# ── send_premarket_brief_email()'s new premarket-movers section — pure composition ────────────

def _capture_send():
    calls = []
    def _fake_send(to, subject, body_html, body_text):
        calls.append({"to": to, "subject": subject, "html": body_html, "text": body_text})
        return True
    return calls, _fake_send


def test_premarket_movers_section_renders_symbol_price_and_change():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_premarket_brief_email(
            to="user@example.com", date_str="d", market="US",
            macro_events=[], my_earnings=[], recent_reactions=[],
            premarket_movers=[{"symbol": "GAPUP", "pre_close": 108.5, "prior_close": 100.0, "change_pct": 8.5, "as_of": None}],
        )
    html, text = calls[0]["html"], calls[0]["text"]
    assert "GAPUP" in html
    assert "108.50" in html
    assert "+8.50%" in html
    assert "GAPUP: 108.50 (+8.50% vs. yesterday's close)" in text


def test_premarket_movers_section_has_explicit_empty_state():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_premarket_brief_email(
            to="user@example.com", date_str="d", market="US",
            macro_events=[], my_earnings=[], recent_reactions=[], premarket_movers=[],
        )
    html, text = calls[0]["html"], calls[0]["text"]
    assert "No significant premarket movers detected." in html
    assert "None detected." in text


def test_premarket_movers_param_defaults_to_none_and_is_treated_as_empty():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        ok = send_premarket_brief_email(
            to="user@example.com", date_str="d", market="US",
            macro_events=[], my_earnings=[], recent_reactions=[],
        )
    assert ok is True
    assert "No significant premarket movers detected." in calls[0]["html"]


def test_negative_gap_renders_red_positive_gap_renders_green():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_premarket_brief_email(
            to="a@example.com", date_str="d", market="US",
            macro_events=[], my_earnings=[], recent_reactions=[],
            premarket_movers=[{"symbol": "DOWN", "pre_close": 90.0, "prior_close": 100.0, "change_pct": -10.0, "as_of": None}],
        )
    down_html = calls[0]["html"]
    calls2, fake2 = _capture_send()
    with patch("src.services.email_service.send_email", fake2):
        send_premarket_brief_email(
            to="a@example.com", date_str="d", market="US",
            macro_events=[], my_earnings=[], recent_reactions=[],
            premarket_movers=[{"symbol": "UP", "pre_close": 110.0, "prior_close": 100.0, "change_pct": 10.0, "as_of": None}],
        )
    up_html = calls2[0]["html"]
    assert "#dc2626" in down_html
    assert "#16a34a" in up_html
    assert "#dc2626" not in up_html
