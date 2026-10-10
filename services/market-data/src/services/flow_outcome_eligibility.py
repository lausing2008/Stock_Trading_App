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

#: VERSIONED. Immutable input facts alone cannot reproduce an old result once the RULES change
#: — a row re-partitioned under a later rule set yields a different verdict from the same
#: expiry and dates. Anything that publishes a filtered cohort must record this alongside it, so
#: an earlier figure can be re-derived by saying which rules produced it.
#:
#:   v1  expiry vs fired_date and entry_date, dates only
#:   v2  last TRADABLE session vs the modelled entry; `timing_unknown` where entry is unknown
ELIGIBILITY_VERSION = "flow-elig-2"
MIN_ELIGIBLE_OUTCOMES = 30
MIN_DISTINCT_DATES = 5

ELIGIBLE = "eligible"
EXPIRED_BEFORE_ALERT = "expired_before_alert"
EXPIRED_BEFORE_ENTRY = "expired_before_entry"
SAME_DAY_EXPIRY = "same_day_expiry"
NO_EXPIRY_RECORDED = "no_expiry_recorded"
TIMING_UNKNOWN = "timing_unknown"

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
    TIMING_UNKNOWN:
        "no entry date is stored, so whether the contract was still tradable at the modelled "
        "entry cannot be established — unknown, not assumed fine",
}

#: Everything that is not `eligible`. Named so a caller cannot forget one.
EXCLUDED = (EXPIRED_BEFORE_ALERT, EXPIRED_BEFORE_ENTRY, SAME_DAY_EXPIRY, NO_EXPIRY_RECORDED,
            TIMING_UNKNOWN)


def last_tradable_session(expiry: date, *, is_trading_day=None) -> date:
    """The last session on which the contract could actually be traded.

    AN EXPIRY DATE IS NOT A TRADING INSTANT. A Friday alert on a contract dated Saturday passes
    "expiry is after the alert" and "expiry is after Monday's entry" is false — but only if the
    comparison uses the right date. Historically US monthly equity options carried a SATURDAY
    expiration and stopped trading the preceding Friday, and any expiry landing on a weekend or
    holiday is untradable on its own stated date.

    So the comparison walks back to the nearest session at or before the expiry. Where no
    calendar is supplied this falls back to the stated date, which is the status quo and is why
    every production caller passes one.
    """
    from datetime import timedelta
    if is_trading_day is None:
        return expiry
    d, guard = expiry, 0
    while guard < 10 and not is_trading_day("US", _noon(d)):
        d -= timedelta(days=1)
        guard += 1
    return d


def _noon(d: date):
    """Midday UTC, because `is_trading_day` resolves an instant into the venue's local calendar
    and a midnight value lands on the previous local day."""
    from datetime import datetime, timezone
    return datetime(d.year, d.month, d.day, 12, tzinfo=timezone.utc)


def classify(expiry: date | None, fired_date: date | None,
             entry_date: date | None = None, *, is_trading_day=None) -> str:
    """Eligibility of one stored outcome for a NEXT-SESSION-ENTRY calibration.

    Order matters: a contract that expired before the alert also expired before entry, and the
    earlier, more specific fact is the one worth reporting.

    THE COMPARISON IS AGAINST THE LAST TRADABLE SESSION, not the stated expiry date. A Friday
    alert on a Saturday-dated contract clears "expiry is after the alert" and would clear a
    naive entry check too, while the contract in fact stopped trading before the modelled entry.

    AND AN UNKNOWN ENTRY IS NOT A PASS. Where no entry date is stored the evaluator has not
    placed the trade yet, so whether the contract was still tradable then is UNKNOWN — reported
    as `timing_unknown` rather than falling through to eligible, which is what the first version
    of this did.
    """
    if expiry is None:
        return NO_EXPIRY_RECORDED
    tradable_to = last_tradable_session(expiry, is_trading_day=is_trading_day)
    if fired_date is not None:
        if tradable_to < fired_date:
            return EXPIRED_BEFORE_ALERT
        if tradable_to == fired_date:
            return SAME_DAY_EXPIRY
    if entry_date is None:
        return TIMING_UNKNOWN
    if tradable_to < entry_date:
        return EXPIRED_BEFORE_ENTRY
    return ELIGIBLE


def partition(rows, *, is_trading_day=None) -> dict:
    """Split stored outcomes into the eligible cohort and the exclusions, by reason.

    `rows` are objects or mappings carrying `expiry`, `fired_date`, `entry_date` and the hit
    flag `is_correct`. Returns the counts a completion report needs — ORIGINAL, ELIGIBLE and
    exclusions by reason — never just the survivors.
    """
    def _get(r, k):
        return r.get(k) if hasattr(r, "get") else getattr(r, k, None)

    eligible, excluded = [], {}
    for r in rows:
        verdict = classify(_get(r, "expiry"), _get(r, "fired_date"), _get(r, "entry_date"),
                           is_trading_day=is_trading_day)
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
        # WHICH RULES PRODUCED THIS SPLIT. Without it an earlier published cohort cannot be
        # re-derived once the rules change, however immutable its inputs are.
        "eligibility_version": ELIGIBILITY_VERSION,
    }


def cohort_measurement_status(partitioned: dict) -> dict:
    """One publication status for every consumer of the eligible cohort."""
    eligible = partitioned["eligible"]
    distinct_dates = len({_get_value(row, "fired_date") for row in eligible
                          if _get_value(row, "fired_date") is not None})
    if len(eligible) < MIN_ELIGIBLE_OUTCOMES:
        status = "insufficient_eligible_history"
    elif distinct_dates < MIN_DISTINCT_DATES:
        status = "clustered_dates"
    else:
        status = "measured"
    return {
        "status": status,
        "distinct_dates": distinct_dates,
        "required_eligible_outcomes": MIN_ELIGIBLE_OUTCOMES,
        "required_distinct_dates": MIN_DISTINCT_DATES,
    }


def _get_value(row, key):
    return row.get(key) if hasattr(row, "get") else getattr(row, key, None)
