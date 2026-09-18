"""T405-FEDWATCH — the CME decomposition that turns Fed Funds futures into FOMC odds.

services/fed_watch.py is pure, so these are real behavioural tests. The arithmetic IS the
product: a wrong probability here is not a cosmetic bug, it is a confidently-stated wrong claim
about what the market expects the Fed to do.
"""
from datetime import date

import pytest

from src.services.fed_watch import (build_meeting_path, contract_symbol, implied_rate,
                                    probabilities, rate_after_meeting)


def test_contract_symbols_use_cbot_month_codes():
    assert contract_symbol(2026, 12) == "ZQZ26.CBT"
    assert contract_symbol(2026, 10) == "ZQV26.CBT"
    assert contract_symbol(2027, 1) == "ZQF27.CBT"


def test_implied_rate_is_100_minus_price():
    assert implied_rate(96.1050) == 3.895


def test_a_flat_month_implies_no_move():
    """The control case. If the contract prices the same rate all month, the decomposition must
    return exactly that rate — not a rounding artefact that looks like a tiny cut."""
    assert rate_after_meeting(implied_avg=4.00, rate_before=4.00,
                              meeting_day=15, days_in_month=30) == 4.00


def test_a_fully_priced_25bp_cut_decomposes_exactly():
    """15 days at 4.00 then 15 days at 3.75 averages 3.875. Given that average, the solver must
    recover 3.75."""
    after = rate_after_meeting(implied_avg=3.875, rate_before=4.00, meeting_day=15, days_in_month=30)
    assert after == 3.75
    p = probabilities(rate_before=4.00, rate_after=after)
    assert p["expected_change_bp"] == -25.0
    assert p["most_likely"] == "Cut 25bp"
    assert p["outcomes"][0]["probability_pct"] == 100.0


def test_a_half_priced_cut_is_a_coin_flip_not_a_12bp_forecast():
    """THE central interpretation. The Fed cannot cut 12.5bp — no such move exists. A -12.5bp
    expectation is the market blending a 25bp cut with no change, 50/50. Reporting it as 'the
    market expects a 12.5bp cut' would be nonsense."""
    after = rate_after_meeting(implied_avg=3.9375, rate_before=4.00, meeting_day=15, days_in_month=30)
    p = probabilities(rate_before=4.00, rate_after=after)
    labels = {o["label"]: o["probability_pct"] for o in p["outcomes"]}
    assert labels == {"No change": 50.0, "Cut 25bp": 50.0}
    assert p["probability_of_any_move_pct"] == 50.0


def test_moves_larger_than_one_increment_blend_the_two_nearest_steps():
    p = probabilities(rate_before=4.00, rate_after=3.70)   # -30bp
    labels = {o["label"]: o["probability_pct"] for o in p["outcomes"]}
    assert set(labels) == {"Cut 25bp", "Cut 50bp"}
    assert labels["Cut 25bp"] == pytest.approx(80.0, abs=0.1)
    assert labels["Cut 50bp"] == pytest.approx(20.0, abs=0.1)


@pytest.mark.parametrize("change", [0.0, -0.10, -0.25, -0.30, 0.13, 0.55, -0.60])
def test_probabilities_always_sum_to_100(change):
    p = probabilities(rate_before=4.00, rate_after=4.00 + change)
    assert sum(o["probability_pct"] for o in p["outcomes"]) == pytest.approx(100.0, abs=0.2)


def test_hikes_are_labelled_as_hikes():
    p = probabilities(rate_before=4.00, rate_after=4.25)
    assert p["direction"] == "hike" and p["most_likely"] == "Hike 25bp"


def test_meeting_on_the_final_day_of_the_month_yields_no_inference():
    """Zero days of the new rate fall inside the contract, so the month's average says nothing
    about it. That must be an explicit absence, never an approximation."""
    assert rate_after_meeting(implied_avg=3.9, rate_before=4.0,
                              meeting_day=31, days_in_month=31) is None


def test_path_chains_each_meeting_from_the_previous_one():
    """Cumulative, not independent. If every meeting restarted from today's rate, a second
    meeting would re-price the first one's cut and double count it."""
    meetings = [date(2026, 10, 28), date(2026, 12, 9)]
    prices = {"ZQV26.CBT": 96.05, "ZQZ26.CBT": 96.30}
    path = build_meeting_path(meetings=meetings, prices=prices, current_rate=3.95)
    assert len(path) == 2 and all(r["available"] for r in path)
    assert path[0]["rate_before_pct"] == 3.95
    assert path[1]["rate_before_pct"] == path[0]["rate_after_pct"], "must chain, not restart"


def test_a_missing_contract_is_reported_not_skipped():
    """Silently shortening the path would make a data gap look like a shorter Fed calendar."""
    meetings = [date(2026, 10, 28), date(2026, 12, 9)]
    path = build_meeting_path(meetings=meetings, prices={"ZQV26.CBT": 96.05}, current_rate=3.95)
    assert len(path) == 2
    assert path[1]["available"] is False and path[1]["reason"] == "no_futures_quote"


def test_a_late_month_meeting_is_flagged_low_precision():
    """One or two days of new rate has to carry the whole inference, so a small quote error is
    amplified enormously. The number is still shown — flagged, not hidden."""
    path = build_meeting_path(meetings=[date(2026, 10, 29)],
                              prices={"ZQV26.CBT": 96.05}, current_rate=3.95)
    assert path[0]["low_precision"] is True
    assert path[0]["days_after_meeting"] == 2


# ── The amplification bug, and the method that fixes it ──────────────────────────────────

# Real ZQ closes measured 2026-09-18. The curve is smooth and entirely sensible — a gradual
# rise from 3.895% to 4.46% across seven months — which is exactly what makes it a good
# regression fixture: any absurd output from THESE inputs is the arithmetic's fault, not the
# market's.
_REAL_PRICES = {
    "ZQV26.CBT": 96.1050,   # Oct 2026 -> 3.895%
    "ZQX26.CBT": 95.9000,   # Nov 2026 -> 4.100%  (no FOMC meeting in November)
    "ZQZ26.CBT": 95.8450,   # Dec 2026 -> 4.155%
    "ZQF27.CBT": 95.7850,   # Jan 2027 -> 4.215%
    "ZQG27.CBT": 95.7000,   # Feb 2027 -> 4.300%  (no FOMC meeting in February)
    "ZQH27.CBT": 95.6300,   # Mar 2027 -> 4.370%
    "ZQJ27.CBT": 95.5400,   # Apr 2027 -> 4.460%
}
_REAL_MEETINGS = [date(2026, 10, 28), date(2026, 12, 9), date(2027, 1, 27), date(2027, 3, 17)]


def test_no_meeting_implies_an_absurd_move_from_a_smooth_curve():
    """THE REGRESSION. The first live run reported 'Hike 100bp, 85.3%' for March 2027 from
    precisely these prices.

    Cause: meetings at Oct-28-of-31 and Jan-27-of-31 left the within-month solver dividing by
    3 and 4 days, so ordinary pricing differences exploded — and each distorted result became
    the next meeting's rate_before, compounding down the chain.

    The futures curve moves ~57bp across seven months. No single meeting can honestly imply
    100bp out of that."""
    path = build_meeting_path(meetings=_REAL_MEETINGS, prices=_REAL_PRICES, current_rate=3.895)
    for r in path:
        if r["available"]:
            assert abs(r["expected_change_bp"]) <= 50, (
                f"{r['meeting_date']} implies {r['expected_change_bp']}bp — "
                f"more than two increments from a curve that only moves ~57bp in total"
            )


def test_the_next_month_method_is_preferred_when_it_is_available():
    """November 2026 and February 2027 contain no FOMC meeting, so the post-meeting rate is in
    effect for every day of them and can be read straight off the contract — no division, so
    nothing to amplify."""
    path = build_meeting_path(meetings=_REAL_MEETINGS, prices=_REAL_PRICES, current_rate=3.895)
    by_date = {r["meeting_date"]: r for r in path}
    assert by_date["2026-10-28"]["method"] == "next_month_average"
    assert by_date["2026-10-28"]["contract_used"] == "ZQX26.CBT"
    assert by_date["2027-01-27"]["method"] == "next_month_average"
    assert by_date["2027-01-27"]["contract_used"] == "ZQG27.CBT"


def test_the_next_month_method_reads_the_rate_directly_off_the_contract():
    """November implies 4.100%, so that IS the rate after the October meeting."""
    path = build_meeting_path(meetings=[date(2026, 10, 28)], prices=_REAL_PRICES, current_rate=3.895)
    assert path[0]["rate_after_pct"] == pytest.approx(4.100, abs=0.0001)
    assert path[0]["low_precision"] is False, "reading a contract directly cannot be imprecise"


def test_within_month_fallback_is_still_used_and_still_flagged():
    """When the following month DOES hold a meeting, the fallback is the only option — and a
    late-month meeting using it must stay flagged, because that is the amplifying path."""
    path = build_meeting_path(
        meetings=[date(2026, 10, 28), date(2026, 11, 20)],   # November now has a meeting
        prices=_REAL_PRICES, current_rate=3.895)
    oct_row = path[0]
    assert oct_row["method"] == "within_month_decomposition"
    assert oct_row["low_precision"] is True, "3 days of inference must be flagged"
