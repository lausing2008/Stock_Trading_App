"""T398-OPTIONS-INCOME-ENGINE: covered-call / cash-secured-put systematic engine.

_score_contract() and settle_position_economics() are pure (no DB access — matching
compute_options_game_plan()'s own established "pure function" precedent in routes.py), so they
get real behavioral unit tests against synthetic numbers. The DB-facing functions
(rank_income_candidates, settle_expired_positions, open_income_positions,
run_options_income_step) are thin glue around those two — nothing meaningful to assert against
a MagicMock session (matching test_options_game_plan_snapshot.py's own established precedent
for this exact class of function), so those are covered by source-text checks for the
properties that actually matter: the SQL's own filters, the scheduler wiring, and the cash
accounting order.
"""
import pathlib

from src.services.options_income_engine import (
    _score_contract, settle_position_economics, _DEFAULT_INCOME_CONFIG, _INCOME_UNIVERSE,
    _INCOME_MIN_DTE, _INCOME_MAX_DTE, _INCOME_MIN_ABS_DELTA, _INCOME_MAX_ABS_DELTA,
)

_ENGINE_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "options_income_engine.py"
_ENGINE_SOURCE = _ENGINE_PATH.read_text()

_SCHEDULER_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
_SCHEDULER_SOURCE = _SCHEDULER_PATH.read_text()


# ── _score_contract() — pure yield/economics math ─────────────────────────────

def test_covered_call_yield_and_cap_price():
    m = _score_contract(strategy="COVERED_CALL", strike=105.0, premium_bid=2.0, current_price=100.0, dte=30)
    assert m["premium_per_contract"] == 200.0
    assert m["effective_cap_price"] == 107.0
    assert m["collateral_required"] == 10_000.0  # buy-write cost basis: price * 100
    # annualized = 2/100 * (365/30) * 100
    assert m["annualized_yield_pct"] == round(2.0 / 100.0 * (365.0 / 30.0) * 100.0, 2)


def test_csp_yield_and_purchase_price():
    m = _score_contract(strategy="CASH_SECURED_PUT", strike=95.0, premium_bid=1.5, current_price=100.0, dte=30)
    assert m["premium_per_contract"] == 150.0
    assert m["effective_purchase_price"] == 93.5
    assert m["collateral_required"] == 9_500.0  # strike-based reserve, not price-based


def test_shorter_dte_produces_higher_annualized_yield_for_the_same_premium():
    # A $2 premium collected in 15 days is a better ANNUALIZED rate than the same $2 in 45 days.
    short = _score_contract(strategy="COVERED_CALL", strike=105.0, premium_bid=2.0, current_price=100.0, dte=15)
    long_ = _score_contract(strategy="COVERED_CALL", strike=105.0, premium_bid=2.0, current_price=100.0, dte=45)
    assert short["annualized_yield_pct"] > long_["annualized_yield_pct"]


# ── settle_position_economics() — the actual trading-correctness surface ──────

def test_covered_call_otm_liquidates_at_market_close():
    r = settle_position_economics(
        strategy="COVERED_CALL", strike=110.0, premium_collected=200.0, contracts=1,
        underlying_entry_price=100.0, close_price=105.0,  # below strike -> OTM, not assigned
    )
    assert r["assigned"] is False
    assert r["settle_price"] == 105.0
    # pnl = premium + (close - entry) * 100 = 200 + 500 = 700
    assert r["pnl"] == 700.0
    assert r["cash_released"] == 10_500.0


def test_covered_call_assigned_caps_gain_at_strike():
    r = settle_position_economics(
        strategy="COVERED_CALL", strike=110.0, premium_collected=200.0, contracts=1,
        underlying_entry_price=100.0, close_price=130.0,  # well above strike -> assigned
    )
    assert r["assigned"] is True
    assert r["settle_price"] == 110.0  # capped at strike, NOT the real 130 close
    # pnl = premium + (strike - entry) * 100 = 200 + 1000 = 1200 (the uncapped gain would be 3200)
    assert r["pnl"] == 1200.0
    assert r["cash_released"] == 11_000.0


def test_csp_otm_keeps_full_premium_no_assignment():
    r = settle_position_economics(
        strategy="CASH_SECURED_PUT", strike=90.0, premium_collected=150.0, contracts=1,
        underlying_entry_price=100.0, close_price=95.0,  # above strike -> OTM
    )
    assert r["assigned"] is False
    assert r["settle_price"] == 90.0  # full collateral released, unchanged
    assert r["pnl"] == 150.0  # pure premium, no assignment loss
    assert r["cash_released"] == 9_000.0


def test_csp_assigned_marks_to_market_and_realizes_the_loss():
    r = settle_position_economics(
        strategy="CASH_SECURED_PUT", strike=90.0, premium_collected=150.0, contracts=1,
        underlying_entry_price=100.0, close_price=80.0,  # below strike -> assigned
    )
    assert r["assigned"] is True
    assert r["settle_price"] == 80.0
    # pnl = premium + (close - strike) * 100 = 150 + (80-90)*100 = 150 - 1000 = -850
    assert r["pnl"] == -850.0
    assert r["cash_released"] == 8_000.0


def test_multiple_contracts_scale_pnl_and_cash_linearly():
    one = settle_position_economics(
        strategy="COVERED_CALL", strike=110.0, premium_collected=200.0, contracts=1,
        underlying_entry_price=100.0, close_price=105.0,
    )
    three = settle_position_economics(
        strategy="COVERED_CALL", strike=110.0, premium_collected=600.0, contracts=3,
        underlying_entry_price=100.0, close_price=105.0,
    )
    assert three["pnl"] == one["pnl"] * 3
    assert three["cash_released"] == one["cash_released"] * 3


# ── Config / universe sanity ───────────────────────────────────────────────────

def test_default_config_enables_both_strategies():
    assert set(_DEFAULT_INCOME_CONFIG["strategies"]) == {"COVERED_CALL", "CASH_SECURED_PUT"}


def test_income_universe_is_the_opthist_archived_symbol_set():
    # Must stay a SUBSET of scheduler.py's own _OPTHIST_SYMBOLS — anything outside that set has
    # no daily-archived chain to read, so rank_income_candidates() would silently find nothing.
    assert "_OPTHIST_SYMBOLS = [" in _SCHEDULER_SOURCE
    for sym in _INCOME_UNIVERSE:
        assert f'"{sym}"' in _SCHEDULER_SOURCE


def test_delta_band_and_dte_window_are_income_appropriate():
    assert 0 < _INCOME_MIN_ABS_DELTA < _INCOME_MAX_ABS_DELTA < 0.5
    assert 0 < _INCOME_MIN_DTE < _INCOME_MAX_DTE


# ── DB-facing glue: source-text checks for the properties that actually matter ─

def test_candidates_are_sold_at_the_bid_not_mid_or_ask():
    assert "premium = float(r.nbbo_bid)" in _ENGINE_SOURCE


def test_candidate_sql_requires_a_real_positive_bid():
    assert "nbbo_bid IS NOT NULL AND nbbo_bid > 0" in _ENGINE_SOURCE


def test_candidate_sql_filters_on_the_delta_band_and_liquidity_floor():
    assert "ABS(delta) BETWEEN :min_delta AND :max_delta" in _ENGINE_SOURCE
    assert "open_interest >= :min_oi" in _ENGINE_SOURCE


def test_settlement_never_guesses_a_missing_close_price():
    body = _ENGINE_SOURCE[_ENGINE_SOURCE.index("def settle_expired_positions"):]
    body = body[:body.index("\ndef ")]
    assert "if close_price is None:" in body
    assert "continue" in body


def test_open_positions_credits_premium_and_debits_collateral():
    body = _ENGINE_SOURCE[_ENGINE_SOURCE.index("def open_income_positions"):]
    body = body[:body.index("\ndef ")]
    assert "- collateral + total_premium" in body


def test_entries_respect_available_cash():
    body = _ENGINE_SOURCE[_ENGINE_SOURCE.index("def open_income_positions"):]
    body = body[:body.index("\ndef ")]
    assert "if collateral > float(portfolio.current_cash):" in body
    assert "continue" in body


def test_run_step_settles_before_opening_new_positions():
    body = _ENGINE_SOURCE[_ENGINE_SOURCE.index("def run_options_income_step"):]
    assert body.index("settle_expired_positions") < body.index("open_income_positions")


def test_run_step_skips_weekends():
    body = _ENGINE_SOURCE[_ENGINE_SOURCE.index("def run_options_income_step"):]
    body = body[:body.index("with SessionLocal")]
    assert "weekday() >= 5" in body


def test_one_portfolio_failure_does_not_abort_the_batch():
    body = _ENGINE_SOURCE[_ENGINE_SOURCE.index("def run_options_income_step"):]
    assert body.count("except Exception:") >= 3  # settle, open, equity-snapshot each isolated


# ── Scheduler wiring ────────────────────────────────────────────────────────────

def test_scheduled_after_the_opthist_daily_capture_same_day():
    idx = _SCHEDULER_SOURCE.index('id="options_income_step"')
    surrounding = _SCHEDULER_SOURCE[idx - 400:idx + 50]
    assert "hour=19, minute=0" in surrounding
    assert 'timezone="America/New_York"' in surrounding


def test_options_income_job_registered_with_replace_existing_and_job_defaults():
    idx = _SCHEDULER_SOURCE.index('id="options_income_step"')
    surrounding = _SCHEDULER_SOURCE[idx - 50:idx + 100]
    assert "replace_existing=True" in surrounding
    assert "_JOB_DEFAULTS" in surrounding
