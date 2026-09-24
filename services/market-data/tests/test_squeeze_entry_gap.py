"""AUD-SQUEEZE-ENTRYGAP: the measured cost of acting on a squeeze alert.

WHY THIS EXISTS. The Short Squeeze Alert fires on an intraday move that has ALREADY happened
(>= _SQUEEZE_MIN_INTRADAY_MOVE_PCT), and a material part of it is typically given back before
anyone can enter. Measured 2026-09-24 over the 15 resolved short_squeeze alerts: a MEDIAN
-6.65% gap between the alert price and the next session's entry price, adverse in 12 of 15.
The same 15 alerts were BELOW the calibrated-win-rate floor of 30, so the email said "not
enough resolved history yet for a measured win rate" — perfectly true, and yet the reader was
being shown a game plan priced off a quote they could not get.

The two statistics answer different questions and deserve different evidence bars:

  * WIN RATE — "was the thesis right?" Needs a directional outcome per alert and is withheld
    below 30, because a rate on a handful of trades is worse than no rate at all.
  * ENTRY GAP — "can you get the price you were shown?" An EXECUTION fact, resolved the very
    next session, whose sign is consistent enough to state at much smaller samples.

Folding the second into the first would have kept the platform silent about a -6.65% median
for as long as the win rate stayed under its floor.

The rendering rule is as important as the number: the warning appears ONLY when the gap is both
adverse and consistent. A near-even split is noise; a positive median means the next session
was historically CHEAPER, which is not a warning. Both render nothing — an "everything is fine"
line trains readers to skip the box.
"""
from src.services.email_service import _entry_gap_html


def _gap(median_pct, n, negative_of):
    return {"median_pct": median_pct, "n": n, "negative_of": negative_of}


# ── When the warning must appear ──────────────────────────────────────────────

def test_a_consistent_adverse_gap_is_warned_about():
    """The real measurement: median -6.65%, adverse in 12 of 15."""
    html, text = _entry_gap_html(_gap(-6.65, 15, 12))
    assert html and text
    assert "-6.7%" in html or "-6.6%" in html
    assert "12 of 15" in html


def test_the_warning_says_the_quote_is_not_the_fill():
    """The actionable part is not the number, it is that the game plan above is priced off a
    quote the reader cannot get."""
    html, _ = _entry_gap_html(_gap(-6.65, 15, 12))
    assert "not the price you get" in html
    assert "game plan" in html.lower()


def test_both_html_and_plaintext_are_produced():
    html, text = _entry_gap_html(_gap(-6.65, 15, 12))
    assert html.startswith("<div")
    assert not text.strip().startswith("<")
    assert "12 of 15" in text


# ── When it must stay silent ──────────────────────────────────────────────────

def test_an_inconsistent_gap_is_not_shown_however_large_the_median():
    """8 of 15 is a coin flip. A large median on a near-even split is one outlier wearing a
    disguise, and dressing it as a warning spends credibility the number has not earned."""
    assert _entry_gap_html(_gap(-9.0, 15, 8)) == ("", "")


def test_a_favourable_gap_is_not_reported_as_a_warning():
    """squeeze_ignition measured +1.36%: entering the next session was historically CHEAPER.
    Warning about that would be nonsense."""
    assert _entry_gap_html(_gap(1.36, 15, 3)) == ("", "")


def test_ordinary_slippage_is_not_dressed_up_as_a_finding():
    """gamma_unwind_puts measured -0.08%. Real, consistent, and far too small to act on — the
    threshold exists so the line means something when it does appear."""
    assert _entry_gap_html(_gap(-0.08, 276, 200)) == ("", "")


def test_a_gap_just_short_of_the_threshold_stays_silent():
    assert _entry_gap_html(_gap(-0.99, 100, 90)) == ("", "")


def test_a_gap_just_past_the_threshold_is_shown():
    html, _ = _entry_gap_html(_gap(-1.01, 100, 90))
    assert html


# ── Absent or malformed input ─────────────────────────────────────────────────

def test_no_measurement_renders_nothing_rather_than_a_placeholder():
    """Below the sample floor the scheduler passes None. An empty box would imply a measurement
    was made and came back clean."""
    assert _entry_gap_html(None) == ("", "")
    assert _entry_gap_html({}) == ("", "")


def test_a_missing_median_is_not_treated_as_zero():
    assert _entry_gap_html({"median_pct": None, "n": 20, "negative_of": 18}) == ("", "")


def test_a_zero_sample_never_divides_by_zero():
    assert _entry_gap_html(_gap(-5.0, 0, 0)) == ("", "")
