"""MU-02: a preview must never consume the slot the actual result needs.

THE INCIDENT (2026-09-30, MU). Ingestion was fast — the Q4 results headline reached the database
**5.08 seconds** after publication and the Q1 guidance headline 5.18 seconds after its own. The
platform had the facts almost immediately and sent nothing.

`check_early_earnings_news_alerts` deduplicated on
`stockai:early_earnings_news:{user}:{symbol}:{day}` — ONE headline per symbol per calendar day. A
"Micron Earnings Ahead" preview at 17:27 consumed that slot; when the real print landed at 20:01
the key already existed, so the notification the reader actually wanted was suppressed by a story
that told them nothing.

Three defects, all fixed here:
  1. dedup had no PHASE, so stages competed for one slot;
  2. the fetcher returned only the FIRST matching headline, so a release that publishes result and
     guidance separately (3 minutes apart, as MU did) could surface at most one;
  3. the candidate query had a lower date bound and NO UPPER BOUND, so a symbol reporting weeks
     from now counted as "pending" and pre-release chatter could notify as if a release were live.
"""
import pathlib
import importlib.util
import sys

import pytest

_SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "earnings_phase.py"
_spec = importlib.util.spec_from_file_location("mu02_phase", _SRC)
ep = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = ep
_spec.loader.exec_module(ep)

SCHED = (pathlib.Path(__file__).resolve().parents[1]
         / "src/services/scheduler.py").read_text()

# The two REAL MU headlines production stored, verbatim from the review.
MU_RESULT = "Micron Technology Q4 Adj EPS $3.42 Beats $1.45 Estimate, Sales $54.229B Beat $50.751B Estimate"
MU_GUIDANCE = "Micron Technology Sees Q1 Adj EPS $7.15-$9.15 vs $5.07 Est, Sees Sales $60.000B-$63.000B vs $56.553B Est"
MU_PREVIEW = "Micron Earnings Ahead"


# ── the classification that decides whether the result gets through ─────────────────────

def test_the_actual_MU_result_headline_is_a_result():
    assert ep.classify_earnings_phase(MU_RESULT) == ep.PHASE_RESULTS


def test_the_actual_MU_guidance_headline_is_guidance_not_a_result():
    """Both real headlines carry 'Q<n>', 'Adj EPS' and an estimate comparison. Only the forward
    verb separates them, and collapsing guidance into results would merge the two stages back
    into one slot — reintroducing the bug for the pair that caused it."""
    assert ep.classify_earnings_phase(MU_GUIDANCE) == ep.PHASE_GUIDANCE


def test_the_actual_MU_preview_headline_is_a_preview():
    assert ep.classify_earnings_phase(MU_PREVIEW) == ep.PHASE_PREVIEW


def test_the_three_MU_stages_are_three_distinct_phases():
    """THE REGRESSION TEST. If any two collapse, one of them is silently suppressed."""
    phases = {ep.classify_earnings_phase(h) for h in (MU_PREVIEW, MU_RESULT, MU_GUIDANCE)}
    assert len(phases) == 3


@pytest.mark.parametrize("headline,expected", [
    ("Micron Technology Q4 Earnings Call Transcript", ep.PHASE_CALL),
    ("Acme Corp Earnings Call Highlights", ep.PHASE_CALL),
    ("Acme Sees FY Revenue Above Consensus", ep.PHASE_GUIDANCE),
    ("Acme Raises FY2027 Guidance", ep.PHASE_GUIDANCE),
    ("Acme Reports Q2 EPS Of $1.10", ep.PHASE_RESULTS),
    ("Acme Misses Q3 Estimates", ep.PHASE_RESULTS),
    ("Acme To Report Earnings Thursday", ep.PHASE_PREVIEW),
    ("What To Expect From Acme Earnings", ep.PHASE_PREVIEW),
])
def test_phase_vocabulary(headline, expected):
    assert ep.classify_earnings_phase(headline) == expected


def test_a_call_transcript_is_not_read_as_a_result():
    """'Q4 Earnings Call Transcript' contains Q4 and Earnings. Ordering `call` first is what stops
    late commentary from occupying the result stage."""
    assert ep.classify_earnings_phase("Micron Q4 Earnings Call Transcript") == ep.PHASE_CALL


def test_an_unrecognised_headline_is_other_and_does_not_notify():
    """Defaulting to `results` would let a vague headline consume the slot the real print needs —
    the same failure one level along."""
    for h in ("Micron Technology Named To Index", "Analyst Raises Price Target On MU"):
        phase = ep.classify_earnings_phase(h)
        assert phase == ep.PHASE_OTHER
        assert ep.phase_is_notifiable(phase) is False


def test_empty_and_missing_headlines_are_handled():
    for h in (None, "", "   "):
        assert ep.classify_earnings_phase(h) == ep.PHASE_OTHER


# ── what each stage is allowed to say ───────────────────────────────────────────────────

def test_each_notifiable_phase_has_a_distinct_subject():
    """A reader must tell a preview from a print in the inbox list without opening anything —
    which is what the single-slot bug destroyed."""
    subs = {ep.phase_subject("MU", p) for p in ep.NOTIFIABLE_PHASES}
    assert len(subs) == len(ep.NOTIFIABLE_PHASES)


def test_the_result_alert_does_not_claim_a_verified_number():
    """This path exists BECAUSE structured EPS has not landed — on the MU incident it was still
    NULL three hours later. Parsing figures out of headline text and presenting them as verified
    results is explicitly out of scope."""
    body = ep.phase_body("MU", ep.PHASE_RESULTS, MU_RESULT)
    assert "not a verified result" in body
    assert "have not landed" in body


def test_the_preview_alert_says_nothing_has_been_reported():
    body = ep.phase_body("MU", ep.PHASE_PREVIEW, MU_PREVIEW)
    assert "Nothing has been reported yet" in body


# ── the scheduler wiring ────────────────────────────────────────────────────────────────

def test_the_dedup_key_includes_the_phase():
    """THE FIX. Without the phase in the key, stages share one slot."""
    assert 'f"stockai:early_earnings_news:{uid}:{sym}:{phase}:{today_str}"' in SCHED


def test_the_job_iterates_every_earnings_headline_not_just_the_first():
    assert "_fetch_earnings_news_headlines(sym)" in SCHED
    assert "for _hl in _fetch_earnings_news_headlines(sym):" in SCHED


def test_unnotifiable_phases_are_skipped_before_sending():
    i = SCHED.index("for _hl in _fetch_earnings_news_headlines(sym):")
    block = SCHED[i:i + 500]
    assert "phase_is_notifiable(phase)" in block
    assert "continue" in block


def test_the_candidate_window_now_has_an_upper_bound():
    """The query said {yesterday, today} in its comment and enforced only the lower half, so a
    symbol reporting weeks ahead counted as pending."""
    i = SCHED.index("still_pending = set(session.execute(")
    block = SCHED[i - 700:i + 500]
    assert "EarningsEvent.report_date >= cutoff" in block
    assert "EarningsEvent.report_date <= _upper" in block


def test_the_plural_fetcher_returns_a_list_and_never_raises():
    i = SCHED.index("def _fetch_earnings_news_headlines")
    block = SCHED[i:i + 1200]
    assert "-> list[str]" in block
    assert "return []" in block, "an unreachable news service must not break the alert cycle"
