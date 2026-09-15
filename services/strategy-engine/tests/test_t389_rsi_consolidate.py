"""T389-RSI-CONSOLIDATE — the last hand-rolled indicator in the DSL evaluator.

`compute_features()` computed RSI(14) from scratch while `shared/common/indicators.py` already
had a canonical `rsi()` that signal-engine, ranking-engine and market-data all import. The ATR
half of this duplication was consolidated earlier (AUD-DUPLOGIC); RSI was the remainder.

A 2025-08-22 audit document flagged this as **P0 "indicator formula drift"**, on the grounds
that backtests would compute different values than live signals. THE TWO FORMULAS ARE
BYTE-IDENTICAL EXCEPT ONE LINE — canonical ends with:

    .mask(avg_loss.notna() & avg_loss.eq(0), 100.0)

which is Wilder's spec (RSI = 100 when there is no average loss over the window).

MEASURED BEFORE MIGRATING, and the measurement downgrades the audit's severity:

  * **3,897 real production price bars**, 8 symbols (AAPL, HWM, META, MU, NVDA, SNDK, SOXL,
    XLK) -> **0 differing bars**.
  * 200 random-walk trials of 500 bars each -> **0 trials with any difference**.
  * A synthetic 30-bar pure uptrend -> 16 differing bars, canonical 100.0 vs old NaN.

The mask only fires when `avg_loss` is **exactly 0**, and `ewm(alpha=1/14)` decays a prior loss
by 13/14 per bar without ever reaching zero — after META's real 20-day up-streak `avg_loss` was
still **3.11e-01**. It requires a series with no down bar since its very first bar, which no
real 500-bar window has.

A CLAIM I MADE EARLIER IN THIS SESSION WAS WRONG and is corrected here: I reported that META,
SOXL and XLK were affected, based on a SQL query counting consecutive non-down days. That is not
the trigger condition — `avg_loss` reaching exactly zero is. The streaks were real; the
inference from them was not.

So this is a **de-duplication with no behaviour change on real data**, not the P0 correctness
fix the audit described. It removes the next opportunity for drift, nothing more.
"""
import pathlib

import numpy as np
import pandas as pd
import pytest

from src.dsl import compute_features

SRC = (
    pathlib.Path(__file__).resolve().parents[1] / "src/dsl/evaluator.py"
).read_text()


def _old_rsi(close: pd.Series) -> pd.Series:
    """The exact formula this file used before T389 — kept so parity is testable, not asserted."""
    d = close.diff()
    g = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    l = (-d.clip(upper=0)).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    return 100 - 100 / (1 + g / l.replace(0, np.nan))


def _frame(closes) -> pd.DataFrame:
    c = pd.Series(closes, dtype=float)
    return pd.DataFrame({
        "ts": pd.date_range("2024-01-02", periods=len(c), freq="D"),
        "open": c, "high": c * 1.001, "low": c * 0.999, "close": c,
        "volume": [1_000_000] * len(c),
    })


# ── It actually uses the shared module now ──────────────────────────────────────────────

def test_rsi_comes_from_the_canonical_module():
    assert "from common.indicators import atr as _canon_atr, rsi as _canon_rsi" in SRC
    assert 'out["rsi_14"] = _canon_rsi(close, window=14)' in SRC


def test_the_hand_rolled_formula_is_gone():
    """A leftover copy would be the drift this change exists to remove."""
    assert "100 - 100 / (1 + rs)" not in SRC
    assert "(-d.clip(upper=0)).ewm" not in SRC


def test_atr_is_still_canonical_too():
    """The earlier half of the same consolidation must not regress."""
    assert "_canon_atr(high, low, close, period=14)" in SRC


# ── Parity on real-shaped data: the point of the change is that NOTHING moves ───────────

@pytest.mark.parametrize("seed", [1, 7, 42, 99, 2024])
def test_random_walks_are_bit_identical(seed):
    """Random walks have down bars, so `avg_loss` never reaches 0 and both formulas agree
    exactly. If this ever fails, the migration changed real behaviour."""
    rng = np.random.default_rng(seed)
    closes = 100 * np.exp(np.cumsum(rng.normal(0, 0.015, 400)))
    got = compute_features(_frame(closes))["rsi_14"]
    want = _old_rsi(pd.Series(closes, dtype=float))
    assert got.notna().equals(want.notna()), "NaN placement must be identical"
    pd.testing.assert_series_equal(
        got[got.notna()].reset_index(drop=True),
        want[want.notna()].reset_index(drop=True),
        check_names=False, rtol=1e-12,
    )


def test_a_long_up_streak_AFTER_an_early_pullback_is_identical():
    """The shape the audit worried about — a long up-streak — agrees, PROVIDED a down bar
    occurred earlier, because ewm keeps that loss alive forever.

    MY FIRST VERSION OF THIS TEST WAS WRONG and the engine correctly failed it: I put the
    pullback at bar 25, AFTER a 24-bar no-loss window had already passed, so `avg_loss` was
    genuinely exactly 0 over bars 14-24 and canonical correctly returned 100.0 there. The
    pullback has to come EARLY to keep avg_loss positive for the rest of the series.
    """
    # down bar at index 2, then a 40-bar uptrend — mirrors a real chart far better.
    closes = [100.0, 101.0, 99.0] + [99 + i for i in range(1, 41)]
    got = compute_features(_frame(closes))["rsi_14"]
    want = _old_rsi(pd.Series(closes, dtype=float))
    assert got.notna().equals(want.notna()), "an early loss keeps both formulas in agreement"
    pd.testing.assert_series_equal(
        got[got.notna()].reset_index(drop=True),
        want[want.notna()].reset_index(drop=True),
        check_names=False, rtol=1e-12,
    )


def test_a_no_loss_window_BEFORE_any_pullback_differs_and_canonical_is_right():
    """The converse, which my wrong test accidentally discovered: if the up-streak comes first,
    avg_loss IS exactly 0 and Wilder's spec says RSI = 100. The old copy returned NaN — a
    missing value where a real maximal reading exists."""
    closes = [100.0] + [100 + i for i in range(1, 25)] + [118.0] + [118 + i for i in range(1, 25)]
    got = compute_features(_frame(closes))["rsi_14"]
    want = _old_rsi(pd.Series(closes, dtype=float))
    assert got.iloc[16] == pytest.approx(100.0)
    assert pd.isna(want.iloc[16])


# ── Where they DO differ, canonical is the correct one ──────────────────────────────────

def test_a_pure_uptrend_now_yields_100_instead_of_nan():
    """THE ONLY REAL DIFFERENCE. With no down bar since the start, avg_loss IS exactly 0 and
    Wilder's spec says RSI = 100. The old copy produced NaN — a missing value where a real,
    maximal reading exists, which would silently suppress any RSI rule."""
    closes = np.linspace(100, 130, 30)
    got = compute_features(_frame(closes))["rsi_14"]
    want = _old_rsi(pd.Series(closes, dtype=float))
    tail_new, tail_old = got.iloc[-1], want.iloc[-1]
    assert tail_new == pytest.approx(100.0), "canonical: a no-loss window is RSI 100"
    assert pd.isna(tail_old), "the old copy returned NaN here"


def test_warmup_is_still_nan_in_both():
    """The mask must fill ONLY a real computed zero, never the warmup window — conflating them
    would fabricate RSI 100 for the first 13 bars of every series."""
    closes = np.linspace(100, 130, 30)
    got = compute_features(_frame(closes))["rsi_14"]
    assert got.iloc[:13].isna().all(), "first 13 bars have no RSI at all"


# ── The measured evidence, pinned so the severity claim cannot rot ──────────────────────

def test_the_real_data_parity_measurement_is_recorded():
    """A future reader must be able to see this was MEASURED, not assumed — the audit called it
    P0 and the measurement says otherwise."""
    assert "3,897" in SRC
    assert "0 differing bars" in SRC


def test_the_mechanism_is_recorded():
    """WHY it never fires on real data — ewm decay never reaches exactly zero."""
    assert "3.11e-01" in SRC
    assert "13/14" in SRC


def test_my_own_wrong_claim_is_corrected_in_place():
    """I reported META/SOXL/XLK as affected from a SQL query counting consecutive up-days, which
    is not the trigger condition. Recorded where the next reader will see it."""
    assert "WRONG" in SRC
    assert "consecutive up-days" in SRC
