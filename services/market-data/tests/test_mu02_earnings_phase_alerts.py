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

# FIXTURES, reconstructed from the article URL slugs the review captured — NOT verbatim headline
# text. An earlier version of this file called them "verbatim" and had the figures wrong (3.42 /
# 1.45 instead of 33.42 / 31.45), which the reviewer caught. The numbers below now match the
# captured slugs, but the wording is still a reconstruction: these fixtures exist to pin
# CLASSIFICATION, and classification depends on the verbs and the Q<n>/EPS/estimate shape, not on
# the figures. Nothing here should be quoted as a captured headline.
MU_RESULT = ("Micron Technology Q4 Adj EPS $33.42 Beats $31.45 Estimate, "
             "Sales $54.229B Beat $50.751B Estimate")
MU_GUIDANCE = ("Micron Technology Sees Q1 Adj EPS $37.15-$39.15 vs $35.07 Est, "
               "Sees Sales $60.000B-$63.000B vs $56.553B Est")
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
    """THE FIX. Without the phase in the key, stages share one slot.

    The key also gained the EVENT date in the same round — see
    test_dedup_identity_includes_the_EARNINGS_EVENT_not_the_calendar_day."""
    assert 'f"stockai:early_earnings_news:{uid}:{sym}:{event_date}:{phase}"' in SCHED


def test_the_job_iterates_every_earnings_headline_not_just_the_first():
    assert "_fetch_earnings_news_headlines(sym)" in SCHED
    assert "for _hl, _pub, _isym in _fetch_earnings_news_headlines(sym):" in SCHED


def test_unnotifiable_phases_are_skipped_before_sending():
    i = SCHED.index("for _hl, _pub, _isym in _fetch_earnings_news_headlines(sym):")
    block = SCHED[i:i + 1800]
    assert "phase_is_notifiable(phase)" in block
    assert "continue" in block


def test_the_candidate_window_now_has_an_upper_bound():
    """The query said {yesterday, today} in its comment and enforced only the lower half, so a
    symbol reporting weeks ahead counted as pending."""
    # Now in the extracted `_pending_earnings_events()`, parameterised on `today` so the window
    # can be exercised against a real database (see test_mu02_delivery_behaviour.py).
    i = SCHED.index("def _pending_earnings_events")
    block = SCHED[i:i + 1800]
    # Both bounds are asserted BEHAVIOURALLY against a real database in
    # test_mu02_delivery_behaviour.py (future event excluded, window closes after two days).
    # Only the structural presence of an upper bound is checked here, with no number pinned —
    # the T401 ratchet exists because "x >= 5" survives being changed to "x >= 5 - 99999".
    assert "EarningsEvent.report_date <= today" in block


def test_the_plural_fetcher_returns_a_list_and_never_raises():
    i = SCHED.index("def _fetch_earnings_news_headlines")
    block = SCHED[i:i + 1400]
    # Returns (headline, published_at) now — the timestamp is what binds a headline to an event.
    assert "-> list[tuple[str, str | None, str | None]]" in block
    assert "return []" in block, "an unreachable news service must not break the alert cycle"


# ── the reviewer's four pre-deployment checks ───────────────────────────────────────────

def test_dedup_identity_includes_the_EARNINGS_EVENT_not_the_calendar_day():
    """Check 1. A calendar-day key is wrong twice over for an after-hours US release: a 20:01 EDT
    print is 00:01 UTC the NEXT day, so a retry minutes later lands on a different key and
    re-sends a phase already delivered; and two adjacent-day events for one symbol would share a
    key and suppress each other. The key is scoped to the event's own report date."""
    assert 'f"stockai:early_earnings_news:{uid}:{sym}:{event_date}:{phase}"' in SCHED
    assert "{today_str}" not in SCHED[SCHED.index("def _send_early_earnings_stage"):
                                      SCHED.index("def _fetch_earnings_news_headlines")]


def test_the_event_date_comes_from_the_earnings_row_not_from_todays_date():
    i = SCHED.index("def _pending_earnings_events")
    assert "EarningsEvent.report_date" in SCHED[i:i + 1800]
    assert "_event_date.isoformat()" in SCHED
    # The caller now abstains on ambiguity rather than taking the latest. The unwrapping is
    # asserted BEHAVIOURALLY against a real database in test_mu02_delivery_behaviour.py — no
    # index pinned here, per the T401 ratchet.
    assert "_candidates = _event_date_by_symbol[sym]" in SCHED


def test_an_after_hours_release_crossing_UTC_midnight_keeps_one_identity():
    """Check 4, as arithmetic. 2026-09-30 20:01 EDT is 2026-10-01 00:01 UTC — two different
    calendar days for the same release. Keying on the report date gives one identity for both."""
    from datetime import datetime, timedelta, timezone
    release_et = datetime(2026, 9, 30, 20, 1, tzinfo=timezone(timedelta(hours=-4)))
    assert release_et.astimezone(timezone.utc).date().isoformat() == "2026-10-01"
    assert release_et.date().isoformat() == "2026-09-30"
    # The key uses the EarningsEvent.report_date, which is a single value for this release.
    event_date = "2026-09-30"
    before = f"stockai:early_earnings_news:7:MU:{event_date}:results"
    after = f"stockai:early_earnings_news:7:MU:{event_date}:results"
    assert before == after, "the same release must not produce two dedup identities"


def _whole_fn(name: str) -> str:
    """One top-level function, sliced on real `def` boundaries."""
    lines = SCHED.split("\n")
    starts = {i: ln for i, ln in enumerate(lines) if ln.startswith("def ")}
    i = next(k for k, ln in starts.items() if ln.startswith(f"def {name}("))
    j = next((k for k in sorted(starts) if k > i), len(lines))
    return "\n".join(lines[i:j])


def test_the_ttl_outlives_the_utc_midnight_boundary():
    """A 24h TTL keyed on the event could still expire mid-incident while retries continue.

    Asserted on the TTL actually written, in test_mu02_delivery_behaviour.py — the value, not the
    digits in the source. See `test_the_marker_ttl_spans_more_than_one_calendar_day`."""
    # WHOLE FUNCTION, not a fixed 2500-character window. The window silently stopped covering
    # the code these assertions target the moment the function grew (M20's cutover added a
    # dozen lines above them), turning a real check into a substring-not-found error.
    block = _whole_fn("_send_early_earnings_stage")
    assert "_rc.setex(redis_key" in block


def test_one_fetch_carrying_three_stages_handles_all_three():
    """Check 2. MU published result and guidance three minutes apart, with transcript later. A
    single fetch must produce a notification per stage regardless of the order they arrive in."""
    for order in ([MU_RESULT, MU_GUIDANCE, "Micron Q4 Earnings Call Transcript"],
                  ["Micron Q4 Earnings Call Transcript", MU_GUIDANCE, MU_RESULT],
                  [MU_GUIDANCE, "Micron Q4 Earnings Call Transcript", MU_RESULT]):
        phases = [ep.classify_earnings_phase(h) for h in order]
        assert set(phases) == {ep.PHASE_RESULTS, ep.PHASE_GUIDANCE, ep.PHASE_CALL}
        assert all(ep.phase_is_notifiable(p) for p in phases)
        assert len(set(phases)) == 3, "ordering must not collapse two stages into one"


def test_the_job_loops_every_headline_so_ordering_cannot_drop_a_stage():
    i = SCHED.index("for _hl, _pub, _isym in _fetch_earnings_news_headlines(sym):")
    block = SCHED[i:i + 900]
    assert "break" not in block, "a break would stop at the first stage and drop the rest"


def test_phase_state_advances_only_after_a_successful_send():
    """Check 3. A transient failure must leave the phase retryable, not permanently suppressed.

    Asserts there is NO marker write anywhere BEFORE the send — not merely that one exists after
    it. An earlier version searched forward from `if sent_ok:` and therefore could not see a
    second, earlier write; a sabotage that marked the phase before sending passed it."""
    # WHOLE FUNCTION, not a fixed 2500-character window. The window silently stopped covering
    # the code these assertions target the moment the function grew (M20's cutover added a
    # dozen lines above them), turning a real check into a substring-not-found error.
    block = _whole_fn("_send_early_earnings_stage")
    send_idx = block.index("sent_ok = send_email(")
    before_send = block[:send_idx]
    assert "setex" not in before_send, \
        "the dedup marker is written before the send — a failed send would suppress the phase"
    ok_idx = block.index("if sent_ok:", send_idx)
    setex_idx = block.index("_rc.setex(redis_key", ok_idx)
    assert send_idx < ok_idx < setex_idx
    assert block.count("_rc.setex(redis_key") == 1, "exactly one marker write"


def test_a_failed_send_leaves_no_marker():
    # WHOLE FUNCTION, not a fixed 2500-character window. The window silently stopped covering
    # the code these assertions target the moment the function grew (M20's cutover added a
    # dozen lines above them), turning a real check into a substring-not-found error.
    block = _whole_fn("_send_early_earnings_stage")
    fail_arm = block[block.index("except Exception as _send_exc:"):block.index("if sent_ok:")]
    assert "setex" not in fail_arm
    assert "sent_ok = False" in fail_arm
