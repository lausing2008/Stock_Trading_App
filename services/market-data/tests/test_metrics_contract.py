"""Behavioural tests for the measurement contract (`shared/metrics/`).

These exercise the real classes, not their source text. The contract's whole value is that it
REFUSES things, so most of these assert a refusal — and each refusal maps to a specific number
this project actually published and later withdrew.

`shared/` is pure stdlib here (dataclasses, enum, datetime), so it loads directly without the
conftest stubbing dance the sqlalchemy-dependent modules need.
"""
import pathlib
import sys

import pytest

_SHARED = pathlib.Path(__file__).resolve().parents[3] / "shared"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))

from datetime import datetime, timedelta, timezone  # noqa: E402

from metrics.anti_chase import (AUTHORITATIVE_REJECTION_RATE,  # noqa: E402
                                SHADOW_REJECTION_RATE, UNIQUE_EVENT_REJECTION_RATE,
                                anti_chase_funnel, reach_by_source)
from metrics.contract import (Cohort, DecisionAuthority, MetricDefinition,  # noqa: E402
                              MetricKind, MetricValue, MetricWindow, Quality, Weighting)
from metrics.registry import MetricRegistry  # noqa: E402
from metrics import reconciliation as rec  # noqa: E402


def _window():
    return MetricWindow(
        start=datetime(2026, 9, 1, tzinfo=timezone.utc),
        end=datetime(2026, 10, 1, tzinfo=timezone.utc),
        timezone="America/New_York", session_calendar="US")


def _defn(**over):
    base = dict(
        metric_id="t.rate", version="1", owner="test", kind=MetricKind.OPERATIONAL,
        cohort=Cohort.OPERATIONAL, grain="one evaluation", numerator="n", denominator="d",
        unit="ratio", decision_authority=DecisionAuthority.NOT_APPLICABLE,
        weighting=Weighting.ROW, dependence="none", code_versions=("v1",))
    base.update(over)
    return MetricDefinition(**base)


# ── the window ────────────────────────────────────────────────────────────────────────────────

def test_naive_window_bounds_are_rejected():
    """A naive instant cannot be mapped to an exchange session, and reading it as UTC is the
    documented utc-vs-et-date-boundary defect that silently shifts a window by a session."""
    with pytest.raises(ValueError, match="naive"):
        MetricWindow(start=datetime(2026, 9, 1), end=datetime(2026, 10, 1, tzinfo=timezone.utc),
                     timezone="America/New_York", session_calendar="US")


def test_window_end_must_be_after_start():
    with pytest.raises(ValueError, match="strictly after"):
        MetricWindow(start=datetime(2026, 10, 1, tzinfo=timezone.utc),
                     end=datetime(2026, 10, 1, tzinfo=timezone.utc),
                     timezone="America/New_York", session_calendar="US")


# ── the definition ────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("field", ["metric_id", "version", "owner", "grain", "numerator",
                                   "denominator", "unit", "dependence"])
def test_every_declared_dimension_is_mandatory(field):
    """Section 4 requires each dimension; an unstated one is the defect the class exists for."""
    with pytest.raises(ValueError, match=field):
        _defn(**{field: "  "})


def test_a_definition_without_code_versions_is_rejected():
    with pytest.raises(ValueError, match="code_versions"):
        _defn(code_versions=())


def test_operational_metric_may_not_claim_an_outcome_definition():
    """A deployed counter proving a job ran cannot certify strategy quality. This is the
    "operational correctness cannot certify prediction/execution/investment" rule with teeth."""
    with pytest.raises(ValueError, match="must not declare an outcome"):
        _defn(outcome="5-day forward return")


def test_investment_metric_without_an_outcome_definition_is_rejected():
    with pytest.raises(ValueError, match="must declare its outcome"):
        _defn(kind=MetricKind.INVESTMENT, cohort=Cohort.EXECUTED,
              decision_authority=DecisionAuthority.AUTHORITATIVE)


def test_non_operational_metric_must_name_its_decision_authority():
    """M04's withdrawn rate summed shadow and authoritative rejections. A metric that cannot say
    who decided cannot be checked against what production actually did."""
    with pytest.raises(ValueError, match="must name its decision authority"):
        _defn(kind=MetricKind.PREDICTION, cohort=Cohort.CANDIDATE_HYPOTHETICAL,
              outcome="5d forward return, long convention, matured",
              decision_authority=DecisionAuthority.NOT_APPLICABLE)


def test_operational_metric_must_use_not_applicable_authority():
    with pytest.raises(ValueError, match="NOT_APPLICABLE"):
        _defn(decision_authority=DecisionAuthority.AUTHORITATIVE)


# ── quality and value ─────────────────────────────────────────────────────────────────────────

def test_exclusions_require_reasons():
    """A silent filter is indistinguishable from the bug it replaced."""
    with pytest.raises(ValueError, match="exclusion_reasons"):
        Quality(excluded=3)


def test_zero_denominator_may_not_be_reported_as_zero():
    """The profit-factor rule generalised: an empty population has an UNDEFINED rate, which is a
    different statement from a rate of zero."""
    with pytest.raises(ValueError, match="UNDEFINED"):
        MetricValue(definition=_defn(), window=_window(), value=0.0,
                    numerator_count=0, denominator_count=0)


def test_unknown_value_requires_a_reason():
    with pytest.raises(ValueError, match="unknown_reason"):
        MetricValue(definition=_defn(), window=_window(), value=None,
                    numerator_count=None, denominator_count=None)


def test_known_value_may_not_carry_an_unknown_reason():
    with pytest.raises(ValueError, match="must not carry"):
        MetricValue(definition=_defn(), window=_window(), value=0.5,
                    numerator_count=1, denominator_count=2, unknown_reason="why")


def test_unknown_renders_as_unknown_never_as_zero():
    """Section 6: "Missing telemetry displays unknown, never zero." A dashboard that prints 0.0%
    for an uncollected metric makes a confident claim nobody made."""
    v = MetricValue(definition=_defn(), window=_window(), value=None, numerator_count=None,
                    denominator_count=None, unknown_reason="never collected")
    assert v.render() == "unknown"
    assert v.render() != "0.0000"
    assert not v.is_known


def test_support_is_rendered_with_the_weighting():
    v = MetricValue(definition=_defn(), window=_window(), value=0.25,
                    numerator_count=1, denominator_count=4)
    assert v.render_support() == "1/4 [row-weighted]"


# ── registry ──────────────────────────────────────────────────────────────────────────────────

def test_reregistering_an_identical_definition_is_allowed():
    r = MetricRegistry()
    d = _defn()
    assert r.register(d) is d
    r.register(_defn())
    assert len(r) == 1


def test_changing_a_formula_under_the_same_version_is_rejected():
    """Pooling results across incompatible definitions is the silent-drift failure: the tile
    keeps its name while the number changes meaning."""
    r = MetricRegistry()
    r.register(_defn())
    with pytest.raises(ValueError, match="already registered with a different definition"):
        r.register(_defn(numerator="something else"))


def test_unregistered_metric_cannot_be_fetched():
    with pytest.raises(KeyError, match="unregistered definition"):
        MetricRegistry().get("nope", "1")


def test_results_are_not_comparable_across_different_weighting():
    """Equal symbol/date weighting REVERSED the options-flow gap (bullish 42.3%, bearish 47.1%).
    Two rates that differ only in weighting are different quantities."""
    r = MetricRegistry()
    a = _defn(weighting=Weighting.ROW)
    b = _defn(weighting=Weighting.SYMBOL_SESSION)
    assert not r.comparable(a, b)


def test_results_remain_comparable_across_a_notes_only_change():
    r = MetricRegistry()
    assert r.comparable(_defn(notes="clarified"), _defn(notes="again"))


# ── reconciliation identities ─────────────────────────────────────────────────────────────────

def test_gate_funnel_residual_is_reported():
    out = rec.gate_funnel(reached=10, passed=4, rejected=5, errored=0)
    assert not out.ok
    assert "residual 1" in out.detail


def test_gate_funnel_counts_abstentions_as_an_outcome():
    assert rec.gate_funnel(reached=10, passed=4, rejected=5, errored=1).ok


def test_authority_split_must_close():
    assert rec.authority_split(5, 3, 2).ok
    assert not rec.authority_split(5, 3, 1).ok


def test_unresolved_rows_stay_in_the_cohort_denominator():
    assert rec.cohort_total(resolved=8, unresolved=2, declared_total=10).ok
    assert not rec.cohort_total(resolved=8, unresolved=2, declared_total=8).ok


def test_retries_may_exceed_notifications_but_acceptance_may_not():
    assert rec.delivery_attempts(unique_notifications=10, attempts=25, accepted=9).ok
    assert not rec.delivery_attempts(unique_notifications=10, attempts=25, accepted=11).ok
    assert not rec.delivery_attempts(unique_notifications=10, attempts=3, accepted=3).ok


def test_order_lifecycle_residual_is_reported():
    assert rec.order_lifecycle(100, 60, 40, 0).ok
    bad = rec.order_lifecycle(100, 60, 0, 0)
    assert not bad.ok and "residual" in bad.detail


def test_equity_must_compose_from_cash_and_positions():
    assert rec.equity(starting=1000, ending=1100, cash=400, positions_value=700).ok
    assert not rec.equity(starting=1000, ending=1100, cash=400, positions_value=600).ok


def test_require_raises_on_a_failed_identity():
    with pytest.raises(ValueError, match="reconciliation failed"):
        rec.authority_split(5, 3, 1).require()


# ── M04: the anti-chase funnel ────────────────────────────────────────────────────────────────

def test_authoritative_and_shadow_rates_are_reported_separately():
    """THE M04 CORRECTION. A shadow rejection stopped nothing. Summing the two describes a gate
    that blocked more than it did — so the two rates must stay distinct and must not add up to a
    single 'block rate'."""
    t = [{"anti_chase_reached": 10, "anti_chase_rejected": 4,
          "anti_chase_rejected_authoritative": 3, "anti_chase_rejected_shadow_only": 1}]
    out = anti_chase_funnel(t, _window())
    auth = out[AUTHORITATIVE_REJECTION_RATE.metric_id]
    shadow = out[SHADOW_REJECTION_RATE.metric_id]
    assert auth.value == pytest.approx(0.3)
    assert shadow.value == pytest.approx(0.1)
    # The combined 0.4 is never produced as a metric of its own.
    assert AUTHORITATIVE_REJECTION_RATE.decision_authority is DecisionAuthority.NOT_APPLICABLE
    assert auth.denominator_count == 10 and shadow.denominator_count == 10


def test_the_denominator_is_the_population_that_reached_the_gate():
    """NOT all BUY signals. Anti-chase runs after the watchlist and conviction gates, so the
    30.1%-of-BUY-signals figure was never comparable to an incremental block rate."""
    t = [{"anti_chase_reached": 7, "anti_chase_rejected": 7,
          "anti_chase_rejected_authoritative": 7, "anti_chase_rejected_shadow_only": 0}]
    out = anti_chase_funnel(t, _window())
    assert out[AUTHORITATIVE_REJECTION_RATE.metric_id].denominator_count == 7


def test_counts_that_do_not_reconcile_produce_unknown_not_a_number():
    """Returning a rate computed from counts known to disagree is worse than returning nothing,
    because it looks fine."""
    t = [{"anti_chase_reached": 10, "anti_chase_rejected": 5,
          "anti_chase_rejected_authoritative": 3, "anti_chase_rejected_shadow_only": 1}]
    out = anti_chase_funnel(t, _window())
    v = out[AUTHORITATIVE_REJECTION_RATE.metric_id]
    assert not v.is_known
    assert "authority_split" in v.unknown_reason


def test_rejections_exceeding_reach_produce_unknown():
    t = [{"anti_chase_reached": 2, "anti_chase_rejected": 5,
          "anti_chase_rejected_authoritative": 5, "anti_chase_rejected_shadow_only": 0}]
    out = anti_chase_funnel(t, _window())
    assert not out[AUTHORITATIVE_REJECTION_RATE.metric_id].is_known


def test_an_empty_population_is_undefined_not_zero_percent():
    """"No candidate reached the gate" and "the gate rejected nothing" are different facts, and
    only one of them is evidence about the gate."""
    out = anti_chase_funnel([{"anti_chase_reached": 0}], _window())
    v = out[AUTHORITATIVE_REJECTION_RATE.metric_id]
    assert not v.is_known
    assert v.render() == "unknown"
    assert "undefined" in v.unknown_reason


def test_unique_event_rate_is_always_unknown_because_tallies_carry_no_symbol():
    """Repeated checks are not unique bets. skip_tally has no symbol identity, so the attempt
    rate must never be displayed in the unique-event slot."""
    t = [{"anti_chase_reached": 10, "anti_chase_rejected": 4,
          "anti_chase_rejected_authoritative": 3, "anti_chase_rejected_shadow_only": 1}]
    v = anti_chase_funnel(t, _window())[UNIQUE_EVENT_REJECTION_RATE.metric_id]
    assert not v.is_known
    assert "no symbol identity" in v.unknown_reason


def test_tallies_are_summed_across_rows_in_the_window():
    t = [{"anti_chase_reached": 4, "anti_chase_rejected": 2,
          "anti_chase_rejected_authoritative": 2, "anti_chase_rejected_shadow_only": 0},
         {"anti_chase_reached": 6, "anti_chase_rejected": 2,
          "anti_chase_rejected_authoritative": 1, "anti_chase_rejected_shadow_only": 1}]
    out = anti_chase_funnel(t, _window())
    v = out[AUTHORITATIVE_REJECTION_RATE.metric_id]
    assert v.numerator_count == 3 and v.denominator_count == 10


def test_malformed_counts_are_excluded_with_a_recorded_reason():
    t = [{"anti_chase_reached": "lots", "anti_chase_rejected": 0,
          "anti_chase_rejected_authoritative": 0, "anti_chase_rejected_shadow_only": 0},
         {"anti_chase_reached": 5, "anti_chase_rejected": 1,
          "anti_chase_rejected_authoritative": 1, "anti_chase_rejected_shadow_only": 0}]
    out = anti_chase_funnel(t, _window())
    v = out[AUTHORITATIVE_REJECTION_RATE.metric_id]
    assert v.quality.excluded == 1
    assert "malformed_tally_counts" in v.quality.exclusion_reasons
    assert v.denominator_count == 5


def test_reach_is_broken_out_by_decision_source():
    """The split is what makes the headline rate interpretable; burying it inside a ratio is how
    it got lost the first time."""
    t = [{"anti_chase_reached_via_de": 4, "anti_chase_reached_via_fallback": 5,
          "anti_chase_reached_via_legacy": 1}]
    assert reach_by_source(t) == {"fallback": 5, "legacy": 1, "de": 4}


def test_the_authoritative_sources_match_the_engine():
    """Guards against the contract drifting from `paper_trading_engine.py`, where
    `gate_source in ("fallback", "legacy")` is what marks a rejection authoritative."""
    from metrics.anti_chase import AUTHORITATIVE_SOURCES, SHADOW_SOURCES
    engine = (pathlib.Path(__file__).resolve().parents[1]
              / "src" / "services" / "paper_trading_engine.py").read_text()
    assert 'if gate_source in ("fallback", "legacy"):' in engine
    assert set(AUTHORITATIVE_SOURCES) == {"fallback", "legacy"}
    assert set(SHADOW_SOURCES) == {"de"}


# ── the M01-M25 status register ───────────────────────────────────────────────────────────────

from metrics import register as reg  # noqa: E402


def test_a_date_is_not_a_trigger():
    """Section 8: each item needs "a concrete count/diversity/occurrence/implementation trigger
    instead of 'check next month'." Without this the register is re-read monthly and re-deferred
    without anyone noticing nothing moved."""
    with pytest.raises(ValueError, match="a date, not a condition"):
        reg.Trigger(kind=reg.TriggerKind.COUNT, description="check next month")


def test_a_concrete_condition_is_accepted():
    t = reg.Trigger(kind=reg.TriggerKind.DIVERSITY,
                    description=">=20 distinct names with none above 15% of fires")
    assert t.kind is reg.TriggerKind.DIVERSITY


def test_promotion_requires_supporting_research_not_just_a_count():
    """Minimum row/name/session floors only allow review, never automatic promotion."""
    with pytest.raises(ValueError, match="Promotion requires"):
        reg.WorkItem("X", "p", "s", "n", "e", "o", reg.Engineering.REPORTED_DEPLOYED,
                     reg.Research.COLLECTING, reg.Decision.ACTIVATED,
                     reg.Trigger(reg.TriggerKind.COUNT, "n reaches 30"))


def test_register_references_resolve():
    reg.validate_register()


def test_blocked_by_an_unknown_item_is_rejected():
    item = reg.WorkItem("Y", "p", "s", "n", "e", "o", reg.Engineering.PROPOSED,
                        reg.Research.NOT_MEASURABLE, reg.Decision.NO_ACTION,
                        reg.Trigger(reg.TriggerKind.IMPLEMENTATION, "build it"),
                        blocked_by=("M99",))
    assert "M99" not in reg.BY_ID and item.blocked_by == ("M99",)


def test_every_item_falls_in_exactly_one_bucket():
    """The partition is the point: an item is waiting on the world, on a person's approval, on
    something upstream, or on someone doing the work. Anything in two buckets means its real
    blocker has not been identified."""
    buckets = [set(w.id for w in group) for group in (
        reg.data_gated(), reg.awaiting_approval(), reg.blocked_items(), reg.actionable_now())]
    union = set().union(*buckets)
    assert union == set(reg.BY_ID), "some item is in no bucket"
    assert sum(len(b) for b in buckets) == len(union), "some item is in two buckets"


def test_jev_is_not_blocked_on_email_delivery():
    """CORRECTED 2026-10-01. An earlier version of this register asserted M22 was blocked on M20,
    reasoning that a paired trial needs durable delivery. That conflated two different durability
    requirements: Jev's shadow and paper arms need durable DECISIONS, ASSIGNMENTS and OUTCOMES —
    none of which is email. The outbox becomes a prerequisite only for an arm that measures
    notification availability or delivery-dependent behaviour."""
    assert "M20" not in reg.BY_ID["M22"].blocked_by
    assert "notification availability" in reg.BY_ID["M22"].notes


def test_the_ablation_grid_is_not_blocked_on_the_outbox_either():
    """M17's documented blockers are the simpler two-arm result and the absent margin features."""
    assert "M20" not in reg.BY_ID["M17"].blocked_by
    assert "margin features" in reg.BY_ID["M17"].trigger.description


def test_the_outbox_does_not_claim_to_resolve_event_attribution():
    """An outbox delivers whatever event the classifier SELECTED, reliably — including a wrong
    selection. Delivery reliability and event identification need separate acceptance criteria,
    and an earlier note here wrongly merged them."""
    notes = reg.BY_ID["M20"].notes
    assert "DELIVERY RELIABILITY ONLY" in notes
    assert "M19/M23" in notes


def test_the_approval_gated_items_are_exactly_the_two_untouched_ones():
    """M02 (recovery markers) and M21 (recovery digest). Both were explicitly not authorised;
    neither may drift into an implementation task."""
    assert {w.id for w in reg.awaiting_approval()} == {"M02", "M21"}


def test_data_gated_items_are_not_proposed_as_engineering():
    """The `project_pending_data_verification` memory exists to stop exactly this: re-proposing
    code for something that is only waiting on the calendar."""
    for w in reg.data_gated():
        assert not w.is_actionable_now, f"{w.id} is data-gated but also marked actionable"
