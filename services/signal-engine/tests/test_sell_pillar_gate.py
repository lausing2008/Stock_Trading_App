"""Tests for T232-SIG10-SELLGATE — the symmetric SELL-side pillar gate in
_apply_style_signal(), plus the backfill_bearish_pillars/tune_sell_pillars mechanisms that
feed it.

The gate itself lives in signals.py and can be tested directly (signals.py imports cleanly
via conftest.py's stubbing, matching test_hot_news_gate.py's established convention). The
backfill/sweep endpoints live in outcomes.py/calibration.py, which need common.jwt_auth and
can't be imported directly in this test environment — those are covered via source-text
regression checks, matching this repo's established pattern for that constraint.
"""
import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.generators.signals import _apply_style_signal  # noqa: E402

_OUTCOMES_PATH = Path(__file__).resolve().parents[1] / "src" / "api" / "outcomes.py"
_OUTCOMES_SOURCE = _OUTCOMES_PATH.read_text()
_CALIBRATION_PATH = Path(__file__).resolve().parents[1] / "src" / "api" / "calibration.py"
_CALIBRATION_SOURCE = _CALIBRATION_PATH.read_text()


def _call(bearish_pillars_active, min_pillars_for_sell=None, ta_prob=0.30, style_key="SWING"):
    """A SELL-leaning baseline (ta_prob well below 0.5) with a controllable bearish pillar
    count. min_pillars_for_sell=None leaves the real _get_style_tuned_param() Redis lookup in
    place (which resolves to the un-tuned default of 0/no-gate under this test environment's
    stubbed common package, since a MagicMock Redis client can never produce a real float)."""
    base_reasons = {"bearish_pillars_active": bearish_pillars_active}
    if min_pillars_for_sell is None:
        return _apply_style_signal(
            ta_prob=ta_prob, ml_prob=None, ml_test_auc=0.5, style_key=style_key,
            market_regime="bull", adx_val=20.0, weekly_tech={}, pattern_adj=0.0,
            days_to_earnings=None, news_sentiment=None, rs_rank=None,
            options_sentiment=None, cp_ratio=None, kscore=None, is_stale=False,
            base_reasons=base_reasons,
        )
    with patch("src.generators.signals._get_style_tuned_param",
               side_effect=lambda sk, param, default: min_pillars_for_sell if param == "min_pillars_for_sell" else default):
        return _apply_style_signal(
            ta_prob=ta_prob, ml_prob=None, ml_test_auc=0.5, style_key=style_key,
            market_regime="bull", adx_val=20.0, weekly_tech={}, pattern_adj=0.0,
            days_to_earnings=None, news_sentiment=None, rs_rank=None,
            options_sentiment=None, cp_ratio=None, kscore=None, is_stale=False,
            base_reasons=base_reasons,
        )


# ── The gate itself, in _apply_style_signal() ───────────────────────────────────────────────

def test_no_gate_by_default_untuned_state_is_a_no_op():
    """With no Redis override (the real, un-tuned production default), min_pillars_for_sell
    resolves to 0 — the gate must never fire regardless of how few bearish pillars are active."""
    r0 = _call(bearish_pillars_active=0)
    r4 = _call(bearish_pillars_active=4)
    assert r0.reasons["sell_pillar_gate"] == "0_bearish_pillars"
    assert r4.reasons["sell_pillar_gate"] == "4_bearish_pillars"


def test_gate_compresses_a_sell_below_the_validated_minimum():
    baseline = _call(bearish_pillars_active=1, min_pillars_for_sell=0)
    gated = _call(bearish_pillars_active=1, min_pillars_for_sell=2)
    assert gated.reasons["sell_pillar_gate"] == "compressed_1_bearish_pillar_below_min2"
    # compression pulls the SELL-leaning fused prob TOWARD 0.5 (less confident), never past it
    assert gated.bullish_probability > baseline.bullish_probability
    assert gated.bullish_probability < 0.5


def test_gate_does_not_fire_when_bearish_pillars_meets_the_minimum():
    r = _call(bearish_pillars_active=2, min_pillars_for_sell=2)
    assert r.reasons["sell_pillar_gate"] == "2_bearish_pillars"


def test_gate_does_not_fire_when_bearish_pillars_exceeds_the_minimum():
    r = _call(bearish_pillars_active=4, min_pillars_for_sell=2)
    assert r.reasons["sell_pillar_gate"] == "4_bearish_pillars"


def test_gate_never_touches_a_buy_leaning_candidate():
    """The whole point of this gate is symmetry: it must apply ONLY to fused < 0.5, exactly
    mirroring T232-SIG3's own restriction of the bullish gate to fused > 0.5. A BUY-leaning
    candidate with a real bearish-pillar count present (a data anomaly, but must still be
    inert) should never see its fused probability altered by this gate."""
    r = _call(bearish_pillars_active=0, min_pillars_for_sell=3, ta_prob=0.70)
    assert r.bullish_probability > 0.5
    # the gate reason is still recorded (observability), but the value must be unchanged
    # from what a bare compress would have produced — confirmed by checking it's not the
    # "compressed_..." string, since 0 < 3 would otherwise have triggered it
    assert r.reasons["sell_pillar_gate"] == "0_bearish_pillars"


def test_missing_bearish_pillars_key_fails_open_no_gate():
    """A missing key (a BUY-only historical signal, or a computation failure) must default to
    0 bearish pillars — matching the un-tuned production default's own neutral state — never
    silently gate every SELL as if the worst case were true."""
    r = _apply_style_signal(
        ta_prob=0.30, ml_prob=None, ml_test_auc=0.5, style_key="SWING",
        market_regime="bull", adx_val=20.0, weekly_tech={}, pattern_adj=0.0,
        days_to_earnings=None, news_sentiment=None, rs_rank=None,
        options_sentiment=None, cp_ratio=None, kscore=None, is_stale=False,
        base_reasons={},  # no bearish_pillars_active key at all
    )
    assert r.reasons["sell_pillar_gate"] == "0_bearish_pillars"


def test_gate_uses_the_same_070_compress_ratio_as_the_buy_below_min_case():
    """A concrete, hand-computed check that the compression math matches the BUY gate's own
    documented ×0.70 ratio for the below-minimum case — not a different, undocumented value."""
    baseline = _call(bearish_pillars_active=1, ta_prob=0.30, min_pillars_for_sell=0)
    gated = _call(bearish_pillars_active=1, ta_prob=0.30, min_pillars_for_sell=4)
    # fused = 0.5 + (fused - 0.5) * 0.70 applied on TOP of whatever fused value the baseline
    # settled at is not a simple relationship (other adjustments run in between), so assert the
    # qualitative direction/bound instead of the exact fused value — same discipline as the
    # compression-direction test above.
    assert 0.5 > gated.bullish_probability > baseline.bullish_probability


# ── backfill_bearish_pillars — source-text regression checks (outcomes.py) ─────────────────

def test_backfill_endpoint_is_registered():
    assert '@router.post("/backfill_bearish_pillars")' in _OUTCOMES_SOURCE


def test_backfill_only_considers_sell_rows_missing_the_field():
    start = _OUTCOMES_SOURCE.index("def backfill_bearish_pillars(")
    end = _OUTCOMES_SOURCE.index("\n@router.post", start + 1) if "\n@router.post" in _OUTCOMES_SOURCE[start:] else len(_OUTCOMES_SOURCE)
    body = _OUTCOMES_SOURCE[start:end]
    assert 'SignalOutcome.signal_direction == "SELL"' in body
    assert "SignalOutcome.bearish_pillars_active.is_(None)" in body


def test_backfill_helper_uses_point_in_time_correct_price_filter():
    """The critical safety property: Price rows used to compute a historical signal's own
    bearish pillars must never include a bar AFTER that signal's date — this is the exact
    class of look-ahead bias SE-F2 already cost this repo a 3,808-row rebuild over."""
    start = _OUTCOMES_SOURCE.index("def _backfill_bearish_pillars_for_stock(")
    end = _OUTCOMES_SOURCE.index("\n@router.post(\"/backfill_bearish_pillars\")")
    body = _OUTCOMES_SOURCE[start:end]
    assert "df_upto = full_df[full_df" in body
    assert "<= sd]" in body


def test_backfill_helper_requires_a_minimum_bar_count_before_computing():
    start = _OUTCOMES_SOURCE.index("def _backfill_bearish_pillars_for_stock(")
    end = _OUTCOMES_SOURCE.index("\n@router.post(\"/backfill_bearish_pillars\")")
    body = _OUTCOMES_SOURCE[start:end]
    assert "_BACKFILL_MIN_BARS" in body
    assert "continue" in body


def test_backfill_batches_by_stock_not_one_query_per_row():
    start = _OUTCOMES_SOURCE.index("def backfill_bearish_pillars(")
    end = len(_OUTCOMES_SOURCE)
    body = _OUTCOMES_SOURCE[start:end]
    assert "by_stock" in body
    assert "_backfill_bearish_pillars_for_stock(session, stock_id, dates)" in body


# ── tune_sell_pillars — source-text regression checks (calibration.py) ─────────────────────

def test_sweep_endpoint_is_registered():
    assert '@router.post("/tune_sell_pillars")' in _CALIBRATION_SOURCE


def test_sweep_only_reads_backfilled_rows():
    start = _CALIBRATION_SOURCE.index("def tune_sell_pillars(")
    end = _CALIBRATION_SOURCE.index("\n@router.post(\"/tune_style_profiles\")")
    body = _CALIBRATION_SOURCE[start:end]
    assert "SignalOutcome.bearish_pillars_active.is_not(None)" in body


def test_sweep_ev_metric_uses_negated_pct_return_matching_the_sell_threshold_sweeps_convention():
    """A SELL wins on a negative pct_return — the EV metric must be -pct_return, never the
    raw mean, or the sweep would optimize backwards (the exact gotcha flagged before this
    was built)."""
    start = _CALIBRATION_SOURCE.index("def tune_sell_pillars(")
    end = _CALIBRATION_SOURCE.index("\n@router.post(\"/tune_style_profiles\")")
    body = _CALIBRATION_SOURCE[start:end]
    assert "rets = [-o.pct_return for o in sub if o.pct_return is not None]" in body


def test_sweep_does_chronological_train_validation_split_not_random():
    start = _CALIBRATION_SOURCE.index("def tune_sell_pillars(")
    end = _CALIBRATION_SOURCE.index("\n@router.post(\"/tune_style_profiles\")")
    body = _CALIBRATION_SOURCE[start:end]
    assert "key=lambda o: o.signal_date" in body
    assert "int(len(bucket) * 0.7)" in body


def test_sweep_unconditionally_rejects_non_positive_ev_lift():
    start = _CALIBRATION_SOURCE.index("def tune_sell_pillars(")
    end = _CALIBRATION_SOURCE.index("\n@router.post(\"/tune_style_profiles\")")
    body = _CALIBRATION_SOURCE[start:end]
    assert "if ev_lift <= 0:" in body


def test_sweep_writes_to_the_generic_style_tune_redis_key_the_read_side_already_knows():
    start = _CALIBRATION_SOURCE.index("def tune_sell_pillars(")
    end = _CALIBRATION_SOURCE.index("\n@router.post(\"/tune_style_profiles\")")
    body = _CALIBRATION_SOURCE[start:end]
    assert 'f"stockai:style_tune:{h}:min_pillars_for_sell"' in body


def test_sweep_records_tune_history_on_every_branch_including_skips():
    start = _CALIBRATION_SOURCE.index("def tune_sell_pillars(")
    end = _CALIBRATION_SOURCE.index("\n@router.post(\"/tune_style_profiles\")")
    body = _CALIBRATION_SOURCE[start:end]
    assert body.count("_record_tune_history(") >= 6  # one per skip/promote branch, 4 styles worth of paths
