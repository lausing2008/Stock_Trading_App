"""EC-02 (2026-09-29 email-fix closure review): a delayed price alert must not assert, in the
present tense, a crossing that has since reverted.

THE DEFECT. The retry path added by EF-02 fetches a real current quote, which was the right
fix for the previous bug (it used to substitute the configured threshold for an observation).
But the renderer still wrote one sentence combining the historical condition with the current
price:

    Subject: Price Alert: RETRY has risen above 90.0
    RETRY is now 80.0000 (risen above your target of 90.0).
    Note: Delayed notification — this alert triggered at ...

Both facts are true and their combination is false. 80 is not above 90. The explanatory note
underneath does not repair the sentence above it — a reader skimming a subject line and a
headline has already been told the wrong thing.

These tests render the ACTUAL function and assert on the ACTUAL output string, so they cannot
pass against a renderer that merely mentions the right words somewhere.
"""
import importlib.util
import pathlib
import sys
from unittest.mock import MagicMock, patch

import pytest

_EMAIL_SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "email_service.py"


@pytest.fixture(scope="module")
def email_mod():
    """Load email_service.py standalone. Its module-level imports resolve against the conftest
    stubs already installed for the suite."""
    spec = importlib.util.spec_from_file_location("ec02_email_service", _EMAIL_SRC)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _render(email_mod, **kw):
    """Call the real renderer and capture what it would have sent."""
    captured = {}

    def _fake_send(to, subject, body_html, body_text):
        captured.update(to=to, subject=subject, html=body_html, text=body_text)
        return True

    with patch.object(email_mod, "send_email", side_effect=_fake_send):
        args = dict(to="audit@example.invalid", symbol="RETRY", condition="above",
                    threshold=90.0, price=80.0, note=None)
        args.update(kw)
        email_mod.send_price_alert_email(**args)
    return captured


# ── the crossing predicate, exercised THROUGH the renderer ──────────────────────────────
#
# The predicate is inline inside `send_price_alert_email` rather than a module-level helper,
# because email_service.py's renderers are extracted and executed in isolation by this repo's
# test and audit harnesses — a sibling function is out of scope exactly where the rendering is
# checked, and writing it as one made three existing EA-04 tests raise NameError. So the matrix
# below drives it through the rendered output, which is the stronger assertion anyway: it pins
# what a reader is actually told, not what an internal function returns.

@pytest.mark.parametrize("condition,threshold,price,reverted", [
    ("above", 90.0, 95.0, False),   # still above
    ("above", 90.0, 90.0, False),   # exactly at the threshold still satisfies "above"
    ("above", 90.0, 80.0, True),    # crossed back — the case the bug produced
    ("below", 90.0, 80.0, False),
    ("below", 90.0, 90.0, False),
    ("below", 90.0, 95.0, True),
])
def test_whether_the_crossing_still_holds(email_mod, condition, threshold, price, reverted):
    out = _render(email_mod, condition=condition, threshold=threshold, price=price)
    assert ("Earlier alert" in out["text"]) is reverted
    assert ("(delayed)" in out["subject"]) is reverted


# ── the reverted case, which is the whole finding ───────────────────────────────────────

def test_a_reverted_crossing_is_never_stated_in_the_present_tense(email_mod):
    """THE REGRESSION TEST. The exact false sentence from the review must not appear."""
    out = _render(email_mod, condition="above", threshold=90.0, price=80.0)
    assert "is now 80.0000 (risen above your target of 90.0)" not in out["text"]
    assert "RETRY is now 80.0000 (risen above" not in out["text"]


def test_a_reverted_crossing_reports_the_event_as_history_and_the_quote_as_current(email_mod):
    out = _render(email_mod, condition="above", threshold=90.0, price=80.0,
                  event_at="2026-09-28T13:31:02+00:00")
    text = out["text"]
    assert "Earlier alert" in text
    assert "risen above your target of 90.0" in text, "the historical crossing is still reported"
    assert "2026-09-28T13:31:02+00:00" in text, "with the time it actually happened"
    assert "Current price is 80.0000" in text
    assert "back below the threshold" in text


def test_the_subject_line_marks_a_reverted_alert_as_delayed(email_mod):
    """The subject is what a reader sees first and often all they see. It has to carry the
    tense too — 'has risen above 90' in an inbox is a claim about now."""
    out = _render(email_mod, condition="above", threshold=90.0, price=80.0)
    assert out["subject"].startswith("Price Alert (delayed):")
    assert "had risen above" in out["subject"]
    assert "has risen above" not in out["subject"]


def test_a_reverted_below_alert_says_back_above(email_mod):
    out = _render(email_mod, condition="below", threshold=90.0, price=95.0)
    assert "fallen below your target of 90.0" in out["text"]
    assert "back above the threshold" in out["text"]


def test_the_reverted_html_does_not_colour_the_number_as_a_win(email_mod):
    """An 'above' alert paints the big number green. On a reverted crossing that green number
    is the same false claim in colour rather than words."""
    out = _render(email_mod, condition="above", threshold=90.0, price=80.0)
    assert "#22c55e" not in out["html"]
    assert "reverted" in out["html"]


# ── the normal cases, which must be untouched ───────────────────────────────────────────

def test_a_crossing_that_still_holds_keeps_the_original_wording(email_mod):
    """The fix must be scoped to the reverted case. A price that is still beyond the threshold
    is correctly described in the present tense and that wording is not a defect."""
    out = _render(email_mod, condition="above", threshold=90.0, price=95.0)
    assert "RETRY is now 95.0000 (risen above your target of 90.0)." in out["text"]
    assert out["subject"] == "Price Alert: RETRY has risen above 90.0"
    assert "Earlier alert" not in out["text"]


def test_a_still_valid_below_alert_keeps_its_wording(email_mod):
    out = _render(email_mod, condition="below", threshold=90.0, price=80.0)
    assert "RETRY is now 80.0000 (fallen below your target of 90.0)." in out["text"]
    assert "Earlier alert" not in out["text"]


def test_an_indicator_alert_is_unaffected(email_mod):
    """EA-04's fix: a descriptive condition is the event and must not acquire a direction. The
    reverted-crossing branch must not capture it — its threshold is in different units entirely
    and there is no crossing to re-evaluate."""
    out = _render(email_mod, condition="MACD Bullish Cross", threshold=0.0, price=12.3,
                  value_label="MACD histogram")
    assert out["subject"] == "Alert: RETRY — MACD Bullish Cross"
    assert "Earlier alert" not in out["text"]
    assert "risen above" not in out["text"]
    assert "fallen below" not in out["text"]


def test_event_at_is_optional_and_its_absence_does_not_fabricate_a_time(email_mod):
    """An ordinary (non-retry) send passes no event time. The reverted wording must still work
    without inventing one."""
    out = _render(email_mod, condition="above", threshold=90.0, price=80.0, event_at=None)
    assert "Earlier alert" in out["text"]
    assert " at None" not in out["text"]
    assert " at " not in out["text"].split("Current price")[0].replace("Earlier alert", "")
