"""Tests for T257-OVERNIGHT-FLOW-BRIEF Phase 2's late-day options-flow section on the
pre-market brief.

send_premarket_brief_email()'s new options_flow section is pure string composition (no DB/
network), so it's tested directly like every other section in test_premarket_brief.py.
send_premarket_brief()/compute_options_flow_snapshots_eod()/_bounded_options_flow_symbols() in
scheduler.py can't be imported directly in this test environment (its import chain pulls in
apscheduler/ingestion.py/paper_trading_engine.py — see test_premarket_brief.py's docstring for
the established reasoning) — those are covered via source-text regression checks, matching
test_premarket_gappers.py's established pattern for this exact class of function.
"""
import pathlib
from unittest.mock import patch

from src.services.email_service import send_premarket_brief_email

_SCHEDULER_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
_SCHEDULER_SOURCE = _SCHEDULER_PATH.read_text()


def _premarket_brief_body() -> str:
    start = _SCHEDULER_SOURCE.index("def send_premarket_brief(")
    end = _SCHEDULER_SOURCE.index("\ndef ", start + 1)
    return _SCHEDULER_SOURCE[start:end]


def _capture_send():
    calls = []
    def _fake_send(to, subject, body_html, body_text):
        calls.append({"to": to, "subject": subject, "html": body_html, "text": body_text})
        return True
    return calls, _fake_send


# ── send_premarket_brief_email()'s new options-flow section — pure composition ──────────────

def test_options_flow_section_renders_symbol_cp_ratio_and_sentiment():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_premarket_brief_email(
            to="user@example.com", date_str="d", market="US",
            macro_events=[], my_earnings=[], recent_reactions=[],
            options_flow=[{"symbol": "AAPL", "cp_ratio": 3.5, "sentiment": "strongly_bullish",
                            "call_premium": 500000.0, "put_premium": 100000.0,
                            "whale_count": 2, "top_whale_premium": 750000.0}],
        )
    html, text = calls[0]["html"], calls[0]["text"]
    assert "AAPL" in html
    assert "3.50" in html
    assert "strongly bullish" in html
    assert "2 whale trades" in html
    assert "$750,000" in html
    assert "AAPL: cp_ratio 3.50, strongly bullish" in text


def test_options_flow_section_has_explicit_empty_state():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_premarket_brief_email(
            to="user@example.com", date_str="d", market="US",
            macro_events=[], my_earnings=[], recent_reactions=[], options_flow=[],
        )
    html, text = calls[0]["html"], calls[0]["text"]
    assert "No notable options flow detected in yesterday's session." in html
    assert "None detected in yesterday's session." in text


def test_options_flow_param_defaults_to_none_and_is_treated_as_empty():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        ok = send_premarket_brief_email(
            to="user@example.com", date_str="d", market="US",
            macro_events=[], my_earnings=[], recent_reactions=[],
        )
    assert ok is True
    html = calls[0]["html"]
    assert "No notable options flow detected in yesterday's session." in html


def test_options_flow_missing_whale_count_shows_no_whale_note_not_a_crash():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_premarket_brief_email(
            to="user@example.com", date_str="d", market="US",
            macro_events=[], my_earnings=[], recent_reactions=[],
            options_flow=[{"symbol": "MSFT", "cp_ratio": 1.1, "sentiment": "neutral"}],
        )
    html = calls[0]["html"]
    assert "MSFT" in html
    assert "whale" not in html


def test_options_flow_missing_cp_ratio_renders_em_dash_not_none_or_crash():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_premarket_brief_email(
            to="user@example.com", date_str="d", market="US",
            macro_events=[], my_earnings=[], recent_reactions=[],
            options_flow=[{"symbol": "NVDA", "cp_ratio": None, "sentiment": None}],
        )
    html = calls[0]["html"]
    assert "cp_ratio —" in html
    assert "neutral" in html  # sentiment=None must degrade to "neutral", matching the
    # underlying compute_options_flow()'s own default when a data point is genuinely unknown


# ── scheduler.py wiring — source-text regression checks ──────────────────────────────────────

def test_options_flow_eod_job_is_registered_as_a_scheduled_job():
    assert 'id="options_flow_eod"' in _SCHEDULER_SOURCE
    assert "compute_options_flow_snapshots_eod" in _SCHEDULER_SOURCE


def test_options_flow_eod_job_scheduled_after_us_close_before_next_days_brief():
    """The EOD job must run after US market close (16:00 ET) and well before the next day's
    08:00 ET pre-market brief that reads its output — otherwise the brief would query rows
    that don't exist yet for that day."""
    start = _SCHEDULER_SOURCE.index('id="options_flow_eod"')
    preceding = _SCHEDULER_SOURCE[max(0, start - 300):start]
    hour_line = next(line for line in preceding.splitlines() if "hour=" in line and "minute=" in line)
    assert "hour=17" in hour_line
    assert "America/New_York" in hour_line


def test_bounded_options_flow_symbols_excludes_delisted_and_inactive_stocks():
    """Both the alert-symbol lookup and the top-K K-Score query must exclude delisted/inactive
    stocks — matching BUG-DELISTED-GENERATION-BLIND's established fix pattern, since this is
    itself a fresh generation-path query that could otherwise repeat the same bug class."""
    start = _SCHEDULER_SOURCE.index("def _bounded_options_flow_symbols(")
    end = _SCHEDULER_SOURCE.index("\ndef ", start + 1)
    body = _SCHEDULER_SOURCE[start:end]
    assert body.count("Stock.delisted.is_(False)") == 2
    assert body.count("Stock.active.is_(True)") == 2


def test_bounded_options_flow_symbols_is_us_only():
    start = _SCHEDULER_SOURCE.index("def _bounded_options_flow_symbols(")
    end = _SCHEDULER_SOURCE.index("\ndef ", start + 1)
    body = _SCHEDULER_SOURCE[start:end]
    assert "Stock.market == Market.US" in body
    assert '.endswith(".HK")' in body  # excludes HK symbols from the PriceAlert-subscribed set


def test_compute_options_flow_snapshots_eod_has_a_per_symbol_sleep():
    """Guards against a regression that removes the inter-symbol rate-limit delay — options-
    chain fetches are the most rate-limit-fragile call this app makes."""
    start = _SCHEDULER_SOURCE.index("def compute_options_flow_snapshots_eod(")
    end = _SCHEDULER_SOURCE.index("\ndef ", start + 1)
    body = _SCHEDULER_SOURCE[start:end]
    assert "time.sleep(" in body


def test_compute_options_flow_snapshots_eod_isolates_per_symbol_errors():
    """One symbol's fetch failure must not abort the whole batch — matches
    _refresh_fundamentals_batch()'s established per-symbol try/except isolation pattern."""
    start = _SCHEDULER_SOURCE.index("def compute_options_flow_snapshots_eod(")
    end = _SCHEDULER_SOURCE.index("\ndef ", start + 1)
    body = _SCHEDULER_SOURCE[start:end]
    for_loop_idx = body.index("for stock_id, symbol in symbols:")
    after_loop = body[for_loop_idx:]
    assert "try:" in after_loop
    assert "except Exception as exc:" in after_loop


def test_premarket_brief_gates_options_flow_fetch_to_us_only():
    body = _premarket_brief_body()
    assert "_fetch_recent_options_flow(session)" in body
    fetch_idx = body.index("_fetch_recent_options_flow(session)")
    preceding = body[:fetch_idx]
    last_us_gate = preceding.rindex('if "US" in markets:')
    between = body[last_us_gate:fetch_idx]
    assert between.count("\n") <= 2


def test_premarket_brief_passes_options_flow_into_the_email_and_the_done_log():
    body = _premarket_brief_body()
    assert "options_flow=recent_options_flow" in body
    done_log_idx = body.index('log.info("premarket_brief.done"')
    done_log_line = body[done_log_idx:body.index("\n", done_log_idx + 400)]
    assert "options_flow=len(recent_options_flow)" in done_log_line


def test_premarket_brief_nothing_to_report_guard_includes_options_flow():
    body = _premarket_brief_body()
    guard_idx = body.index('log.info("premarket_brief.nothing_to_report"')
    guard_line_start = body.rindex("if not macro_today", 0, guard_idx)
    guard_line = body[guard_line_start:guard_idx]
    assert "not recent_options_flow" in guard_line


def test_fetch_recent_options_flow_reads_only_persisted_rows_no_live_yfinance_call():
    """Matches this file's own established discipline of never hammering yfinance from a
    report path — see check_volume_anomalies()'s and _fetch_premarket_gappers()'s own
    docstrings for the same reasoning applied to sibling features."""
    start = _SCHEDULER_SOURCE.index("def _fetch_recent_options_flow(")
    end = _SCHEDULER_SOURCE.index("\ndef ", start + 1)
    body = _SCHEDULER_SOURCE[start:end]
    assert "import yfinance" not in body
    assert "OptionsFlowSnapshot" in body
