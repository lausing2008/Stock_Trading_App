"""Do the thirteen buckets keep the four rules they exist for?"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from intel_reports.evidence_buckets import (  # noqa: E402
    BUCKETS, Bucket, Claim, DIRECTIONAL, WORK_REMAINING, POLICY_VERSION,
    BULLISH, BEARISH, NEUTRAL, UNKNOWN, PASS, FAIL, INSUFFICIENT, NOT_COLLECTED, STALE,
    TECHNICAL, FUNDAMENTALS, VALUATION, RISK, EARNINGS,
    digest, policy_fingerprint, summarise, technical_bucket, fundamentals_bucket,
    from_assessment, unmeasured)


def test_there_are_thirteen_buckets_and_none_is_a_score():
    assert len(BUCKETS) == 13 and len(set(BUCKETS)) == 13
    b = Bucket(name=TECHNICAL, direction=NEUTRAL, status=PASS)
    assert "score" not in b.as_dict() and "overall" not in b.as_dict()


def test_an_undeclared_bucket_name_is_refused():
    with pytest.raises(ValueError, match="unknown bucket"):
        Bucket(name="vibes", direction=NEUTRAL, status=PASS)


# ---- rule 1: UNKNOWN is not NEUTRAL --------------------------------------------------------

def test_an_unmeasured_bucket_cannot_claim_a_direction():
    """A gap in our work and a finding about the market are different things."""
    for st in WORK_REMAINING:
        with pytest.raises(ValueError, match="must be UNKNOWN"):
            Bucket(name=VALUATION, direction=NEUTRAL, status=st)


def test_unmeasured_builds_an_unknown_direction_with_its_reason():
    b = unmeasured(VALUATION, NOT_COLLECTED, "no assessment is stored")
    assert b.direction is UNKNOWN and b.status == NOT_COLLECTED
    assert "no assessment is stored" in b.evidence[0].claim


# ---- rule 2: support quality is not predictive confidence ----------------------------------

def test_predictive_confidence_is_never_set_by_this_layer():
    b = Bucket(name=TECHNICAL, direction=BULLISH, status=PASS,
               evidence=[Claim("x", "s1"), Claim("y", "s2"), Claim("z", "s3")])
    assert b.as_dict()["predictive_confidence"] is None
    assert b.support_quality == "HIGH"       # well evidenced, which is a different claim


def test_support_quality_is_none_where_nothing_was_measured():
    """A support grade on an absent reading would describe the quality of nothing."""
    assert unmeasured(RISK, INSUFFICIENT, "none").support_quality is None


def test_a_decisive_objection_lowers_support_however_many_sources_agree():
    b = Bucket(name=TECHNICAL, direction=BULLISH, status=PASS,
               evidence=[Claim("a", "s1"), Claim("b", "s2"), Claim("c", "s3")],
               contradictions=[Claim("fatal", "s4", materiality="decisive")])
    assert b.support_quality != "HIGH"


# ---- rule 3: contradictions are weighed, grouped, and never discarded ----------------------

def test_five_objections_from_one_filing_are_five_findings_and_one_source():
    """MU's 10-K really does supply five separate objections. All are kept; they count once
    toward independence, so support_quality cannot read HIGH because one document said five
    things."""
    b = Bucket(name=TECHNICAL, direction=BULLISH, status=PASS,
               evidence=[Claim("reading", "price", source_group="price")],
               contradictions=[Claim(f"risk {i}", "MU FY2025 10-K", source_group="MU-10K")
                               for i in range(5)])
    assert len(b.as_dict()["contradictions"]) == 5, "no finding may be discarded"
    assert b.independent_sources == 2, "five objections from one filing are ONE source"


def test_every_claim_carries_its_materiality_and_source():
    d = Claim("c", "src", source_ref="Item 1A", materiality="decisive").as_dict()
    assert d["materiality"] == "decisive" and d["source"] == "src"
    assert d["source_ref"] == "Item 1A" and d["source_group"] == "src"


# ---- builders -------------------------------------------------------------------------------

def test_the_technical_bucket_reads_a_range_position_without_forecasting():
    b = technical_bucket({"direction": "range", "label": "Inside range — no directional setup",
                          "factors": ["Volume 0.85×"], "limitations": ["No earnings check"]},
                         session="2026-10-06")
    assert b.direction is NEUTRAL and b.status == PASS
    joined = " ".join(c.claim for c in b.contradictions)
    assert "uncalibrated and carry no probability" in joined


def test_no_usable_prices_is_not_collected_rather_than_neutral():
    assert technical_bucket(None).status == NOT_COLLECTED
    assert technical_bucket(None).direction is UNKNOWN
    assert technical_bucket({"direction": "unknown"}).direction is UNKNOWN


def test_a_stale_statement_series_is_stale_not_a_direction():
    b = fundamentals_bucket({"annual_periods": 4, "revenue": 1.0, "revenue_prior": 1.0,
                             "reported_year_age_days": 402})
    assert b.status == STALE and b.direction is UNKNOWN
    assert "402 days ago" in b.evidence[0].claim
    assert "is not checked here" in b.evidence[0].claim


def test_a_current_series_produces_a_direction_with_its_comparability_caveat():
    b = fundamentals_bucket({"annual_periods": 4, "revenue": 37378e6, "revenue_prior": 25111e6,
                             "reported_year_age_days": 120, "period_end": "2025-08-28"})
    assert b.direction is BULLISH and b.status == PASS
    assert "accounting basis is stored" in " ".join(c.claim for c in b.contradictions)


def test_an_insufficient_assessment_is_finished_work_and_still_unmeasured():
    b = from_assessment(VALUATION, {"verdict": "insufficient", "version": 3,
                                    "unresolved": "which earnings level is sustainable?",
                                    "findings": [{"claim": "70.0x the mean",
                                                  "source": "10-K",
                                                  "counterevidence": "a reference case"}]},
                        absent="none")
    assert b.status == INSUFFICIENT and b.direction is UNKNOWN
    # The open question is a LIMITATION on this bucket, not counterevidence against another.
    assert any(c.materiality == "limitation" for c in b.limitations)
    assert not any(c.materiality == "decisive" for c in b.contradictions)


def test_no_assessment_is_not_collected_and_names_the_gap():
    b = from_assessment(VALUATION, None, absent="no valuation assessment is stored")
    assert b.status == NOT_COLLECTED and b.direction is UNKNOWN


# ---- the summary ----------------------------------------------------------------------------

def _mu_buckets():
    return [technical_bucket({"direction": "range", "label": "Inside range",
                              "factors": ["Volume 0.85×"], "limitations": ["lim"]}),
            fundamentals_bucket({"annual_periods": 4, "revenue": 1.0, "revenue_prior": 1.0,
                                 "reported_year_age_days": 402}),
            unmeasured(VALUATION, NOT_COLLECTED, "no assessment"),
            unmeasured(RISK, INSUFFICIENT, "five classes unexamined"),
            unmeasured(EARNINGS, NOT_COLLECTED, "nothing stored")]


def test_the_summary_answers_direction_factors_counterevidence_and_what_is_unmeasured():
    s = summarise("stock:MU", _mu_buckets(), horizon_label="1-4w", horizon_sessions=20)
    assert s["direction"] in (NEUTRAL, UNKNOWN, BULLISH, BEARISH)
    assert len(s["three_factors"]) <= 3
    assert s["strongest_counterevidence"]["claim"]
    assert set(s["unmeasured_buckets"]) >= {VALUATION, RISK, EARNINGS, FUNDAMENTALS}


def test_the_summary_never_publishes_a_predictive_confidence():
    s = summarise("stock:MU", _mu_buckets(), horizon_label="1-4w", horizon_sessions=20)
    assert s["predictive_confidence"] is None
    assert "establishes no skill by itself" in s["predictive_confidence_note"]


def test_disagreeing_buckets_are_reported_as_disagreement_not_averaged():
    bulls = Bucket(name=TECHNICAL, direction=BULLISH, status=PASS,
                   evidence=[Claim("up", "px")])
    bears = Bucket(name=FUNDAMENTALS, direction=BEARISH, status=PASS,
                   evidence=[Claim("down", "stmt")])
    s = summarise("stock:X", [bulls, bears], horizon_label="1-4w", horizon_sessions=20)
    assert s["direction"] is NEUTRAL
    assert "disagree" in s["direction_basis"]
    assert "averaged into a conviction nobody holds" in s["direction_basis"]


def test_an_unmeasured_bucket_does_not_block_the_summary():
    """A permanent unknown blocks only the conclusions that need it."""
    s = summarise("stock:MU", _mu_buckets(), horizon_label="1-4w", horizon_sessions=20)
    assert "do not block price observation or outcome measurement" in s["unmeasured_note"]


def test_only_measurable_buckets_may_contribute_a_direction():
    assert set(DIRECTIONAL) <= set(BUCKETS)
    assert VALUATION not in DIRECTIONAL, \
        "a bucket that cannot produce a direction today must not invent one"


# ---- fingerprint -----------------------------------------------------------------------------

def test_the_policy_fingerprint_changes_when_a_rule_changes():
    a = policy_fingerprint()
    assert len(a) == 32 and a == policy_fingerprint()
    assert digest({"x": 1}) != digest({"x": 2})


# ---- an open question is a research limitation, not counterevidence -------------------------
# An unresolved valuation question explains why the VALUATION bucket has no direction. It is
# not evidence against a price reading in the technical bucket, and treating it as a
# contradiction made it surface as the "strongest counterevidence" to a direction it does not
# address.

def test_an_unresolved_question_is_a_limitation_not_a_contradiction():
    b = from_assessment(VALUATION, {"verdict": "insufficient", "version": 3,
                                    "unresolved": "which earnings level is sustainable?",
                                    "findings": []}, absent="none")
    assert [c.claim for c in b.limitations] == ["which earnings level is sustainable?"]
    assert b.contradictions == []
    assert "limitations" in b.as_dict()


def test_the_strongest_counterevidence_comes_from_a_measured_bucket():
    """An objection inside an unmeasured bucket is a reason THAT bucket is unmeasured."""
    measured = technical_bucket({"direction": "breakout", "label": "Up — range break observed",
                                 "factors": [], "limitations": []})
    unmeasured_val = from_assessment(
        VALUATION, {"verdict": "insufficient", "version": 3,
                    "unresolved": "which earnings level is sustainable?",
                    "findings": [{"claim": "70x the mean", "source": "10-K",
                                  "counterevidence": "a reference case, not a level"}]},
        absent="none")
    s = summarise("stock:MU", [measured, unmeasured_val], horizon_label="1-4w",
                  horizon_sessions=20)
    assert s["strongest_counterevidence"]["bucket"] == "technical"
    assert "reference case" not in s["strongest_counterevidence"]["claim"]


def test_research_limitations_are_reported_separately_and_not_lost():
    measured = technical_bucket({"direction": "breakout", "label": "Up", "factors": [],
                                 "limitations": []})
    val = from_assessment(VALUATION, {"verdict": "insufficient", "version": 3,
                                      "unresolved": "which earnings level is sustainable?",
                                      "findings": []}, absent="none")
    s = summarise("stock:MU", [measured, val], horizon_label="1-4w", horizon_sessions=20)
    claims = [x["claim"] for x in s["research_limitations"]]
    assert "which earnings level is sustainable?" in claims
    assert s["research_limitations"][0]["bucket"] == "valuation"


def test_a_summary_with_no_measured_bucket_has_no_counterevidence_to_offer():
    s = summarise("stock:X", [unmeasured(VALUATION, NOT_COLLECTED, "none")],
                  horizon_label="1-4w", horizon_sessions=20)
    assert s["strongest_counterevidence"] is None
