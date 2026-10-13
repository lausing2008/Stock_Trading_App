"""The price-risk rules themselves, run directly. No database, no stand-ins.

`price_risk` is pure for this reason: the comparable logic elsewhere in this codebase was only
reachable through an injected lambda that implemented a different rule, and the suite was green
on behaviour the live caller could not produce. Nothing here is injected.
"""
from datetime import date, timedelta

from src.services import price_risk as pr


def _series(values, start=date(2026, 10, 1)):
    """Ascending (date, close) bars, one per calendar day — sessions are the unit, not dates."""
    return [(start + timedelta(days=i), float(v)) for i, v in enumerate(values)]


USABLE = {"usable": True, "basis": "split_adjusted_price", "evidence_status": "verified",
          "reason": "source documents complete action history"}


# ── the ladder ───────────────────────────────────────────────────────────────

def test_a_move_is_reported_at_the_highest_step_it_clears():
    assert pr.step_for(2.9, pr.SESSION_DECLINE_STEPS) == 0
    assert pr.step_for(3.0, pr.SESSION_DECLINE_STEPS) == 1
    assert pr.step_for(7.9, pr.SESSION_DECLINE_STEPS) == 2
    assert pr.step_for(40.0, pr.SESSION_DECLINE_STEPS) == len(pr.SESSION_DECLINE_STEPS)


def test_a_benchmark_uses_a_lower_ladder_than_a_single_stock():
    """A 2% index move and a 2% single-stock move are not the same event."""
    assert pr.step_for(2.0, pr.BENCHMARK_DECLINE_STEPS) == 1
    assert pr.step_for(2.0, pr.SESSION_DECLINE_STEPS) == 0


# ── what actually gets measured ──────────────────────────────────────────────

def test_the_session_move_is_close_to_close():
    move = pr.session_move(_series([100, 95]))
    assert move["change_pct"] == -5.0
    assert move["from_price"] == 100 and move["to_price"] == 95


def test_overnight_and_regular_session_are_reported_separately_when_the_open_is_known():
    """A gap that happened while the market was shut and a decline during it are different
    events with different available responses."""
    bars = _series([100, 95])
    move = pr.session_move(bars, opens={bars[-1][0]: 97.0})
    assert round(move["overnight_pct"], 4) == -3.0
    assert round(move["regular_session_pct"], 4) == round((95 - 97) / 97 * 100, 4)


def test_a_missing_open_is_not_reported_as_a_zero_gap():
    move = pr.session_move(_series([100, 95]), opens={})
    assert move["overnight_pct"] is None and move["regular_session_pct"] is None


def test_a_pullback_is_measured_from_the_window_peak_not_its_first_bar():
    """A stock that rose then gave it all back HAS drawn down, which first-to-last misses."""
    bars = _series([100, 110, 108, 104, 99, 96])
    out = pr.pullback(bars, sessions=5)
    assert out["from_price"] == 110.0
    assert round(out["change_pct"], 4) == round((96 - 110) / 110 * 100, 4)
    flat = pr.pullback(_series([100, 110, 100]), sessions=5)
    assert flat["change_pct"] < 0, "first-to-last is flat; the drawdown is real"


# ── breadth, and the bias it refuses to hide ─────────────────────────────────

def test_breadth_counts_unmeasured_members_as_unobserved_not_as_non_decliners():
    moves = {"A": -3.0, "B": -3.0, "C": None, "D": None, "E": None}
    out = pr.basket_breadth(moves, member_count=5)
    assert out["observed_count"] == 2 and out["declined_count"] == 2
    assert out["declined_share"] == 1.0, "share is of OBSERVED members"
    assert out["sufficient_evidence"] is False
    assert out["triggered"] is False, "100% of two members is not breadth"
    assert "coverage" in out["insufficient_reason"]


def test_breadth_triggers_only_with_enough_coverage_and_sample():
    moves = {s: -3.0 for s in "ABCD"} | {s: 1.0 for s in "EF"}
    out = pr.basket_breadth(moves, member_count=6)
    assert out["observed_count"] == 6 and out["coverage"] == 1.0
    assert out["declined_share"] == round(4 / 6, 4)
    assert out["triggered"] is True


def test_breadth_does_not_trigger_on_a_minority():
    moves = {s: -3.0 for s in "AB"} | {s: 1.0 for s in "CDEF"}
    assert pr.basket_breadth(moves, member_count=6)["triggered"] is False


# ── the anti-noise rule ──────────────────────────────────────────────────────

def test_a_first_observation_is_reported():
    emit, why = pr.should_emit(None, {"step": 1, "change_pct": -3.5})
    assert emit and "first" in why


def test_the_same_episode_at_the_same_severity_is_not_repeated():
    """'Still down' is true on every polling cycle; it is not news."""
    emit, why = pr.should_emit({"step": 2, "worst_pct": 6.0},
                               {"step": 2, "change_pct": -6.1})
    assert emit is False and "same episode" in why


def test_an_escalation_is_reported():
    emit, why = pr.should_emit({"step": 1, "worst_pct": 3.5},
                               {"step": 3, "change_pct": -9.0})
    assert emit and "escalated" in why


def test_a_recovery_closes_the_episode_so_the_next_decline_is_new():
    """Without this, a stock that drops 9%, recovers, and drops 9% again is silent the second
    time — a high-water mark alone never resets."""
    closed, why = pr.should_emit({"step": 3, "worst_pct": 9.0},
                                 {"step": 1, "change_pct": -3.0})
    assert closed is False and "recovered" in why
    again, why2 = pr.should_emit({"step": 3, "worst_pct": 9.0, "episode_closed": True},
                                 {"step": 3, "change_pct": -9.0})
    assert again and "new episode" in why2


# ── identity ─────────────────────────────────────────────────────────────────

def test_the_event_key_is_session_scoped_so_a_rerun_cannot_duplicate():
    a = pr.event_key("symbol", "NVDA", "session_decline", date(2026, 10, 9))
    b = pr.event_key("symbol", "NVDA", "session_decline", date(2026, 10, 9))
    c = pr.event_key("symbol", "NVDA", "session_decline", date(2026, 10, 10))
    assert a == b and a != c


# ── composition, including the case that must not produce a number ───────────

def test_a_qualifying_decline_produces_an_event_carrying_its_basis():
    out = pr.evaluate("NVDA", "symbol", _series([100, 94]), USABLE)
    rules = {e["rule"]: e for e in out}
    assert "session_decline" in rules
    assert rules["session_decline"]["evidence_status"] == "verified"
    assert rules["session_decline"]["basis"] == "split_adjusted_price"


def test_a_rise_produces_nothing():
    assert pr.evaluate("NVDA", "symbol", _series([100, 106]), USABLE) == []


def test_a_decline_below_the_first_rung_produces_nothing():
    assert pr.evaluate("NVDA", "symbol", _series([100, 98]), USABLE) == []


def test_an_unmeasurable_window_produces_a_RECORD_not_silence():
    """A split inside the window makes the number wrong — a 2:1 split reads as a 50% crash — so
    no event may be raised from it. Reporting nothing would be indistinguishable from 'nothing
    happened', which is how a broken feed looks exactly like a quiet market."""
    verdict = {"usable": False, "evidence_status": "unverified",
               "reason": "corporate action in window could not be applied: split"}
    out = pr.evaluate("NVDA", "symbol", _series([100, 50]), verdict)
    assert len(out) == 1
    assert out[0]["rule"] == pr.UNMEASURABLE
    assert "split" in out[0]["reason"]
    assert "change_pct" not in out[0], "an unmeasurable window must not carry a magnitude"


# ── thresholds must be reachable against the real baskets ────────────────────

def test_every_maintained_basket_can_actually_trigger():
    """A threshold no real input can satisfy is not a strict rule, it is a disabled one.

    `BREADTH_MIN_OBSERVED` was written as a flat 5 while every maintained theme holds three or
    four symbols, so 0 of 8 baskets could ever satisfy it and basket breadth was dead on
    arrival — indistinguishable, from outside, from "no theme fell". This repository has the
    same shape on record as BUG-SA33-UNREACHABLETHRESHOLD. The guard belongs here rather than in
    a comment because the next person to resize a basket will not read the comment.
    """
    from src.services.theme_baskets import THEMES

    assert THEMES, "no baskets at all means breadth can never fire"
    unreachable = []
    for theme, members in THEMES.items():
        # Every member observed and every one falling: the most favourable real input possible.
        best = pr.basket_breadth({m: -99.0 for m in members}, len(members))
        if not best["triggered"]:
            unreachable.append((theme, len(members), best["insufficient_reason"]))
    assert not unreachable, (
        f"these baskets cannot trigger under any input: {unreachable}")


def test_a_basket_smaller_than_the_nominal_minimum_still_requires_full_coverage():
    """Capping the minimum at the basket size must not become "any two quotes will do"."""
    three = {"A": -5.0, "B": -5.0, "C": None}
    out = pr.basket_breadth(three, member_count=3)
    assert out["triggered"] is False, "2 of 3 is 67% coverage, below the 80% floor"
    whole = pr.basket_breadth({"A": -5.0, "B": -5.0, "C": 1.0}, member_count=3)
    assert whole["sufficient_evidence"] is True
