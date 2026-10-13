"""Price-risk detection: measured moves in followed stocks, themes and benchmarks.

WHY THIS EXISTS. The signal engine reports a DIRECTION LABEL, and a label does not change
merely because a price falls — NVDA and AVGO sat at HOLD through a decline while the only
price-based coverage was user-created absolute thresholds that fire once and then stay
consumed. So a position could keep falling with nothing to say about it. This detects the
move itself, independent of BUY/HOLD/WAIT/SELL.

EVERYTHING HERE IS PURE. No session, no network, no clock of its own. The caller resolves
prices and their adjustment evidence and passes them in; this decides what, if anything,
happened. That is deliberate: the equivalent logic elsewhere in this codebase was only ever
testable through an injected stand-in that implemented a different rule, and the suite was
green on behaviour the live caller could not produce.

THRESHOLDS BELOW ARE PROPOSED STARTING SETTINGS, not validated optima and not advice. They
exist to be measured in shadow mode against real volume before anything is delivered.
"""
from __future__ import annotations

from datetime import date

#: Escalation ladders. A move is reported at the HIGHEST step it clears, and re-reported only
#: when it clears a HIGHER one — see `should_emit`. Without a ladder, "price is still down" is
#: true on every polling cycle and the channel becomes noise that gets muted, which is a worse
#: outcome than not having it.
SESSION_DECLINE_STEPS = (3.0, 5.0, 8.0, 12.0)
BENCHMARK_DECLINE_STEPS = (2.0, 3.0, 5.0, 8.0)
PULLBACK_STEPS = (7.0, 12.0, 18.0, 25.0)
PULLBACK_SESSIONS = 5

#: SLOW DECLINE: first-to-last change over N trading sessions. The gap it closes is the 2026-10
#: AI selloff: NVDA lost 5.8% over five sessions without one day worse than -1.3%, so neither
#: the session ladder (-3%) nor the pullback ladder (-7%) fired. A steady bleed is what a holder
#: most needs told, because no single day looks like news (AUD-PRICERISK-SLOWBLEED).
#: -5% / 5 sessions is a STARTING value to evaluate in shadow, not a validated one; both are
#: overridable at runtime (see `price_risk_watch.load_config`).
SLOW_DECLINE_SESSIONS = 5
SLOW_DECLINE_STEPS = (5.0, 8.0, 12.0, 18.0)

#: A basket says something only when enough of it was actually observed. Breadth over three
#: stale members is not breadth.
#:
#: BREADTH_MIN_OBSERVED IS A CEILING, NOT A FLOOR, and that distinction was a real defect before
#: it was a comment. Written as a flat 5 it was UNREACHABLE: every maintained theme holds three
#: or four symbols, so 0 of 8 baskets could ever satisfy it and the rule was dead on arrival —
#: the same shape as this codebase's own BUG-SA33-UNREACHABLETHRESHOLD. Capping it at the
#: basket's own size keeps the intent (do not call two stale quotes "breadth") while leaving the
#: coverage floor to do the real work: at 80%, a four-member basket needs all four, because
#: three of four is 75%. `test_every_maintained_basket_can_actually_trigger` is what keeps this
#: honest when a basket is next resized.
BREADTH_MEMBER_DECLINE_PCT = 2.0
BREADTH_FRACTION = 0.60
BREADTH_MIN_OBSERVED = 5
BREADTH_MIN_COVERAGE = 0.80
#: Multi-session breadth: a member counts as declining when it is down this much over the
#: pullback window. The one-day rule above needs most of a basket down 2% ON THE SAME DAY, so a
#: sector grinding lower a percent a day never triggered it.
BASKET_PULLBACK_MEMBER_PCT = 4.0

#: A move that has recovered past this fraction of its worst point ends the episode, so the
#: next decline is reported as new rather than suppressed forever by a stale high-water mark.
EPISODE_RECOVERY_FRACTION = 0.5

UNMEASURABLE = "unmeasurable"


def _pct(from_value: float, to_value: float) -> float:
    return (to_value - from_value) / from_value * 100.0


def step_for(magnitude_pct: float, steps: tuple[float, ...]) -> int:
    """Highest ladder step this magnitude clears; 0 means it clears none.

    Returned as an INDEX rather than a threshold value so a caller comparing two observations
    compares positions on one ladder, not two floats whose meaning depends on which rule
    produced them.
    """
    cleared = 0
    for i, threshold in enumerate(steps, start=1):
        if magnitude_pct >= threshold:
            cleared = i
    return cleared


def session_move(bars: list[tuple[date, float]], opens: dict | None = None) -> dict | None:
    """Close-to-close change for the most recent session, split overnight vs regular where known.

    `opens` maps a session date to that session's OPEN. When the open is present the change is
    reported in two parts — previous close to open (overnight), and open to close (regular
    session) — because a gap that happened while the market was shut and a decline that happened
    during it are different events with different available responses. When it is absent the
    parts are None rather than inferred; a missing open is not a zero gap.
    """
    if len(bars) < 2:
        return None
    (_, prev_close), (as_of, close) = bars[-2], bars[-1]
    if prev_close <= 0:
        return None
    opening = (opens or {}).get(as_of)
    overnight = regular = None
    if opening is not None and opening > 0:
        overnight = _pct(prev_close, opening)
        regular = _pct(opening, close)
    return {"as_of": as_of, "from_date": bars[-2][0], "from_price": prev_close,
            "to_price": close, "change_pct": _pct(prev_close, close),
            "overnight_pct": overnight, "regular_session_pct": regular}


def pullback(bars: list[tuple[date, float]], sessions: int = PULLBACK_SESSIONS) -> dict | None:
    """Peak-close-to-latest-close decline over a trailing window of SESSIONS, not calendar days.

    Measured from the window's highest close rather than its first, because a stock that rose
    and then gave it all back has drawn down even though first-to-last is flat — and that is
    the shape somebody holding it experiences.
    """
    if len(bars) < 2:
        return None
    window = bars[-(sessions + 1):] if len(bars) > sessions else bars
    peak_date, peak = max(window, key=lambda b: b[1])
    as_of, close = window[-1]
    if peak <= 0 or peak_date == as_of:
        return None
    return {"as_of": as_of, "from_date": peak_date, "from_price": peak, "to_price": close,
            "change_pct": _pct(peak, close), "window_sessions": len(window) - 1}


def basket_breadth(member_moves: dict[str, float | None], member_count: int, *,
                   decline_pct: float = BREADTH_MEMBER_DECLINE_PCT,
                   fraction: float = BREADTH_FRACTION,
                   min_observed: int = BREADTH_MIN_OBSERVED,
                   min_coverage: float = BREADTH_MIN_COVERAGE) -> dict:
    """How much of a basket fell, WITH the coverage that makes the fraction readable.

    `member_moves` carries None for a member whose price could not be measured. Those are
    counted as unobserved, never as "did not fall" — treating an unmeasured member as a
    non-decliner silently biases every breadth reading downward, and the direction of that bias
    is invisible in the output. Coverage and sample size travel with the fraction for the same
    reason.
    """
    observed = {s: v for s, v in member_moves.items() if v is not None}
    declined = [s for s, v in observed.items() if v <= -abs(decline_pct)]
    coverage = (len(observed) / member_count) if member_count else 0.0
    share = (len(declined) / len(observed)) if observed else 0.0
    required = min(min_observed, member_count) if member_count else min_observed
    sufficient = len(observed) >= required and coverage >= min_coverage
    return {
        "member_count": member_count, "observed_count": len(observed),
        "declined_count": len(declined), "declined_symbols": sorted(declined),
        "coverage": round(coverage, 4), "declined_share": round(share, 4),
        "threshold_pct": decline_pct, "sufficient_evidence": sufficient,
        "triggered": bool(sufficient and share >= fraction),
        "insufficient_reason": None if sufficient else (
            f"observed {len(observed)} of {member_count} members "
            f"({coverage:.0%} coverage); need {required} and {min_coverage:.0%}"),
    }


def window_change(bars: list[tuple[date, float]], sessions: int = SLOW_DECLINE_SESSIONS) -> float | None:
    """First-to-last change over the trailing window of SESSIONS; the basket-level slow move."""
    if len(bars) < 2:
        return None
    window = bars[-(sessions + 1):] if len(bars) > sessions else bars
    start = window[0][1]
    return None if start <= 0 else _pct(start, window[-1][1])


def dividend_adjust(bars: list[tuple[date, float]], dividends: list[dict]):
    """Add cash dividends back so an ex-dividend drop is not reported as a decline.

    Returns `(bars, applied, unknown)`. Standard back-adjustment: every close BEFORE an ex-date
    is scaled by `1 - amount / close_before_ex`, so the series measures what a holder lost
    rather than the mechanical drop when the dividend leaves the share price. `unknown` lists
    ex-dates whose amount was not recorded — those cannot be adjusted, and the caller must not
    present a move across them as a measured decline.
    """
    applied, unknown = [], []
    adjusted = list(bars)
    for div in sorted(dividends or [], key=lambda d: d["ex_date"]):
        ex = div["ex_date"]
        before = [c for d, c in bars if d < ex]
        if not before or not any(d >= ex for d, _ in bars):
            continue  # ex-date outside the bars: nothing in this series straddles it
        amount = div.get("cash_amount")
        if not amount or amount <= 0:
            unknown.append(ex.isoformat())
            continue
        factor = 1.0 - float(amount) / before[-1]
        if factor <= 0:
            unknown.append(ex.isoformat())
            continue
        adjusted = [(d, c * factor) if d < ex else (d, c) for d, c in adjusted]
        applied.append({"ex_date": ex.isoformat(), "cash_amount": float(amount),
                        "pct_of_price": round(float(amount) / before[-1] * 100, 3)})
    return adjusted, applied, unknown


def event_key(scope: str, subject: str, rule: str, as_of: date, step: int = 0,
              mode: str = "shadow") -> str:
    """Identity for one detection. Two runs of the same session at the same step collide.

    Scoped to the SESSION rather than the wall clock, so a job that runs several times a day
    cannot mint a new event for a move it already reported — the duplicate is refused by a
    unique constraint rather than by remembering to check.

    STEP is part of the key because the job now runs intraday: a -3% morning that becomes -6%
    by the afternoon is an escalation inside one session, and a session-only key refused it as
    a duplicate. MODE is part of it so a live run can report an episode that a shadow run had
    already recorded — the shadow row proves detection, not that anybody was told.
    """
    return f"risk:{scope}:{subject}:{rule}:{as_of.isoformat()}:s{int(step)}:{mode}"


def episode_closed(prior_evidence: dict | None, closes_since: list[float]) -> bool:
    """Has the price recovered enough since the prior event to end that episode?

    Measured from PRICES, not from a flag. The flag this replaced (`episode_closed` in the
    event's evidence) was read but never written, so a decline -> recovery -> second decline
    was suppressed as "the same episode" forever — the exact case `should_emit` documents.

    Recovered means some close after the prior event regained `EPISODE_RECOVERY_FRACTION` of
    that event's drop. Without the endpoint prices the answer is False: an episode that cannot
    be shown to have ended is not assumed to have ended.
    """
    ev = prior_evidence or {}
    try:
        top, bottom = float(ev["from_price"]), float(ev["to_price"])
    except (KeyError, TypeError, ValueError):
        return False
    if top <= bottom:
        return False
    line = bottom + (top - bottom) * EPISODE_RECOVERY_FRACTION
    return any(c >= line for c in closes_since)


def should_emit(prior: dict | None, candidate: dict) -> tuple[bool, str]:
    """Report this, or stay quiet? The whole anti-noise rule, in one place.

    Three cases, and the third is the one that is usually missed:
      * nothing seen before -> report;
      * a HIGHER ladder step than last time -> report, because the situation got worse;
      * recovered past `EPISODE_RECOVERY_FRACTION` of the prior worst and fallen again -> report
        as a NEW episode. Without this, a stock that drops 9%, recovers, and drops 9% again is
        silent the second time, because a high-water mark alone never resets.

    Anything else is the same episode at the same or lesser severity, which is "still true", not
    news.
    """
    if prior is None:
        return True, "first observation of this episode"
    prior_step = int(prior.get("step") or 0)
    worst = abs(float(prior.get("worst_pct") or 0.0))
    step = int(candidate.get("step") or 0)
    now = abs(float(candidate.get("change_pct") or 0.0))
    # Checked FIRST: once the prior episode has ended, its ladder position is irrelevant. It was
    # checked last, behind the "same episode" return, so a closed episode could never reopen.
    if prior.get("episode_closed") and step > 0:
        return True, "new episode after recovery"
    if step > prior_step:
        return True, f"escalated from step {prior_step} to {step}"
    if worst > 0 and now <= worst * (1.0 - EPISODE_RECOVERY_FRACTION):
        return False, "recovering; not yet a new decline"
    return False, f"same episode, step {step} not above {prior_step}"


def slow_decline(bars: list[tuple[date, float]], sessions: int = SLOW_DECLINE_SESSIONS) -> dict | None:
    """First-to-last change over the trailing `sessions` trading sessions."""
    if len(bars) <= sessions:
        return None
    window = bars[-(sessions + 1):]
    (from_date, start), (as_of, close) = window[0], window[-1]
    if start <= 0:
        return None
    return {"as_of": as_of, "from_date": from_date, "from_price": start, "to_price": close,
            "change_pct": _pct(start, close), "window_sessions": sessions}


def evaluate(subject: str, scope: str, bars: list[tuple[date, float]], verdict: dict, *,
             opens: dict | None = None, steps: tuple[float, ...] | None = None,
             slow_sessions: int = SLOW_DECLINE_SESSIONS,
             slow_steps: tuple[float, ...] = SLOW_DECLINE_STEPS) -> list[dict]:
    """Every candidate for one subject, or one record saying why it could not be measured.

    AN UNMEASURABLE SUBJECT PRODUCES A RECORD, NOT SILENCE. A corporate action inside the
    window that cannot be applied makes the number wrong — a 2:1 split reads as a 50% crash —
    so no event may be raised from it. But reporting nothing would be indistinguishable from
    "nothing happened", and this codebase has repeatedly found that an absence nobody can
    read is how a broken feed looks exactly like a quiet market.
    """
    ladder = steps or (BENCHMARK_DECLINE_STEPS if scope in ("benchmark", "sector")
                       else SESSION_DECLINE_STEPS)
    if not verdict.get("usable"):
        return [{"scope": scope, "subject": subject, "rule": UNMEASURABLE, "step": 0,
                 "evidence_status": verdict.get("evidence_status"),
                 # WHY, in a form a reader can act on: which limit bit, what the best possible
                 # status is for this source however fresh it gets, and how far the evidence
                 # actually reaches compared with the window that was asked about.
                 "limiting_factor": verdict.get("limiting_factor"),
                 "best_achievable": verdict.get("best_achievable"),
                 "coverage_evidenced_to": verdict.get("coverage_evidenced_to"),
                 "window_end": verdict.get("window_end"),
                 "reason": verdict.get("reason"), "as_of": bars[-1][0] if bars else None}]

    out = []
    for rule, measured, rung in (("session_decline", session_move(bars, opens), ladder),
                                 ("pullback", pullback(bars), PULLBACK_STEPS),
                                 ("slow_decline", slow_decline(bars, slow_sessions), slow_steps)):
        if not measured or measured["change_pct"] >= 0:
            continue
        step = step_for(abs(measured["change_pct"]), rung)
        if step == 0:
            continue
        out.append({**measured, "scope": scope, "subject": subject, "rule": rule, "step": step,
                    "direction": "down", "ladder": list(rung),
                    "basis": verdict.get("basis"),
                    "evidence_status": verdict.get("evidence_status"),
                    "reason": verdict.get("reason")})
    # A pullback is measured from the window's PEAK, a slow decline from its FIRST bar, and the
    # peak is never below the first bar — so whenever a pullback clears its rung it already
    # reports this move at least as large. Emitting both put the same move in a digest twice
    # (AMD, MU, TSM in the 2026-10-13 production dry run). The slow rule exists for the bleed
    # the pullback ladder misses, and that is the only case it now reports.
    if any(c["rule"] == "pullback" for c in out):
        out = [c for c in out if c["rule"] != "slow_decline"]
    return out
