"""AQ05 — which stored signal outcomes may enter the displayed confidence calibration.

MEASURED IN PRODUCTION (2026-10-10): of 21,520 resolved outcomes in the trailing 180 days,
**17 have `entry_date = signal_date`** — entered on the signal's own day, under an evaluator
whose stated convention is entry at the NEXT session's close. They are few, and the rates barely
move (GROWTH/BUY 0.4004 -> 0.3998), but a calibration that silently includes them is not
measuring the convention it claims.

FOUR SEPARATE PROBLEMS, and only the first is about size:

  * TIMING — same-day entries under a T+1 convention measure a trade nobody could have placed.
  * CLUSTERING — the only floor was 30 rows. Thirty outcomes from two market days describe
    those two days, exactly as the options flow calibration learned (AUD-OPT6).
  * SILENT FALLBACK — `_calibrated_win_rate` drops from a market-specific bucket to a pooled
    one and returns the same shape, so a reader cannot tell which they were shown.
  * VERSION POOLING — outcomes from different model and rule generations are averaged together.
    `SignalOutcome` stores no model or policy version, so this module CANNOT repair it; it is
    reported as a standing limitation rather than silently left out.

PURE. The calibration and its tests execute this same code.
"""
from __future__ import annotations

ELIGIBLE = "eligible"
ENTERED_SAME_DAY = "entered_same_day"
ENTERED_BEFORE_SIGNAL = "entered_before_signal"
NO_ENTRY_DATE = "no_entry_date"

EXCLUDED = (ENTERED_SAME_DAY, ENTERED_BEFORE_SIGNAL, NO_ENTRY_DATE)

EXCLUSION_REASON = {
    ENTERED_SAME_DAY:
        "entered on the signal's own day, while the evaluator's convention is entry at the "
        "next session — this measures a trade the convention does not describe",
    ENTERED_BEFORE_SIGNAL:
        "entry precedes the signal, which cannot be a trade taken on it",
    NO_ENTRY_DATE:
        "no entry date is stored, so the timing convention cannot be established",
}

#: Floors for PUBLICATION. A rate below either is withheld, and the counts are kept.
MIN_ELIGIBLE_OUTCOMES = 30
MIN_DISTINCT_DATES = 5

#: What this calibration still cannot separate. Published beside every rate rather than
#: discovered later by a reader who assumed otherwise.
STANDING_LIMITATIONS = (
    "Outcomes from different model and rule generations are pooled: `signal_outcomes` stores "
    "no model or policy version, so a rate here spans every generation active in the window.",
    "A measured hit rate over the underlying is not a calibrated probability, and says nothing "
    "about the return after costs.",
)


def classify(signal_date, entry_date) -> str:
    """Timing eligibility of one stored outcome for a next-session-entry calibration."""
    if entry_date is None:
        return NO_ENTRY_DATE
    if signal_date is None:
        return ELIGIBLE          # nothing to contradict; the signal date is the thing missing
    if entry_date == signal_date:
        return ENTERED_SAME_DAY
    if entry_date < signal_date:
        return ENTERED_BEFORE_SIGNAL
    return ELIGIBLE


def partition(rows) -> dict:
    """Split outcomes into the eligible cohort and the exclusions, by reason."""
    def _get(r, k):
        return r.get(k) if hasattr(r, "get") else getattr(r, k, None)

    eligible, excluded = [], {}
    for r in rows:
        verdict = classify(_get(r, "signal_date"), _get(r, "entry_date"))
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


def publication_status(eligible_rows) -> dict:
    """May a rate be published for this cohort, and if not, what is missing?

    A count floor alone cannot see clustering: thirty outcomes from two market days describe
    those two days. The distinct-date floor is the same correction the options flow calibration
    already carries.
    """
    def _get(r, k):
        return r.get(k) if hasattr(r, "get") else getattr(r, k, None)

    n = len(eligible_rows)
    dates = {_get(r, "signal_date") for r in eligible_rows if _get(r, "signal_date") is not None}
    if n < MIN_ELIGIBLE_OUTCOMES:
        status, missing = "insufficient_eligible_history", (
            f"{n} eligible outcome(s); {MIN_ELIGIBLE_OUTCOMES} required")
    elif len(dates) < MIN_DISTINCT_DATES:
        status, missing = "clustered_dates", (
            f"{len(dates)} distinct signal date(s); {MIN_DISTINCT_DATES} required — a rate over "
            f"this few days describes those days, not the signal")
    else:
        status, missing = "measured", None
    return {"status": status, "eligible_count": n, "distinct_dates": len(dates),
            "required_eligible_outcomes": MIN_ELIGIBLE_OUTCOMES,
            "required_distinct_dates": MIN_DISTINCT_DATES,
            "missing_requirement": missing,
            "limitations": list(STANDING_LIMITATIONS)}
