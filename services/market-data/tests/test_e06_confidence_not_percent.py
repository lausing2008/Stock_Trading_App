"""AUD-E06-CONFIDENCEPCT — send_signal_alert_email() presented `confidence` (=
abs(fused_score - 0.5) * 200, a DISTANCE from a neutral fused score) with a "%" suffix and the
label "conf"/"Confidence" — the exact unit and word a real, observed trade-win probability
would use. The generator's own comment already warns against reading it as accuracy, but the
email's subject/body still invited that misreading.

Fixed by relabeling to "strength N/100" (subject tag), "Signal strength: N/100" (text body),
"Strength N/100" (HTML card) — never a percentage. `bullish_prob` is relabeled "Fused bullish
score" rather than "probability", matching the same "needs cohort-specific calibration before
it can represent a validated probability" caveat from the audit.

email_service.py can't be imported directly in this test environment — source-scanned, matching
this repo's established convention for this file.
"""
import pathlib

_SOURCE = (
    pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "email_service.py"
).read_text()


def _send_signal_alert_email_body() -> str:
    start = _SOURCE.index("def send_signal_alert_email(")
    end = _SOURCE.index("\ndef ", start + 10)
    return _SOURCE[start:end]


_BODY = _send_signal_alert_email_body()


def test_subject_tag_never_shows_confidence_as_a_percent():
    assert "_conf_tag = f\" · strength {float(confidence):.0f}/100\"" in _BODY
    assert '{float(confidence):.0f}% conf' not in _BODY


def test_text_body_labels_it_signal_strength_out_of_100_not_a_percent_confidence():
    assert "Signal strength: {float(confidence):.1f}/100" in _BODY
    assert "Confidence: {float(confidence)" not in _BODY


def test_text_body_calls_bullish_prob_a_fused_score_not_a_probability():
    assert "Fused bullish score:" in _BODY


def test_html_card_labels_strength_out_of_100_not_a_percent_confidence():
    assert "Strength {float(confidence):.0f}/100" in _BODY
    assert "Confidence {float(confidence):.0f}%" not in _BODY


def test_html_card_labels_bullish_prob_a_fused_score():
    assert "Fused bullish score</div>" in _BODY
