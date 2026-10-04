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
