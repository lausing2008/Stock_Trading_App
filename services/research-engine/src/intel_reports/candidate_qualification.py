"""Code-reviewed issuer research, frozen into each valuation report.

Reviews are research decisions, not provider ratings. Adding or changing one requires a
reviewable source diff; there is deliberately no endpoint that accepts arbitrary gate passes.
"""
from copy import deepcopy
from datetime import date
import math
from urllib.parse import urlparse

REQUIRED_GATES = ("business_quality", "competitive_durability", "valuation",
                  "entry_condition", "value_trap_risk")
REVIEW_GATES = ("business_quality", "competitive_durability", "valuation", "value_trap_risk")
INPUT_CHECKS = ("dilution", "senior_claims", "material_filings", "accounting_basis", "model_assumptions")
RISK_CLASSES = ("leverage", "cash_burn", "customer_concentration", "dilution",
                "segment_concentration", "accounting_basis", "debt_maturities", "restatements",
                "related_parties", "auditor_going_concern", "litigation", "supplier_concentration")

REVIEWS = {
    "ADBE": {
        "symbol": "ADBE", "cik": 796343, "version": 1, "reviewed_at": "2026-10-29",
        "author": "Issuer-filing research review", "decision": "Research only",
        "summary": "The large screen discount is sensitive to stock compensation, the share denominator and growth assumptions. Keep Adobe as a research lead; a durable AI-era advantage and a defensible valuation are unresolved.",
        "sources": [
            {"id": "annual", "title": "Adobe FY2025 10-K · cash flows and diluted EPS",
             "url": "https://www.sec.gov/Archives/edgar/data/796343/000079634326000003/adbe-20251128.htm",
             "published_at": "2026-01-15", "period_end": "2025-11-28"},
            {"id": "quarter", "title": "Adobe Q3 FY2026 10-Q · liquidity, debt and risks",
             "url": "https://www.sec.gov/Archives/edgar/data/796343/000079634326000156/adbe-20260828.htm",
             "published_at": "2026-09-22", "period_end": "2026-08-28"},
        ],
        "findings": [
            {"claim": "FY2025 operating cash flow was $10.031bn, capital spending $179m, stock compensation $1.942bn and weighted diluted shares 427m.",
             "source_ids": ["annual"], "counterevidence": "Cash flow adds back compensation paid in equity. The provider model uses current basic shares and does not charge this owner cost."},
            {"claim": "Q3 filings report $5.639bn cash and short-term investments, $6.15bn senior-note principal and $250m commercial paper; $1.35bn notes mature in 2027.",
             "source_ids": ["quarter"], "counterevidence": "Refinancing, acquisitions and future buybacks can change cash available to equity holders."},
        ],
        "thesis": ["Creative and document workflows offer a case for recurring cash generation; test whether AI monetization protects that position."],
        "monitor": ["Compare organic recurring-revenue growth with acquisition contributions in the next results.",
                    "Track cash generation after stock compensation and capital spending, rather than adjusted EPS alone."],
        "invalidation": ["Reassess if AI competition weakens retention, pricing or recurring-revenue growth.",
                         "Reassess if owner cash generation falls or debt refinancing and acquisitions consume the assumed cash surplus."],
        "unresolved": ["AI-era switching costs and pricing durability are not established.",
                       "Issuer-specific discount rates and sustainable growth have not been calibrated.",
                       "A complete material-filing sweep through the report cutoff has not been verified.",
                       "The provider annual period (2025-11-30) differs from the filed fiscal year end (2025-11-28). Reconcile the statements before qualification.",
                       "The full value-trap risk checklist remains incomplete."],
        "gates": {}, "input_checks": {}, "risk_checks": {},
        # This is a sensitivity, not an approved model-input replacement or fair-value claim.
        "owner_cash_sensitivity": {"operating_cashflow": 10031e6, "capex": 179e6,
            "stock_compensation": 1942e6, "diluted_shares": 427e6,
            "period_end": "2025-11-28", "source_ids": ["annual"],
            "scenarios": {"bear": [0.0, .12, .02], "base": [.05, .11, .025], "bull": [.08, .10, .025]}},
    },
}


def _age(stamp, now):
    try:
        return (now.date() - date.fromisoformat(stamp)).days
    except (TypeError, ValueError):
        return None


def resolve_review(raw, now):
    """Fail closed on old, incomplete or issuer-mismatched reviews; preserve their findings."""
    spec = REVIEWS.get(raw["symbol"])
    if spec is None:
        return None, ["A dated issuer-specific qualification review is required"], raw
    review = deepcopy(spec)
    blockers = list(review.get("unresolved") or [])
    age = _age(review.get("reviewed_at"), now)
    current = age is not None and 0 <= age <= 30
    if not current:
        blockers.append("Issuer qualification review is stale or future-dated (30-day limit)")
    valid_sources = set()
    recent_statement = False
    for source in review.get("sources", []):
        url = urlparse(source.get("url", ""))
        source_age = _age(source.get("published_at"), now)
        if (url.scheme == "https" and url.hostname == "www.sec.gov"
                and f"/Archives/edgar/data/{review.get('cik')}/" in url.path
                and source_age is not None and source_age >= 0 and source.get("id")):
            valid_sources.add(source["id"])
            period_age = _age(source.get("period_end"), now)
            recent_statement |= period_age is not None and 0 <= period_age <= 150

    if not recent_statement:
        blockers.append("A sourced annual or interim statement within 150 days is required")

    def supported(item):
        refs = item.get("source_ids") or []
        return (item.get("status") == "supported" and bool(item.get("rationale"))
                and bool(item.get("counterevidence")) and bool(refs)
                and all(ref in valid_sources for ref in refs))

    if review.get("symbol") != raw["symbol"] or not review.get("author") or not review.get("version"):
        blockers.append("Issuer review identity, author or version is missing")
    if (not review.get("findings") or not review.get("invalidation") or not review.get("monitor")
            or any(not f.get("claim") or not f.get("counterevidence") or not f.get("source_ids")
                   or any(ref not in valid_sources for ref in f["source_ids"])
                   for f in review.get("findings", []))):
        blockers.append("Sourced findings, counterevidence, monitoring and thesis invalidation are required")
    for name in REVIEW_GATES:
        if not supported(review.get("gates", {}).get(name, {})):
            blockers.append(f"Sourced issuer review required: {name}")
    for name in INPUT_CHECKS:
        if not supported(review.get("input_checks", {}).get(name, {})):
            blockers.append(f"Verified model input check required: {name}")
    for name in RISK_CLASSES:
        if not supported(review.get("risk_checks", {}).get(name, {})):
            blockers.append(f"Value-trap review required: {name}")
    sweep_age = _age(review.get("filings_checked_through"), now)
    if sweep_age is None or not 0 <= sweep_age <= 7:
        blockers.append("Material filings must be checked through a date within seven days of generation")
    # A supported review must supply the actual reconciled model inputs, not merely assert
    # that basic shares / missing claims were checked. All values remain in the snapshot.
    verified = review.get("verified_inputs") or {}
    annuals = verified.get("annuals") or []
    refs = verified.get("source_ids") or []
    if (len(annuals) < 2 or not refs or any(ref not in valid_sources for ref in refs)
            or verified.get("currency") != "USD" or verified.get("financial_currency") != "USD"
            or not isinstance(verified.get("diluted_shares"), (float, int))
            or isinstance(verified.get("diluted_shares"), bool)
            or not math.isfinite(verified["diluted_shares"]) or verified["diluted_shares"] <= 0
            or any(row.get(k) is None for row in annuals for k in ("preferred", "minority_interest"))
            or any(row.get("period_end") not in {s.get("period_end") for s in review.get("sources", [])
                                                 if s.get("id") in refs} for row in annuals)):
        blockers.append("Reconciled annuals, explicit senior claims and a sourced diluted-share basis are required")
    review["status"] = "current" if current else "stale"
    review["qualification_blockers"] = sorted(set(blockers))
    model_raw = raw
    if not blockers:
        model_raw = {**raw, "annuals": deepcopy(annuals),
                     "currency": "USD", "financial_currency": "USD",
                     "info": {**raw.get("info", {}), "sharesOutstanding": verified["diluted_shares"]}}
    return review, review["qualification_blockers"], model_raw
