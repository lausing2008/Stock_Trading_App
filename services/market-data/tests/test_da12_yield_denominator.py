"""DA-12: annualised yield and reserved collateral used different denominators.

THE DEFECT. `_score_contract()` reported `premium_bid / current_price * 365/DTE` for BOTH
strategies. But a cash-secured put reserves `strike x 100` while a buy-write reserves
`underlying x 100`. For any put whose strike is not spot, the reported figure is therefore
premium-on-spot, not return on reserved capital — and the ranking and the minimum-yield filter
were comparing the two strategies on bases that do not match.

The audit's worked example — spot $100, strike $90, $1 premium, 30 DTE:

    displayed                12.17%   (premium / spot)
    on reserved collateral   13.52%   (premium / strike, $9,000 actually tied up)

WHY THE OLD FIELD IS PRESERVED RATHER THAN CORRECTED IN PLACE. It feeds `quality_score`, the
`min_annualized_yield_pct` filter, and the BACKTESTED weight calibration in
backtest/options_income_weights.py, whose 25/75/0 weights (T398/T399) were derived on this exact
definition across 417 trades. Redefining the denominator underneath them would silently
invalidate that study while every number kept rendering — the failure mode being fixed, repeated
one layer up. The correct yield is a second, explicitly named field; re-pointing the ranking at
it is a separate change that must re-run the calibration first.

Annualising also does not promise reinvestment at that rate, and neither figure is a strategy
return — assignment losses are not in either.
"""
import pytest

from src.services.options_income_engine import _score_contract

PUT = dict(strategy="CASH_SECURED_PUT", strike=90.0, premium_bid=1.0, current_price=100.0, dte=30)
CALL = dict(strategy="COVERED_CALL", strike=110.0, premium_bid=1.0, current_price=100.0, dte=30)


# ── The reported numbers ─────────────────────────────────────────────────────

def test_the_audits_worked_example_reproduces_exactly():
    r = _score_contract(**PUT)
    assert r["annualized_yield_pct"] == pytest.approx(12.17, abs=0.01)
    assert r["annualized_yield_on_collateral_pct"] == pytest.approx(13.52, abs=0.01)


def test_the_collateral_yield_matches_the_collateral_actually_reserved():
    """The identity that makes the new field meaningful: premium over the capital this
    strategy ties up, which is the same number `collateral_required` reports."""
    r = _score_contract(**PUT)
    premium_dollars = r["premium_per_contract"]
    reserved = r["collateral_required"]
    annual = premium_dollars / reserved * (365.0 / 30) * 100.0
    assert r["annualized_yield_on_collateral_pct"] == pytest.approx(annual, abs=0.01)


def test_a_buy_write_reserves_the_underlying_so_both_figures_agree():
    """Covered calls were never mis-stated — their collateral IS the underlying. A fix that
    changed them would be introducing an error, not removing one."""
    r = _score_contract(**CALL)
    assert r["annualized_yield_pct"] == pytest.approx(r["annualized_yield_on_collateral_pct"], abs=1e-9)
    assert r["collateral_required"] == pytest.approx(100.0 * 100, abs=1e-9)


def test_an_at_the_money_put_also_agrees():
    """The two bases coincide exactly when strike == spot, which is why the discrepancy hid:
    it is invisible on the one case anyone checks by hand."""
    r = _score_contract(strategy="CASH_SECURED_PUT", strike=100.0, premium_bid=1.0,
                        current_price=100.0, dte=30)
    assert r["annualized_yield_pct"] == pytest.approx(r["annualized_yield_on_collateral_pct"], abs=1e-9)


def test_a_deeper_out_of_the_money_put_diverges_further():
    """Monotonic in the gap between strike and spot — a hardcoded correction factor would pass
    the single audit example and fail here."""
    near = _score_contract(strategy="CASH_SECURED_PUT", strike=95.0, premium_bid=1.0,
                           current_price=100.0, dte=30)
    far = _score_contract(strategy="CASH_SECURED_PUT", strike=70.0, premium_bid=1.0,
                          current_price=100.0, dte=30)
    gap_near = near["annualized_yield_on_collateral_pct"] - near["annualized_yield_pct"]
    gap_far = far["annualized_yield_on_collateral_pct"] - far["annualized_yield_pct"]
    assert gap_far > gap_near > 0


# ── The basis must be self-describing ────────────────────────────────────────

def test_each_contract_states_which_denominator_it_used():
    """A consumer should never have to infer the basis from the strategy string."""
    assert _score_contract(**PUT)["yield_denominator"] == "strike"
    assert _score_contract(**CALL)["yield_denominator"] == "underlying_price"


# ── The ranking input must be untouched ──────────────────────────────────────

def test_the_original_field_keeps_its_original_definition():
    """The calibration in backtest/options_income_weights.py was derived on premium-over-SPOT
    across 417 trades. Silently redefining this field would invalidate that study while every
    number kept rendering — the exact failure being fixed, one layer up."""
    r = _score_contract(**PUT)
    # The field is rounded to 2dp at source, so the tolerance matches that rather than
    # demanding exact float equality against an unrounded recomputation.
    assert r["annualized_yield_pct"] == pytest.approx(1.0 / 100.0 * (365.0 / 30) * 100.0, abs=5e-3)


def test_the_ranking_and_filter_still_read_the_spot_based_field():
    """Pins the boundary of this change: the new field is additive, and re-pointing the ranking
    at it is a separate change that has to re-run the calibration first."""
    import re
    from pathlib import Path

    eng = (Path(__file__).resolve().parents[1] / "src" / "services" / "options_income_engine.py").read_text()
    wts = (Path(__file__).resolve().parents[1] / "src" / "backtest" / "options_income_weights.py").read_text()
    # quality_score's own parameter and the config filter both still take the original field.
    assert re.search(r"annualized_yield_pct=cand\[.annualized_yield_pct.\]", eng)
    assert 'cand["annualized_yield_pct"] < cfg.get("min_annualized_yield_pct"' in eng
    assert 't["annualized_yield_pct"]' in wts
    assert "annualized_yield_on_collateral_pct" not in wts
