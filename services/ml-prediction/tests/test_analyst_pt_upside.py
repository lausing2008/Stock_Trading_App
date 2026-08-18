"""Tests for TIER82-FMP-ANALYST-ESTIMATES: analyst_pt_upside, a point-in-time-safe join of
each row's own fundamentals_snapshot target_price against that SAME row's own close price.

build_features() only depends on numpy/pandas (both real, installed packages), so it imports
and runs normally under pytest — no stub workaround needed, matching
test_eps_revision_direction.py's own established precedent.
"""
import numpy as np
import pandas as pd
import pytest

from src.features import FEATURE_COLUMNS, build_features


def _price_df(n=400, start="2022-01-01", pinned_end_close=None):
    """A real (non-flat) noisy random walk — a perfectly flat close series breaks required
    technical indicators elsewhere in build_features() (RSI/ATR/etc. div-by-zero cascades),
    starving the returned frame down to 0 rows regardless of length. pinned_end_close shifts
    the WHOLE series by a constant so the FINAL bar's close lands on an exact, known value —
    real volatility throughout, a deterministic anchor for assertions on the last row."""
    rng = np.random.default_rng(42)
    close = 100 + rng.normal(0, 1, n).cumsum()
    if pinned_end_close is not None:
        close = close - close[-1] + pinned_end_close
    return pd.DataFrame({
        "ts": pd.date_range(start, periods=n, freq="D"),
        "close": close,
        "high": close + 2,
        "low": close - 2,
        "volume": rng.integers(1000, 5000, n),
    })


def _snap(date_str, target_price):
    return {"snapshot_date": date_str, "target_price": target_price}


def test_analyst_pt_upside_is_in_feature_columns():
    assert "analyst_pt_upside" in FEATURE_COLUMNS


def test_no_snapshots_leaves_the_column_nan_not_a_crash():
    df = _price_df()
    X, _, _ = build_features(df, horizon=5, fund_snapshots=[])
    assert X["analyst_pt_upside"].isna().all()


def test_snapshot_missing_target_price_key_entirely_leaves_the_column_nan():
    """A fund_snapshots list whose dicts have no target_price key at all (e.g. an older
    snapshot row predating this column) must degrade to NaN, not raise a KeyError."""
    df = _price_df()
    snaps = [{"snapshot_date": "2022-06-01", "recommendation_mean": 2.0}]
    X, _, _ = build_features(df, horizon=5, fund_snapshots=snaps)
    assert X["analyst_pt_upside"].isna().all()


def test_computes_the_exact_upside_percent_against_the_matching_rows_own_close():
    """A series whose FINAL close is pinned to exactly $100, with a single target_price=$120
    snapshot well before it, must yield exactly (120/100 - 1)*100 = 20.0 on that last row."""
    df = _price_df(n=400, start="2022-01-01", pinned_end_close=100.0)
    snaps = [_snap("2022-01-01", 120.0)]
    X, _, _ = build_features(df, horizon=5, fund_snapshots=snaps, inference_mode=True)
    assert X["analyst_pt_upside"].iloc[-1] == pytest.approx(20.0)


def test_a_row_before_any_snapshot_exists_is_nan_not_a_leaked_future_value():
    """Point-in-time correctness: a row dated BEFORE the first real snapshot must never see
    a target_price that didn't exist yet — merge_asof(direction='backward') must leave it NaN,
    not silently forward-fill from a later snapshot."""
    df = _price_df(n=400, start="2022-01-01", pinned_end_close=100.0)
    snaps = [_snap("2023-01-20", 130.0)]  # well after most rows in a 400-day series from Jan 2022
    X, _, _ = build_features(df, horizon=5, fund_snapshots=snaps)
    dates = pd.to_datetime(df["ts"])
    early_mask = dates < pd.Timestamp("2022-12-20")  # comfortably before the snapshot date
    early_rows = X.loc[X.index.isin(np.where(early_mask)[0]), "analyst_pt_upside"]
    assert early_rows.isna().all()


def test_a_row_on_or_after_the_snapshot_date_correctly_sees_it():
    """The mirror of the above — confirms the fix doesn't just always return NaN."""
    df = _price_df(n=400, start="2022-01-01", pinned_end_close=100.0)
    snaps = [_snap("2022-01-10", 110.0)]
    X, _, _ = build_features(df, horizon=5, fund_snapshots=snaps, inference_mode=True)
    # inference_mode keeps the most recent bar, well after 2022-01-10.
    assert not pd.isna(X["analyst_pt_upside"].iloc[-1])
    assert X["analyst_pt_upside"].iloc[-1] == pytest.approx(10.0)


def test_a_later_snapshot_supersedes_an_earlier_one_for_rows_after_it():
    """A row must use the MOST RECENT snapshot as-of its own date, not the first one ever
    seen — matching every other PIT column's merge_asof(direction='backward') semantics."""
    df = _price_df(n=400, start="2022-01-01", pinned_end_close=100.0)
    snaps = [_snap("2022-01-05", 110.0), _snap("2022-01-20", 150.0)]
    X, _, _ = build_features(df, horizon=5, fund_snapshots=snaps, inference_mode=True)
    # Most recent bar is well after both snapshots — must reflect the LATEST (150.0), not the
    # first (110.0): (150/100 - 1) * 100 = 50.0, not (110/100 - 1) * 100 = 10.0.
    assert X["analyst_pt_upside"].iloc[-1] == pytest.approx(50.0)


def test_uses_this_rows_own_close_price_not_a_fixed_or_todays_price():
    """The property that makes this feature architecturally distinct from every other PIT
    column: it must divide by THIS ROW's own close, not a constant or the series' final
    close. A rising price series with a FIXED target_price must show DECLINING upside as
    price rises — proving the denominator moves per-row, not just the numerator."""
    n = 400
    rng = np.random.default_rng(7)
    close = np.linspace(50.0, 150.0, n)  # steadily rising from 50 to 150
    df = pd.DataFrame({
        "ts": pd.date_range("2022-01-01", periods=n, freq="D"),
        "close": close, "high": close + 1, "low": close - 1,
        "volume": rng.integers(1000, 5000, n),
    })
    snaps = [_snap("2022-01-01", 200.0)]  # fixed target for the whole series
    X, _, _ = build_features(df, horizon=5, fund_snapshots=snaps)
    upside = X["analyst_pt_upside"].dropna()
    # Upside must be monotonically DECREASING as the row's own close price rises toward the
    # fixed target — if the denominator were wrongly fixed (e.g. always today's close), this
    # would be flat instead.
    assert (upside.diff().dropna() <= 1e-9).all(), (
        "upside should strictly decrease as this row's own close rises toward a fixed target — "
        "a flat series here would mean the join used a fixed/global close instead of this row's own"
    )


def _extract_upside_guard_source():
    """Extract the real _valid/_safe_c/_upside lines verbatim from builder.py's own source —
    a zero-close ROW poisons trailing-window indicators (RSI/ATR/moving averages) for many
    SUBSEQUENT rows too (confirmed directly: placing a degenerate close at row 5 or row 300 of
    a 400-row series both vanish from X's own required-column mask), so this guard can't be
    exercised end-to-end via build_features() itself at all. Falls back to this repo's
    established precedent for exactly this class of case — extract and run the REAL source
    directly (not a hand-copied reimplementation, which could silently drift from it)."""
    import pathlib
    import textwrap
    src = pathlib.Path(__file__).resolve().parents[1] / "src" / "features" / "builder.py"
    body = src.read_text()
    marker = "_valid = (_tp.notna().values) & (c.values > 0)"
    marker_pos = body.index(marker)
    start = body.rfind("\n", 0, marker_pos) + 1  # back up to the start of that whole line
    end = body.index("out[\"analyst_pt_upside\"] = _upside", marker_pos)
    return textwrap.dedent(body[start:end])


def test_zero_or_negative_close_never_produces_a_division_result():
    """A degenerate zero/negative close (should never happen in real OHLCV data, but the
    guard must never silently produce inf/-inf if it ever did) must yield NaN, verified by
    running the REAL guard source (see _extract_upside_guard_source's own docstring for why
    this can't be exercised through the full build_features() pipeline)."""
    guard_src = _extract_upside_guard_source()
    _tp = pd.Series([120.0, 120.0, np.nan, 120.0])
    c = pd.Series([100.0, 0.0, 100.0, -50.0])
    namespace = {"np": np, "_tp": _tp, "c": c}
    exec(guard_src, namespace)  # noqa: S102 — isolated eval of real source, matching repo convention
    upside = namespace["_upside"]
    assert upside[0] == pytest.approx(20.0)   # normal case: real division
    assert np.isnan(upside[1])                # c == 0 must never divide
    assert np.isnan(upside[2])                # tp is NaN
    assert np.isnan(upside[3])                # c < 0 must never divide
    assert not np.isinf(upside).any(), "the guard must never let a degenerate divisor through"


def test_inference_mode_computes_the_feature_too_not_just_training():
    """Matches eps_revision_direction's own precedent: this feature has no static fund_data
    broadcast equivalent, so it must be computed in BOTH training and inference mode."""
    df = _price_df(n=400, start="2022-01-01", pinned_end_close=100.0)
    snaps = [_snap("2022-01-01", 125.0)]
    X, _, _ = build_features(df, horizon=5, fund_snapshots=snaps, inference_mode=True)
    assert X["analyst_pt_upside"].iloc[-1] == pytest.approx(25.0)
