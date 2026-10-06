# Fundamentals data audit — what exists before building anything

Date: October 6, 2026. Read-only production queries. No code or settings changed by the audit itself.

**Your point was correct:** Stock Outlook hardcodes `company_condition` as unavailable, and that
says nothing about whether the platform holds the data. It does.

## What exists

`financial_statements` — one row per (symbol, period_end, period_type), backfilled from yfinance.

| | |
|---|---|
| Rows | 1,424 |
| Symbols | 156 |
| Period range | 2021-09-30 .. 2026-07-31 |
| Annual rows | 653 across 156 symbols |
| Quarterly rows | 771 across 155 symbols |

Field completeness on annual rows:

| Field | Present | Field | Present |
|---|---:|---|---:|
| total_debt | 97.9% | total_equity | 94.8% |
| total_revenue | 95.3% | cash_and_equivalents | 94.8% |
| net_income | 95.3% | total_assets | 94.2% |
| free_cashflow | 95.3% | capital_expenditure | 94.3% |
| operating_cashflow | 95.1% | ebit | 89.7% |
| operating_income | 88.8% | gross_profit | 86.4% |

MU has five fiscal years of annuals plus recent quarterlies, with revenue, gross profit, EBIT,
operating cash flow and capex all present — enough for growth, margin and cash-generation
analysis without any new ingestion.

## Three demonstrated gaps

**1. The table is stale, and its newest MU annual predates the quarter just reported.**
`max(fetched_at)` is **2026-09-07** — a month old. MU's newest annual row is period-end
2025-08-31, while MU has since reported fiscal 2026 (period ended 2026-09-03). Any
multi-period analysis built on this alone would describe a company one full fiscal year out of
date while an issuer-verified release for the newer year sits elsewhere in the platform.

*Required:* a refresh, and until then the staleness must be stated in the report rather than
implied away by showing whatever is newest.

**2. No share count anywhere, so dilution cannot be analysed.** Neither `fundamentals` nor
`fundamentals_snapshot` carries shares outstanding or diluted shares — the only share-related
column in either is `short_percent_of_float`. Dilution is one of the four things the business
section is meant to cover and it cannot be computed from stored data.

*Required:* ingestion, or the field stays unavailable with that reason. It must not be inferred
from net income and EPS, which would silently assume the EPS basis.

**3. No accounting basis and no publication timestamp per row.** The table records `fetched_at`
— when the platform retrieved it — and nothing about when the issuer filed it, nor whether the
figures are GAAP or adjusted. yfinance does not supply either.

*Consequence:* these figures can be shown as reported history, but cannot support a
point-in-time claim ("what was knowable on date X"), and a comparison against an
issuer-release figure crosses an unverified basis boundary — the same limitation already
stated for the earnings estimate.

## Conclusion

Build the business-performance section on `financial_statements` now; add ingestion only for
share count. Keep dilution unavailable with its reason rather than deriving it. Surface the
as-of period and the fetch age on every figure, because the newest row is not the newest
reality.
