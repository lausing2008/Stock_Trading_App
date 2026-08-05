"""Tests for AUD-SECTOR-EMERGING-ALERT: check_sector_rotation_alerts() (scheduler.py) and
send_sector_rotation_email() (email_service.py) — the opportunity-finding alert that fires
when a sector NEWLY becomes an "Emerging Leader" (its K-Score rank among sectors is climbing
into the top half), paired with the top stocks in that sector by K-Score.

send_sector_rotation_email() is pure string composition (no DB/network dependency), so it's
tested directly with real inputs. check_sector_rotation_alerts() itself can't be imported in
this test environment — scheduler.py's import chain pulls in apscheduler and other unstubbed
modules — so the scan logic/wiring is covered by source-text regression checks instead,
matching test_short_squeeze_alert.py's / test_squeeze_watch_revert_alert.py's established
pattern.
"""
import pathlib
from unittest.mock import patch

from src.services.email_service import send_sector_rotation_email

_scheduler_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
_scheduler_source = _scheduler_path.read_text()


def _capture_send():
    calls = []
    def _fake_send(to, subject, body_html, body_text):
        calls.append({"to": to, "subject": subject, "html": body_html, "text": body_text})
        return True
    return calls, _fake_send


def _check_sector_rotation_alerts_body() -> str:
    start = _scheduler_source.index("def check_sector_rotation_alerts(")
    end = _scheduler_source.index("\ndef ", start + 1)
    return _scheduler_source[start:end]


def _candidate(sector="Technology", delta=5.2, rank=2, top_stocks=None):
    if top_stocks is None:
        top_stocks = [{"symbol": "AAA", "name": "Alpha Co", "k_score": 82.5}, {"symbol": "BBB", "name": "Beta Co", "k_score": 78.1}]
    return {"sector": sector, "delta": delta, "rank": rank, "top_stocks": top_stocks}


# ── send_sector_rotation_email() — pure composition, tested directly ────────────────────────

def test_single_sector_renders_name_rank_delta_and_stocks():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_sector_rotation_email("user@example.com", [_candidate()])
    html, text = calls[0]["html"], calls[0]["text"]
    assert "Technology" in html and "#2" in html and "+5.2 pts" in html
    assert "AAA" in html and "BBB" in html
    assert "82" in html  # k_score rounded
    assert "Technology" in text and "AAA" in text


def test_subject_reflects_sector_count():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_sector_rotation_email("user@example.com", [_candidate("Tech"), _candidate("Energy")])
    assert "2 sectors" in calls[0]["subject"]


def test_singular_subject_for_one_sector():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_sector_rotation_email("user@example.com", [_candidate()])
    assert "1 sector " in calls[0]["subject"] or "1 sector newly" in calls[0]["subject"]
    assert "1 sectors" not in calls[0]["subject"]


def test_multiple_sectors_all_rendered():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_sector_rotation_email("user@example.com", [
            _candidate("Technology", top_stocks=[{"symbol": "AAA", "name": "A", "k_score": 80.0}]),
            _candidate("Energy", top_stocks=[{"symbol": "ZZZ", "name": "Z", "k_score": 75.0}]),
        ])
    html = calls[0]["html"]
    assert "Technology" in html and "Energy" in html
    assert "AAA" in html and "ZZZ" in html


def test_missing_delta_or_rank_degrades_gracefully_not_crash():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        result = send_sector_rotation_email("user@example.com", [_candidate(delta=None, rank=None)])
    assert result is True
    assert "—" in calls[0]["html"]


def test_empty_top_stocks_shows_a_real_message_not_blank():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        result = send_sector_rotation_email("user@example.com", [_candidate(top_stocks=[])])
    assert result is True
    assert "No top-K-Score stocks available" in calls[0]["html"]
    assert "no candidates" in calls[0]["text"]


def test_body_never_asserts_a_guarantee():
    """Matches this repo's established alert-honesty discipline (e.g. the gamma-unwind alert's
    own equivalent test) — rank/delta are a measured signal, not a promise of outperformance."""
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_sector_rotation_email("user@example.com", [_candidate()])
    html = calls[0]["html"].lower()
    assert "not a guarantee" in html


# ── check_sector_rotation_alerts() — source-text regression checks ──────────────────────────

def test_fires_only_on_transition_to_emerging_leader():
    """The dedup mechanism must diff against a PRIOR set of already-emerging sectors, matching
    check_short_squeeze_alerts()'s own "only email on the transition" property — not re-alert
    every week a sector stays Emerging Leader."""
    body = _check_sector_rotation_alerts_body()
    assert "prev_emerging" in body
    assert "newly_emerging" in body
    assert "emerging - prev_emerging" in body


def test_classifies_using_trajectory_field_not_a_new_computation():
    """Must reuse the SAME trajectory classification _compute_sector_rotation() already
    computes — no separate/duplicate rank-comparison logic."""
    body = _check_sector_rotation_alerts_body()
    assert 'data.get("trajectory") == "Emerging Leader"' in body


def test_resyncs_the_tracked_set_even_when_nothing_newly_emerged():
    """A sector that fades OUT of Emerging Leader must be removed from the tracked set, so it
    correctly re-alerts if it later re-emerges — matches check_short_squeeze_alerts()'s own
    always-resync pattern."""
    body = _check_sector_rotation_alerts_body()
    resync_idx = body.index("_rc.delete(state_key)")
    guard_idx = body.index("if not newly_emerging:")
    assert resync_idx < guard_idx


def test_delivered_only_to_price_alert_subscribed_recipients():
    body = _check_sector_rotation_alerts_body()
    assert "PriceAlert.triggered.is_(False)" in body


def test_top_stocks_query_excludes_delisted_and_inactive_stocks():
    """Matches this repo's own BUG-DELISTED-GENERATION-BLIND discipline — a confirmed-delisted
    or inactive stock must never be recommended as a top candidate in a newly-emerging sector."""
    body = _check_sector_rotation_alerts_body()
    assert "Stock.active.is_(True)" in body
    assert "Stock.delisted.is_(False)" in body


def test_top_stocks_query_scoped_to_us_market_and_the_specific_sector():
    body = _check_sector_rotation_alerts_body()
    assert "Stock.sector == sector" in body
    assert "Stock.market == Market.US" in body


def test_top_stocks_query_uses_recent_rankings_only():
    """A stale, months-old Ranking row must never be recommended as "top right now"."""
    body = _check_sector_rotation_alerts_body()
    assert "Ranking.as_of >=" in body


def test_top_n_is_capped():
    body = _check_sector_rotation_alerts_body()
    assert "_SECTOR_ROTATION_ALERT_TOP_N" in body
    assert ".limit(_SECTOR_ROTATION_ALERT_TOP_N)" in body


def test_called_inline_from_compute_sector_rotation_not_a_separate_cron_job():
    """Deliberately called inline right after _compute_sector_rotation() builds `rotation`,
    not as its own scheduled job — guarantees it always reads the SAME fresh dict, never a
    stale/race-prone re-read of the Redis cache from a differently-timed job."""
    start = _scheduler_source.index("def _compute_sector_rotation(")
    end = _scheduler_source.index("\n\n\n_SECTOR_ROTATION_ALERT_TOP_N", start)
    body = _scheduler_source[start:end]
    assert "check_sector_rotation_alerts(rotation)" in body
