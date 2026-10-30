# Valuation candidate qualification

## Outcome and scope

Reports → Valuation & Opportunities adds a **Reviewed candidates** filter and a dated issuer review inside company details. Reviews include primary-source findings, counterevidence, thesis invalidation, monitoring questions and unresolved research. CSV exports the review version, dates, sources and sensitivity values. Existing snapshots retain their original behavior and data.

The first review covers **ADBE**, the only below-model-value company in report 54. It remains **Research only**. The screen's approximately 50% discount is not a supported recommendation: provider period alignment, AI-era durability, valuation assumptions, a current material-filing sweep and the risk checklist remain unresolved.

## Research finding

The [FY2025 10-K](https://www.sec.gov/Archives/edgar/data/796343/000079634326000003/adbe-20251128.htm) supplies operating cash flow $10.031bn, capital spending $179m, stock compensation $1.942bn and weighted diluted shares 427m. A separate owner-cash sensitivity deducts both capital spending and stock compensation, leaving $7.910bn, and uses that diluted denominator. It produces **$173.99 / $247.84 / $318.67** under the explicitly disclosed bear/base/bull assumptions, compared with the report's $248.12 session price. This demonstrates assumption sensitivity; it does not replace the provider model or establish fair value.

The [Q3 FY2026 10-Q](https://www.sec.gov/Archives/edgar/data/796343/000079634326000156/adbe-20260828.htm) provides updated liquidity and debt context. Acquisitions, refinancing and repurchases require reconciliation. The provider's annual period label also differs from the filed year end. These gaps are displayed explicitly.

Owner cash is a levered equity proxy. Its sensitivity discounts equity cash flows directly and adds no enterprise-to-equity cash/debt adjustment or buyback benefit. Annual diluted weighted-average shares are a conservative sensitivity denominator, not a claim about today's fully diluted capitalization. Growth, discount and terminal rates are assumptions rather than calibrated forecasts.

## Qualification contract

Policy: `valuation-v3-candidate-review`.

1. All five named Quality & Value gates must pass: business quality, competitive durability, valuation, entry condition and value-trap risk. Missing or duplicate gates cannot qualify.
2. A code-reviewed issuer assessment must match the symbol and SEC CIK, identify its author/version, be no older than 30 days and contain source-linked findings, counterevidence, monitoring and invalidation conditions.
3. Fundamental review gates, five model-input checks and all 12 declared risk classes must have supported findings with valid primary references. Unresolved research blocks eligibility.
4. Source dates cannot be future-dated. A cited annual/interim statement must end within 150 days. A documented material-filing check must reach a date within seven days of generation.
5. A passing review supplies reconciled annual statements, explicit preferred/minority claims and a sourced diluted-share denominator. Annual periods must match cited filings. Those inputs actually replace provider model inputs before scenarios are recomputed; an assertion that inputs were checked cannot remove the blocker.
6. The existing liquidity, identity, price freshness, supported-model and at least 30% discount requirements still apply. The deterministic model validates cash-flow inputs and signs. A current review cannot turn an unsupported model into a qualified opportunity.

The former unconditional dilution/filing blocker is removed only after these checks pass and the reconciled model succeeds. No issuer in the initial registry is pre-approved. A complete synthetic evidence fixture demonstrates that the qualification path is reachable.

## Implementation and operation

`candidate_qualification.py` owns the reviewed registry and pure validation. `valuation_report.py` applies it, adds review evidence to the frozen evidence book and includes the result in report fingerprints. The frontend displays additive optional fields, so older reports remain readable. No schema change, paid data endpoint or arbitrary assessment-write API is introduced.

To extend coverage, add a new versioned registry entry with issuer sources, findings and explicit remaining gaps. Only a completed supported review should supply approved model replacements. Deploy research-engine and frontend, then generate a new snapshot. A later review revision changes the fingerprint without changing prior snapshots. Review/filing-check expiry prevents old approvals carrying forward silently. Archived reports describe their generation cutoff; refresh to reassess current eligibility.

This release improves one current lead and establishes the review path. Broader issuer research, whole-market coverage, durable scheduling and sector-specific models remain separate work.

## Validation

Unit coverage includes a reachable positive case, complete required gates, duplicate/failing gates, stale/future review dates, stale filing checks, issuer/source mismatches, missing findings and risk reviews, unresolved issues, nonfinite diluted shares, absent senior claims, annual-period mismatches, statement freshness, frozen revisions and the Adobe sensitivity. Frontend checks cover reviewed filtering, primary links, invalidation, sensitivity disclosure, empty qualified results and CSV columns. **180 selected research tests, all 1,001 frontend tests and frontend typecheck passed.** Release acceptance is recorded separately after deployment.

Deployed revision `66e4c7ee` to research-engine and frontend. Production **report 55 / v4** contains 60 companies, one reviewed issuer and zero qualified opportunities. Desktop 1440px and mobile 390px checks passed for reviewed filtering, source links, sensitivity/invalidation disclosure and CSV export, with no runtime errors or page overflow. All 15 containers were healthy; all 12 backend source trees matched the integrated release. See the [release audit](../audits/2026-10-29-valuation-candidate-qualification-release.md).
