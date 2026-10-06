# Fundamentals, competitive durability and external research integration

Date: October 5, 2026 (Pacific). Status: proposed implementation and integration plan; repository inspection only, not a new production audit. This document authorizes no subscription purchase, production migration, model activation, notification or trading action. User request: document the fundamentals/moat recommendations and access to Bloomberg, Morgan Stanley and other research, with implementation and integration plans.

Companions: [interpretation and decision support](2026-10-03-report-interpretation-and-decision-support-design.md), [evidence-grounded narration](2026-10-03-evidence-grounded-report-narration-design.md), [N1 packets](2026-10-04-n1-evidence-packets-and-narration-validation.md), [measurement framework](2026-09-30-measurement-framework-and-improvement-backlog.md), [news cost audit](../audits/2026-10-05-news-classification-volume-review.md).

Related product plan: [Quality & Value Opportunities dashboard and email alerts](2026-10-06-quality-value-opportunities-dashboard-and-alerts.md), covering quality/value/entry gates, thesis monitoring, explicit opt-in delivery and phased rollout.

## 1. Product outcome and scope

Stock Outlook should answer: Is the business improving? What could sustain the improvement? What threatens it? How does valuation and price behaviour compare with the business evidence? Market Outlook should explain observed earnings, growth, inflation and rates developments, competing interpretations, and observable triggers.

Use three distinct inputs: primary-source facts, attributed external analyst views, and our own reproducible calculations/interpretations. Do not merge them into a generic bullish score. An investment-bank forecast is not an observed fact; repeated news coverage of one note is not independent corroboration. An AI-written summary is not a new source.

First screen: at most three material findings, each with evidence, counterevidence, horizon and next observable trigger. Detailed financial tables, external arguments, sources and methods follow. Missing evidence and evidence to obtain remain separate from market triggers. No report creates order authority or bypasses existing risk checks.

Scope includes fundamentals, competitive durability, valuation, external-research acquisition and synthesis, market research, prospective estimate/macro capture, cost controls, testing and rollout. It does not commit to buying an institutional terminal or replacing existing data providers.

## 2. Current implementation and reuse inventory

Paths below were inspected in this checkout; production availability and data quality must be measured separately. Code has advanced since earlier conversation: Stock Outlook now calls `A.business_performance` and derives company-condition findings.

| Existing component | Reuse | Verification/gap |
|---|---|---|
| `shared/db/models.py`: Fundamental, FundamentalsSnapshot, FinancialStatement | Existing ratios, snapshots and annual/quarterly raw statements | Audit actual coverage, identity, units, basis, timestamps and revisions; fetch time is not filing time |
| `services/market-data/src/services/scheduler.py`: backfill_financial_statements | Existing statement ingestion | Unique symbol/period/type rows alone cannot preserve restatement vintages |
| `services/research-engine/src/intel_reports/adapters.py`: business_performance | Existing statement-to-report adapter | Verify comparison eligibility and cutoff limitations before expanding |
| `generators.py`, `interpretation.py`, `verdicts.py` in that directory | Report assembly, evidence selection and deterministic interpretation | Extend existing fields; do not create a parallel report generator |
| `IssuerDocument`, `intel_reports/documents.py` | Official releases, fiscal identity, citations and corrections | Broker notes must retain external provenance and never certify issuer facts merely by association |
| `shared/intelligence/{report_contract,evidence_packet,quantities,claims}.py` | Typed quantities, permission gates and server-rendered claims | New claim kinds need explicit prerequisites; never restore an unrestricted prose path |
| `services/research-engine/src/api/routes.py`, `scoring.py` | Existing AI Research moat fields and research transports | Model-generated ratings are attributed legacy assessments, not validated moat evidence |
| `frontend/src/pages/intelligence-reports.tsx`, `lib/intelReportLayout.ts`, `lib/intelReportView.ts` | Rendering, temporal sections and expandable reasoning | Add cards and source drilldowns; keep metadata out of the lead |
| Existing LLM usage/admission system | Reservations, settlement and metering | Verify extraction is inside admission control and budgets apply to every enabled call |

Historical moat documents: [quantitative moat scoping](../2026-09-06/SCOPING_QUANTITATIVE_MOAT_SCORE.md), [remaining work](../2026-09-06/SCOPE_MOAT_REMAINING_WORK.md). Their stored-data work is reusable. Any implication that a few years of high ROIC automatically establish a wide moat is not adopted here. Economic durability requires mechanism evidence, counterevidence and accounting/cycle context.

## 3. Access and acquisition plan

| Source | Access route | Integration decision |
|---|---|---|
| SEC filings and issuer investor relations | Public filings APIs, releases, presentations and permitted transcripts | Foundation for independent factual checks; reuse official-document ingestion |
| Bloomberg | Terminal research access and separately licensed enterprise/data products | Obtain content-specific quote and entitlements; Data License availability does not prove a broker note is included |
| Morgan Stanley | Client research portal and selected public commentary | Use public material or appropriately authorized client documents; do not assume an unrestricted research API |
| S&P Capital IQ investment research | Real-time/aftermarket subscriptions and Research API | Evaluate as a multi-provider route; confirm publisher entitlements and delays individually |
| Other banks, independent analysts and asset managers | Official public research, permitted client exports, or licensed feeds | Evaluate coverage and rights per collection; syndicated news is a secondary source |
| Brokerage research access | Existing account entitlements, where applicable | Human reading access does not establish server ingestion or AI processing rights |

Before procurement, obtain written scope for: US/HK/company coverage; report types; real-time versus delayed delivery; historical vintages; API/export and rate limits; storage/retention; external-model processing; embeddings; internal derived analysis; user-facing distribution; entitlements per user; deletion obligations; total price. Do not invent prices or assume all collections share one license. No scraping authenticated portals or bypassing access restrictions.

Implementation starts with permitted public documents and authorized uploads. An upload requires source and permission metadata; possession of a PDF alone is not sufficient permission for every downstream use. If rights permit reading but not external AI processing, use a manual structured extraction workflow or another explicitly permitted path. Unsupported rights remain pending.

Source references checked during the preceding research discussion (reconfirm commercial terms before procurement):

- [Bloomberg Data License](https://professional.bloomberg.com/products/data/data-license/) — enterprise delivery includes REST API, SFTP and cloud options.
- [Morgan Stanley research access FAQ](https://www.morganstanley.com/about-us-ir/faq) and [public research](https://www.morganstanley.com/what-we-do/research/).
- [S&P investment research](https://www.spglobal.com/market-intelligence/en/solutions/investment-research) and [Research API description](https://www.support.marketplace.spglobal.com/content/dam/spglobal/mi/en/documents/marketplace/newsletters/2024/marketplace_data__solutions_communique_july_31_2024.pdf).
- [SEC EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces).

### Free-source starting stack

Added at the user's request following the free-source review. Start without an institutional research subscription. Public access does not establish permission for unlimited automated retrieval, external-model processing or redistribution; verify source and series terms before enabling each connector. Processing, storage and LLM usage still cost money.

| Source and official reference | Useful evidence | Access and integration limits |
|---|---|---|
| [SEC EDGAR developer resources](https://www.sec.gov/about/developer-resources) | US company filings, XBRL financial facts, segment/business disclosures, risk factors and earnings-release exhibits | Prefer official JSON APIs and filing documents; comply with SEC access policies. Normalize fiscal periods, units and amendments rather than assuming every fact is comparable. |
| Company investor-relations websites | Official earnings releases, guidance, presentations and transcripts where published | Register exact issuer domains/URLs; document retrieval is source-specific. Transcripts are not universally available. Management statements remain attributed claims. |
| [HKEXnews](https://www.hkexnews.hk/index.htm) | Hong Kong annual/interim results and company announcements | Public documents; do not assume a free bulk API or unrestricted crawling. Preserve stock/issuer identity, currency, language and publication time. |
| [FRED / ALFRED API](https://fred.stlouisfed.org/docs/api/fred/) | Rates, credit and macro series; historical vintages where available | Reuse the existing rates integration first; configure API credentials where required and check series-specific terms. Preserve observation dates separately from vintages and retrieval times. Vintage dates do not automatically establish intraday availability. |
| [BLS public data API](https://www.bls.gov/bls/api_features.htm) | Inflation, employment and other published time series | Respect registration/rate limits; retain release identity and revisions. Current historical observations must not silently replace first-release values. |
| [BEA data API](https://apps.bea.gov/api/signup/) | GDP, income, spending and other economic statistics | Register for access as required; retain units, frequency, release dates and revisions. |
| [Federal Reserve research and analysis](https://www.federalreserve.gov/publications/research-and-analysis.htm) | Policy/economic context, research and statistical publications | Public publications; distinguish official policy statements from research authors' interpretations and projections. |
| [Morgan Stanley Insights](https://www.morganstanley.com/insights/) and [Thoughts on the Market](https://www.morganstanley.com/insights/podcasts/thoughts-on-the-market) | Selected institutional market views and commentary | Public reading sources, not the full client equity-research library. Confirm extraction/AI/display permissions; retain speaker, date, horizon and attribution. |
| [BlackRock Investment Institute](https://www.blackrock.com/corporate/insights/blackrock-investment-institute/insights) | Market outlooks, macro themes and competing investment arguments | Public commentary; confirm processing and reuse permissions. Third-party data quoted inside a publication may have separate restrictions. |

These sources support four distinct report uses:

- **Stock fundamentals:** existing stored statements plus SEC/issuer evidence; HKEXnews for Hong Kong disclosures.
- **Competitive durability:** issuer business disclosures and comparable peer history, with sourced competitive evidence and counterevidence. A management assertion or strong margin is not itself a verified moat.
- **Market analysis:** observed FRED/BLS/BEA series plus policy publications, retaining release timing and vintage information.
- **External perspectives:** a small permitted collection of Morgan Stanley/BlackRock commentary, clearly separated from primary facts and our own interpretation.

### Free-source integration order and acceptance

1. **Inventory and reuse:** map stored statements and current FRED/rates coverage to exact source series and document versions. Report gaps before adding another connector.
2. **First automated slice:** SEC plus issuer releases for MU and the contrasting pilot company, alongside the existing rates integration. Require an idempotent import, field-level evidence locators, comparable fiscal periods, and a report generated without a new model call for unchanged data.
3. **Macro and HK extensions:** add specific BLS/BEA releases and HKEX documents only where the report has a defined missing input. Validate revision handling, publication/capture timing and access limits before scheduling.
4. **Public-commentary pilot:** begin with a small allowlisted, permission-reviewed collection. Manual structured extraction is acceptable where automated processing rights are unresolved. Show an attributed external thesis, contrary evidence and our independently supported assessment; do not claim consensus from two publishers.
5. **Evaluate before purchasing:** measure coverage, source usefulness, extraction cost and reader comprehension. Seek paid research only for a demonstrated gap that free sources cannot fill, under the procurement checks above.

Free sources do not automatically solve **historical analyst consensus** or **pre-release macro expectations**. Released actuals are not expectations; issuer guidance is not analyst consensus. Preserve P1's prospective capture work, and keep revisions/surprises unavailable until the specific comparability and before-publication requirements are met. A public forecast can be an attributed forecast without qualifying as market consensus.

No connectors, credentials, schedules or paid processing are activated by this source catalog. The phase gates, evidence rules and budget controls below apply equally to free and paid inputs.

## 4. Proposed evidence model

Names are logical contracts, not a requirement to create one table per row. Final migrations follow the data audit and should extend suitable existing models.

| Record | Required information |
|---|---|
| Research document/version | Publisher, author, source URL/provider ID, type, issuers/regions, published/retrieved timestamps, original bytes hash if available, supersession link, access scope, retention/AI/display permissions |
| Extraction version | Document version, extractor/model/prompt/policy versions, processing status, facts hash, evidence locators, validation result, actual token usage |
| Financial fact version | Issuer, metric, value, currency/units/scale, GAAP/non-GAAP basis, period start/end, duration/instant, source version and page/table/XBRL locator, availability/retrieval timestamps, restatement relationship |
| External research claim | Author/publisher, thesis or forecast, target metric/period/horizon, assumptions, supporting passages, counterarguments, price-target currency and horizon if any, evidence status |
| Our finding | Input fact/claim IDs, deterministic calculation or interpretation rule version, support, contrary evidence, uncertainty, trigger and expiry |
| Estimate snapshot | Provider, issuer, target period, metric, units/basis, value, contributor population if known, provider timestamp and capture time; append-only |
| Macro release vintage | Series/release/reference period identity, scheduled/actual publication times, expectation source/value/capture time, first-released actual and separate revisions |

Preserve raw source bytes only where permitted. A hash of extracted facts must never be labelled a source-bytes hash. Corrections create versions; old report payloads remain immutable and can receive correction pointers. Identity should use stable issuer identifiers, not ticker alone, including listing/currency changes.

Cutoff eligibility uses evidence actually available by the report cutoff. Observation/period dates do not establish availability. Historical data retrieved today is usable with disclosed current-snapshot provenance; it is not retroactively eligible for a historical forecast. Unknown publication or vintage information must not silently pass point-in-time checks.

## 5. Business performance and valuation rules

Prefer the most recent eight comparable quarters and five annual periods where available; show actual coverage and do not fabricate missing periods. Shorter histories can support narrower findings.

| Dimension | Deterministic work | Important refusal/qualification |
|---|---|---|
| Growth | Revenue and EPS changes against matching periods | Nonpositive EPS baselines need explicit treatment; do not call an arbitrary percentage meaningful growth |
| Profitability | Gross/operating margin levels and changes | Same currency/unit/basis; changes in margin are percentage points |
| Cash generation | Operating cash flow, capex and FCF | Normalize capex sign explicitly; issuer adjusted FCF differs from OCF-minus-capex |
| Balance sheet | Debt, cash, liquidity and maturity exposure where sourced | Restricted cash and investments are not automatically available cash |
| Dilution | Comparable diluted share-count changes | Separate weighted-average diluted shares from period-end outstanding shares; handle splits |
| Returns on capital | Explicit NOPAT and average invested-capital definitions | No default tax rate without disclosure; invalid denominators/refinancing/buybacks can distort results |
| Valuation | Price/enterprise-value multiples, historical and peer comparisons | Align price date, fiscal metric and estimate vintage; distinguish trailing/forward; do not average incompatible or invalid ratios |

Fiscal calendars, 52/53-week years, annual/quarterly/YTD durations and restatements must be normalized explicitly. Do not sum overlapping YTD cash flows into TTM. Do not splice GAAP history into an adjusted growth series. Banks, insurers and ETFs need separate applicability rules; industrial ROIC/FCF templates are not universal.

Select findings for materiality, not merely data availability. Rule thresholds must be explicit, versioned and evaluated on fixtures. Positive technical returns, strong earnings and high valuation answer different questions and should not collapse into one quality score.

## 6. Competitive advantage and durability

Evaluate switching costs, cost/scale advantages, intellectual property, network effects, distribution advantages and regulatory/contractual barriers where relevant. Each card contains the claimed mechanism, sourced evidence, beneficiary/segment, contrary evidence, time horizon and an observable deterioration trigger.

A publisher's moat rating remains attributed. Our own default output is a supported/mixed/insufficient-evidence assessment by mechanism, not a universal numeric moat score. Sustained ROIC and margins can support a thesis but do not establish its cause or durability across an unobserved cycle. Do not label returns "excess" without an explicit capital-cost comparison and its uncertainty.

For a hypothetical semiconductor case, manufacturing know-how or customer qualification could support an advantage; capacity additions, pricing pressure and customer concentration could weaken it. These are research questions, not assertions about MU. Confirm from evidence before publication.

## 7. External research to independent analysis

Pipeline: discover/upload → entitlement check → immutable document version → cached extraction → typed claims and locators → factual verification → source disagreement analysis → deterministic findings → optional validated wording → frozen report.

Extract thesis, forecast, horizon, assumptions, catalysts, downside cases and invalidation conditions. Preserve an analyst's estimate separately from an issuer's reported result. Check facts against primary documents; record disagreements without silently choosing the prestigious publisher. Distinguish estimates targeting different periods before declaring disagreement.

Group syndicated articles by their underlying research note. Three outlets quoting one bank are one analyst view. Do not infer consensus from a small convenience sample, average incompatible price targets, or weight a firm solely by reputation. Any future accuracy weighting needs comparable prospective forecast histories and sufficient resolved outcomes.

External sources may attribute a cause; render that as their attributed argument. Our mechanism analysis remains conditional unless separately supported. Extracted documents are untrusted data: embedded instructions cannot change prompts, access tools, disclose credentials or override claim gates.

Reuse N1 typed quantities and server-rendered numbers. Permit new synthesis statements only through explicit structured permissions. Never use optional narration as a way around the existing no-ungated-prose rules.

## 8. Market Outlook integration

Use the same document and claim pipeline for market strategy research, with geographic scope and horizon. Drivers include earnings/revisions, policy/rates/credit, inflation/growth, energy/supply, geopolitical policy and themes such as AI. A theme is not assumed to dictate the market.

Join external arguments to the existing observed rates, breadth and price evidence. Yields are market observations, not policy decisions. Two latest non-null observations may have different dates across series; show each pair and do not imply simultaneous movements. Participation direction requires comparable symbol populations and aligned sessions.

Earnings revision eligibility requires comparable snapshots for the same target fiscal period and known cohort. Macro surprise eligibility requires an expectation captured before publication and a comparable first-release actual; later revisions are separate. Start these append-only capture paths early because the missing past cannot be reconstructed from overwritten current values.

## 9. Service and API integration

- **Market-data:** owns statement/fundamentals acquisition and versioned numeric observations. Reuse current backfill; add only measured provenance or field gaps.
- **Event-intelligence:** owns issuer releases, event associations and macro release/expectation timing. Preserve exact identity rules and preview any historical association repair.
- **Research-engine:** owns external-note extraction orchestration, claim normalization, fundamental/moat interpretation and report assembly. Reuse existing model transport and metering; no additional independent LLM client.
- **Shared contracts/storage:** migrations and typed evidence/finding contracts. Keep numerical rules testable without ORM imports.
- **API gateway/frontend:** entitlement-aware endpoints and report/source rendering. Do not make source retrieval or model calls on every page render.

Proposed API surface (not existing endpoints): authorized upload/import and ingestion status; source-version metadata; permission-filtered evidence passages; report evidence drilldown. Reads must enforce viewer entitlements, including for cached reports. Provider connectors have per-source credentials, quotas, retries, backoff and health states. Never substitute empty data for authentication or rate-limit failures.

Keep official issuer documents distinct from external research, via a separate external-document record or an explicit typed common envelope. External analyst notes must not become eligible issuer-result documents merely because their fiscal period matches. Preserve existing report IDs, history and correction links; old snapshots do not acquire new fields retroactively.

## 10. Cost, security and operational controls

Cache extraction by permitted document version/content hash plus extractor/policy version. A report reusing the same extraction spends no new extraction tokens. Reprocessing changed content or policy is identified separately from accidental repeats.

Use the established PostgreSQL admission ledger for every enabled model operation, with an explicit extraction scope/parent allowance, idempotent settlement and conservative reservation semantics. Show actual tokens separately from reservations. Defer over-budget work durably; do not mislabel it as neutral or evidence-free. Retrieve only relevant passages for synthesis and reject irrelevant documents before model processing.

Apply file-size/page-count and decompression limits, malware/type checks and bounded parsing/OCR. Keep secrets out of documents, prompts and logs. Enforce access checks for originals, extracted passages, indexes and derived displays. Rights expiration/deletion must remove prohibited cached content; preserve only permitted audit metadata and show unavailable citations honestly.

Paid feeds and optional model narration default off until their source permissions, budgets and acceptance tests are established. No email, signal or trade side effects from document ingestion or report generation.

## 11. Phased implementation and integration sequence

| Phase | Work and integration | Exit evidence |
|---|---|---|
| P0: inventory and baseline | Audit MU and a contrasting operating company (proposed MSFT), existing statements/releases, legacy research fields, coverage and provenance; record current report behaviour | Reproducible coverage matrix and explicit reuse/missing-data decisions; no duplicate pipeline |
| P1: prospective capture, parallel after P0 | Append-only estimate snapshots and macro expectations/actual vintages using existing ingestion owners | Idempotent capture; before-publication gate; corrected release identity; no invented historical expectations |
| P2: business performance | Validate/extend current adapter and deterministic findings; add missing version/provenance support before eligible historical comparisons | Comparable growth/margin/cash/debt findings, evidence drilldown, honest missing states on both pilot companies |
| P3: research-document pilot | Public/authorized documents only, source permissions, immutable versions, extraction cache and claim schema | One primary source plus two genuinely independent external views where available; otherwise state insufficient breadth; rights and budget gates pass |
| P4: durability and valuation | Add mechanism cards and counterevidence, then comparable valuation views | No automatic moat-from-ROIC inference; quantities reconcile; different business models do not receive identical unsupported explanations |
| P5: report integration | Stock external views/disagreements/our assessment; Market sources joined to measured drivers; concise first screen | Reader can identify thesis, strongest contrary evidence and next trigger; complete authenticated desktop/mobile journey |
| P6: vendor decision and expansion | Compare pilot gaps with quotes for Bloomberg/MS/S&P or other sources; shadow wider universe and optional narration | Evidence of incremental value and permitted use; bounded costs; rollback tested; purchase is a separate decision |

Do not wait for a paid-feed decision to begin P1. Existing budgeting/batching acceptance work remains a prerequisite for activating new paid model workloads, not a reason to postpone model-free prospective capture. Integrate small, reviewable PRs per phase; no all-at-once rollout.

## 12. Acceptance and validation

- Pure calculations: negative/zero baselines, units/scales/currency, fiscal mismatch, split adjustments, cash-flow duration/sign, missing shares and restatements.
- Real PostgreSQL integration: immutable versions, transactional writes, duplicate imports, concurrent claims, budget rejection/recovery and snapshot cutoff selection. Fakes alone do not establish these properties.
- Evidence: no future publication in historical packets; revisions preserve previous versions; external forecasts never become reported actuals; issuer/external types remain distinct; every displayed finding resolves to evidence.
- Research: conflicting sources, syndicated copies, incomplete excerpts, attribution/negation, stale forecasts and prompt-injection fixtures. Unsupported claims refuse or fall back without dropping material counterevidence.
- Permissions: unauthorized upload/view/model processing, expired entitlements, cache access and deletion paths.
- Browser: genuine authenticated loading, primary metrics and source drilldowns, correction links, keyboard/mobile interaction, first-screen readability and no overflow. Headless layout-unit tests alone are insufficient.
- Cost: one extraction for repeated unchanged imports; versioned re-extraction is visible; report views make no model calls; separate observed token cost from theoretical savings.
- Lifecycle/deploy: real application lifespan, scheduler registration, bounded shutdown and durable retries. Serialize deployments; for shared-code changes rebuild/check all affected backends plus frontend separately. Verify generated payload and rendered report, not just HTTP 200 or strings in chunks.

Use MU plus the contrasting company for pilot fixtures, then one missing-data issuer and one non-applicable instrument. Tests must demonstrate actual failure under meaningful implementation sabotage, without treating sabotage counts as product validation.

## 13. Measurement, rollout and rollback

Measure factual/citation correctness, comparable-data coverage, source diversity (independent origins), reader comprehension, processing latency and token/provider cost per new useful document. Proposed release gates: all published quantities reproducible and cited; zero unresolved critical cutoff/permission defects in acceptance fixtures; known unsupported claims refused; no unapproved model calls. Establish numeric latency/cost targets from pilot measurements before wider rollout.

Forecast value is a separate prospective evaluation with frozen predictions, horizons and baselines; clearer wording and more citations do not prove returns improve. Do not tune selection against a few favourable historical examples.

Deploy additive migrations and feature gates first, shadow generation second, limited visible pilot third. Disable extraction or display independently on regression, retaining permitted evidence and immutable snapshots. Never delete historical reports to hide corrections. Do not activate paid feeds, automatic notifications or trading integration as a side effect of this documentation.

## 14. First implementation handoff

Start with P0: produce the data/provenance matrix for MU and the second company and review the current `business_performance` implementation. In parallel, prepare P1's append-only contracts. The next concrete PR should contain tested reuse/eligibility improvements and the resulting report examples, not a new model prompt that invents missing fundamentals or moat evidence.
