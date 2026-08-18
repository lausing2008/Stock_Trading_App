"""Tests for wsz-analyst-accuracy-weighting's scheduler-side outcome evaluation
(_evaluate_analyst_target_outcomes) and its ingestion-side wiring in get_fundamentals().

scheduler.py can't be imported directly in this test environment (apscheduler isn't installed
locally) — source-text regression checks matching test_scheduler_static_names.py's established
pattern. routes.py similarly can't be imported directly (fastapi/yfinance/common.config module-
level imports) — matches test_fundamentals_empty_fetch_guard.py's established technique for
this exact constraint.
"""
import pathlib

_SCHED_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
_SCHED_SOURCE = _SCHED_PATH.read_text()

_ROUTES_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "routes.py"
_ROUTES_SOURCE = _ROUTES_PATH.read_text()


def _evaluate_outcomes_body() -> str:
    start = _SCHED_SOURCE.index("def _evaluate_analyst_target_outcomes(")
    end = _SCHED_SOURCE.index("\n\n\n_EARNINGS_BEAT_SCREENER_LOOKBACK_DAYS", start)
    return _SCHED_SOURCE[start:end]


def test_only_rows_past_their_own_outcome_window_are_selected_for_scoring():
    """A target graded last week hasn't had its full year to be reached yet — scoring it now
    would be a real, avoidable false negative. The query must filter on grade_date, not just
    outcome_evaluated_at being null."""
    body = _evaluate_outcomes_body()
    assert "AnalystPriceTarget.outcome_evaluated_at.is_(None)" in body
    assert "AnalystPriceTarget.grade_date <= cutoff_date" in body


def test_achieved_check_uses_both_high_and_low_not_just_close():
    """A genuine intraday/interday touch of the target should count, matching how a real
    trader would judge "did the stock get there" — using only close would miss real touches."""
    body = _evaluate_outcomes_body()
    assert "Price.high" in body
    assert "Price.low" in body
    assert "max_high" in body and "min_low" in body


def test_achieved_check_is_symmetric_for_upside_and_downside_targets():
    """Both an upside target (price rising to meet it) and a downside target (price falling
    to meet it) must be handled by the same tolerance-band check — not two separate branches
    that could diverge."""
    body = _evaluate_outcomes_body()
    assert "(max_high >= lo) or (min_low <= hi)" in body


def test_a_row_with_zero_matching_price_data_is_skipped_not_scored_as_missed():
    """No Price rows covering the window means the outcome is genuinely unknown — scoring it
    as target_achieved=False would be a fabricated negative, not a real measurement."""
    body = _evaluate_outcomes_body()
    assert "if not price_rows:" in body
    assert "n_skipped_no_price_data += 1" in body
    assert "continue" in body


def test_the_scan_is_bounded_per_run_not_an_unbounded_single_pass():
    """A large historical backlog must drain over several daily cycles, not attempt to score
    an unbounded number of rows in one job invocation."""
    body = _evaluate_outcomes_body()
    assert ".limit(" in body


def test_job_is_registered_daily_in_start_scheduler():
    assert '_evaluate_analyst_target_outcomes' in _SCHED_SOURCE
    assert 'id="analyst_target_outcomes_daily"' in _SCHED_SOURCE


# ── Ingestion side: get_fundamentals()'s new AnalystPriceTarget persist block ───────────────

def _get_fundamentals_body() -> str:
    start = _ROUTES_SOURCE.index("def get_fundamentals(")
    end = _ROUTES_SOURCE.index("\n\n\ndef ", start)
    return _ROUTES_SOURCE[start:end]


def test_current_price_target_capture_treats_a_bare_zero_as_none_not_a_real_target():
    """yfinance returns exactly 0.00 for currentPriceTarget when an action has no real price
    target attached (e.g. a plain reiteration) — this must never be captured as a literal $0
    target, which would corrupt any accuracy scoring downstream."""
    body = _get_fundamentals_body()
    assert 'current_price_target = float(_cpt) if _cpt not in (None, 0, 0.0) else None' in body


def test_analyst_price_target_persist_is_idempotent_via_on_conflict_do_nothing():
    """Re-fetching the same 90-day analyst_actions window on a later day must never duplicate
    an already-captured historical action."""
    body = _get_fundamentals_body()
    assert "on_conflict_do_nothing(" in body
    assert 'constraint="uq_analyst_price_target_stock_firm_date"' in body


def test_rows_with_no_captured_price_target_are_skipped_not_persisted_as_a_phantom_row():
    body = _get_fundamentals_body()
    assert 'if act.get("current_price_target") is None:' in body


def test_analyst_price_target_persist_is_isolated_from_the_fundamentals_persist_failure():
    """A failure persisting Fundamental rows must not prevent the (unrelated) AnalystPriceTarget
    persist from being attempted — each concern gets its own independent try/except."""
    body = _get_fundamentals_body()
    fund_persist_pos = body.index("# Persist key fields to DB for ML feature use")
    apt_persist_pos = body.index("# wsz-analyst-accuracy-weighting: persist each per-firm price-target action")
    assert fund_persist_pos < apt_persist_pos
    # Both blocks must have their own except clause between them / after them (2 independent
    # try/except pairs, not one shared block).
    segment = body[fund_persist_pos:apt_persist_pos]
    assert "except Exception" in segment
