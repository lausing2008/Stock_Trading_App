"""Tests for AUD-OPTIONS4-GAMEPLANBATCH's options-game-plan section in send_signal_alert_email()
and its Advanced-tier-gated wiring in check_signal_alerts() (scheduler.py).

send_signal_alert_email() is pure string composition (no DB/network dependency beyond
send_email itself), so it's tested directly with real inputs, matching
test_short_squeeze_alert.py's own established convention for this file's sibling email
functions. The scheduler wiring (recipient tier check, snapshot lookup, never a live fetch)
can't be imported in this test environment — scheduler.py's import chain pulls in apscheduler
— so it's covered via source-text regression checks, matching test_short_squeeze_alert.py's/
test_scheduler_static_names.py's established pattern.
"""
import pathlib
from types import SimpleNamespace
from datetime import date
from unittest.mock import patch

from src.services.email_service import send_signal_alert_email

_scheduler_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
_scheduler_source = _scheduler_path.read_text()


def _capture_send():
    calls = []
    def _fake_send(to, subject, body_html, body_text):
        calls.append({"to": to, "subject": subject, "html": body_html, "text": body_text})
        return True
    return calls, _fake_send


def _fake_snapshot(**overrides):
    base = dict(
        as_of=date(2026, 9, 3),
        put_strike=None, put_expiry=None, put_mid_price=None,
        call_strike=None, call_expiry=None, call_mid_price=None,
        expected_move_pct=None, expected_move_dte=None, iv_rank_1y=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# ── send_signal_alert_email()'s new options_game_plan section — pure composition ───────────

def test_options_game_plan_renders_both_legs_when_present():
    snap = _fake_snapshot(
        put_strike=140.0, put_expiry="2026-10-15", put_mid_price=3.0,
        call_strike=168.0, call_expiry="2026-09-30", call_mid_price=1.85,
    )
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_signal_alert_email(
            "user@example.com", "AAPL", "HOLD", "BUY", "buy",
            options_game_plan=snap,
        )
    html, text = calls[0]["html"], calls[0]["text"]
    assert "Options Game Plan" in html and "Advanced tier" in html
    assert "$140.00" in html and "2026-10-15" in html
    assert "$168.00" in html and "2026-09-30" in html
    assert "Options Game Plan" in text and "$140.00" in text and "$168.00" in text


def test_options_game_plan_renders_only_the_leg_that_exists():
    """A symbol whose covered-call leg had no listed contract in the target DTE window today
    (a real, documented case — see OptionsGamePlanSnapshot's own model docstring) must render
    only the put, not a fabricated or blank call row."""
    snap = _fake_snapshot(put_strike=140.0, put_expiry="2026-10-15", put_mid_price=3.0)
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_signal_alert_email(
            "user@example.com", "AAPL", "HOLD", "BUY", "buy",
            options_game_plan=snap,
        )
    html = calls[0]["html"]
    assert "Protective Put" in html
    assert "Covered Call" not in html


def test_none_options_game_plan_renders_no_section_at_all():
    """The common case — recipient not Advanced-tier, symbol outside the bounded snapshot set,
    or no snapshot computed yet — must render nothing, not an empty/placeholder section."""
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_signal_alert_email(
            "user@example.com", "AAPL", "HOLD", "BUY", "buy",
            options_game_plan=None,
        )
    html, text = calls[0]["html"], calls[0]["text"]
    assert "Options Game Plan" not in html
    assert "Options Game Plan" not in text


def test_options_game_plan_omitted_for_non_buy_transitions():
    """Even if a snapshot were somehow passed for a non-BUY transition, this section is
    scoped to BUY only — matching the existing stock game_plan's own new_signal == 'BUY' gate."""
    snap = _fake_snapshot(put_strike=140.0, put_expiry="2026-10-15", put_mid_price=3.0)
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_signal_alert_email(
            "user@example.com", "AAPL", "BUY", "SELL", "sell",
            options_game_plan=snap,
        )
    assert "Options Game Plan" not in calls[0]["html"]


def test_both_legs_missing_renders_no_section():
    """A snapshot row can exist with BOTH legs None (compute_options_game_plan_snapshot()
    itself returns None in that case and no row is even upserted — but defend here too in
    case a caller ever passes a degenerate snapshot object directly)."""
    snap = _fake_snapshot()
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_signal_alert_email(
            "user@example.com", "AAPL", "HOLD", "BUY", "buy",
            options_game_plan=snap,
        )
    assert "Options Game Plan" not in calls[0]["html"]


# ── check_signal_alerts()'s scheduler-side wiring — source-text regression checks ──────────

def _check_signal_alerts_body() -> str:
    start = _scheduler_source.index("def check_signal_alerts(")
    end = _scheduler_source.index("\ndef ", start + 1)
    return _scheduler_source[start:end]


# ── IV Rank / expected-move row (AUD-IVRANK) ───────────────────────────────────────────────

def test_iv_and_expected_move_render_alongside_legs():
    snap = _fake_snapshot(
        put_strike=140.0, put_expiry="2026-10-15", put_mid_price=3.0,
        expected_move_pct=6.2, expected_move_dte=30, iv_rank_1y=72.0,
    )
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_signal_alert_email(
            "user@example.com", "AAPL", "HOLD", "BUY", "buy",
            options_game_plan=snap,
        )
    html, text = calls[0]["html"], calls[0]["text"]
    assert "Implied Volatility" in html
    assert "6.2%" in html and "30d" in html
    assert "IV Rank 72" in html
    assert "options relatively expensive" in html
    assert "Implied Volatility" in text and "IV Rank 72" in text


def test_iv_rank_reading_labels_low_high_and_mid_range_correctly():
    for iv_rank, expected_label in ((85.0, "options relatively expensive"), (10.0, "options relatively cheap"), (50.0, "mid-range")):
        snap = _fake_snapshot(put_strike=140.0, put_expiry="2026-10-15", put_mid_price=3.0, iv_rank_1y=iv_rank)
        calls, fake = _capture_send()
        with patch("src.services.email_service.send_email", fake):
            send_signal_alert_email("user@example.com", "AAPL", "HOLD", "BUY", "buy", options_game_plan=snap)
        assert expected_label in calls[0]["html"]


def test_iv_row_renders_even_when_no_legs_exist():
    """IV data is independently useful -- a symbol with real IV/IV-Rank data but no listed put
    or call in today's DTE window should still show the IV row, not render nothing at all."""
    snap = _fake_snapshot(expected_move_pct=4.1, expected_move_dte=30, iv_rank_1y=45.0)
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_signal_alert_email("user@example.com", "AAPL", "HOLD", "BUY", "buy", options_game_plan=snap)
    html = calls[0]["html"]
    assert "Implied Volatility" in html
    assert "Protective Put" not in html
    assert "Covered Call" not in html


def test_no_iv_data_and_no_legs_still_renders_no_section():
    snap = _fake_snapshot()
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_signal_alert_email("user@example.com", "AAPL", "HOLD", "BUY", "buy", options_game_plan=snap)
    assert "Options Game Plan" not in calls[0]["html"]


# ── Per-contract Greeks suffix (AUD-GREEKS) ────────────────────────────────────────────────

def test_greeks_suffix_renders_when_present_on_a_leg():
    snap = _fake_snapshot(
        put_strike=140.0, put_expiry="2026-10-15", put_mid_price=3.0,
        put_delta=-0.45, put_theta=-0.04, put_vega=0.11,
    )
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_signal_alert_email("user@example.com", "AAPL", "HOLD", "BUY", "buy", options_game_plan=snap)
    html, text = calls[0]["html"], calls[0]["text"]
    assert "-0.45" in html and "-0.04" in html and "0.11" in html
    assert "-0.45" in text and "-0.04" in text and "0.11" in text


def test_greeks_suffix_omitted_entirely_when_all_three_are_none():
    """No placeholder/empty parens when Unusual Whales had no Greeks for this contract."""
    snap = _fake_snapshot(put_strike=140.0, put_expiry="2026-10-15", put_mid_price=3.0)
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_signal_alert_email("user@example.com", "AAPL", "HOLD", "BUY", "buy", options_game_plan=snap)
    html = calls[0]["html"]
    assert "$140.00" in html
    assert "()" not in html


def test_greeks_suffix_is_independent_per_leg():
    snap = _fake_snapshot(
        put_strike=140.0, put_expiry="2026-10-15", put_mid_price=3.0, put_delta=-0.45,
        call_strike=168.0, call_expiry="2026-09-30", call_mid_price=1.85,
    )
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_signal_alert_email("user@example.com", "AAPL", "HOLD", "BUY", "buy", options_game_plan=snap)
    html = calls[0]["html"]
    assert "-0.45" in html  # put's own delta rendered
    # call has no Greeks -- must not accidentally inherit the put's suffix
    call_row_start = html.index("Covered Call")
    call_row = html[call_row_start:call_row_start + 200]
    assert "-0.45" not in call_row


def test_options_game_plan_is_gated_on_admin_or_advanced_tier():
    body = _check_signal_alerts_body()
    assert "alert.user.role == UserRole.ADMIN or alert.user.tier == UserTier.ADVANCED" in body


def test_options_game_plan_reads_the_snapshot_never_a_live_fetch():
    """The exact rate-limit-amplification risk this whole feature was built to avoid — must
    read get_latest_options_game_plan() (a DB read of the daily batch snapshot), never call
    yfinance or compute_options_game_plan_snapshot() (the live-fetching function) directly
    from inside the per-recipient email loop."""
    body = _check_signal_alerts_body()
    assert "get_latest_options_game_plan(session, _stock_row)" in body
    assert "compute_options_game_plan_snapshot(" not in body
    assert "yf.Ticker" not in body


def test_options_game_plan_lookup_is_scoped_to_buy_transitions_only():
    body = _check_signal_alerts_body()
    ogp_section_start = body.index("options_game_plan = None")
    ogp_section_end = body.index("email_ok = send_signal_alert_email(")
    ogp_section = body[ogp_section_start:ogp_section_end]
    assert 'if current == "BUY":' in ogp_section


def test_options_game_plan_lookup_fails_open_never_crashes_the_whole_scan():
    body = _check_signal_alerts_body()
    ogp_section_start = body.index("options_game_plan = None")
    ogp_section_end = body.index("email_ok = send_signal_alert_email(")
    ogp_section = body[ogp_section_start:ogp_section_end]
    assert "except Exception" in ogp_section


def test_options_game_plan_is_threaded_into_the_email_call():
    body = _check_signal_alerts_body()
    assert "options_game_plan=options_game_plan," in body


def test_scheduler_gate_also_accepts_iv_only_snapshots_with_no_legs():
    """AUD-IVRANK-EMAILGATE: a snapshot with real IV data but no put/call leg in today's DTE
    window must still be passed through to the email -- the original gate (put OR call) would
    have silently dropped a real, useful IV read just because no listed contract existed."""
    body = _check_signal_alerts_body()
    ogp_section_start = body.index("options_game_plan = None")
    ogp_section_end = body.index("email_ok = send_signal_alert_email(")
    ogp_section = body[ogp_section_start:ogp_section_end]
    assert "_snap.expected_move_pct is not None or _snap.iv_rank_1y is not None" in ogp_section
