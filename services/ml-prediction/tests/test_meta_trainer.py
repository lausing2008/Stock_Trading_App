"""Tests for meta_trainer.py's meta-feature construction — specifically the ordering
invariant between train_meta_model() and predict_meta().

T247-ML-META-FEATURE-ORDER: predict_meta() previously appended `direction` LAST instead of
4th (right after horizon_code), while train_meta_model() always appended it 4th (added at
AUD232-046). Since both functions build the meta-feature suffix via independent, hand-written
append sequences (not a shared helper), nothing enforced they stay in sync.

This test calls the REAL predict_meta() function directly (not a hand-written mirror of its
logic) — an earlier version of this test asserted on two separately-maintained reimplementation
helpers, which passed even when predict_meta()'s real append order was reverted back to the
pre-fix (LAST instead of 4th) via adversarial verification. That flaw is why this version
patches predict_meta()'s internal joblib.load/DB/build_features calls instead: only exercising
the actual function catches a real regression in its source.

meta_trainer.py is loaded directly via importlib rather than `from src.training.meta_trainer
import ...` — the normal import goes through training/__init__.py, which eagerly imports
trainer.py, which pulls in every model backend (xgboost, torch, lightgbm) just to reach this
one function. meta_trainer.py's only OWN module-level cross-file dependency is
`_HORIZON_BY_STYLE` from trainer.py (a plain dict), stubbed here directly instead of pulling in
the real trainer module and its heavy transitive imports.
"""
import importlib.util as _ilu
import pathlib as _pathlib
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

_meta_trainer_path = _pathlib.Path(__file__).resolve().parents[1] / "src" / "training" / "meta_trainer.py"

# Stub the one thing meta_trainer.py imports from its sibling trainer.py at module level.
_fake_trainer = MagicMock()
_fake_trainer._HORIZON_BY_STYLE = {"SHORT": 5, "SWING": 10, "LONG": 20, "GROWTH": 10}
sys.modules.setdefault("src.training", MagicMock())
sys.modules["src.training.trainer"] = _fake_trainer

_spec = _ilu.spec_from_file_location("meta_trainer_under_test", _meta_trainer_path)
_meta_trainer_mod = _ilu.module_from_spec(_spec)
# meta_trainer.py does `from .trainer import ...` — a relative import, which requires the
# module to have a real __package__ matching an importable parent. Register it under the
# package name its own `from .trainer import` expects, using the stub above.
_meta_trainer_mod.__package__ = "src.training"
_spec.loader.exec_module(_meta_trainer_mod)

predict_meta = _meta_trainer_mod.predict_meta

# FEATURE_COLUMNS is imported by predict_meta() as a LOCAL name inside its own function body
# (`from ..features.builder import FEATURE_COLUMNS, ...`), so it's never bound as a module-level
# attribute on meta_trainer_under_test — import the real builder module directly for it here.
from src.features.builder import FEATURE_COLUMNS  # noqa: E402


def _synthetic_price_rows(n=300, seed=7):
    """Real OHLCV bars (plain objects with the attributes predict_meta()'s DB-row loop
    reads: ts/open/high/low/close/volume) — enough history (300 bars) that build_features()'s
    longest-window column (momentum_12_1, needs 252+21 bars) is non-NaN on the last row, so
    build_features runs for REAL (pure pandas/numpy, no mocking needed) rather than being
    stubbed out — matching this codebase's existing indicator-test convention (see
    services/technical-analysis/tests/test_indicators.py)."""
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0, 0.5, n))
    high = close + np.abs(rng.normal(0, 0.3, n))
    low = close - np.abs(rng.normal(0, 0.3, n))
    # volume_z (a "required" column — see builder.py's FEATURE_COLUMNS) divides by
    # vol.rolling(20).std(): a constant volume makes that std() exactly 0.0 -> replaced with
    # NaN -> volume_z NaN on every row -> build_features()'s inference_mode mask drops ALL
    # rows, leaving X empty. Must vary volume day-to-day like a real series.
    volume = rng.uniform(5e5, 1.5e6, n)
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    return [
        SimpleNamespace(ts=dates[i], open=close[i], high=high[i], low=low[i], close=close[i], volume=volume[i])
        for i in range(n)
    ]


class _FakeSession:
    """Mimics `with SessionLocal() as sess:` and the two sess.execute(...) call sites in
    predict_meta(): the first returns a single Stock-like row (.scalar_one_or_none()), the
    second returns the price history (.scalars().all())."""

    def __init__(self, stock_row, price_rows):
        self._stock_row = stock_row
        self._price_rows = price_rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, *_args, **_kwargs):
        result = MagicMock()
        result.scalar_one_or_none.return_value = self._stock_row
        result.scalars.return_value.all.return_value = self._price_rows
        return result


def _run_predict_meta_and_capture_scaled_vector(monkeypatch, *, direction: str):
    """Drives the REAL predict_meta() end-to-end and returns the exact feature vector it
    passed to scaler.transform(...) — i.e. the real, live output of predict_meta()'s own
    vec-building code, not a reimplementation of it.

    non_const selects every column (np.arange over the full vector length) so nothing is
    dropped before scaler.transform() — this test needs to see the whole vector, including
    both the core price/technical features (real, computed by the real build_features()) and
    the 7 meta features appended after them (sector_code, market_cap_bin, horizon_code,
    direction, confidence, fused_prob, ta_score, in that order per the fix)."""
    price_rows = _synthetic_price_rows()
    stock_row = SimpleNamespace(id=1, symbol="TEST", market_cap=5e9)

    captured = {}

    def _fake_transform(x):
        captured["vec"] = x[0].copy()
        return x  # identity — pass through unchanged so predict_proba below gets the same values

    fake_scaler = MagicMock()
    fake_scaler.transform.side_effect = _fake_transform

    fake_model = MagicMock()
    fake_model.predict_proba.return_value = np.array([[0.0, 0.5]])

    def _fake_joblib_load(_path):
        # non_const must cover the full real vector (len(feature_columns) + 7 meta features)
        # so predict_meta()'s bounds check passes and scaler.transform() sees every slot.
        feature_columns = list(FEATURE_COLUMNS)
        return {
            "auc": 0.7,
            "model": fake_model,
            "scaler": fake_scaler,
            "non_const": np.arange(len(feature_columns) + 7),
            "feature_columns": feature_columns,
        }

    monkeypatch.setattr(_meta_trainer_mod, "META_MODEL_PATH", SimpleNamespace(exists=lambda: True))

    fake_joblib = MagicMock()
    fake_joblib.load.side_effect = _fake_joblib_load
    monkeypatch.setitem(sys.modules, "joblib", fake_joblib)

    fake_db = MagicMock()
    fake_db.SessionLocal.side_effect = lambda: _FakeSession(stock_row, price_rows)
    # Stock.symbol / Price.* need to behave like real SQLAlchemy InstrumentedAttributes for the
    # `.where(Stock.symbol == ..., Price.ts >= since, ...)` expressions inside predict_meta() to
    # build without raising (a plain string attribute breaks on `"ts_col" >= datetime.date(...)`).
    # _FakeSession.execute() ignores the actual clause and returns fixed rows regardless, so all
    # that matters here is that constructing the expression itself doesn't blow up.
    _cmp_attr = MagicMock()
    _cmp_attr.__eq__ = MagicMock(return_value=True)
    _cmp_attr.__ge__ = MagicMock(return_value=True)
    fake_db.Stock = SimpleNamespace(symbol=_cmp_attr)
    fake_db.Price = SimpleNamespace(stock_id=_cmp_attr, timeframe=_cmp_attr, ts=_cmp_attr)
    fake_db.TimeFrame = SimpleNamespace(D1="D1")
    monkeypatch.setitem(sys.modules, "db", fake_db)

    prob = predict_meta(
        symbol="TEST",
        horizon="SWING",
        confidence=72.0,
        fused_prob=0.65,
        ta_score=0.58,
        sector="Technology",
        market_cap=5e9,
        direction=direction,
    )
    assert prob is not None, "predict_meta() returned None — check mocks/fixture wiring"
    assert "vec" in captured, "scaler.transform was never called — predict_meta() likely hit its except-block early"
    return captured["vec"]


def test_predict_meta_appends_direction_as_the_4th_meta_feature(monkeypatch):
    """Core regression guard for T247-ML-META-FEATURE-ORDER: the REAL predict_meta() must
    place direction at index -4 of the vector (4th-from-last: sector_code, market_cap_bin,
    horizon_code, direction, confidence, fused_prob, ta_score are the last 7 slots), matching
    train_meta_model()'s training-time order. Before the fix, direction was appended LAST
    (index -1) instead."""
    vec = _run_predict_meta_and_capture_scaled_vector(monkeypatch, direction="BUY")
    # Meta features are the last 7 entries: [..., sector, mcap_bin, horizon, direction,
    # confidence, fused_prob, ta_score]. direction is at index -4.
    assert vec[-4] == 1.0, f"expected direction=BUY at index -4, got {vec[-7:]}"
    # ta_score (the real, final meta feature) must be last — not direction.
    assert vec[-1] == pytest.approx(0.58)


def test_predict_meta_sell_direction_encodes_as_zero_at_index_minus_4(monkeypatch):
    vec = _run_predict_meta_and_capture_scaled_vector(monkeypatch, direction="SELL")
    assert vec[-4] == 0.0, f"expected direction=SELL (0.0) at index -4, got {vec[-7:]}"


def test_predict_meta_meta_feature_suffix_matches_training_order(monkeypatch):
    """Cross-checks the full 7-slot meta-feature suffix produced by the real predict_meta()
    against train_meta_model()'s documented append order (sector_code, market_cap_bin,
    horizon_code, direction, confidence, fused_prob, ta_score — see meta_trainer.py's own
    comment above train_meta_model()'s append block, lines ~237-252)."""
    vec = _run_predict_meta_and_capture_scaled_vector(monkeypatch, direction="BUY")
    sector_code, mcap_bin, horizon_code, direction_v, confidence_v, fused_prob_v, ta_score_v = vec[-7:]
    assert sector_code == float(_meta_trainer_mod.SECTOR_MAP["Technology"])
    assert mcap_bin == float(_meta_trainer_mod._market_cap_bin(5e9))
    assert horizon_code == float(_meta_trainer_mod.HORIZON_MAP["SWING"])
    assert direction_v == 1.0
    assert confidence_v == pytest.approx(72.0)
    assert fused_prob_v == pytest.approx(0.65)
    assert ta_score_v == pytest.approx(0.58)
