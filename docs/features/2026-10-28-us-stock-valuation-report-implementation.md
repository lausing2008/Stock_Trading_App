# US valuation report — first implementation

Implemented locally on October 28, 2026. Open **Reports → Valuation & Opportunities**, or `/reports?tab=valuation`, select US, then **Generate first report**. Later use **Refresh report**. Generation runs in the background and the browser resumes its job after returning to the page. The initial provider collection can take several minutes.

**Production release completed October 29:** [live report](https://lausing.com/reports?tab=valuation), final code `c1cf0ecf`. Report 53/v2 contains 60 names, including 38 beyond the previously tracked universe; 7 below-model research leads and 18 analyst risk flags. Desktop/mobile, HTTPS API, history/export, container health and source parity passed. See the [release record](../audits/2026-10-29-us-valuation-report-release.md).

Release completion: a production-network probe found Yahoo HTTP 429/invalid-crumb failures. The implementation now falls back to the existing enabled UW stock screener, company information, full financials, daily candles and dated analyst ratings. A live OTIS check returned 202 completed daily bars, five aligned annual periods and a dated analyst action, and produced an explicitly labeled FCFF proxy scenario. The existing key returned 403 for Advanced+ profiles and forward estimates; those endpoints are not added to the normal collection path and no upgrade is required.

## Delivered behavior

- Yahoo `undervalued_large_caps`, `undervalued_growth_stocks` and `most_actives` screens discover US listings independently of watchlists. A bounded sample reserves up to 40 places for discovery and fills remaining places from covered US symbols, up to 60 total. Actual discovered/sample counts and screen failures are visible. This is not exhaustive US-market coverage.
- Completed daily session selection respects the existing US holiday/early-close calendar and allows a 30-minute finalization buffer. Unknown future calendar coverage refuses generation. Same-session input caches are reused for up to 24 hours; older inputs can be retained for seven days and are explicitly stale when freshness checks fail.
- Market-data collects company profiles, annual statement inputs aligned by period, daily prices and dated analyst actions. It does not add securities to the Stock table, change watchlists, or create trade orders. UW fallback uses four bounded, metered calls per uncached company through the existing enabled integration; short-interest context remains cache-only. Provider identity and source links are visible per row.
- The report calculates trailing/forward P/E from its frozen price and provider EPS, displays provider PEG with its methodology limitation, and calculates Wilder RSI from at least 100 dividend/split-adjusted closes. Analyst aggregate targets remain separately labeled and lack verified publication dates/horizons.
- Profitable supported USD nonfinancial companies receive conditional bear/base/bull FCFF scenarios when sufficient inputs exist. FCFF uses EBIT after effective tax, depreciation, signed capex and signed working-capital cash impact; the enterprise-to-equity bridge accounts for cash, debt and available senior claims. All growth/discount/terminal assumptions and missing-claim assumptions appear in detail and export.
- Where UW lacks working-capital detail, the scenario explicitly uses an **FCFF proxy**: operating cash flow plus after-tax interest expense less capital spending. This requires review of cash-interest classification/tax assumptions and cannot qualify a company for investment. UW EPS is a dated sum of four consecutive reported quarters with unverified GAAP/adjusted basis. Missing forward EPS, PEG and price-adjustment metadata remain unavailable; raw UW closes are never passed off as adjusted RSI. Exact exchange/quote-currency metadata absent from UW's basic company profile is disclosed.
- Filters cover below-model-value research, above-model-value companies, dated analyst risk flags and qualified opportunities. Search, sector filtering, sorting, 20-row pagination and company expansion operate on the selected frozen report. Analyst risk remains independent of valuation classification.
- Existing Quality & Value gates are consulted. Qualified opportunities remain blocked while diluted share basis/latest material filings or required quality evidence are unverified. The initial scenario calculation itself does not close those gaps. Below-model-value research leads remain available for investigation, with explicit blockers.
- Immutable reports use the existing `IntelligenceReport` table and store evidence values, not only source URLs. Identical inputs reuse the existing snapshot. Versions link to predecessors; older contents remain intact. Classification differences are relative to the previous generated snapshot, not a weekly market-history claim. First observations say new coverage.
- CSV exports include all report rows, metric states/reasons, source dates, scenario values and assumptions. Existing tracked names can use the existing watchlist picker; newly discovered names link to their Yahoo source until added to the app's tracked universe through existing flows.

## Code and operational integration

| Area | Files |
|---|---|
| Provider ownership | Market-data `services/valuation_inputs.py` and `api/valuation.py` |
| Calculations / frozen report | Research `intel_reports/valuation_report.py` |
| Jobs / reads / persistence | Research `api/valuation_routes.py`; existing report store |
| Reports interface | `frontend/src/components/reports/ValuationReport.tsx`, `lib/valuationReport.ts` |
| Registration | Both service `main.py` files; frontend API, Reports tab and Reports navigation |

The existing `intel_reports/valuation.py` scenario-assessment module is preserved; the new screen uses `valuation_report.py`. The report enum adds `us_equity_valuation` without changing the semantics of the four existing types. No database migration or paid-provider purchase is needed. The gateway already routes `/stocks/*` and `/intel/*` to their owning services.

Authenticated routes:

```text
POST /stocks/valuation-inputs/refresh    bounded Yahoo input refresh
POST /intel/valuation/generate          202, job ID; five-minute generation cooldown
GET  /intel/valuation/jobs/{job_id}     queued/running/complete/failed
GET  /intel/valuation/latest            latest saved report or null
GET  /intel/valuation/history           bounded report summaries
GET  /intel/valuation/reports/{id}      one saved public market report
```

Redis holds job state, provider input caches and token-owned generation leases. Jobs run through FastAPI background tasks; this is not a durable queue. A worker restart can interrupt a job; leases/job records expire after 30 minutes and generation can be retried. The market-data collection request has a 20-minute research-side timeout. Entirely failed input refreshes preserve the last successful report. A generation lease and five-minute cooldown reduce repeated report requests; provider requests are bounded to four concurrent company fetches. Operational retries and scheduled daily generation are future extensions.

## Validation and release status

Validation uses synthetic provider inputs, a real temporary SQLite database, real report persistence and route handlers, and interactive DOM tests. It covers discovery outside tracked stocks, holidays/early closes, stale fallback, FCFF bridging, RSI edge cases, EPS repricing, discount/upside denominators, unsupported models, missing evidence, idempotence, immutable versions, failed-refresh preservation, authentication dependencies, private-report refusal, filters, source disclosures and CSV spreadsheet escaping.

The host's installed Starlette/AnyIO TestClient portal hangs before the first request; the workflow probe invokes the registered route handlers and background callbacks directly and checks their registered authentication dependencies. It does not claim an HTTP transport integration test. PostgreSQL concurrency guarantees rely on the existing report store and were not re-certified by the SQLite probe.

The initial local slice passed 105 focused tests. Release completion passed 999 frontend tests, 83 research tests, 5 market-data tests and 14 gateway tests: **1,101 total**, plus typecheck and production build. Live provider probes, production HTTPS checks, desktop/mobile Chromium checks and deployment/source verification are recorded in the release record. Broad-universe quota/coverage validation and historical/prospective return evaluation have not been established.

Further delivery against the [design](2026-10-27-us-stock-valuation-report-design.md) includes a complete security master, verified current analyst metadata, sector-specific valuation models, issuer-confirmed latest filings/diluted shares, dated company catalysts, an independent weekly baseline, durable scheduled jobs and measured point-in-time performance. These are not prerequisites for inspecting the delivered research screen, and they must not be inferred from its classifications.
