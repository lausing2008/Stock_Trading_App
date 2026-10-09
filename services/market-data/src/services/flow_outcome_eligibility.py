"""O03 — which stored flow-alert outcomes may enter a calibration, and why.

THE DEFECT (2026-10-08 options review, measured). `_build_options_flow_alert_calibration`
selected every non-null 10-day hit flag for a direction with no contract-validity filter at all.
A follow-up query found **1,102 of 1,998 selected rows (55.16%) were contracts that had ALREADY
EXPIRED before the alert fired** — 516 of 983 bullish, 586 of 1,015 bearish. Recent captures are
clean; that does not repair the history the calibration is computed over.

THREE DISTINCT STATES, and collapsing them is the error:

  * EXPIRED BEFORE THE ALERT — the contract was dead when the alert fired. There was never a
    trade to measure. Invalid, not merely unmeasurable.
  * SAME-DAY EXPIRY — legitimate intraday research: the contract existed and traded on the
    alert day. But the evaluator enters at the NEXT session's open, by which time the contract
    no longer exists, so it cannot measure THIS trade. Excluded from a next-day-entry
    calibration without being called invalid.
  * EXPIRED BEFORE ENTRY (but after the alert) — same problem, one day wider.

PURE, and module-level, so the calibration and its tests execute the same rule. Two sabotage
runs in this session caught tests that re-implemented a rule they were checking.
"""
from __future__ import annotations

from datetime import date

ELIGIBLE = "eligible"
EXPIRED_BEFORE_ALERT = "expired_before_alert"
EXPIRED_BEFORE_ENTRY = "expired_before_entry"
SAME_DAY_EXPIRY = "same_day_expiry"
NO_EXPIRY_RECORDED = "no_expiry_recorded"

#: Why each exclusion exists, in a reader's words. Served beside any count so an excluded
#: cohort can be understood rather than merely subtracted.
EXCLUSION_REASON = {
    EXPIRED_BEFORE_ALERT:
        "the contract had already expired when the alert fired — there was never a trade here "
        "to measure",
    EXPIRED_BEFORE_ENTRY:
        "the contract expired between the alert and the next-session entry this evaluator "
        "assumes, so the measured move is of the underlying only",
    SAME_DAY_EXPIRY:
        "the contract expired on the alert day. That can be legitimate intraday research, but "
        "an evaluator entering at the next session cannot measure THIS trade",
    NO_EXPIRY_RECORDED:
        "no expiry is stored, so contract validity cannot be established either way",
}

#: Everything that is not `eligible`. Named so a caller cannot forget one.
EXCLUDED = (EXPIRED_BEFORE_ALERT, EXPIRED_BEFORE_ENTRY, SAME_DAY_EXPIRY, NO_EXPIRY_RECORDED)


def classify(expiry: date | None, fired_date: date | None,
             entry_date: date | None = None) -> str:
    """Eligibility of one stored outcome for a NEXT-DAY-ENTRY calibration.

    Order matters: a contract that expired before the alert also expired before entry, and the
    earlier, more specific fact is the one worth reporting.
    """
    if expiry is None:
        return NO_EXPIRY_RECORDED
    if fired_date is not None:
        if expiry < fired_date:
            return EXPIRED_BEFORE_ALERT
        if expiry == fired_date:
            return SAME_DAY_EXPIRY
    if entry_date is not None and expiry < entry_date:
        return EXPIRED_BEFORE_ENTRY
    return ELIGIBLE


def partition(rows) -> dict:
    """Split stored outcomes into the eligible cohort and the exclusions, by reason.

    `rows` are objects or mappings carrying `expiry`, `fired_date`, `entry_date` and the hit
    flag `is_correct`. Returns the counts a completion report needs — ORIGINAL, ELIGIBLE and
    exclusions by reason — never just the survivors.
    """
    def _get(r, k):
        return r.get(k) if hasattr(r, "get") else getattr(r, k, None)

    eligible, excluded = [], {}
    for r in rows:
        verdict = classify(_get(r, "expiry"), _get(r, "fired_date"), _get(r, "entry_date"))
        if verdict == ELIGIBLE:
            eligible.append(r)
        else:
            excluded.setdefault(verdict, []).append(r)
    return {
        "original_count": len(rows),
        "eligible": eligible,
        "eligible_count": len(eligible),
        "excluded_counts": {k: len(v) for k, v in sorted(excluded.items())},
        "excluded_total": sum(len(v) for v in excluded.values()),
        "exclusion_reasons": {k: EXCLUSION_REASON[k] for k in sorted(excluded)},
    }
