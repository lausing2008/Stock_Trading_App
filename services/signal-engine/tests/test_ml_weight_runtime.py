"""Production/calibration parity and global-cap population regression checks."""
import math
from datetime import date

import pytest
from src.generators import signals
from test_calibrate_ml_weight_ratchet import (
    _CAL_SOURCE, _build_fixture, _calibration_helpers, _make_row, _run_core,
)


_DYNAMIC_BUY_READER = signals._get_dynamic_buy_threshold


@pytest.fixture
def helpers(monkeypatch):
    monkeypatch.setattr(signals, '_get_style_tuned_param', lambda style, param, default: default)
    monkeypatch.setattr(signals, '_get_dynamic_buy_threshold', lambda style, regime: .81)
    monkeypatch.setattr(signals, '_get_dynamic_sell_threshold', lambda style: None)
    return _calibration_helpers()


@pytest.mark.parametrize('style', ['SHORT', 'SWING', 'LONG', 'GROWTH'])
@pytest.mark.parametrize('cap', [0., .1, .3, .65, 1.])
@pytest.mark.parametrize('auc,ml,ta', [
    (.0, .8, .7), (.49, .8, .7), (.50, .8, .7), (.525, .8, .7),
    (.55, .8, .7), (.60, .8, .7), (.70, .8, .7), (.95, .8, .7),
    (.70, .8, .55), (.70, .8, .5), (.70, .9, .5), (.70, .95, .2),
    (.70, 1., .8), (.70, 0., .8),
])
def test_calibration_equals_production(helpers, monkeypatch, style, cap, auc, ml, ta):
    # Capture the unrounded weight actually consumed by production, before later gates.
    weights = []
    real = signals._calculate_ml_weight

    def capture(*args):
        weight = real(*args)
        weights.append(weight)
        return weight

    monkeypatch.setattr(signals, '_calculate_ml_weight', capture)
    monkeypatch.setattr(signals, '_ml_weight_global_cap', cap)
    result = signals._apply_style_signal(
        ta_prob=ta, ml_prob=ml, ml_test_auc=auc, style_key=style, market_regime='bull',
        adx_val=30, weekly_tech={}, pattern_adj=0, days_to_earnings=None,
        news_sentiment=None, rs_rank=None, options_sentiment=None, cp_ratio=None,
        kscore=None, is_stale=False, base_reasons={})
    production_weight = weights[0]
    inputs = dict(ml=ml, ta=ta, auc=auc, floor=signals._STYLE_PROFILES[style]['ml_weight_floor'])
    score = helpers['_ml_candidate_score'](inputs, cap)
    assert weights == [production_weight, production_weight]
    assert result.reasons['ml_weight'] == round(production_weight, 2)
    assert score == production_weight * min(.95, max(.05, ml)) + (1-production_weight) * ta


@pytest.mark.parametrize('auc,ml,ta,cap,expected', [
    (.7, .8, .7, 0., .15),       # zero cap retains the full SWING floor
    (.525, .8, .7, 0., .0375),   # scaled floor for a weak model
    (.49, .8, .7, 0., 0.),      # inverse model is never resurrected
    (.5, .8, .7, 1., 0.),
    (.525, .8, .7, 1., .1),     # lower AUC ramp
    (.55, .8, .7, 1., .2),
    (.7, .8, .7, 1., .75),      # upper AUC ramp
    (.9, .8, .7, 1., .75),
    (.7, .8, .5, 0., .1125),    # floor precedes intermediate disagreement cut
    (.7, .9, .4, 0., .09375),   # graduated disagreement cut
    (.7, .95, .2, 0., .075),    # maximum disagreement cut
])
def test_weight_contract(auc, ml, ta, cap, expected):
    assert signals._calculate_ml_weight(ml, ta, auc, cap, .15) == pytest.approx(expected)


def test_per_style_override_still_wins(helpers, monkeypatch):
    monkeypatch.setattr(signals, '_ml_weight_global_cap', 0.)
    monkeypatch.setattr(signals, '_get_style_tuned_param', lambda style, param, default: .4)
    result = signals._apply_style_signal(
        ta_prob=.8, ml_prob=.9, ml_test_auc=.7, style_key='SWING', market_regime='bull',
        adx_val=30, weekly_tech={}, pattern_adj=0, days_to_earnings=None,
        news_sentiment=None, rs_rank=None, options_sentiment=None, cp_ratio=None,
        kscore=None, is_stale=False, base_reasons={})
    assert result.reasons['ml_weight'] == .4
    assert helpers['_ml_weight_policy']() == {}


@pytest.mark.parametrize('auc', [None, math.nan, math.inf, -.1, 1.1, 'bad', True])
def test_missing_or_invalid_auc_is_excluded_and_counted(helpers, auc):
    (sig, _), _ = _make_row(1, date(2026, 1, 1), .9, .8, 100., 105.)
    sig.reasons['ml_test_auc'] = auc
    policy = helpers['_ml_weight_policy']()
    excluded = helpers['_ml_weight_evaluation'](policy)['excluded']
    assert helpers['_ml_weight_inputs'](sig, policy, excluded) is None
    assert excluded['missing_or_invalid_auc'] == 1


def _validation_core(helpers):
    # Execute the real GET computational body with injected observations, matching the
    # existing calibrator harness; no DB/Redis writes or Docker dependencies are needed.
    start = _CAL_SOURCE.index('    _pts: dict[int, list] = {}', _CAL_SOURCE.index('def ml_weight_validation('))
    end = _CAL_SOURCE.index('\n\n@router.post("/calibrate_ml_weight")', start)
    body = _CAL_SOURCE[start:end]
    ns = dict(helpers, _actions={}, _coverage={}, lookback_days=180,
              bisect=__import__('bisect'), datetime=__import__('datetime').datetime)
    from common.outcome_price_basis import classify_observation_window, resolve_window
    ns['_classify_window'] = classify_observation_window
    ns['_resolve_outcome_basis'] = resolve_window
    basis_start = _CAL_SOURCE.index('def _observation_basis(')
    basis_end = _CAL_SOURCE.index('\n\n\n', basis_start)
    exec(_CAL_SOURCE[basis_start:basis_end], ns)
    exec('def core(rows, price_rows):\n'
         '    policy = _ml_weight_policy()\n    evaluation = _ml_weight_evaluation(policy)\n' + body, ns)
    return ns['core']


@pytest.mark.parametrize('ml,ta,auc,fires', [(.81,.81,0.,False), (.9,.7,0.,False), (.9,.8,.7,True)])
@pytest.mark.parametrize('endpoint', ['validation', 'calibration'])
def test_endpoints_scope_auc_and_strict_buy_boundary(helpers, monkeypatch, endpoint, ml, ta, auc, fires):
    # Only SWING is globally governed; SHORT precedes SWING on the same stock/date.
    monkeypatch.setattr(signals, '_get_style_tuned_param',
                        lambda style, param, default: None if style == 'SWING' else .3)
    rows, prices = _build_fixture([(ml, ta, 100., 105.)] * 10, [])
    for sig, _ in rows:
        sig.reasons['ml_test_auc'] = auc
    short = type(rows[0][0])(rows[0][0].stock_id, rows[0][0].ts, 'SHORT', .9, .8)
    rows.insert(0, (short, 'TEST'))
    missing = type(short)(99, short.ts, 'SWING', .9, .8)
    missing.reasons.pop('ml_test_auc')
    rows.append((missing, 'TEST'))
    if endpoint == 'validation':
        result = _validation_core(helpers)(rows, prices)
    else:
        result, _, applied = _run_core(rows, prices, .3)
        assert not applied
    assert result['signal_count'] == 10
    assert result['evaluation']['excluded'] == {
        'style_not_governed_by_global_cap': 1, 'ta_score_not_stored_for_style': 0,
        'missing_or_invalid_auc': 1, 'missing_or_invalid_scores': 0}
    assert list(result['evaluation']['policy']) == ['SWING']
    assert result['curve'][0]['accuracy'] == (100. if fires else None)
    if not fires:
        assert all(point['accuracy'] is None for point in result['curve'])


def test_policy_uses_production_regime_threshold_reader(helpers, monkeypatch):
    monkeypatch.setattr(signals, '_get_dynamic_buy_threshold', lambda *args: None)
    monkeypatch.setattr(signals, '_redis_get_float',
                        lambda key: .72 if key == 'stockai:watchdog:SWING:threshold' else None)
    monkeypatch.setattr(signals, '_get_dynamic_buy_threshold', _DYNAMIC_BUY_READER)
    policy = helpers['_ml_weight_policy']()
    for regime, threshold in policy['SWING']['buy_thresholds'].items():
        assert signals._decide_style(threshold, 'SWING', regime)[0] != 'BUY'
        assert signals._decide_style(threshold + 1e-8, 'SWING', regime)[0] == 'BUY'


def test_legacy_regime_uses_unknown_boundary(helpers):
    (sig, _), _ = _make_row(1, date(2026, 1, 1), .9, .8, 100., 105.)
    sig.reasons['market_regime'] = 'high_vol'
    policy = helpers['_ml_weight_policy']()
    policy['SWING']['buy_thresholds']['unknown'] = .76
    excluded = helpers['_ml_weight_evaluation'](policy)['excluded']
    assert helpers['_ml_weight_inputs'](sig, policy, excluded)['buy_threshold'] == .76


def test_growth_rows_are_excluded_because_stored_ta_is_not_growth_ta(helpers):
    # Production fuses GROWTH with ta_prob + _growth_ta_adjustment(); reasons["ta_score"] holds
    # only the shared ta_prob, so replaying a stored GROWTH row would use the wrong TA input.
    (swing, _), _ = _make_row(1, date(2026, 1, 1), .9, .8, 100., 105.)
    sig = type(swing)(1, swing.ts, 'GROWTH', .9, .8)
    policy = helpers['_ml_weight_policy']()
    assert 'GROWTH' in policy  # governed by the global cap when no per-style cap exists
    excluded = helpers['_ml_weight_evaluation'](policy)['excluded']
    assert helpers['_ml_weight_inputs'](sig, policy, excluded) is None
    assert excluded['ta_score_not_stored_for_style'] == 1
