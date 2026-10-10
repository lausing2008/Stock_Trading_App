"""Run the real generator with deterministic external inputs, keeping fusion real."""
import numpy as np
import pandas as pd
from src.generators import signals


def test_every_horizon_retains_its_own_model_evidence(monkeypatch):
    close = np.linspace(90, 110, 240)
    frame = pd.DataFrame(dict(ts=pd.date_range('2025-01-01', periods=240),
        close=close, high=close + 1, low=close - 1, open=close, volume=np.full(240, 100000)))
    monkeypatch.setattr(signals, '_fetch_prices', lambda _: frame)
    returns = {
        '_fetch_market_regime': ('bull', 50), '_fetch_market_breadth': None,
        '_fetch_earnings_proximity': None, '_fetch_earnings_beat_rate': None,
        '_fetch_news_sentiment': None, '_fetch_relative_strength': (None, None, None, None),
        '_fetch_options_flow': (None, None), '_fetch_hot_news': None,
        '_fetch_patterns_from_ta': [], '_fetch_kscore': None,
        '_fetch_short_interest': (None, None), '_fetch_analyst_momentum': (0, 0),
        '_check_uw_short_interest_disagreement': None, '_check_price_staleness': False,
        '_fetch_sr_context_from_ta': None, '_get_dynamic_buy_threshold': None,
        '_get_dynamic_sell_threshold': None,
    }
    for name, value in returns.items():
        monkeypatch.setattr(signals, name, lambda *a, _value=value, **kw: _value)
    monkeypatch.setattr(signals, '_get_style_tuned_param', lambda style, param, default: default)
    monkeypatch.setattr(signals, '_ml_weight_global_cap', None)
    model_data = {
        'SHORT': (.6, .95, {'ml_model': 'short-model', 'ml_quality_status': 'measured'}),
        'SWING': (.7, .71, {'ml_model': 'swing-model', 'ml_quality_status': 'measured'}),
        'LONG': (.8, 0., {'ml_model': 'long-model', 'ml_quality_status': 'unavailable'}),
        'GROWTH': (None, 0., {}),
    }
    monkeypatch.setattr(signals, '_fetch_ml_data', lambda symbol, style: model_data[style])
    result = signals.generate_all_signals('TEST')
    assert result['SHORT'].reasons['ml_test_auc'] == .95
    assert result['SHORT'].reasons['ml_weight'] <= signals._STYLE_PROFILES['SHORT']['ml_weight_cap']
    assert result['SWING'].reasons['ml_test_auc'] == .71
    assert result['SHORT'].reasons['ml_model'] == 'short-model'
    assert result['LONG'].reasons['ml_model'] == 'long-model'
    assert result['LONG'].reasons['ml_test_auc'] is None
    assert result['LONG'].reasons['ml_weight'] == 0
    assert result['GROWTH'].reasons['ml_model'] is None
    assert result['GROWTH'].reasons['ml_test_auc'] is None
    fusion = signals._apply_style_signal(
        ta_prob=.6, ml_prob=.6, ml_test_auc=.95, style_key='SHORT', market_regime='bull',
        adx_val=30, weekly_tech={}, pattern_adj=0, days_to_earnings=None,
        news_sentiment=None, rs_rank=None, options_sentiment=None, cp_ratio=None,
        kscore=None, is_stale=False, base_reasons={})
    assert fusion.reasons['ml_weight'] == .30
