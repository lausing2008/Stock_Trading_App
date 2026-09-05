"""AUD-LIVEBAR-INFERENCE: `predict_latest()` computed every rolling feature (SMA/RSI/ATR/
z-scores) off a live, partially-observed "today" bar, while `train_model()` has always
excluded it ("Exclude any bar timestamped today — partially-observed intraday bars skew
rolling features"). The Price table has no is_final/is_settled column and the D1 row for the
current trading day is upserted every ~5 min as it live-updates, so it is indistinguishable
from a settled close to any consumer.

The defect is the train/inference ASYMMETRY, not the live bar per se: the same nominal
feature meant one thing at fit time (settled) and another at predict time (partial).

`trainer.py` can't be imported directly in this local test environment (its import chain
pulls in `lightgbm`, not installed locally) — the same constraint already documented in
test_predict_latest_ensemble_falsy_zero.py and meta_trainer's tests. The guard is therefore
verified by asserting on `predict_latest()`'s real source text, plus a behavioral test of the
exact pandas filtering expression it uses.
"""
import pathlib
from datetime import date, timedelta

import pandas as pd

_TRAINER_PATH = (
    pathlib.Path(__file__).resolve().parents[1] / "src" / "training" / "trainer.py"
)
_TRAINER_SOURCE = _TRAINER_PATH.read_text()


def _predict_latest_source() -> str:
    start = _TRAINER_SOURCE.index("def predict_latest(")
    end = _TRAINER_SOURCE.index("\n\n\ndef ", start)
    return _TRAINER_SOURCE[start:end]


# ── the guard exists in predict_latest() ──────────────────────────────────────

def test_predict_latest_drops_today_bar_before_building_features():
    src = _predict_latest_source()
    load_idx = src.index('_load_prices(symbol, lookback_days=400)')
    filter_idx = src.index('df[pd.to_datetime(df["ts"]).dt.date < _today]')
    build_idx = src.index("build_features(")
    # The drop must happen AFTER loading prices and BEFORE features are built off them.
    assert load_idx < filter_idx < build_idx


def test_predict_latest_guard_uses_strict_less_than_not_lte():
    """`<= today` would keep the very bar being excluded — the whole point is a strict cut."""
    src = _predict_latest_source()
    assert 'dt.date < _today' in src
    assert 'dt.date <= _today' not in src


def test_predict_latest_returns_neutral_when_no_settled_bars_remain():
    """A symbol whose only bar is today's (e.g. a fresh listing, or post-open ingest) must
    return a neutral 0.5/0-confidence result rather than proceeding with an empty frame."""
    src = _predict_latest_source()
    assert "predict_latest.no_settled_bars" in src
    guard_idx = src.index("if df.empty:")
    build_idx = src.index("build_features(")
    assert guard_idx < build_idx


def test_predict_latest_does_not_add_a_new_feature_column():
    """Inference pins X to each model's saved feature_columns (X.reindex(columns=saved_cols)),
    so a NEW live-price column would be silently dropped for all existing models until every
    one is retrained. The fix must not introduce one."""
    src = _predict_latest_source()
    for banned in ("live_price", "current_price", "last_price"):
        assert banned not in src


def test_train_model_still_has_its_own_equivalent_guard():
    """The fix is about SYMMETRY — if training ever loses its guard, inference matching it is
    meaningless. Pin both sides."""
    start = _TRAINER_SOURCE.index("def train_model(")
    end = _TRAINER_SOURCE.index("\n\n\ndef ", start)
    train_src = _TRAINER_SOURCE[start:end]
    assert 'df[pd.to_datetime(df["ts"]).dt.date < today]' in train_src


# ── the filtering expression actually behaves correctly ───────────────────────

def _apply_guard(df: pd.DataFrame, today: date) -> pd.DataFrame:
    """The exact expression predict_latest() uses."""
    return df[pd.to_datetime(df["ts"]).dt.date < today].copy()


def test_guard_removes_only_todays_bar_and_keeps_all_history():
    today = date(2026, 9, 4)
    df = pd.DataFrame({
        "ts": [today - timedelta(days=n) for n in (3, 2, 1, 0)],
        "close": [10.0, 11.0, 12.0, 99.0],  # 99.0 = the live, still-forming bar
    })
    out = _apply_guard(df, today)
    assert len(out) == 3
    assert 99.0 not in list(out["close"]), "today's live bar must be gone"
    assert list(out["close"]) == [10.0, 11.0, 12.0], "all settled history must survive"


def test_guard_is_a_noop_when_no_today_bar_exists():
    """Outside market hours / before today's ingest, nothing should change."""
    today = date(2026, 9, 4)
    df = pd.DataFrame({
        "ts": [today - timedelta(days=n) for n in (3, 2, 1)],
        "close": [10.0, 11.0, 12.0],
    })
    assert len(_apply_guard(df, today)) == 3


def test_guard_yields_empty_frame_when_only_today_bar_exists():
    today = date(2026, 9, 4)
    df = pd.DataFrame({"ts": [today], "close": [99.0]})
    assert _apply_guard(df, today).empty


def test_rolling_features_change_once_the_live_bar_is_excluded():
    """The actual harm being prevented: a partial bar shifts rolling stats. This is what made
    the same nominal feature mean different things at fit vs. predict time."""
    today = date(2026, 9, 4)
    df = pd.DataFrame({
        "ts": [today - timedelta(days=n) for n in (3, 2, 1, 0)],
        "close": [10.0, 10.0, 10.0, 40.0],  # live bar is a large intraday outlier
    })
    contaminated = df["close"].mean()
    clean = _apply_guard(df, today)["close"].mean()
    assert contaminated != clean
    assert clean == 10.0
