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

import pytest

from src.services.options_income_engine import (
    _score_contract, settle_position_economics, _DEFAULT_INCOME_CONFIG, _INCOME_UNIVERSE,
    _INCOME_MIN_DTE, _INCOME_MAX_DTE, _INCOME_MIN_ABS_DELTA, _INCOME_MAX_ABS_DELTA,
    _INCOME_MAX_CHAIN_STALENESS_DAYS, _INCOME_MIN_CUSHION_PCT, quality_score,
    leverage_factor, _LEVERAGED_SYMBOLS,
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
    """SUPERSEDED BY A BEHAVIORAL TEST — see test_a17_settlement_behavior.py.

    This asserted `"if settled is None:" in body`: a check on a local VARIABLE NAME. It was
    satisfied by source that raised TypeError on every successful settlement (AUD-A17), and it
    broke the moment that variable was correctly renamed — failing for a rename while passing
    for a real defect, which is precisely backwards.

    The property it meant to guard (a missing settlement session leaves the position OPEN rather
    than settling against another day's close) is now asserted against real behaviour by
    `test_missing_settlement_price_returns_int_not_none`, which checks `pos.stage == "open"`,
    an integer return of 0, and that nothing was committed.

    What remains here is the one thing worth pinning textually: settlement resolves the exact
    expected session and never falls back to the lenient entry-pricing helper.
    """
    body = _ENGINE_SOURCE[_ENGINE_SOURCE.index("def settle_expired_positions"):]
    body = body[:body.index("\ndef ")]
    assert "_settlement_close(" in body, "settlement must use the exact-session helper"
    assert "_chain_as_of_on_or_before" not in body, "must never use the lenient backward window"


def test_open_positions_credits_premium_and_debits_collateral():
    body = _ENGINE_SOURCE[_ENGINE_SOURCE.index("def open_income_positions"):]
    body = body[:body.index("\ndef ")]
    assert "- collateral + total_premium" in body


def test_entries_respect_available_cash():
    body = _ENGINE_SOURCE[_ENGINE_SOURCE.index("def open_income_positions"):]
    body = body[:body.index("\ndef ")]
    assert "if collateral > float(portfolio.current_cash):" in body
    assert "continue" in body


def test_entries_respect_the_per_position_concentration_cap():
    # AUD-T398-CONCENTRATION: measured live — a single AMD cash-secured put needed $50,000 of
    # collateral (one contract on a $500 stock), which on a $50k portfolio would otherwise
    # consume the ENTIRE book in one trade regardless of max_positions. This is the gate that
    # stops that: sized against initial_capital, not current_cash, so it doesn't get looser or
    # tighter as the day's cash balance moves.
    body = _ENGINE_SOURCE[_ENGINE_SOURCE.index("def open_income_positions"):]
    body = body[:body.index("\ndef ")]
    assert "max_collateral_pct_per_position" in body
    assert "float(portfolio.initial_capital)" in body
    assert "if collateral > max_collateral:" in body


def test_concentration_cap_has_a_sane_default():
    assert 0 < _DEFAULT_INCOME_CONFIG["max_collateral_pct_per_position"] <= 0.5


def test_entries_per_day_is_counted_across_calls_not_per_call():
    # AUD-T398-PERCALL-NOT-PERDAY: a second same-day call (admin /run-step, a misfire retry)
    # must NOT get a fresh max_entries_per_day budget on top of what already opened today.
    body = _ENGINE_SOURCE[_ENGINE_SOURCE.index("def open_income_positions"):]
    body = body[:body.index("\ndef ")]
    assert "already_opened_today" in body
    assert "OptionsIncomePosition.entry_date == today" in body
    assert "todays_remaining_budget" in body
    # The weak form of this check just confirms the variable is DEFINED somewhere in the
    # function — it must also be the thing max_new is actually computed FROM, or a sabotage
    # that quietly reverts max_new to the raw per-call config value passes undetected (caught
    # exactly this way once already while writing this test).
    assert "max_new = min(todays_remaining_budget," in body


def test_settlement_reresolves_a_missing_stock_id_instead_of_zombieing_forever():
    # AUD-T398-ZOMBIEPOSITION: stock_id is only set once at entry; if that lookup failed then
    # (symbol not yet in the Stock table), the position must not be permanently unsettleable.
    body = _ENGINE_SOURCE[_ENGINE_SOURCE.index("def settle_expired_positions"):]
    body = body[:body.index("\ndef ")]
    assert "Stock.symbol == pos.symbol" in body
    assert "pos.stock_id = stock.id" in body


def test_quality_score_prefers_cushion_over_raw_yield():
    """The whole point: the richest premium in the universe is rich BECAUSE it's riskiest.
    A high-yield contract sitting at the money must not outrank a moderate one with real
    buffer and real liquidity — that's adverse selection, which raw-yield ranking guarantees."""
    yield_trap = quality_score(annualized_yield_pct=60, otm_cushion_pct=0.5, open_interest=100)
    solid = quality_score(annualized_yield_pct=25, otm_cushion_pct=8.4, open_interest=10716)
    assert solid > yield_trap


def test_quality_score_components_saturate():
    """No single component may run away with the score — an absurd yield can't outweigh
    having no cushion and no liquidity."""
    absurd_yield = quality_score(annualized_yield_pct=100_000, otm_cushion_pct=0, open_interest=0)
    assert absurd_yield <= 41.0  # the yield weight alone, nothing more


def test_quality_score_is_bounded_0_to_100():
    assert quality_score(annualized_yield_pct=0, otm_cushion_pct=0, open_interest=0) == 0.0
    assert quality_score(annualized_yield_pct=999, otm_cushion_pct=999, open_interest=999999) == 100.0


def test_quality_score_handles_missing_open_interest():
    # open_interest is nullable in the chain archive — must not raise.
    assert quality_score(annualized_yield_pct=20, otm_cushion_pct=5, open_interest=None) >= 0


def test_cushion_dominates_the_derived_weights():
    """T399-WEIGHTDERIV: weights are derived from 50,787 settled backtest contracts, not chosen.
    Cushion must carry the most weight — yield and cushion push assignment risk in OPPOSITE
    directions (assignment rises 10.4%->28.7% across yield bands, falls 66.7%->19.4% across
    cushion bands), so the original equal weighting made them cancel."""
    from src.services.options_income_engine import (
        _Q_WEIGHT_YIELD, _Q_WEIGHT_CUSHION, _Q_WEIGHT_LIQUIDITY,
    )
    assert _Q_WEIGHT_CUSHION > _Q_WEIGHT_YIELD
    assert pytest.approx(_Q_WEIGHT_YIELD + _Q_WEIGHT_CUSHION + _Q_WEIGHT_LIQUIDITY) == 1.0


def test_liquidity_weight_is_zero_on_purpose():
    """Guards a counter-intuitive derived result against being 'fixed' back. Open interest is
    NEGATIVELY correlated with cushion in the real pool (OI>=2000 averages 8.13% cushion,
    OI<2000 averages 11.18%), so weighting liquidity pulls selection toward THINNER cushion and
    fights the strongest signal — adding even 0.05 cost 3.16% -> 2.40% out of sample.
    Illiquidity risk is handled by the hard _INCOME_MIN_OPEN_INTEREST floor, a filter not a
    weight, so OI still gates candidates even at zero weight."""
    from src.services.options_income_engine import _Q_WEIGHT_LIQUIDITY, _INCOME_MIN_OPEN_INTEREST
    assert _Q_WEIGHT_LIQUIDITY == 0.0
    assert _INCOME_MIN_OPEN_INTEREST > 0, "the OI floor is what still protects against illiquidity"


def test_open_interest_no_longer_moves_the_score():
    # Direct consequence of the zero weight — asserted behaviourally, not just on the constant.
    a = quality_score(annualized_yield_pct=30, otm_cushion_pct=8, open_interest=50, symbol="NVDA")
    b = quality_score(annualized_yield_pct=30, otm_cushion_pct=8, open_interest=50_000, symbol="NVDA")
    assert a == b


def test_leveraged_etfs_are_penalised_relative_to_ordinary_underlyings():
    """AUD-T398-LEVERAGEPENALTY: a 3x ETF's premium is rich because the underlying moves 3x as
    hard, not because the trade is better. Measured live, TQQQ took the #1 and #5 slots purely
    on that effect. Identical yield/cushion/liquidity must NOT score identically."""
    args = dict(annualized_yield_pct=48.4, otm_cushion_pct=10.5, open_interest=3111)
    assert quality_score(symbol="TQQQ", **args) < quality_score(symbol="NVDA", **args)
    assert quality_score(symbol="QLD", **args) < quality_score(symbol="NVDA", **args)
    # 3x should be penalised harder than 2x.
    assert quality_score(symbol="TQQQ", **args) < quality_score(symbol="QLD", **args)


def test_leverage_factor_is_the_reciprocal_of_the_multiple():
    assert leverage_factor("TQQQ") == pytest.approx(1 / 3)
    assert leverage_factor("QLD") == 0.5
    assert leverage_factor("NVDA") == 1.0
    assert leverage_factor("nvda") == 1.0  # case-insensitive


def test_quality_score_without_a_symbol_applies_no_penalty():
    # symbol is optional so the scorer stays usable/testable standalone.
    args = dict(annualized_yield_pct=40, otm_cushion_pct=10, open_interest=2000)
    assert quality_score(**args) == 100.0


def test_candidates_need_a_real_cushion_not_just_technically_otm():
    # AUD-T398-THINCUSHION: measured live — a QQQ put 0.08% OTM and an SPY put 0.18% OTM were
    # being presented as ~0.33-delta trades. Those are at-the-money in all but name.
    body = _ENGINE_SOURCE[_ENGINE_SOURCE.index("def rank_income_candidates"):]
    body = body[:body.index("\ndef ")]
    assert "cushion_pct < _INCOME_MIN_CUSHION_PCT" in body
    assert _INCOME_MIN_CUSHION_PCT > 0


def test_ranking_uses_quality_score_not_raw_yield():
    body = _ENGINE_SOURCE[_ENGINE_SOURCE.index("def rank_income_candidates"):]
    body = body[:body.index("\ndef ")]
    # Both the per-symbol pick AND the final cross-symbol sort must use the risk-adjusted
    # score; using yield for either one re-introduces adverse selection.
    assert 'cand["quality_score"] > best["quality_score"]' in body
    assert 'out.sort(key=lambda c: c["quality_score"], reverse=True)' in body
    assert 'out.sort(key=lambda c: c["annualized_yield_pct"]' not in body


def test_dte_is_measured_from_today_not_the_chains_as_of_date():
    # AUD-T398-DTEFROMSTALE: measuring DTE from `as_of` silently stops enforcing the stated
    # minimum the moment the chain is stale. Measured live on a 5-day-old chain: every
    # candidate the engine advertised as "14 DTE" was really a 9-day trade.
    body = _ENGINE_SOURCE[_ENGINE_SOURCE.index("def rank_income_candidates"):]
    body = body[:body.index("\ndef ")]
    assert "dte = (r.expiry - today).days" in body
    assert "dte = (r.expiry - as_of).days" not in body


def test_candidates_spanning_an_earnings_report_are_skipped():
    # AUD-T398-EARNINGSWINDOW: an earnings gap is the event that inverts the "assignment is the
    # minority outcome" premise these strategies rest on.
    body = _ENGINE_SOURCE[_ENGINE_SOURCE.index("def rank_income_candidates"):]
    body = body[:body.index("\ndef ")]
    assert "earnings_by_symbol" in body
    assert "today <= _er <= r.expiry" in body


def test_earnings_lookup_is_one_query_for_the_whole_universe():
    # This runs inside the candidate scan — a per-symbol query here would be N round-trips.
    body = _ENGINE_SOURCE[_ENGINE_SOURCE.index("def _next_earnings_by_symbol"):]
    body = body[:body.index("\ndef ")]
    assert "symbol = ANY(:syms)" in body
    assert body.count("session.execute") == 1


def test_moneyness_is_rechecked_against_the_current_price():
    # AUD-T398-STALEMONEYNESS: measured live — a 500-strike AMD put was opened as a "0.35
    # delta" trade with the stock already at 493.41, i.e. in the money at entry.
    body = _ENGINE_SOURCE[_ENGINE_SOURCE.index("def rank_income_candidates"):]
    body = body[:body.index("\ndef ")]
    assert 'strategy == "COVERED_CALL" and strike_f <= price' in body
    assert 'strategy == "CASH_SECURED_PUT" and strike_f >= price' in body


def test_candidates_surface_staleness_and_cushion():
    # Both were invisible before: a consumer could not tell a fresh candidate from a 5-day-old
    # one, nor how much room the strike actually had left at today's price.
    body = _ENGINE_SOURCE[_ENGINE_SOURCE.index("def rank_income_candidates"):]
    body = body[:body.index("\ndef ")]
    assert '"days_stale"' in body
    assert '"otm_cushion_pct"' in body


def test_stale_option_chains_are_skipped_not_silently_traded():
    # AUD-T398-STALECHAIN: a chain older than the self-healing window signals the OPTHIST
    # capture job itself has been failing, not a green light to price candidates off it.
    body = _ENGINE_SOURCE[_ENGINE_SOURCE.index("def rank_income_candidates"):]
    body = body[:body.index("\ndef ")]
    assert "_INCOME_MAX_CHAIN_STALENESS_DAYS" in body
    assert "stale_chain_skipped" in body
    assert 0 < _INCOME_MAX_CHAIN_STALENESS_DAYS <= 10


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


# ── AUD-T399-CRONRESTARTGAP: capture must survive restarts, not just outages ──

def test_opthist_startup_check_exists_and_is_registered():
    """A CronTrigger recomputes its next fire time from startup, so a container recreated
    after 18:45 ET simply waits until tomorrow — the 6h misfire grace does NOT cover that.
    It bit for real on 2026-09-16/17: repeated deploys pushed the slot each time, the archive
    fell 6 days behind past the engine's own staleness guard, and the engine returned ZERO
    candidates. Same shape as the MD-RVOL2 interval-reset bug already documented in this file."""
    assert 'id="opthist_startup_check"' in _SCHEDULER_SOURCE
    idx = _SCHEDULER_SOURCE.index('id="opthist_startup_check"')
    surrounding = _SCHEDULER_SOURCE[idx - 400:idx + 120]
    assert '"date"' in surrounding, "must be a one-shot startup job, not another recurring cron"
    assert "run_date=" in surrounding


def test_opthist_startup_check_skips_when_the_archive_is_current():
    """A routine restart must not re-run a 400s capture that burns UW quota for nothing —
    it returns early unless the archive is genuinely behind the last completed trading day."""
    body = _SCHEDULER_SOURCE[_SCHEDULER_SOURCE.index("def _opthist_startup_check"):]
    body = body[:body.index("\n    _scheduler.add_job(")]
    assert "if newest is not None and newest >= probe:" in body
    assert "return" in body


def test_opthist_startup_check_never_takes_the_service_down():
    body = _SCHEDULER_SOURCE[_SCHEDULER_SOURCE.index("def _opthist_startup_check"):]
    body = body[:body.index("\n    _scheduler.add_job(")]
    assert "except Exception as exc:" in body
    assert "startup_check_failed" in body


# ── AUD-T400: findings from the 2026-09-17 external system audit ──────────────

def test_short_option_liability_makes_opening_a_position_equity_neutral():
    """AUD-T400-SHORTLIABILITY: the equity curve counted collected premium as cash and added
    back full collateral while never deducting the obligation that premium paid for, so selling
    an option MANUFACTURED equity equal to the premium. The audit's own two worked examples
    both reported ~$200/$150 of instant phantom gain on a $10,000 account."""
    from src.services.options_income_engine import short_option_liability

    # CSP: $10k account, reserve $9,000, collect $150 -> cash 1,150 + committed 9,000
    liab, src = short_option_liability(
        strategy="CASH_SECURED_PUT", strike=90.0, underlying_price=100.0,
        contracts=1, quote_ask=1.50)
    assert src == "quote_ask"
    assert 1150 + 9000 - liab == pytest.approx(10_000)  # was 10,150

    # CC: $10k account, buy 100 shares at $100, sell a call for $200 -> cash 200 + committed 10,000
    liab_cc, _ = short_option_liability(
        strategy="COVERED_CALL", strike=105.0, underlying_price=100.0,
        contracts=1, quote_ask=2.00)
    assert 200 + 10_000 - liab_cc == pytest.approx(10_000)  # was 10,200


def test_short_option_liability_marks_at_the_ask_not_the_bid():
    """Closing a SHORT means BUYING it back, and a buyer pays the ask — the conservative
    direction for a liability. Using the bid would understate what it costs to get out."""
    from src.services.options_income_engine import short_option_liability
    liab, src = short_option_liability(
        strategy="CASH_SECURED_PUT", strike=100.0, underlying_price=99.0,
        contracts=2, quote_ask=3.25)
    assert src == "quote_ask"
    assert liab == pytest.approx(3.25 * 100 * 2)


def test_short_option_liability_falls_back_to_intrinsic_and_says_so():
    """With no quote the liability is floored at intrinsic value, which is always computable
    and never stale. It understates by any remaining time value, so the SOURCE is returned
    rather than hidden — a stale/approximate mark must be visible, not silent."""
    from src.services.options_income_engine import short_option_liability
    itm, src = short_option_liability(
        strategy="CASH_SECURED_PUT", strike=100.0, underlying_price=80.0, contracts=1)
    assert src == "intrinsic"
    assert itm == pytest.approx(2000.0)  # 20 points in the money
    otm, _ = short_option_liability(
        strategy="CASH_SECURED_PUT", strike=90.0, underlying_price=100.0, contracts=1)
    assert otm == 0.0  # out of the money -> no intrinsic obligation


def test_equity_snapshot_deducts_the_short_liability():
    body = _ENGINE_SOURCE[_ENGINE_SOURCE.index("def _snapshot_income_equity_curve"):]
    body = body[:body.index("\ndef ")]
    assert "- short_liability" in body, "equity must be assets MINUS the short obligation"
    assert "short_option_liability(" in body


def test_expected_settlement_session_resolves_weekends_and_holidays():
    """AUD-T400-SETTLESUBSTITUTE: a normal expiry settles on ITSELF; only a weekend/holiday
    expiry legitimately rolls back. Conflating that with missing data is the actual bug."""
    from datetime import date as _d
    from src.services.options_income_engine import expected_settlement_session as E
    assert E(_d(2026, 9, 18)) == _d(2026, 9, 18)   # Friday -> itself
    assert E(_d(2026, 9, 19)) == _d(2026, 9, 18)   # Saturday -> back to Friday
    assert E(_d(2026, 9, 20)) == _d(2026, 9, 18)   # Sunday -> back to Friday
    assert E(_d(2026, 1, 1)) == _d(2025, 12, 31)   # New Year's Day -> prior session


def test_settlement_requires_the_exact_session_and_never_substitutes():
    """Previously any close within 7 days before expiry settled the position PERMANENTLY. A
    $100 short put settled on a stale $101 books as expired-worthless even if the real
    settlement close was $90 and it should have been assigned."""
    body = _ENGINE_SOURCE[_ENGINE_SOURCE.index("def _settlement_close"):]
    body = body[:body.index("\ndef ")]
    assert "func.date(Price.ts) == want" in body, "must match the exact session, not a range"
    assert "expected_settlement_session(expiry)" in body

    settle_body = _ENGINE_SOURCE[_ENGINE_SOURCE.index("def settle_expired_positions"):]
    settle_body = settle_body[:settle_body.index("\ndef ")]
    assert "_settlement_close(" in settle_body
    assert "settlement_session_missing" in settle_body, "a missing session must be logged, not silent"


def test_backtest_settlement_uses_the_same_resolver_as_the_live_engine():
    """Sharing one lenient helper for entry pricing AND settlement is what let this defect
    exist in two places at once. Entry may use a nearby close; settlement may not."""
    bt = (pathlib.Path(__file__).resolve().parents[1]
          / "src" / "backtest" / "options_income_backtest.py").read_text()
    assert "def _settlement_close_bt" in bt
    assert "expected_settlement_session(expiry)" in bt
    assert '_settlement_close_bt(closes, cand["symbol"], expiry)' in bt
