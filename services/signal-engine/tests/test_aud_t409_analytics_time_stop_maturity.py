"""AUD-T409-UTCDATEBOUNDARY follow-up (2026-09-21) — trade_performance()'s hypothetical
time-stop maturity check in analytics.py used `datetime.now(timezone.utc).date()` — a naive
UTC truncation. For ~4-5 hours of every evening, this reads one calendar day ahead of the real
US trading date, so `today >= max_exit_date` could resolve "has this hypothetical hold window
matured" a day EARLY — closing a simulated position on the wrong day, exactly the same class of
maturity decision evaluate_signal_outcomes() (outcomes.py) was already fixed for by T409.

analytics.py can't be imported directly in this test environment (matching this repo's own
established precedent — test_bare_gt_zero_hurdle_fix.py, test_outcomes_summary_era_split.py,
and others all source-extract from this same file rather than importing it) — verified via
source-text regression check.
"""
import pathlib

_PATH = (
    pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "analytics.py"
)
_SOURCE = _PATH.read_text()


def test_time_stop_maturity_check_uses_the_shared_today_et_helper():
    start = _SOURCE.index("# No exit signal found — apply time-stop if position has exceeded limit")
    end = _SOURCE.index("if today >= max_exit_date:", start) + len("if today >= max_exit_date:")
    body = _SOURCE[start:end]
    assert "from .signals_shared import _today_et" in body
    assert "today = _today_et()" in body
    assert "datetime.now(timezone.utc).date()" not in body


def test_today_et_import_is_the_same_shared_helper_used_elsewhere_in_this_file():
    """Regression guard: must reuse signals_shared._today_et(), not a new, second
    reimplementation of the same ET conversion — this file already imports it once elsewhere
    (the outcomes-summary `since` default), confirmed by this second import matching it."""
    assert _SOURCE.count("from .signals_shared import _today_et") >= 2
