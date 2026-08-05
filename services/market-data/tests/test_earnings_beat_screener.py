"""Tests for AUD-EARNINGS-BEAT-SCREENER: check_earnings_beat_screener_alerts() (scheduler.py)
and send_earnings_beat_screener_email() (email_service.py) — the opportunity-finding alert
for stocks with BOTH a real recent earnings beat AND improving analyst sentiment.

send_earnings_beat_screener_email() is pure string composition (no DB/network dependency), so
it's tested directly with real inputs. check_earnings_beat_screener_alerts() itself can't be
imported in this test environment — scheduler.py's import chain pulls in apscheduler and other
unstubbed modules — so the scan logic/wiring is covered by source-text regression checks
instead, matching test_sector_rotation_alert.py's established pattern.
"""
import pathlib
from unittest.mock import patch

from src.services.email_service import send_earnings_beat_screener_email

_scheduler_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
_scheduler_source = _scheduler_path.read_text()


def _capture_send():
    calls = []
    def _fake_send(to, subject, body_html, body_text):
        calls.append({"to": to, "subject": subject, "html": body_html, "text": body_text})
        return True
    return calls, _fake_send


def _check_earnings_beat_screener_alerts_body() -> str:
    start = _scheduler_source.index("def check_earnings_beat_screener_alerts(")
    end = _scheduler_source.index("\ndef ", start + 1)
    return _scheduler_source[start:end]


def _candidate(symbol="AAA", name="Alpha Co", report_date="2026-07-28", surprise_pct=12.5,
               revenue_surprise_pct=4.2, rec_mean_improvement=0.35):
    return {
        "symbol": symbol, "name": name, "report_date": report_date,
        "surprise_pct": surprise_pct, "revenue_surprise_pct": revenue_surprise_pct,
        "rec_mean_improvement": rec_mean_improvement,
    }


# ── send_earnings_beat_screener_email() — pure composition, tested directly ────────────────

def test_single_candidate_renders_symbol_surprise_and_rec_improvement():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_earnings_beat_screener_email("user@example.com", [_candidate()])
    html, text = calls[0]["html"], calls[0]["text"]
    assert "AAA" in html and "+12.5%" in html and "0.35" in html
    assert "AAA" in text


def test_subject_reflects_stock_count():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_earnings_beat_screener_email("user@example.com", [_candidate("AAA"), _candidate("BBB")])
    assert "2 stocks" in calls[0]["subject"]


def test_singular_subject_for_one_stock():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_earnings_beat_screener_email("user@example.com", [_candidate()])
    assert "1 stock " in calls[0]["subject"] or "1 stock" in calls[0]["subject"]
    assert "1 stocks" not in calls[0]["subject"]


def test_multiple_candidates_all_rendered():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_earnings_beat_screener_email("user@example.com", [_candidate("AAA"), _candidate("BBB")])
    html = calls[0]["html"]
    assert "AAA" in html and "BBB" in html


def test_missing_revenue_surprise_degrades_gracefully_not_crash():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        result = send_earnings_beat_screener_email("user@example.com", [_candidate(revenue_surprise_pct=None)])
    assert result is True
    assert "revenue +" not in calls[0]["html"]


def test_body_never_asserts_a_positive_guidance_claim():
    """The one honest-framing property this alert must have — "guidance" is a claim this app
    has no real data source for (no earnings-call-transcript parsing exists). The body DOES
    legitimately mention "guidance" once, in its own explicit disclaimer ("not a claim about
    future guidance") — that's the correct behavior, not a violation. What must never appear
    is a POSITIVE guidance claim like "rising guidance" or "guidance raised"."""
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_earnings_beat_screener_email("user@example.com", [_candidate()])
    html = calls[0]["html"].lower()
    assert "rising guidance" not in html
    assert "guidance raised" not in html
    assert "not a claim about future guidance" in html  # the correct, honest disclaimer IS present


# ── check_earnings_beat_screener_alerts() — source-text regression checks ──────────────────

def test_requires_a_real_positive_surprise_pct():
    body = _check_earnings_beat_screener_alerts_body()
    assert "EarningsEvent.surprise_pct > _EARNINGS_BEAT_SCREENER_MIN_SURPRISE_PCT" in body


def test_requires_recent_report_date():
    body = _check_earnings_beat_screener_alerts_body()
    assert "EarningsEvent.report_date >= cutoff" in body
    assert "_EARNINGS_BEAT_SCREENER_LOOKBACK_DAYS" in body


def test_excludes_delisted_and_inactive_stocks():
    """Matches this repo's own BUG-DELISTED-GENERATION-BLIND discipline."""
    body = _check_earnings_beat_screener_alerts_body()
    assert "Stock.active.is_(True)" in body
    assert "Stock.delisted.is_(False)" in body


def test_reuses_the_same_rec_delta_formula_as_signals_py_eps_revision_direction():
    """Must reuse the EXACT SAME (old - recent) delta direction and threshold concept as
    signals.py's own eps_revision_direction feature — never a fresh, second computation that
    could silently drift from it."""
    body = _check_earnings_beat_screener_alerts_body()
    assert "float(snaps[-1][0]) - float(snaps[0][0])" in body
    assert "_EARNINGS_BEAT_SCREENER_MIN_REC_IMPROVEMENT" in body


def test_requires_at_least_two_snapshots_to_compute_a_trend():
    body = _check_earnings_beat_screener_alerts_body()
    assert "len(snaps) < 2" in body


def test_dedup_is_per_user_symbol_and_report_date():
    """A user must get a fresh alert for a genuinely NEW qualifying beat, but never a repeat
    of the same earnings event already reported."""
    body = _check_earnings_beat_screener_alerts_body()
    assert 'f"stockai:earnings_beat_screener:{uid}:{c[\'symbol\']}:{c[\'report_date\']}"' in body


def test_top_n_is_capped():
    body = _check_earnings_beat_screener_alerts_body()
    assert "_EARNINGS_BEAT_SCREENER_TOP_N" in body
    assert "candidates[:_EARNINGS_BEAT_SCREENER_TOP_N]" in body


def test_delivered_only_to_price_alert_subscribed_recipients():
    body = _check_earnings_beat_screener_alerts_body()
    assert "PriceAlert.triggered.is_(False)" in body


def test_called_inline_from_snapshot_fundamentals_not_a_separate_cron_job():
    """Deliberately called inline right after _snapshot_fundamentals() completes, guaranteeing
    the analyst-recommendation-revision history it depends on is always fresh — same reasoning
    as check_sector_rotation_alerts()'s own inline call from _compute_sector_rotation()."""
    start = _scheduler_source.index("def _snapshot_fundamentals(")
    end = _scheduler_source.index("\n\n\n_EARNINGS_BEAT_SCREENER_LOOKBACK_DAYS", start)
    body = _scheduler_source[start:end]
    assert "check_earnings_beat_screener_alerts()" in body
