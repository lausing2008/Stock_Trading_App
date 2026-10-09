"""SF-01…SF-05 — the 2026-10-02 system follow-up audit, fixed.

Every test below uses that audit's OWN witness. The witnesses were reproduced before any fix
was written (`docs/audits/evidence/2026-10-02-system-followup-probes.py`), so each test is
known to have failed against the code as it stood.

  SF-01  a closed position could still be claimed for a real broker order
  SF-02  two surfaces on one page disagreed about whether a quote was usable
  SF-03  one share qualified an account for a covered call
  SF-04  price provenance was recorded and never shown
  SF-05  an expired contract was the primary recommendation

O01 NOTE (2026-10-08): the fixtures below now declare `directional_exposure`, as every real
structure does. A structure that does not declare which way it leans cannot be shown compatible
with a stated view, so without it these tests would exercise the exposure constraint rather than
the holdings constraint they are about.
"""
import ast
import importlib.util
import pathlib

import pytest

_SVC = pathlib.Path(__file__).resolve().parents[1] / "src"
_BROKER = (_SVC / "services" / "broker_submission.py").read_text()
_ROUTES = (_SVC / "api" / "routes.py").read_text()

_spec = importlib.util.spec_from_file_location(
    "osx_sf", _SVC / "services" / "options_strategies.py")
osx = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(osx)


# ── SF-01: one eligibility predicate, shared ────────────────────────────────────────────

def test_the_claim_rechecks_every_condition_discovery_used():
    """THE DEFECT. `claimable()` tested five conditions; `begin_submission()` re-tested two.
    Everything else was as old as the SELECT, so a position closed in that window was still
    claimable — the audit's probe got `claimed=True` on a row reading `stage='closed'`."""
    tree = ast.parse(_BROKER)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "begin_submission")
    body = ast.get_source_segment(_BROKER, fn) or ""
    assert "_eligibility()" in body, \
        "the atomic claim must re-evaluate the full predicate, not just id+state"


def test_discovery_and_claim_cannot_drift_apart():
    """A predicate that exists twice is one that will disagree with itself."""
    assert _BROKER.count("def _eligibility(") == 1
    tree = ast.parse(_BROKER)
    for name in ("claimable", "begin_submission"):
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == name)
        assert "_eligibility()" in (ast.get_source_segment(_BROKER, fn) or ""), \
            f"{name} does not use the shared predicate"


@pytest.mark.parametrize("condition", [
    "broker_submission_state.in_(RETRYABLE)",
    "broker_order_id.is_(None)",
    'stage == "open"',
    'broker_submission_path == "deferred"',
    "broker_submit_attempts < MAX_SUBMIT_ATTEMPTS",
])
def test_every_original_condition_survived_the_consolidation(condition):
    tree = ast.parse(_BROKER)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_eligibility")
    assert condition in (ast.get_source_segment(_BROKER, fn) or "")


# ── SF-02: one quote contract across both surfaces ──────────────────────────────────────

def test_the_legacy_route_uses_the_same_quote_helper_as_the_matrix():
    """WITNESS: bid 12 / ask 2 produced a covered-call mid of 7.0 in the route while the
    matrix returned None for the identical quote."""
    assert "_mid_shared(contract)" in _ROUTES
    assert "(contract[\"bid\"] + contract[\"ask\"]) / 2.0" not in _ROUTES


def test_the_shared_helper_still_refuses_the_witness_quote():
    assert osx._mid({"bid": 12.0, "ask": 2.0}) is None
    assert osx._mid({"bid": 2.0, "ask": 3.0}) == pytest.approx(2.5)


def test_an_unusable_quote_yields_a_reason_not_a_payoff():
    for leg in ("protective_put", "covered_call"):
        assert f'result["{leg}_unavailable"]' in _ROUTES, \
            f"{leg} must report WHY it is missing rather than silently vanishing"


def test_a_refused_quote_never_reaches_the_arithmetic():
    """`round(None / price)` is the crash this guard prevents, and a rendered 0 would be
    worse than the crash."""
    for anchor in ("if contract and mid is not None:",):
        assert _ROUTES.count(anchor) == 2, "both legs must be guarded"


# ── SF-03: deliverable units, not truthiness ────────────────────────────────────────────

@pytest.mark.parametrize("shares,expected", [
    (0, 0), (1, 0), (99, 0), (100, 1), (150, 1), (200, 2), (None, 0),
])
def test_coverable_contracts_counts_deliverable_lots(shares, expected):
    """The audit's own acceptance list: 0, 1, 99, 100, 150, 200."""
    assert osx._coverable_contracts(shares) == expected


def test_one_share_no_longer_qualifies_an_account_for_a_covered_call():
    """THE WITNESS. `shares=1` selected `covered_call` and said "You hold shares, so covered
    calls and collars are available", while every payoff beside it was per 100-share contract
    and the frontend separately required 100."""
    rec = osx._recommend(
        singles={"covered_call": {"directional_exposure": "income_long", "name": "Covered Call", "max_profit_per_contract": 500.0},
                 "long_call": {"directional_exposure": "bullish", "name": "Long Call", "max_profit_per_contract": None}},
        combos={}, signal="BUY", iv_rank=90.0,
        holds_shares=osx._coverable_contracts(1) >= 1,
        coverable_contracts=osx._coverable_contracts(1), holds_any_shares=True)
    assert rec["primary"] != "covered_call"


def test_a_hundred_shares_still_qualifies():
    """The fix must not refuse a real covered-call holder."""
    rec = osx._recommend(
        singles={"covered_call": {"directional_exposure": "income_long", "name": "Covered Call", "max_profit_per_contract": 500.0}},
        combos={}, signal="BUY", iv_rank=90.0,
        holds_shares=osx._coverable_contracts(100) >= 1,
        coverable_contracts=osx._coverable_contracts(100), holds_any_shares=True)
    assert rec["primary"] == "covered_call"
    assert "1 standard contract" in rec["constraint"]


def test_holding_some_stock_is_not_reported_as_holding_none():
    """99 shares is not zero shares, and saying so sends the reader to check the wrong thing."""
    partial = osx._constraint_text(0, True)
    none_at_all = osx._constraint_text(0, False)
    assert partial != none_at_all
    assert "not the 100 shares" in partial
    assert "no shares" in none_at_all


def test_the_coverable_count_travels_with_the_recommendation():
    rec = osx._recommend(
        singles={"covered_call": {"directional_exposure": "income_long", "name": "Covered Call", "max_profit_per_contract": 500.0}},
        combos={}, signal="BUY", iv_rank=90.0, holds_shares=True,
        coverable_contracts=3, holds_any_shares=True)
    assert rec["coverable_contracts"] == 3


# ── SF-04: provenance reaches the reader ────────────────────────────────────────────────

_MATRIX_TSX = (pathlib.Path(__file__).resolve().parents[3]
               / "frontend/src/components/OptionStrategyMatrix.tsx").read_text()
_API_TS = (pathlib.Path(__file__).resolve().parents[3]
           / "frontend/src/lib/api.ts").read_text()


def test_the_leg_type_carries_price_source():
    assert "price_source?:" in _API_TS


def test_a_last_trade_price_is_labelled_in_the_ui():
    """WITNESS: no bid/ask, last 2.10 — primary recommendation, `price_source='last_trade'`,
    `spread_pct=None`. The spread warning cannot fire because a MISSING spread is not greater
    than the threshold, so absence read as reassurance."""
    assert "l.price_source === 'last_trade'" in _MATRIX_TSX


def test_the_primary_recommendation_itself_is_qualified():
    """The structure comparison must carry quote provenance beside the result."""
    i = _MATRIX_TSX.index("Structure comparison:")
    block = _MATRIX_TSX[i:i + 1400]
    assert "price_source === 'last_trade'" in block
    assert "research estimates" in block


@pytest.mark.parametrize("quote,expected", [
    ({"bid": 1.0, "ask": 1.2}, "quote_mid"),
    ({"bid": 0, "ask": 0, "last_price": 2.10}, "last_trade"),
    # A crossed book is not a quote. With no last price there is no number at all; with one,
    # the last trade is what remains — and it is labelled as such rather than as a quote.
    ({"bid": 12.0, "ask": 2.0}, "none"),
    ({"bid": 12.0, "ask": 2.0, "last_price": 5.0}, "last_trade"),
    ({}, "none"),
])
def test_the_backend_records_the_distinction(quote, expected):
    """Written out as real cases after the first version of this test ended in `or True`,
    which cannot fail and therefore tested nothing."""
    assert osx._price_source(quote) == expected


# ── SF-05: an expired contract is not a plan ────────────────────────────────────────────

@pytest.mark.parametrize("dte,built", [(-5, False), (-1, False), (0, True), (1, True)])
def test_an_expired_contract_cannot_become_a_leg(dte, built):
    """WITNESS: on October 2 an October 1 call was the PRIMARY recommendation at DTE -1."""
    leg = osx._leg({"strike": 105.0, "bid": 2.0, "ask": 2.2, "right": "call"},
                   "sell", "2026-10-01", dte)
    assert (leg is not None) is built


def test_same_day_expiry_is_deliberately_still_allowed():
    """DTE 0 is tradeable until its last trading time — a session question this pure module
    cannot answer. Treating it as expired would silently drop same-day structures."""
    assert osx._leg({"strike": 105.0, "bid": 2.0, "ask": 2.2, "right": "call"},
                    "sell", "2026-10-02", 0) is not None


def test_an_unknown_dte_is_not_treated_as_expired():
    """None means 'not computed', which is not the same as 'in the past'."""
    assert osx._leg({"strike": 105.0, "bid": 2.0, "ask": 2.2, "right": "call"},
                    "sell", "2026-11-20", None) is not None


# ══════════════════════════════════════════════════════════════════════════════════════════
# RESIDUALS from the 2026-10-02 remediation review
# (docs/audits/2026-10-02-sf-remediation-review.md)
#
# The reviewer found both original fixes incomplete and was right on both counts:
#
#   SF-03  the fix reordered PREFERENCES but left ineligible structures in `available`, so
#          the final `next(iter(available))` fallback handed back a covered call for one
#          share anyway — and without the coverage metadata the ordinary path carries.
#   SF-05  `_dte()` returns None for an unparseable date and the guard only rejected
#          NEGATIVE dte, so `expiry='not-a-date'` produced a priced leg with
#          days_to_expiry=None. My own test asserting "unknown DTE still builds" could not
#          distinguish an omitted calculation from an unreadable contract identity.
#
# These tests run the FULL BUILDER, because the reviewer's other point stands: a helper-only
# `shares // 100` test cannot establish that no path recommends an ineligible structure.
# ══════════════════════════════════════════════════════════════════════════════════════════

def _reviewer_fixture(shares, *, call_expiry="2026-11-06"):
    """The review's own witness: spot 100, target 105, rich IV, no put chain, ATM call
    CROSSED at 12/2 (so the long call cannot be built) and the target call valid at 2/2.2
    (so the covered call can be priced). Only an ineligible structure survives pricing."""
    from datetime import date as _date
    calls = [{"strike": 100.0, "bid": 12.0, "ask": 2.0, "last_price": 0.0, "iv": .4, "oi": 100},
             {"strike": 105.0, "bid": 2.0, "ask": 2.2, "last_price": 0.0, "iv": .4, "oi": 100}]
    return osx.build_strategy_matrix(
        current_price=100., stop_loss=None, take_profit=105., signal="BUY",
        put_rows=[], put_expiry=None, call_rows=calls, call_expiry=call_expiry,
        shares=shares, iv_rank=80., today=_date(2026, 10, 2))


@pytest.mark.parametrize("shares", [0, 1, 99])
def test_the_fallback_cannot_recommend_a_structure_the_holding_cannot_carry(shares):
    """THE REVIEWER'S WITNESS. The fallback previously returned `covered_call` here."""
    rec = _reviewer_fixture(shares)["recommendation"]
    assert rec["primary"] is None
    assert "covered_call" not in [a["key"] for a in rec.get("alternatives", [])]


@pytest.mark.parametrize("shares", [100, 200])
def test_a_sufficient_holding_still_gets_the_covered_call(shares):
    """The filter must not refuse a holder who genuinely qualifies."""
    rec = _reviewer_fixture(shares)["recommendation"]
    assert rec["primary"] == "covered_call"


def test_an_ineligible_priced_structure_is_reported_not_silently_dropped():
    """'We found nothing' and 'we found something you cannot use' lead to different next
    steps, so they must not render identically."""
    rec = _reviewer_fixture(1)["recommendation"]
    assert rec["ineligible_for_holding"] == ["covered_call"]
    assert "do not hold enough" in rec["reason"]


@pytest.mark.parametrize("shares,expected", [(0, 0), (1, 0), (99, 0), (100, 1), (200, 2)])
def test_coverage_metadata_is_present_on_every_return_path(shares, expected):
    """The fallback dropped it entirely — a recommendation with no statement of what the
    holding supports."""
    rec = _reviewer_fixture(shares)["recommendation"]
    assert rec["coverable_contracts"] == expected
    assert rec["constraint"]


def test_no_structures_at_all_still_carries_coverage_metadata():
    from datetime import date as _date
    rec = osx.build_strategy_matrix(
        current_price=100., stop_loss=None, take_profit=105., signal="BUY",
        put_rows=[], put_expiry=None, call_rows=[], call_expiry=None,
        shares=1., iv_rank=80., today=_date(2026, 10, 2))["recommendation"]
    assert rec["primary"] is None
    assert rec["coverable_contracts"] == 0
    assert "constraint" in rec


# ── SF-05 residual: invalid identity is not unknown identity ────────────────────────────

@pytest.mark.parametrize("bad", ["not-a-date", "2026-13-45", "20261106", "Nov 6 2026", ""])
def test_a_malformed_expiry_produces_no_recommendation(bad):
    """THE REVIEWER'S WITNESS: the full matrix recommended a long call with
    expiry='not-a-date' and days_to_expiry=None."""
    m = _reviewer_fixture(100, call_expiry=bad)
    assert m["singles"] == {} and m["combos"] == {}
    assert m["recommendation"]["primary"] is None


def test_a_valid_expiry_still_builds():
    """The guard must reject invalid identity, not all identity."""
    assert "covered_call" in _reviewer_fixture(100)["singles"]


def test_an_omitted_dte_calculation_is_not_the_same_as_an_unreadable_expiry():
    """The distinction the first fix could not make: `dte=None` with a VALID expiry string is
    a calculation nobody performed; `dte=None` with an unparseable one is a contract whose
    identity cannot be read."""
    c = {"strike": 105.0, "bid": 2.0, "ask": 2.2, "right": "call"}
    assert osx._leg(c, "sell", "2026-11-20", None) is not None
    assert osx._leg(c, "sell", "not-a-date", None) is None


def test_same_day_expiry_remains_available_for_analysis():
    """Agreed with the reviewer: DTE 0 may be priced, but this module does not assert current
    tradability — last-trading-time rules belong to the execution consumer."""
    c = {"strike": 105.0, "bid": 2.0, "ask": 2.2, "right": "call"}
    assert osx._leg(c, "sell", "2026-10-02", 0) is not None


# ── SF-02 residual: the legacy card must consume the unavailable reason ─────────────────

_CARD_TSX = (pathlib.Path(__file__).resolve().parents[3]
             / "frontend/src/components/OptionsGamePlanCard.tsx").read_text()


def test_the_legacy_card_does_not_render_nothing_for_an_unusable_quote():
    """Replacing a wrong number with a blank space is not a fix: the reader cannot tell
    'no such contract' from 'the quote is unusable right now'."""
    assert "protective_put_unavailable" in _CARD_TSX
    assert "!ppWhy && !ccWhy" in _CARD_TSX, "the null-guard must account for the reasons"
    assert "quote unusable" in _CARD_TSX
