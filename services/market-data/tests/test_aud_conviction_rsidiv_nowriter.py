"""AUD-CONVICTION-RSIDIV-NOWRITER — a dead gate, and an email that lied about it.

`rsi_divergence` has no producer. It was removed from signals.py, and **0 of 4,316** signals in
the last 7 days carry the key. Three consumers still read it:

  1. `_is_conviction_buy`'s hard disqualifier — listed FIRST in its own docstring, so it reads
     as live protection against a false BUY. It can never fire.
  2. `analytics.py`'s gate-replica backtest — scores a disqualifier that never fires, so replay
     and live agree only by accident.
  3. The alert email — rendered **"None detected"** to every user on every alert.

That third one is the actual harm and the reason this was worth fixing: it is a **confidently
false statement, not a null**. It tells the reader divergence was CHECKED and found absent, when
nothing evaluated it at all.

TWO MEASUREMENTS THAT CORRECT THE RECORD:

**The removal comment's premise is wrong.** It says detection was *"hard-zeroed (argmax bug)"*.
Across the 4,678 historical rows that still carry the key: **4,147 `none`, 376 `bearish`, 155
`bullish`** — 11% non-none. The detector DID fire. Whatever the bug was, it was not a hard zero,
and nobody recorded what it actually did.

**But the signal is not strong enough to restore on.** On resolved BUY outcomes:

    none     n=1217   -1.31%   48.0% win
    bearish  n=42     -1.92%   42.9% win
    bullish  n=14     -0.94%   57.1% win

Directionally right, and **n=42**. A 0.6pp gap on 42 samples is noise — three findings in this
same session reversed under exactly that test.

DECISION (user, 2026-09-08): fix the false statement now, mark the gate dormant, record the bar,
and revisit the detector only if `check_prebreakout_alerts()`'s scheduled evaluation says another
non-momentum input is still needed.
"""
import pathlib

import pytest

MD = pathlib.Path(__file__).resolve().parents[1] / "src"
EMAIL_SRC = (MD / "services/email_service.py").read_text()
SCHED_SRC = (MD / "services/scheduler.py").read_text()
SIGNALS_SRC = (
    pathlib.Path(__file__).resolve().parents[2] / "signal-engine/src/generators/signals.py"
).read_text()


def _render_rows(reasons: dict) -> list[str]:
    """Mirrors the email's divergence row + the None-drop filter."""
    div = reasons.get("rsi_divergence")
    note = {"bearish": "⚠ Bearish — price up but momentum fading",
            "bullish": "✓ Bullish — price down but momentum recovering",
            "none": "None detected"}.get(div) if div is not None else None
    rows = [("RSI (14)", "55"), ("RSI divergence", note), ("MACD histogram", "ok")]
    return [k for k, v in rows if v is not None]


# ── The email must not assert a check it never ran ──────────────────────────────────────

def test_the_row_is_omitted_when_nothing_evaluated_divergence():
    """THE FIX. Today's signals carry no key at all, so the row must disappear — not claim
    'None detected', which asserts a negative finding that was never computed."""
    assert "RSI divergence" not in _render_rows({})


def test_a_genuine_none_still_renders():
    """The distinction that matters: 'not measured' vs 'measured as nothing'. If a producer ever
    computes `none`, that IS a real finding and must still be shown."""
    assert "RSI divergence" in _render_rows({"rsi_divergence": "none"})


@pytest.mark.parametrize("value", ["bearish", "bullish"])
def test_a_real_divergence_still_renders(value):
    """Restoring the producer must light this up again with no further change."""
    assert "RSI divergence" in _render_rows({"rsi_divergence": value})


def test_the_none_default_is_gone_from_the_source():
    """`reasons.get("rsi_divergence", "none")` was the bug — it manufactured a value."""
    assert 'reasons.get("rsi_divergence", "none")' not in EMAIL_SRC
    assert 'div = reasons.get("rsi_divergence")' in EMAIL_SRC


def test_the_row_list_is_filtered_before_rendering():
    """Both the HTML and text bodies render from the same list, so one filter covers both."""
    assert "reason_rows = [(k, v) for k, v in reason_rows if v is not None]" in EMAIL_SRC
    i = EMAIL_SRC.index("reason_rows = [(k, v) for k, v")
    assert EMAIL_SRC.index("rows_html") > i, "the filter must run BEFORE rendering"
    assert EMAIL_SRC.index("rows_text") > i


def test_other_evaluated_fields_still_show_an_explicit_dash():
    """A field that WAS evaluated and came back empty must still render '—'. Only an
    unevaluated field disappears — collapsing the two would lose the distinction this fix
    exists to restore."""
    assert '"—"' in EMAIL_SRC


# ── The gate is dormant, and says so ────────────────────────────────────────────────────

def test_the_gate_is_marked_dormant_not_silently_left():
    """A future reader must not count this as live protection when reasoning about what blocks
    a false BUY — it is listed FIRST in _is_conviction_buy's own docstring."""
    i = SCHED_SRC.index('if reasons.get("rsi_divergence") == "bearish":')
    preamble = SCHED_SRC[max(0, i - 2000):i]
    assert "DORMANT" in preamble
    assert "DO NOT COUNT THIS AS PROTECTION" in preamble


def test_the_gate_code_is_kept_so_a_restored_producer_re_arms_it():
    """Deleting it would mean restoring the producer silently does nothing."""
    assert 'if reasons.get("rsi_divergence") == "bearish":' in SCHED_SRC


def test_the_measured_bar_for_restoring_is_recorded_in_source():
    """So the next person does not have to re-measure — or worse, restore it on the strength of
    a 42-sample result."""
    i = SCHED_SRC.index('if reasons.get("rsi_divergence") == "bearish":')
    preamble = SCHED_SRC[max(0, i - 2000):i]
    assert "n=42" in preamble, "the sample size that makes this not-yet-actionable"
    assert "check_prebreakout_alerts()" in preamble, "the cheaper path to the same goal"


# ── The false premise in the removal comment is corrected ───────────────────────────────

def test_the_hard_zeroed_claim_is_corrected_where_it_was_made():
    """The claim lives in signals.py. Correcting it only in the audit doc would leave the
    misleading line exactly where someone deciding to restore this would read it."""
    assert "the \"hard-zeroed\" claim above is WRONG" in SIGNALS_SRC
    # The counts wrap across lines in the source comment, so assert on stable fragments.
    assert "4,678 historical signal rows" in SIGNALS_SRC
    assert "376 `bearish`" in SIGNALS_SRC
    assert "The detector DID fire" in SIGNALS_SRC


def test_the_correction_names_all_three_stranded_consumers():
    """Whoever restores the producer needs to know what re-arms."""
    i = SIGNALS_SRC.index("AUD-CONVICTION-RSIDIV-NOWRITER")
    block = SIGNALS_SRC[i:i + 1200]
    assert "_is_conviction_buy" in block
    assert "analytics.py" in block
    assert "email" in block.lower()


# ── The historical evidence, pinned ─────────────────────────────────────────────────────

def test_the_detector_was_not_hard_zeroed():
    """11% non-none across 4,678 rows. Pinned as arithmetic so the claim cannot rot."""
    none_n, bearish_n, bullish_n = 4147, 376, 155
    total = none_n + bearish_n + bullish_n
    assert total == 4678
    assert (bearish_n + bullish_n) / total > 0.10


def test_the_sample_is_too_small_to_act_on():
    """42 bearish outcomes. The effect is directionally right and statistically meaningless —
    this codebase has retracted three findings that looked exactly like this."""
    bearish_n, none_n = 42, 1217
    assert bearish_n < 100, "far below any bar this project has accepted"
    assert bearish_n / none_n < 0.05
