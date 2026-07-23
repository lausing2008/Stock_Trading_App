"""Tests for T257-OVERNIGHT-FLOW-BRIEF Phase 1's overnight futures section.

scheduler.py can't be imported directly in this test environment (its import chain pulls in
apscheduler/ingestion.py/paper_trading_engine.py, none of which are stubbed — see
test_price_alert_price_check.py's docstring for the established reasoning). _fetch_overnight_
futures()'s real source is extracted and exec()'d with a fake `yf`/redis injected, matching
test_backfill_realized_ev.py's/test_tune_strategy.py's established source-text-extraction
technique — this exercises the ACTUAL function under test, not a hand-copied duplicate.

send_premarket_brief_email()'s new overnight-futures section is pure string composition (no
DB/network), so it's tested directly like every other section in test_premarket_brief.py.
"""
import pathlib
from unittest.mock import patch

import pandas as pd

from src.services.email_service import send_premarket_brief_email

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


def _make_multi_ticker_df(closes: dict[str, list[float]]) -> pd.DataFrame:
    """Builds a yf.download(group_by="ticker")-shaped multi-index DataFrame — columns are
    (ticker, price_type) tuples, matching what _fetch_live_bulk()'s own tests would expect."""
    frames = {}
    for ticker, series in closes.items():
        frames[(ticker, "Close")] = pd.Series(series)
        frames[(ticker, "Volume")] = pd.Series([0] * len(series))
    df = pd.DataFrame(frames)
    df.columns = pd.MultiIndex.from_tuples(df.columns)
    return df


def _extract_fetch_overnight_futures():
    start = _SCHEDULER_SOURCE.index("_FUTURES = [")
    end = _SCHEDULER_SOURCE.index("\ndef send_premarket_brief(")
    func_source = _SCHEDULER_SOURCE[start:end]

    class _FakeYfModule:
        def __init__(self, df):
            self._df = df

        def download(self, symbols, **kwargs):
            return self._df

    namespace = {
        "json": __import__("json"),
        "log": type("L", (), {"warning": staticmethod(lambda *a, **k: None)})(),
    }
    exec(func_source, namespace)  # noqa: S102 — isolated eval of real source, matching repo convention
    return namespace["_fetch_overnight_futures"], namespace["_FUTURES_CACHE_KEY"]


_fetch_overnight_futures, _FUTURES_CACHE_KEY = _extract_fetch_overnight_futures()


def _run_with_fake_yf(df, redis_obj):
    """_fetch_overnight_futures() does `import yfinance as yf` locally inside its own body —
    patch sys.modules so that local import resolves to our fake instead of the real yfinance
    package (which would otherwise attempt a real network call in these tests). Also injects
    a fake `_get_redis` directly into the exec()'d function's own globals dict, since the
    extraction namespace never defined one."""
    import sys
    fake_yf = type("FakeYf", (), {"download": staticmethod(lambda *a, **k: df)})()
    _fetch_overnight_futures.__globals__["_get_redis"] = lambda: redis_obj
    with patch.dict(sys.modules, {"yfinance": fake_yf}):
        return _fetch_overnight_futures()


# ── _fetch_overnight_futures() — real source, fake yfinance/redis ────────────────────────────

def test_computes_change_pct_from_prior_and_current_close():
    df = _make_multi_ticker_df({
        "ES=F": [100.0, 100.0, 108.0],
        "NQ=F": [200.0, 200.0, 200.0],
        "YM=F": [50.0, 50.0, 50.0],
        "RTY=F": [30.0, 30.0, 30.0],
    })
    results = _run_with_fake_yf(df, _FakeRedis())
    es = next(r for r in results if r["ticker"] == "ES=F")
    assert es["price"] == 108.0
    assert es["change_pct"] == 8.0


def test_returns_empty_list_when_cache_hit():
    """A warm Redis cache must short-circuit before any yf.download() call at all."""
    df = _make_multi_ticker_df({"ES=F": [], "NQ=F": [], "YM=F": [], "RTY=F": []})
    cached_redis = _FakeRedis(cached='[{"name": "cached", "ticker": "ES=F", "price": 1.0, "change_pct": 0.0}]')
    results = _run_with_fake_yf(df, cached_redis)
    assert results == [{"name": "cached", "ticker": "ES=F", "price": 1.0, "change_pct": 0.0}]


def test_caches_the_result_after_a_real_fetch():
    df = _make_multi_ticker_df({
        "ES=F": [100.0, 105.0], "NQ=F": [200.0, 210.0], "YM=F": [50.0, 49.0], "RTY=F": [30.0, 31.0],
    })
    fresh_redis = _FakeRedis()
    _run_with_fake_yf(df, fresh_redis)
    assert _FUTURES_CACHE_KEY in fresh_redis.writes
    ttl, value = fresh_redis.writes[_FUTURES_CACHE_KEY]
    assert ttl == 60
    assert "ES=F" in value


def test_skips_a_ticker_with_fewer_than_two_valid_closes():
    """A ticker with only 0 or 1 valid daily closes (e.g. a data gap) must be silently
    excluded, not crash the whole fetch or report a fabricated change_pct."""
    df = _make_multi_ticker_df({
        "ES=F": [100.0],  # only 1 close — can't compute change_pct
        "NQ=F": [200.0, 205.0],
        "YM=F": [50.0, 49.0],
        "RTY=F": [30.0, 31.0],
    })
    results = _run_with_fake_yf(df, _FakeRedis())
    tickers = {r["ticker"] for r in results}
    assert "ES=F" not in tickers
    assert "NQ=F" in tickers


def test_returns_empty_list_on_download_failure():
    class _RaisingYf:
        @staticmethod
        def download(*a, **k):
            raise RuntimeError("network error")
    import sys
    _fetch_overnight_futures.__globals__["_get_redis"] = lambda: _FakeRedis()
    with patch.dict(sys.modules, {"yfinance": _RaisingYf()}):
        results = _fetch_overnight_futures()
    assert results == []


# ── send_premarket_brief_email()'s new overnight-futures section — pure composition ──────────

def _capture_send():
    calls = []
    def _fake_send(to, subject, body_html, body_text):
        calls.append({"to": to, "subject": subject, "html": body_html, "text": body_text})
        return True
    return calls, _fake_send


def test_overnight_futures_section_renders_name_price_and_change():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_premarket_brief_email(
            to="user@example.com", date_str="d", market="US",
            macro_events=[], my_earnings=[], recent_reactions=[],
            overnight_futures=[{"name": "E-mini S&P 500", "ticker": "ES=F", "price": 5123.25, "change_pct": 0.82}],
        )
    html, text = calls[0]["html"], calls[0]["text"]
    assert "E-mini S&P 500" in html
    assert "5,123.25" in html
    assert "+0.82%" in html
    assert "E-mini S&P 500: 5,123.25 (+0.82%)" in text


def test_overnight_futures_section_has_explicit_empty_state():
    """Matches this file's established convention (every section must show an explicit
    empty-state note, never a blank/omitted section) — an empty list, not None, is the
    default shape send_premarket_brief() always passes."""
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_premarket_brief_email(
            to="user@example.com", date_str="d", market="US",
            macro_events=[], my_earnings=[], recent_reactions=[], overnight_futures=[],
        )
    html, text = calls[0]["html"], calls[0]["text"]
    assert "Overnight futures data unavailable this morning." in html
    assert "Unavailable this morning." in text


def test_overnight_futures_param_defaults_to_none_and_is_treated_as_empty():
    """Backward compatibility: an older caller not passing overnight_futures at all must not
    crash — None must degrade to the same empty-state rendering as an explicit []."""
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        ok = send_premarket_brief_email(
            to="user@example.com", date_str="d", market="US",
            macro_events=[], my_earnings=[], recent_reactions=[],
        )
    assert ok is True
    assert "Overnight futures data unavailable this morning." in calls[0]["html"]


def test_negative_change_pct_renders_red_positive_renders_green():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_premarket_brief_email(
            to="a@example.com", date_str="d", market="US",
            macro_events=[], my_earnings=[], recent_reactions=[],
            overnight_futures=[{"name": "Down One", "ticker": "X=F", "price": 1.0, "change_pct": -1.5}],
        )
    down_html = calls[0]["html"]
    calls2, fake2 = _capture_send()
    with patch("src.services.email_service.send_email", fake2):
        send_premarket_brief_email(
            to="a@example.com", date_str="d", market="US",
            macro_events=[], my_earnings=[], recent_reactions=[],
            overnight_futures=[{"name": "Up One", "ticker": "Y=F", "price": 1.0, "change_pct": 1.5}],
        )
    up_html = calls2[0]["html"]
    assert "#dc2626" in down_html  # red
    assert "#16a34a" in up_html    # green
    assert "#dc2626" not in up_html


def test_missing_change_pct_renders_em_dash_not_none_or_crash():
    """The literal string 'None' must not leak into the futures row itself — the page as a
    whole legitimately contains the word "None" elsewhere (e.g. the earnings section's own
    empty-state note, "None of your watched symbols..."), so this scopes the check to just
    the futures row rather than asserting on the whole page."""
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_premarket_brief_email(
            to="a@example.com", date_str="d", market="US",
            macro_events=[], my_earnings=[], recent_reactions=[],
            overnight_futures=[{"name": "No Data", "ticker": "Z=F", "price": None, "change_pct": None}],
        )
    html = calls[0]["html"]
    row_start = html.index("No Data")
    row_end = html.index("</div></div>", row_start)
    futures_row = html[row_start:row_end]
    assert "None" not in futures_row
    assert "—" in futures_row


# ── send_premarket_brief() wiring — source-text regression checks ────────────────────────────

def _premarket_brief_body() -> str:
    start = _SCHEDULER_SOURCE.index("def send_premarket_brief(")
    end = _SCHEDULER_SOURCE.index("\ndef ", start + 1)
    return _SCHEDULER_SOURCE[start:end]


def test_premarket_brief_gates_futures_fetch_to_us_only():
    body = _premarket_brief_body()
    # the actual call site — `body.index` alone would find the docstring's own prose mention
    # of _fetch_overnight_futures() first, so anchor on the assignment form instead
    fetch_idx = body.index("overnight_futures = _fetch_overnight_futures()")
    # confirm the fetch call sits inside an `if "US" in markets:` guard, not unconditional
    preceding = body[:fetch_idx]
    last_us_gate = preceding.rindex('if "US" in markets:')
    # nothing else should sit between the gate and the fetch call except the assignment line
    between = body[last_us_gate:fetch_idx]
    assert between.count("\n") <= 2


def test_premarket_brief_passes_futures_into_the_email_and_the_done_log():
    body = _premarket_brief_body()
    assert "overnight_futures=overnight_futures" in body
    done_log_idx = body.index('log.info("premarket_brief.done"')
    done_log_line = body[done_log_idx:body.index("\n", done_log_idx + 200)]
    assert "futures=len(overnight_futures)" in done_log_line


def test_premarket_brief_nothing_to_report_guard_includes_futures():
    """A brief with no macro/earnings/reactions but real futures data must still send —
    the early-return guard must check `not overnight_futures` too, not just the other 3."""
    body = _premarket_brief_body()
    guard_idx = body.index('log.info("premarket_brief.nothing_to_report"')
    guard_line_start = body.rindex("if not macro_today", 0, guard_idx)
    guard_line = body[guard_line_start:guard_idx]
    assert "not overnight_futures" in guard_line
