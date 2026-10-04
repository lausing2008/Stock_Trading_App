"""The report must give ONE answer per question.

Every test here is a contradiction that was visible on the rendered page: an official figure
displayed beside "unavailable", a confirmed fiscal period beside "no confirmed period", a
stated accounting basis beside "the platform does not store the basis".
"""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "shared"))

from intelligence.report_contract import FieldState, observed, unavailable, unknown  # noqa: E402
from intel_reports import documents as D  # noqa: E402

FACTS = {
    "revenue": {"value": 54_230_000_000, "units": "USD", "basis": "GAAP",
                "period": "FY2026 Q4"},
    "eps_adjusted": {"value": 33.42, "units": "USD/share", "basis": "non-GAAP",
                     "period": "FY2026 Q4"},
    "guidance_q1_revenue": {"value": 61_500_000_000, "units": "USD", "basis": "GAAP",
                            "period": "FY2027 Q1"},
}


def _fields():
    return {
        "revenue_actual": unavailable("no revenue actual on file"),
        "eps_actual": observed(33.42, evidence_ids=["earnings_event:1"]),
        "revenue_surprise_pct": unavailable("needs both an estimate and an actual"),
        "eps_surprise_pct": observed({"pct": 5.03, "absolute": 1.6}, units="pct"),
        "accounting_basis": unknown("the platform does not store whether these are GAAP"),
        "guidance_change": unavailable("not joined to this report"),
    }


def test_official_figure_replaces_the_unavailable_actual():
    f = _fields()
    D.reconcile_into_metrics(f, FACTS, document_id=7)
    assert f["revenue_actual"].state is FieldState.OK
    assert f["revenue_actual"].value["value"] == 54_230_000_000
    assert f["revenue_actual"].value["units"] == "USD"
    assert f["revenue_actual"].value["basis"] == "GAAP"
    assert "issuer_document:7" in f["revenue_actual"].evidence_ids


def test_guidance_figures_are_not_promoted_into_the_reported_actual():
    """`guidance_q1_revenue` is revenue too, and it is NOT the quarter's reported revenue."""
    f = _fields()
    D.reconcile_into_metrics(f, FACTS, document_id=7)
    assert f["revenue_actual"].value["value"] == 54_230_000_000
    assert f["revenue_actual"].value["period"] == "FY2026 Q4"


def test_a_provider_disagreement_is_preserved_not_overwritten():
    f = _fields()
    f["revenue_actual"] = observed(54_000_000_000, evidence_ids=["earnings_event:1"])
    D.reconcile_into_metrics(f, FACTS, document_id=7)
    assert f["revenue_actual"].value["value"] == 54_230_000_000
    assert f["revenue_actual"].value["provider_value"] == 54_000_000_000
    assert "conflict" in f["revenue_actual"].value


def test_the_surprise_says_which_actual_it_used():
    """It was computed from the provider's actual against an estimate of unknown units."""
    f = _fields()
    D.reconcile_into_metrics(f, FACTS, document_id=7)
    s = f["eps_surprise_pct"]
    assert s.state is FieldState.OK
    assert s.value["computed_against"] == 33.42
    assert "PROVIDER" in s.value["note"]


def test_a_surprise_is_not_invented_from_the_issuer_figure():
    """No estimate units are stored, so dividing the issuer's figure into it would assume them."""
    f = _fields()
    D.reconcile_into_metrics(f, FACTS, document_id=7)
    assert f["revenue_surprise_pct"].state is FieldState.UNKNOWN
    assert "units" in (f["revenue_surprise_pct"].reason or "")


def test_current_guidance_is_available_but_the_change_is_not_claimed():
    f = _fields()
    D.reconcile_into_metrics(f, FACTS, document_id=7)
    assert f["guidance_current"].state is FieldState.OK
    assert f["guidance_current"].value["guidance_q1_revenue"]["value"] == 61_500_000_000
    # The REPORTED quarter's revenue is not guidance. Both keys contain "revenue"; only one is
    # a forecast, and a substring match would file the completed quarter as an outlook.
    assert "revenue" not in f["guidance_current"].value
    assert "eps_adjusted" not in f["guidance_current"].value
    g = f["guidance_change"]
    assert g.state is FieldState.UNKNOWN
    assert g.value["current_guidance_available"] is True
    assert "RAISED" in (g.reason or "")


def test_the_stated_basis_replaces_the_blanket_unknown():
    f = _fields()
    D.reconcile_into_metrics(f, FACTS, document_id=7)
    b = f["accounting_basis"]
    assert b.state is FieldState.OK
    assert b.value["stated_by_issuer_per_metric"]["revenue"] == "GAAP"
    assert b.value["stated_by_issuer_per_metric"]["eps_adjusted"] == "non-GAAP"
    # The ESTIMATE's basis is still unknown, so the surprise is still not a verified beat.
    assert b.value["estimate_basis"] == "UNKNOWN"


def test_nothing_is_promoted_when_the_document_carries_no_figures():
    f = _fields()
    D.reconcile_into_metrics(f, {}, document_id=7)
    assert f["revenue_actual"].state is FieldState.UNAVAILABLE
    assert f["accounting_basis"].state is FieldState.UNKNOWN
    assert "guidance_current" not in f


def test_a_null_valued_fact_does_not_overwrite_anything():
    f = _fields()
    D.reconcile_into_metrics(f, {"revenue": {"value": None, "units": "USD"}}, document_id=7)
    assert f["revenue_actual"].state is FieldState.UNAVAILABLE


def test_the_confirmed_period_answers_the_fiscal_period_field():
    import datetime
    doc = SimpleNamespace(id=7, fiscal_period_end=datetime.date(2026, 9, 3),
                          fiscal_label="FY2026 Q4", fiscal_source="investors.micron.com",
                          source_url="https://example/x")
    fld = D.confirmed_fiscal_period({}, doc)
    assert fld.state is FieldState.OK
    assert fld.value["label"] == "FY2026 Q4"
    assert fld.value["period_end"] == "2026-09-03"


def test_an_unconfirmed_document_does_not_answer_the_fiscal_period_field():
    doc = SimpleNamespace(id=7, fiscal_period_end=None, fiscal_label=None,
                          fiscal_source=None, source_url="https://example/x")
    assert D.confirmed_fiscal_period({}, doc) is None


def test_the_forward_verdict_does_not_deny_guidance_the_report_carries():
    """The exact contradiction this round removes, reintroduced one field lower: a verdict
    saying "guidance unavailable" directly beneath the guidance the issuer supplied."""
    from intel_reports.verdicts import forward_verdict as _forward_verdict
    with_guidance = {"guidance_current": observed({"guidance_q1_revenue": {"value": 1}})}
    v = _forward_verdict(with_guidance)
    assert "unavailable" not in v.lower()
    assert "RAISE" in v, "it must still refuse to call available guidance a raise"
    assert "cannot be formed" in _forward_verdict({})
    assert "cannot be formed" in _forward_verdict(
        {"guidance_current": unavailable("none joined")})


def test_the_result_verdict_reflects_a_stated_basis_when_one_exists():
    from intel_reports.verdicts import result_verdict as _result_verdict
    assert "basis is unverified" in _result_verdict({})
    v = _result_verdict({"accounting_basis": observed({"stated_by_issuer_per_metric": {}})})
    assert "issuer states a basis" in v
    assert "not a verified beat" in v, "the ESTIMATE's basis is still unknown"


# ---------------------------------------------------------------- the opening assessment

def test_the_opening_read_says_what_is_available_and_what_is_not_established():
    from intel_reports.verdicts import post_earnings_assessment
    f = _fields()
    D.reconcile_into_metrics(f, FACTS, document_id=7)
    f["official_figures"] = observed(FACTS)
    f["pre_report_link"] = unavailable("no frozen pre-earnings report exists")
    f["return_1d"] = observed({"pct": 3.03, "window": "2026-09-29 close to 2026-10-01 close"},
                              units="pct")
    a = post_earnings_assessment(f)
    t = a["read_this_first"]
    assert "Official results are available" in t
    assert "Current guidance is available" in t
    assert "UNVERIFIED" in t, "the comparison with pre-release expectations is not established"
    assert "2026-09-29 close to 2026-10-01 close" in t
    assert "does NOT isolate the announcement" in t
    assert "pre report link" in a["not_established"]


def test_the_opening_read_cannot_contradict_the_body_it_summarises():
    """It is derived, not written: with nothing available it must not claim anything is."""
    from intel_reports.verdicts import post_earnings_assessment
    a = post_earnings_assessment({})
    t = a["read_this_first"]
    assert "No official issuer release" in t
    assert "No company guidance" in t
    assert "No matured share-price reaction" in t
    assert a["available"] == []


def test_the_outlook_opening_leads_with_price_structure_and_participation():
    from intel_reports.verdicts import outlook_assessment
    f = {
        "price_as_of": observed({"close": 769.64, "ts": "2026-10-02T00:00:00"}),
        "trend_structure": observed({"structure": "above both moving averages"}),
        "breadth": observed({"participation_pct": 54.2}),
        "volatility": unavailable("no volatility series is ingested"),
        "execution_status": observed({"status": "information_only"}),
    }
    t = outlook_assessment(f, subject="The US benchmark")["read_this_first"]
    assert t.startswith("The US benchmark last closed at 769.64")
    assert "above both moving averages" in t
    assert "54.2% of covered symbols" in t
    assert "volatility" in t, "missing inputs are NAMED, not counted"
    assert "information_only" not in t, "the disclaimer is not the headline"


# ---------------------------------------------------------------- completeness vs quality

def test_coverage_counts_completeness_separately_from_provenance_and_conflicts():
    from intelligence.report_contract import coverage
    f = {
        "cited": observed(1, evidence_ids=["issuer_document:7"]),
        "uncited": observed(2),
        "disagreeing": observed({"value": 3, "conflict": "provider and issuer differ"},
                                evidence_ids=["issuer_document:7"]),
        "basis_unknown": observed({"estimate_basis": "UNKNOWN"}),
        "missing": unavailable("nothing on file"),
    }
    c = coverage(f)
    assert c["ok"] == 4 and c["total"] == 5, "completeness"
    assert c["sourced"] == 2, "a resolved field need not be a citable one"
    assert c["conflicts"] == 1, "a resolved field is not a settled one"
    assert c["comparability_unverified"] == 1
    assert "COMPLETENESS only" in c["note"]
