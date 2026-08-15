"""Direct behavioral tests for _regime_warning_lines() (T264-SQUEEZEFAMILY-REGIME-FLAG,
2026-08-15) — a shared, pure string-composition helper used by all 3 squeeze-family emails
(send_short_squeeze_email/send_gamma_unwind_email/send_prebreakout_email). No DB/network
dependency, so importable directly under this test environment's conftest.py stubs, matching
test_gamma_unwind_alert.py's own established "pure string composition, test directly with real
inputs" convention.
"""
from src.services.email_service import (
    _regime_warning_lines,
    send_gamma_unwind_email,
    send_prebreakout_email,
    send_short_squeeze_email,
)


def test_bull_regime_produces_no_warning():
    html, text = _regime_warning_lines("bull")
    assert html == ""
    assert text == ""


def test_none_regime_produces_no_warning():
    """A candidate with no regime data at all (a lookup failure upstream, or a caller that
    never set the field) must degrade to silence, not a spurious warning about an unknown
    state."""
    html, text = _regime_warning_lines(None)
    assert html == ""
    assert text == ""


def test_risk_off_regime_produces_a_visible_warning_naming_the_regime():
    html, text = _regime_warning_lines("risk_off")
    assert "risk_off" in html
    assert "risk_off" in text
    assert html != ""
    assert text != ""


def test_choppy_regime_also_produces_a_warning():
    """Any non-bull regime warns — not just risk_off specifically."""
    html, text = _regime_warning_lines("choppy")
    assert "choppy" in html
    assert html != ""


def test_warning_never_claims_the_alert_was_suppressed_or_blocked():
    """The whole point of a SOFT flag: the copy must never imply the alert was held back or
    that the user should distrust it outright — it fired on its own merits regardless."""
    html, text = _regime_warning_lines("risk_off")
    for phrase in ("suppressed", "blocked", "not sent", "withheld"):
        assert phrase not in html.lower()
        assert phrase not in text.lower()


def test_short_squeeze_email_includes_the_regime_warning_for_a_non_bull_candidate():
    candidates = [{
        "symbol": "AAPL", "short_percent_of_float": 22.5, "short_interest_date": None,
        "change_pct": 5.0, "price": 150.0, "short_ratio": None, "days_to_cover_critical": False,
        "calibrated_win_rate": None, "calibrated_win_rate_count": None, "market_regime": "risk_off",
    }]
    calls = []
    import src.services.email_service as es
    original_send = es.send_email
    try:
        es.send_email = lambda to, subject, html, text: calls.append({"html": html, "text": text}) or True
        send_short_squeeze_email("test@example.com", candidates)
    finally:
        es.send_email = original_send
    assert "risk_off" in calls[0]["html"]
    assert "risk_off" in calls[0]["text"]


def test_short_squeeze_email_omits_the_regime_warning_for_a_bull_candidate():
    candidates = [{
        "symbol": "AAPL", "short_percent_of_float": 22.5, "short_interest_date": None,
        "change_pct": 5.0, "price": 150.0, "short_ratio": None, "days_to_cover_critical": False,
        "calibrated_win_rate": None, "calibrated_win_rate_count": None, "market_regime": "bull",
    }]
    calls = []
    import src.services.email_service as es
    original_send = es.send_email
    try:
        es.send_email = lambda to, subject, html, text: calls.append({"html": html, "text": text}) or True
        send_short_squeeze_email("test@example.com", candidates)
    finally:
        es.send_email = original_send
    assert "Market regime" not in calls[0]["html"]


def test_gamma_unwind_email_includes_the_regime_warning():
    candidates = [{
        "symbol": "AAPL", "dominant_side": "puts", "concentration_pct": 62.0,
        "days_to_expiry": 3, "expiry": "2026-08-21", "total_oi_near_money": 1000, "price": 150.0,
        "calibrated_win_rate": None, "calibrated_win_rate_count": None, "market_regime": "risk_off",
    }]
    calls = []
    import src.services.email_service as es
    original_send = es.send_email
    try:
        es.send_email = lambda to, subject, html, text: calls.append({"html": html, "text": text}) or True
        send_gamma_unwind_email("test@example.com", candidates)
    finally:
        es.send_email = original_send
    assert "risk_off" in calls[0]["html"]


def test_prebreakout_email_includes_the_regime_warning():
    candidates = [{
        "symbol": "AAPL", "short_percent_of_float": 22.5, "short_interest_date": None,
        "bb_width_pctile": 0.1, "atr_pctile": 0.15, "volume_dried_up": True, "price": 150.0,
        "market_regime": "risk_off",
    }]
    calls = []
    import src.services.email_service as es
    original_send = es.send_email
    try:
        es.send_email = lambda to, subject, html, text: calls.append({"html": html, "text": text}) or True
        send_prebreakout_email("test@example.com", candidates)
    finally:
        es.send_email = original_send
    assert "risk_off" in calls[0]["html"]
