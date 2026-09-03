"""Test for AUD-SQUEEZE2-MIXEDDIRECTIONRETURN (Short Squeeze Alerts deep audit, 2026-09-03):
options_flow_alert_backtest()'s _bucket_stats() (admin.py) previously averaged RAW (unflipped)
returns for by_sweep/by_volume_oi_band — groupings that mix BOTH bullish and bearish alerts
into the same bucket. A genuine bullish win (+5%) and a genuine bearish win (-5%) would average
to ~0%, understating real performance whenever a bucket mixed directions. by_direction itself
was already fine (each of its buckets is single-direction by construction).

Fixed: _bucket_stats() now sign-flips the bearish rows' return before it enters avg_return_pct,
so the returned number always means "return in the direction the alert's own thesis predicted"
(positive = thesis was right) — consistent across all 3 groupings, matching is_correct's own
semantics exactly.

Uses the SAME source-text-exec extraction technique as test_options_flow_alert_backtest.py
(admin.py needs a real DB session end-to-end; _bucket_stats/_windows_for are pure,
dependency-free nested functions).
"""
import re
import pathlib

_admin_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "admin.py"
_admin_source = _admin_path.read_text()


def _function_body(name: str, source: str, end_marker: str) -> str:
    start = source.index(f"def {name}(")
    end = source.index(end_marker, start)
    return source[start:end]


_BACKTEST_BODY = _function_body(
    "options_flow_alert_backtest", _admin_source, "\n\n@router.get(\"/watchlist-rotation-history\")"
)


def _extract_bucket_stats():
    start = _BACKTEST_BODY.index("def _bucket_stats(")
    end = _BACKTEST_BODY.index("\n\n    by_direction = [")
    body = _BACKTEST_BODY[start:end]
    body = re.sub(r"(?m)^    ", "", body)
    namespace = {
        "min_samples": 1,
        "_SQUEEZE_OUTCOME_WIN_HURDLE_PCT": 0.005,
    }
    exec(body, namespace)  # noqa: S102 — isolated eval of one pure function's real source
    return namespace["_bucket_stats"]


_bucket_stats = _extract_bucket_stats()


def _row(direction: str, ret: float):
    return {"direction": direction, "returns": {1: ret}}


def test_mixed_bucket_bullish_win_and_bearish_win_no_longer_cancel_out():
    """The exact confirmed bug: a genuine +5% bullish win and a genuine -5% bearish win
    (both real successes) previously averaged to ~0% when blended into the same bucket
    (e.g. by_sweep or by_volume_oi_band) — must now both contribute positively."""
    rows = [_row("bullish", 0.05), _row("bearish", -0.05)]
    stats = _bucket_stats(rows, 1)
    assert stats["win_rate"] == 1.0
    assert stats["avg_return_pct"] == 5.0  # not ~0.0


def test_bullish_only_bucket_return_sign_unchanged():
    rows = [_row("bullish", 0.03), _row("bullish", -0.01)]
    stats = _bucket_stats(rows, 1)
    assert stats["avg_return_pct"] == 1.0  # (3 + -1) / 2, unchanged from before the fix


def test_bearish_only_bucket_return_is_now_sign_flipped():
    """Regression guard: by_direction's own bearish bucket previously showed the RAW/unflipped
    average (e.g. a genuine bearish win at ret=-0.05 showed avg_return_pct=-5.0). After the fix
    it must show the THESIS-direction return (+5.0) — positive means the bearish call was
    right, consistent with the other two groupings and with is_correct's own semantics."""
    rows = [_row("bearish", -0.05), _row("bearish", -0.03)]
    stats = _bucket_stats(rows, 1)
    assert stats["win_rate"] == 1.0
    assert stats["avg_return_pct"] == 4.0  # (5 + 3) / 2, sign-flipped


def test_mixed_bucket_with_a_bearish_loss_scores_correctly():
    """A bearish alert where price ROSE (a genuine loss) must still contribute a NEGATIVE
    thesis-return, not get double-flipped into a false positive."""
    rows = [_row("bullish", 0.05), _row("bearish", 0.03)]  # bearish here LOST (price rose)
    stats = _bucket_stats(rows, 1)
    assert stats["win_rate"] == 0.5
    assert stats["avg_return_pct"] == 1.0  # (5 + -3) / 2
