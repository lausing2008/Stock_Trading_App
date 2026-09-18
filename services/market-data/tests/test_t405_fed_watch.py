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
