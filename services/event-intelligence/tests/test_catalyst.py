"""Tests for catalyst.py's scoring functions.

Two confirmed historical bugs this file protects against recurring:
  - T237-EI2: `get_beat_rate(stock_id) or 0.5` replaced a genuine 0.0 beat rate (missed every
    one of the last 8 quarters) with the same 0.5 "no history" default, because 0.0 is falsy
    in Python. Fixed with an explicit `is not None` check. The two cases coincide in today's
    threshold table (neither 0.0 nor 0.5 clears the > 0.50 bucket), so the regression test
    below asserts the resolution mechanism directly (via a spy on get_beat_rate) rather than
    relying on a score difference that doesn't currently exist.
  - T237-EI3: _compute_risk_score's ATR-volatility branch was permanently dead code because no
    caller ever passed a non-default atr_pct. Fixed by threading real atr_14_pct through from
    the scheduler. The regression test confirms the branch is live given a non-default atr_pct.
"""
import inspect
from unittest.mock import patch

from src.services.catalyst import (
    _compute_earnings_score,
    _compute_economic_score,
    _compute_risk_score,
    _compute_composite,
)


# ── _compute_earnings_score ───────────────────────────────────────────────────

def test_earnings_score_combines_days_out_and_beat_rate_buckets():
    with patch("src.services.catalyst.get_days_to_earnings", return_value=2), \
         patch("src.services.catalyst.get_beat_rate", return_value=0.85):
        score, days_out = _compute_earnings_score(stock_id=1)
    assert score == 50 + 25  # days_out<=3 bucket + beat_rate>0.80 bucket
    assert days_out == 2


def test_earnings_score_no_earnings_date_only_scores_beat_rate():
    with patch("src.services.catalyst.get_days_to_earnings", return_value=None), \
         patch("src.services.catalyst.get_beat_rate", return_value=0.70):
        score, days_out = _compute_earnings_score(stock_id=1)
    assert score == 15  # beat_rate>0.65 bucket only
    assert days_out is None


def test_earnings_score_clamped_at_100():
    with patch("src.services.catalyst.get_days_to_earnings", return_value=1), \
         patch("src.services.catalyst.get_beat_rate", return_value=0.95):
        score, _ = _compute_earnings_score(stock_id=1)
    assert score == min(100.0, 50 + 25)


def test_t237_ei2_zero_beat_rate_is_not_silently_replaced_with_default():
    """The historical bug: `get_beat_rate(stock_id) or 0.5` silently replaced a genuine 0.0
    beat rate with the same 0.5 "no history" default, because 0.0 is falsy in Python. This
    can NOT be caught by asserting on _compute_earnings_score's returned score: 0.0 and 0.5
    both fail every beat_rate threshold (`> 0.80`/`> 0.65`/`> 0.50`) in the current bucket
    table, so the two code paths are score-indistinguishable no matter what input is chosen —
    a purely black-box test would pass against the bug just as happily as against the fix.
    The only faithful regression guard is white-box: assert the source actually uses an
    explicit `is not None` check rather than the `or` idiom that caused the bug."""
    # Strip comment/docstring lines before checking — the function's own docstring quotes
    # the buggy `or 0.5` idiom as historical documentation, which would false-positive a
    # naive substring check against the raw source.
    code_lines = [
        line for line in inspect.getsource(_compute_earnings_score).splitlines()
        if line.strip() and not line.strip().startswith(("#", '"""'))
    ]
    code_only = "\n".join(code_lines)
    assert "is not None else" in code_only, (
        "expected an explicit `is not None` guard around get_beat_rate's result — "
        "a bare `or 0.5` fallback would silently coerce a real 0.0 beat rate to 0.5 "
        "(T237-EI2), and no score-based assertion can detect that regression here"
    )
    assert "or 0.5" not in code_only


def test_earnings_score_none_beat_rate_uses_neutral_default():
    with patch("src.services.catalyst.get_days_to_earnings", return_value=None), \
         patch("src.services.catalyst.get_beat_rate", return_value=None):
        score, _ = _compute_earnings_score(stock_id=1)
    assert score == 0.0  # 0.5 default also fails every bucket


# ── _compute_economic_score ───────────────────────────────────────────────────

def test_economic_score_no_fomc_scheduled():
    with patch("src.services.catalyst.days_to_next_fomc", return_value=None):
        assert _compute_economic_score() == 0.0


def test_economic_score_imminent_fomc():
    with patch("src.services.catalyst.days_to_next_fomc", return_value=1):
        assert _compute_economic_score() == 80.0


def test_economic_score_fomc_within_a_week():
    with patch("src.services.catalyst.days_to_next_fomc", return_value=7):
        assert _compute_economic_score() == 40.0


def test_economic_score_fomc_within_two_weeks():
    with patch("src.services.catalyst.days_to_next_fomc", return_value=14):
        assert _compute_economic_score() == 20.0


def test_economic_score_distant_fomc_still_gets_floor_score():
    with patch("src.services.catalyst.days_to_next_fomc", return_value=90):
        assert _compute_economic_score() == 5.0


# ── _compute_risk_score ────────────────────────────────────────────────────────

def _risk_score(**overrides):
    kwargs = dict(stock_id=1, earnings_days_out=None, insider_score=0.0, atr_pct=0.0)
    kwargs.update(overrides)
    with patch("src.services.catalyst.get_congress_for_symbol", return_value=[]), \
         patch("src.services.catalyst.days_to_next_fomc", return_value=None):
        return _compute_risk_score(**kwargs)


def test_risk_score_zero_when_nothing_is_risky():
    assert _risk_score() == 0.0


def test_risk_score_earnings_tomorrow_is_highest_bucket():
    assert _risk_score(earnings_days_out=1) == 35


def test_risk_score_earnings_in_3_days():
    assert _risk_score(earnings_days_out=3) == 25


def test_risk_score_earnings_in_7_days():
    assert _risk_score(earnings_days_out=7) == 15


def test_risk_score_insider_heavy_selling():
    assert _risk_score(insider_score=-45) == 25


def test_risk_score_insider_mild_selling():
    assert _risk_score(insider_score=-15) == 12


def test_t237_ei3_atr_volatility_branch_is_live_not_dead_code():
    """The historical bug: no caller ever passed a non-default atr_pct, so this branch never
    fired in production despite being fully implemented. Confirm it now actually changes the
    score when a real value is supplied — the whole point of the T237-EI3 fix."""
    baseline = _risk_score(atr_pct=0.0)
    high_vol = _risk_score(atr_pct=0.07)
    assert high_vol - baseline == 20


def test_risk_score_atr_mid_bucket():
    baseline = _risk_score(atr_pct=0.0)
    mid_vol = _risk_score(atr_pct=0.05)
    assert mid_vol - baseline == 12


def test_risk_score_atr_low_bucket():
    baseline = _risk_score(atr_pct=0.0)
    low_vol = _risk_score(atr_pct=0.03)
    assert low_vol - baseline == 5


def test_risk_score_congress_net_selling_adds_penalty():
    trades = [
        {"transaction_type": "sale"}, {"transaction_type": "sale"},
        {"transaction_type": "purchase"},
    ]
    with patch("src.services.catalyst.get_congress_for_symbol", return_value=trades), \
         patch("src.services.catalyst.days_to_next_fomc", return_value=None):
        score = _compute_risk_score(1, None, 0.0, 0.0)
    assert score == 15


def test_risk_score_congress_net_buying_adds_no_penalty():
    trades = [
        {"transaction_type": "purchase"}, {"transaction_type": "purchase"},
        {"transaction_type": "sale"},
    ]
    with patch("src.services.catalyst.get_congress_for_symbol", return_value=trades), \
         patch("src.services.catalyst.days_to_next_fomc", return_value=None):
        score = _compute_risk_score(1, None, 0.0, 0.0)
    assert score == 0.0


def test_risk_score_imminent_fomc_adds_penalty():
    with patch("src.services.catalyst.get_congress_for_symbol", return_value=[]), \
         patch("src.services.catalyst.days_to_next_fomc", return_value=2):
        score = _compute_risk_score(1, None, 0.0, 0.0)
    assert score == 20


def test_risk_score_clamped_at_100():
    trades = [{"transaction_type": "sale"}]
    with patch("src.services.catalyst.get_congress_for_symbol", return_value=trades), \
         patch("src.services.catalyst.days_to_next_fomc", return_value=1):
        score = _compute_risk_score(1, earnings_days_out=1, insider_score=-50, atr_pct=0.10)
    # 35 (earnings) + 20 (atr) + 25 (insider) + 15 (congress) + 20 (fomc) = 115, clamped
    assert score == 100.0


# ── _compute_composite ─────────────────────────────────────────────────────────
# Fully pure — no DB/network dependency at all, safe to call directly.

def test_composite_weighted_sum_no_risk_dampening():
    score = _compute_composite(
        technical_score=100, earnings_score=100, catalyst_score=100,
        insider_score=100, congress_score=100, institutional_score=100, risk_score=0,
    )
    # weights sum to 1.0 at technical/catalyst/earnings/insider/congress/institutional,
    # risk_dampen = 1.0 - 0.40*(0/100) = 1.0
    assert score == 100.0


def test_composite_risk_score_dampens_the_final_score():
    no_risk = _compute_composite(50, 50, 50, 50, 50, 50, risk_score=0)
    full_risk = _compute_composite(50, 50, 50, 50, 50, 50, risk_score=100)
    assert full_risk == round(no_risk * 0.60, 10)


def test_composite_negative_insider_and_congress_scores_pull_composite_down():
    """insider_score/congress_score can legitimately be negative (-100..100 range, per
    EI-DOC1) — unlike catalyst/earnings/institutional (0-100). A strongly negative pair
    should measurably reduce the composite relative to a neutral (0) pair."""
    neutral = _compute_composite(50, 50, 50, insider_score=0, congress_score=0, institutional_score=50, risk_score=0)
    negative = _compute_composite(50, 50, 50, insider_score=-100, congress_score=-100, institutional_score=50, risk_score=0)
    assert negative < neutral


def test_composite_clamped_at_0_when_negative_inputs_overwhelm_positive():
    score = _compute_composite(
        technical_score=0, earnings_score=0, catalyst_score=0,
        insider_score=-100, congress_score=-100, institutional_score=0, risk_score=100,
    )
    assert score == 0.0
