"""Qualification can be reached with evidence, and cannot be reached by omitting gates."""
from copy import deepcopy
from datetime import timedelta

import pytest

from src.intel_reports import candidate_qualification as C
from src.intel_reports import valuation_report as V
from test_valuation_report import source, inputs, NOW


def approved_review(raw):
    review = deepcopy(C.REVIEWS["ADBE"])
    review.update(symbol=raw["symbol"], cik=123, version=1, reviewed_at=NOW.date().isoformat(),
                  filings_checked_through=NOW.date().isoformat(), unresolved=[], decision="Qualified research")
    review["sources"] = [{"id": str(i), "url": f"https://www.sec.gov/Archives/edgar/data/123/000000012326{i:06d}/filing.htm",
                          "published_at": NOW.date().isoformat(), "period_end": period}
                         for i, period in enumerate([r["period_end"] for r in raw["annuals"]] + ["2026-08-31"])]
    item = {"status": "supported", "rationale": "Reviewed source finding", "source_ids": ["0", "2"],
            "counterevidence": "The adverse case was considered and is bounded by these assumptions"}
    review["gates"] = {key: deepcopy(item) for key in C.REVIEW_GATES}
    review["input_checks"] = {key: deepcopy(item) for key in C.INPUT_CHECKS}
    review["risk_checks"] = {key: deepcopy(item) for key in C.RISK_CLASSES}
    review["findings"] = [{"claim": "Reviewed issuer finding", "source_ids": ["0"], "counterevidence": "Adverse case"}]
    review["verified_inputs"] = {"annuals": deepcopy(raw["annuals"]), "diluted_shares": 100e6,
                                 "source_ids": ["0", "1", "2"], "currency": "USD", "financial_currency": "USD"}
    review.pop("owner_cash_sensitivity", None)
    review["filing_dispositions"] = {f"0000000123-26-{i:06d}": {"status": "reviewed", "rationale": "Reviewed fixture filing",
        "reviewed_at": NOW.date().isoformat()} for i in range(3)}
    return review


def quality():
    return {"gates": [{"gate": name, "status": "pass"} for name in C.REQUIRED_GATES], "blocking": []}


def register(monkeypatch):
    raw = source()
    for bar in raw["bars"]:
        bar["close"] = 10  # Genuine >=30% model discount, sufficient liquidity.
    review = approved_review(raw)
    raw["filing_verification"] = {"state": "OK", "symbol": raw["symbol"], "cik": 123,
        "checked_at": NOW.isoformat(), "since": NOW.date().isoformat(), "filings": [
            {"accession": f"0000000123-26-{i:06d}", "form": "10-K" if i < 2 else "10-Q", "filed": NOW.date().isoformat()}
            for i in range(3)]}
    raw["financial_verification"] = {"state": "OK", "cik": 123, "checked_at": NOW.isoformat(),
        "latest_period_end": raw["annuals"][0]["period_end"],
        "latest_annual": {"revenue": {"value": raw["annuals"][0]["revenue"]}}}
    monkeypatch.setitem(C.REVIEWS, raw["symbol"], review)
    return raw, review


def test_complete_sourced_current_review_can_qualify_and_uses_reconciled_inputs(monkeypatch):
    raw, review = register(monkeypatch)
    raw["info"]["sharesOutstanding"] = 1e6  # Provider denominator must not win.
    frozen = deepcopy(raw)
    row = V.build_row(raw, "2026-10-27", NOW, quality())
    assert row["qualified"], row["blockers"]
    assert row["valuation"]["inputs"]["shares_outstanding"] == 100e6
    assert row["valuation"]["inputs"]["review_version"] == review["version"]
    assert raw == frozen
    row["candidate_review"]["findings"].clear()
    assert review["findings"]  # Snapshot copy never mutates the reviewed registry.


@pytest.mark.parametrize("issue", ["missing_gate", "failed_gate", "duplicate_gate", "stale_review",
    "future_review", "old_sweep", "future_source", "wrong_issuer", "missing_risk", "unsupported",
    "unresolved", "missing_claims", "infinite_shares", "missing_finding", "wrong_period", "old_statement"])
def test_incomplete_or_inconsistent_evidence_never_qualifies(monkeypatch, issue):
    raw, review = register(monkeypatch)
    gates = quality()
    if issue == "missing_gate": gates["gates"].pop()
    if issue == "failed_gate": gates["gates"][0]["status"] = "fail"
    if issue == "duplicate_gate": gates["gates"].append(gates["gates"][0])
    if issue == "stale_review": review["reviewed_at"] = (NOW - timedelta(days=31)).date().isoformat()
    if issue == "future_review": review["reviewed_at"] = (NOW + timedelta(days=1)).date().isoformat()
    if issue == "old_sweep": review["filings_checked_through"] = (NOW - timedelta(days=8)).date().isoformat()
    if issue == "future_source": review["sources"][0]["published_at"] = "2027-01-01"
    if issue == "wrong_issuer": review["sources"][0]["url"] = "https://www.sec.gov/Archives/edgar/data/999/filing.htm"
    if issue == "missing_risk": review["risk_checks"].pop("dilution")
    if issue == "unsupported": review["gates"]["competitive_durability"]["counterevidence"] = ""
    if issue == "unresolved": review["unresolved"] = ["Critical issue remains unresolved"]
    if issue == "missing_claims": review["verified_inputs"]["annuals"][0]["preferred"] = None
    if issue == "infinite_shares": review["verified_inputs"]["diluted_shares"] = float("inf")
    if issue == "missing_finding": review["findings"] = []
    if issue == "wrong_period": review["verified_inputs"]["annuals"][0]["period_end"] = "2025-12-30"
    if issue == "old_statement": review["sources"][-1]["period_end"] = "2025-08-31"
    row = V.build_row(raw, "2026-10-27", NOW, gates)
    assert not row["qualified"]


def test_adobe_review_exposes_owner_cost_and_does_not_approve_unreviewed_risks():
    raw = source("ADBE")
    now = NOW + timedelta(days=1)
    row = V.build_row(raw, "2026-10-27", now, quality())
    review = row["candidate_review"]
    assert not row["qualified"]
    assert review["sensitivity"]["cashflow"] == 7910e6
    assert review["sensitivity"]["diluted_shares"] == 427e6
    assert "enterprise_value" not in review["sensitivity"]["scenarios"]["base"]
    assert review["sensitivity"]["scenarios"]["base"]["fair_value"] < 300
    assert review["sources"][1]["published_at"] == "2026-09-22"
    assert any("material" in blocker.lower() for blocker in row["blockers"])


def test_unreviewed_company_stays_a_lead_even_with_legacy_gate_passes():
    row = V.build_row(source(), "2026-10-27", NOW, quality())
    assert not row["qualified"]
    assert row["candidate_review"] is None
    assert any("issuer-specific" in blocker for blocker in row["blockers"])


def test_review_revisions_change_fingerprint_without_mutating_prior_snapshot(monkeypatch):
    raw, review = register(monkeypatch)
    first = V.build_report(inputs(raw), now=NOW, quality={raw["symbol"]: quality()})
    review["version"] += 1
    review["unresolved"] = ["New material issue"]
    second = V.build_report(inputs(raw), now=NOW, quality={raw["symbol"]: quality()})
    assert first[2]["fingerprint"] != second[2]["fingerprint"]
    old = first[0]["valuation"].value["rows"][0]
    assert old["qualified"]
    assert old["candidate_review"]["version"] == 1
    assert not second[0]["valuation"].value["rows"][0]["qualified"]


def test_verified_inputs_can_resolve_a_provider_period_discrepancy(monkeypatch):
    raw, _ = register(monkeypatch)
    raw["annuals"][0]["period_end"] = "2025-12-30"
    row = V.build_row(raw, "2026-10-27", NOW, quality())
    assert row["candidate_review"]["reconciliation_flags"]
    assert row["qualified"], row["blockers"]
    assert row["valuation"]["period_end"] == "2025-12-31"
