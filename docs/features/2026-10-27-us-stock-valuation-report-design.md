# US Stock Valuation & Opportunities — design and implementation plan

Date: 2026-10-27. Status: design specification; a bounded first version was implemented October 28 and [released to production October 29](../audits/2026-10-29-us-valuation-report-release.md). See [implementation scope and validation](2026-10-28-us-stock-valuation-report-implementation.md) for delivered behavior and remaining phases.

Updated 2026-10-28: use yfinance plus existing Unusual Whales (UW) access for v1. No Morningstar subscription or new paid provider is required for the initial report.

## Provider decision: yfinance + existing UW

Use yfinance as the primary fundamentals/consensus source, the app's own transparent valuation models for estimated intrinsic value, and UW for discovery and risk context where the existing API entitlement permits. Morningstar remains a layout/research reference; omit its proprietary rating/moat/fair-value columns from v1.

| Report input | Primary route | Existing app support / remaining work |
|---|---|---|
| P/E, PEG, EPS, margins, cash/debt, FCF | yfinance `Ticker.info` and financial statements | Fundamentals endpoint already maps many fields; verify coverage and persist dated model inputs. |
| Analyst buy/hold/sell distribution and consensus targets | yfinance recommendations summary and analyst price targets | Existing fundamentals response includes recommendations and target ranges. Show provider, analyst count and available dates; aggregate retrieval time is not an opinion publication date. |
| Individual upgrades/downgrades and target changes | yfinance `upgrades_downgrades`; optionally UW analyst screener | Existing Yahoo ingestion and `AnalystPriceTarget` model provide a starting point. Audit persisted dates and fields. UW analyst ingestion requires a new adapter and entitlement check. |
| Forward earnings/growth | yfinance earnings estimates, growth estimates and EPS trends | Already referenced in the fundamentals endpoint; matching periods/basis and PEG growth horizon still need validation. |
| RSI and price history | Stored completed-session daily bars, Yahoo fallback | Calculate locally; source corporate-action handling and warmup are required. |
| Fair value / discount | App scenario valuation and peer comparisons | Label **Model fair value**. Analyst targets remain a separate opinion, never a replacement for intrinsic value. |
| US-market candidate discovery | Existing UW stock screener plus a persisted eligible listing universe | `get_stock_screener()` exists, but its current limited live result is not a complete security master. Add pagination/coverage checks where supported; freeze report inputs. A bounded Yahoo screener is an alternative discovery path, not proof of exhaustive coverage. |
| Short interest, volatility, options flow, dark-pool activity | Existing UW adapters | Context for risk/timing only. Call buying, dark-pool volume or short interest alone cannot establish undervaluation or analyst opinion. |

Recommended delivery: start with Yahoo metrics, consensus and sourced individual actions plus app model valuations over a clearly labeled US coverage subset. Add UW discovery and risk enrichment using existing access; expand coverage as ingestion and quota measurements permit. Missing UW enrichment must not prevent the fundamentals report from rendering. Missing valuation or quality evidence can still block opportunity eligibility.

UW's official documentation lists analyst screeners and financial data, but endpoint access varies. In particular, company profile, forward-estimate and active/delisted-security endpoints are documented as Advanced+; do not assume the current key includes them. Confirm access with small read-only probes during implementation; an unauthorized response disables that enrichment and does not trigger an upgrade purchase. Existing adapters do not demonstrate that UW analyst/financial endpoints are already integrated.

yfinance offers these analysis interfaces without a Morningstar subscription, but has missing fields, throttling and no guaranteed service availability. Use bounded background ingestion, cache successful responses, retry with backoff, retain last-good snapshots and show stale states. Its software availability does not establish unrestricted rights to redistribute Yahoo data; review applicable usage terms if the app will be offered to others.

Sources checked October 28: [yfinance analysis interfaces](https://ranaroussi.github.io/yfinance/reference/yfinance.analysis.html), [UW developer overview](https://unusualwhales.com/developers), and [UW endpoint catalog and tier notes](https://api.unusualwhales.com/docs/llms.txt). These establish documented capabilities, not live field coverage or this account's entitlements. The provider choices in this section govern the implementation phases below.

## Outcome and scope

Add **Valuation & Opportunities** under Reports, accessible at `/reports?tab=valuation` and from the Reports navigation group. Help users discover US-listed companies beyond their watchlist, inspect expensive or analyst-flagged companies, and find undervalued businesses with credible long-term prospects. Default investment research horizon: 3–5 years; analyst price-target horizons are displayed separately and never assumed.

Use a concise weekly editorial summary, sortable evidence table, and individual investment cases inspired by Morningstar’s list format. Current means **latest verified evidence as of the displayed cutoff**, not an assertion of real-time coverage. No fixed promise of 25 opportunities or guaranteed future returns. Generate fewer or zero qualifying candidates when appropriate.

This is a research feature. Adding a candidate to a watchlist is an explicit user action; generating the report does not initiate trades or modify portfolios.

## Repository findings and integration decisions

| Existing component | Reuse / necessary change |
|---|---|
| `frontend/src/pages/reports.tsx` | Existing market selector and seven tabs; add a thin valuation tab hosting a separate component. |
| `frontend/src/pages/_app.tsx` | Add one Reports navigation link. |
| `frontend/src/pages/intelligence-reports.tsx` | Reuse report history/export conventions; avoid another report storage system. |
| `shared/intelligence/report_contract.py`, research `intel_reports/store.py` | Extend the versioned report contract with a market-wide valuation report; retain immutable inputs, provenance, explicit missing states, and idempotence. |
| Research `intel_reports/quality_value.py`, `api/quality_value_routes.py` | Reuse quality, durability, valuation and value-trap gates. Unknown evidence must never become a pass. Cross-link the existing Quality & Value page. |
| Market-data `api/routes.py` | Fundamentals output exposes P/E, PEG, FCF, EPS and analyst targets. Field existence is not proof of populated, licensed, fresh, historically stored data. Audit coverage. |
| Research `scoring.py::_dcf_fair_value` | Existing simplified DCF is exploratory. It uses growth fallbacks and sector discount rates; do not promote its `high_conviction` flag to a recommendation. |
| Research `intel_reports/prospective_capture.py` | Existing forward-data capture documents missing target horizons. Extend source metadata before reporting rating changes or target horizons. |

The Reports tab presents the latest frozen report plus history; the research service owns generation. This preserves the existing distinction between live market aggregation and versioned documents without requiring a new service. Existing Quality & Value remains the canonical eligibility evaluator, while this report supplies valuation comparisons and editorial explanations.

The October 6 fundamentals audit is historical evidence of gaps, not a current production inventory. Recheck share-count persistence, statement publication dates, accounting basis and statement freshness before implementation.

## User experience

```text
Reports / Valuation & Opportunities                   US market
US-listed coverage | Price session | Generated time | History | Export
Covered  … / eligible universe … | Rated … | Missing evidence …

[Undervalued opportunities] [Overvalued] [Analyst risk flags] [All]
Universe: US listed | Sector | Market cap | Uncertainty | Watchlist only: off
Changes: All / Newly entered / Rating changed | Search symbol or company

This week's changes
  New entrants, exits, fair-value revisions, and principal risk themes

Company | App assessment | Price/FV | Discount | Fwd P/E | PEG | RSI | Evidence
  expand → thesis, valuation range, analyst view, catalysts, risks, sources
```

Use existing dark cards and typography. Show text labels with colors; do not rely on red/green alone. Keep company and assessment visible on mobile, with secondary metrics in an accessible expandable row. Tables support keyboard sorting, explicit sort direction, pagination and accessible disclosure controls.

**Header:** price session and timezone, generation time, valuation source/date, universe definition, coverage counts, policy version, refresh/job status. A refreshed retrieval timestamp does not make an old analyst opinion current.

**Summary:** three to five sourced findings, including newly expensive names and newly eligible opportunities. Explain whether a transition came from price, changed fair value, new evidence, or a methodology revision. A first observation is “new coverage,” never “newly undervalued.” Compare with the previous weekly completed-session snapshot using the same policy and universe membership; show policy/universe changes separately.

**Table:** default columns above; optional trailing P/E, peer median, FCF yield, estimated fair-value range, analyst consensus/count, target range, uncertainty, rating age, and watchlist badge. Show analyst ratings in a dedicated source-labeled column, distinct from app classifications. Detail view includes analyst author/firm, original rating, normalized rating if mapped, publication date, target horizon, and original source link. Never call an app label a Morningstar rating or reproduce their star system as ours.

**Company detail:** “Why it may be mispriced,” 3–5-year business thesis, two or three dated catalysts, bear/base/bull valuation assumptions, supporting financial history, downside risks, and explicit thesis invalidation conditions. Include a price-versus-fair-value history only where dated estimates actually exist. Link to stock research and Quality & Value; offer an explicit Add to watchlist action.

**States:** loading skeleton; empty qualifying list with exclusions; provider error with last successful report; stale report banner; unavailable metric with reason; conflicting valuations shown individually. HK selection displays “US valuation coverage only” rather than silently showing US data under HK. A table failure must not appear as “no opportunities.” Export frozen CSV and existing report export formats with dates and source attribution.

## Universe and ingestion

Default universe: active NYSE/Nasdaq/NYSE American common stocks and separately labeled ADRs; exclude ETFs, funds, warrants, preferreds, OTC and inactive/delisted listings. Proposed default investability filters: market cap ≥ $1 billion, price ≥ $5, and trailing 60 completed-session average dollar volume ≥ $5 million, requiring at least 60 valid sessions. These are configurable product choices, not valuation principles. ADR currency, share ratio and domicile must be explicit.

Use an exchange/security-master feed to discover listings independently of watchlists. Report total discovered, excluded by reason, price-covered, fundamentals-covered, valuation-covered and qualifying counts. A pilot over existing active symbols must say “covered US subset,” never “entire US market.” Persist dated membership for future evaluation, including delistings.

Market-data owns provider ingestion, batching, rate limits and corporate actions. Research reads stored snapshots; opening a report must not fan out to external providers. Refresh prices after a verified completed US session; refresh ratings/targets daily subject to provider capacity; refresh fundamentals after new filings and through a daily stale-data sweep. Schedule by exchange calendar, including holidays and early closes.

Before enabling broad coverage, select a provider with demonstrated endpoint availability, source dates, exchange coverage, analyst count, growth horizons, corporate-action handling and display/storage rights. Morningstar data is optional and requires appropriate access; public articles are design/research references, not a production scraping feed. Without such access, show the app's own valuation and available third-party opinions with attribution; mark Morningstar fields unavailable.

## Metric definitions and interpretation

All derived values record input IDs, units, currency, accounting basis, fiscal period and formula version.

| Metric | Definition and guardrail |
|---|---|
| Trailing P/E | Same-session price / diluted EPS TTM. EPS ≤ 0 → not meaningful; no negative-P/E bargain labels. |
| Forward P/E | Price / consensus EPS for a specified NTM or fiscal-year period. Keep periods and GAAP/adjusted basis separate. |
| PEG | P/E divided by positive annual EPS growth in percentage points; 20× / 10% = 2.0. Require a disclosed matching provider methodology/horizon; do not substitute a single-quarter growth rate. Missing/negative growth → unavailable/not meaningful. |
| RSI(14) | Wilder smoothing over completed daily closes adjusted consistently for splits. Use a documented warmup of at least 100 bars; flat prices → 50, gain-only → 100, loss-only → 0. Above 70/below 30 are momentum context, not proof of over/undervaluation. |
| Price / fair value | Price divided by positive fair-value estimate, on the same currency/share basis. Preserve model and external estimates separately. |
| Discount / margin of safety | `(fair_value - price) / fair_value × 100`; negative means premium. |
| Upside / downside to fair value | `(fair_value - price) / price × 100`. Price 80 and fair value 100 means 20% discount and 25% upside. |
| FCF yield | Defined equity cash flow / equity market cap, with cash-flow basis and period disclosed. Do not mix enterprise cash flow with equity value. |
| Peer context | Industry, profitability and business-model matched positive multiples; minimum 10 valid peers, sample count and dispersion. Sector fallback explicitly labeled; insufficient peers → unavailable. |

The existing `_dcf_fair_value` calls upside `margin_of_safety_pct`. Introduce correct fields in the new contract and a compatibility/deprecation plan for existing consumers; do not silently change the meaning of their field.

P/E above 25, PEG above 2, or RSI above 70 can be configurable screening flags, but none independently establishes overvaluation. Cyclical peak profits can make an expensive business look cheap on trailing P/E. Banks need capital/ROE and book-value analysis; REITs need FFO/AFFO; unprofitable firms require explicit cash-burn/dilution scenarios. Unsupported models remain unclassified.

## Fair value, classification and candidate selection

Build an auditable scenario valuation for supported nonfinancial businesses. Use either FCFF discounted at WACC, then subtract net debt and other senior claims and divide by diluted shares, or FCFE discounted at cost of equity. Never mix these conventions. Record normalization, growth, reinvestment, terminal growth, discount rate, cash/debt and dilution assumptions. Require discount rate > terminal growth and show terminal-value sensitivity. Growth defaults cannot establish candidate eligibility.

Prefer bear/base/bull estimates and sensitivity ranges over a single precise number. A scenario range is not a statistical confidence interval. Analyst price targets are opinions over their own horizon and cannot substitute for intrinsic value. Disagreement between models is evidence to inspect, not justification to average into “high conviction.”

Proposed v1 policy, versioned and subject to validation:

- **Undervalued:** supported current base fair value with price/fair value ≤ 0.80.
- **Overvalued:** supported current base fair value with price/fair value ≥ 1.20.
- **Near fair value:** between those thresholds. Display raw premium/discount.
- **Insufficient/conflicting evidence:** no defensible comparable value, stale critical inputs, or unresolved material model conflict. Do not force classification.
- **Opportunity candidate:** undervalued plus all required existing quality/durability/value-trap gates explicitly pass, a sourced long-term thesis, and no critical risk blocker. Higher valuation uncertainty requires a larger discount: proposed 30% for high and 40% for very high, with sensitivity disclosed. Preserve existing gates if stricter.

Keep overvaluation and risk as independent dimensions. An undervalued company can still carry an analyst risk flag. “Analyst flagged” requires a dated attributable downgrade, sell/underperform opinion, uncertainty assessment or specific cited concern; the app's own risk signal is separately labeled. Target dispersion needs analyst count and aligned horizons; insufficient data does not imply consensus.

Rank eligible opportunities by uncertainty tier, then discount within tier, then liquidity and symbol for deterministic ties. Show a diversified featured shortlist (up to 10; at most two per sector) while keeping all eligible names accessible. Overvalued results sort by premium; risk results by explicit severity then opinion date. No uncalibrated probability-of-success score. Missing data never improves rank.

Proposed freshness rules: price must match the report session; targets/ratings older than 90 calendar days or lacking publication dates cannot support “current analyst view”; growth consensus older than 30 days is stale. Statements require latest available applicable filings, with a 150-day quarterly/450-day annual fallback ceiling. New earnings, material filings, splits or thesis-changing events invalidate affected valuations pending reassessment. These are configurable operational limits, not claims that all data within them remains reliable.

## Initial research shortlist, not live recommendations

I would prioritize **OTIS, BR and RELX** for further long-term research, with **YUMC** as a geographically riskier growth candidate. This is a research-priority judgment, not a verified buy recommendation at October 27 prices. The accessible Morningstar article was published October 2 and uses **September 28, 2026** data. Its observations below are historical; refresh prices, valuations and filings before eligibility. P/E, PEG and RSI were not verified for these examples and must remain blank until sourced.

| Candidate | Source P/FV | Business case to investigate | Risk / invalidation to investigate |
|---|---:|---|---|
| Otis (OTIS) | 0.63 | Recurring elevator service and modernization | China construction weakness; service retention or margins deteriorate |
| Broadridge (BR) | 0.65 | Recurring investor communications and financial technology | Acquisition execution; recurring growth weakens |
| RELX ADR (RELX) | 0.58 | Subscription analytics and workflow tools | AI substitution and currency exposure; retention weakens |
| Yum China (YUMC) | 0.53 | Restaurant expansion and scale efficiencies | China demand and geopolitical exposure; store economics deteriorate |

Source: [Morningstar, The 10 best US companies to invest in now](https://www.morningstar.com.au/stocks/10-best-us-companies-invest-now-2). Business cases paraphrase that research; risk/invalidation checks are proposed independent research questions. The shortlist is not a hardcoded product result and does not assume these stocks are in the user's watchlist.

For an expensive/high-uncertainty example, Morningstar's search-indexed [weekly overvaluation article](https://www.morningstar.com/stocks/13-newly-overvalued-stocks-this-week) reported PLTR at a 29% premium to its $153 estimate with Very High uncertainty, and MA at a 13% premium to $550 with Medium uncertainty. The index indicated publication two days before this design; direct article access and the underlying price cutoff were not verified. Treat these as source-reported examples awaiting validation, not current app classifications. MA also illustrates why Morningstar's uncertainty-dependent ratings must not be equated to our proposed fixed premium threshold.

## Data contract and API plan

Extend the report type registry with `us_equity_valuation` and validate compatible storage/export behavior before schema changes. Proposed payload:

```text
report: id, schema_version, policy_version, model_version, input_fingerprint,
        generated_at, cutoff_at, price_session, market, universe_snapshot_id,
        previous_report_id, coverage, status, sections, candidate_ids
row: instrument_id, symbol, exchange, domicile, currency, sector,
     metrics{value, unit, state, reason, evidence_ids},
     valuation{method, bear, base, bull, assumptions, assessed_at},
     app_classification, gate_results, eligibility, exclusion_reasons,
     analyst_opinions[], risk_flags[], thesis, catalysts, invalidation_conditions
evidence: provider, source_url, published_at, first_available_at, retrieved_at,
          period_end, revision_at, accounting_basis, horizon, raw_snapshot_id
```

Store snapshots for security-master membership, valuation assumptions and analyst opinions where existing tables cannot preserve them. Distinguish provider updates from retrieval timestamps; retain superseded opinions. Never reconstruct a historical consensus using today's data.

Proposed routes under the existing authenticated `/intel` prefix (confirm naming against route registry): `GET /intel/valuation/latest`, `GET /intel/valuation/{report_id}`, `GET /intel/valuation/{report_id}/rows`, and an authorized `POST /intel/valuation/generate` returning an asynchronous job ID. Reuse history/export patterns where supported. Static routes precede dynamic report IDs. Queries support bounded pagination, sort, sector, classification and risk filters; include all filters in SWR cache keys.

Public market snapshots must contain no user watchlists/holdings. Apply watchlist overlays through authenticated owner-scoped reads and cache keys. Generation is rate-limited and idempotent on frozen inputs plus policy/model versions. Publish atomically only after validation; on partial ingestion, expose gaps and retain the last valid snapshot. Retry provider jobs with backoff; no provider secrets or raw private payloads in exports.

## Implementation sequence and acceptance

1. **Inventory and provider contract.** Measure current US symbol/field coverage and freshness. Confirm licensing, growth periods, analyst provenance and persistent shares. Deliver a field-to-source matrix and labeled pilot universe. Broad-market/current-rating claims remain disabled until these pass.
2. **Snapshot ingestion.** Extend market-data ingestion for listing discovery, statements, corporate actions, forward estimates and analyst opinions. Use existing job infrastructure with batching and quota budgets. Acceptance: a non-watchlist eligible US stock is discovered, dated and refreshed without a page-view provider call.
3. **Deterministic valuation engine.** Add focused research modules for metric normalization, supported sector models, classifications and quality gate adaptation. Acceptance: audited formulas, explicit unsupported sectors, no missing-as-pass behavior, reproducible scenario assumptions, and corrected discount/upside semantics.
4. **Report generation and history.** Extend contract, generator, persistence and routes; add gateway mapping only if required. Freeze weekly comparisons and daily completed-session snapshots. Acceptance: identical inputs return the same snapshot; concurrent runs cannot publish duplicates; missing history does not invent transitions.
5. **Reports interface.** Add `frontend/src/components/reports/ValuationReport.tsx`, API types/helpers, navigation link and tab wiring. Deliver table, filters, details, historical selection, export and all failure states. Reuse shared report rendering where practical rather than copying the Quality & Value page.
6. **Shadow validation and release.** Feature-flag the tab, run on covered US names, reconcile counts and manually review representative sectors and non-watchlist candidates. Then expand the security master/provider coverage. Preserve previous reports and disable the flag for rollback; no destructive migration is required.

Required verification when implemented:

- Unit tests for P/E/PEG units, nonpositive denominators, RSI smoothing/flat series, DCF equity bridge, terminal assumptions, ADR/split normalization, and 20% discount versus 25% upside example.
- Gate and ranking tests for missing/stale/conflicting evidence, critical blockers, uncertainty buffers, sector exceptions, peer sample size, and stable ties.
- Integration tests for cutoff timestamps, report persistence/versioning, completed-session calendars, ingestion outages, source revisions, concurrent jobs, gateway routing, authentication and watchlist isolation.
- UI tests for tab deep links, US/HK behavior, filters/cache keys, empty/error/stale states, accessible controls, and exports matching displayed frozen values; frontend typecheck and production build.
- Reconcile discovered/excluded/covered/eligible counts and verify provider usage stays within measured quotas. Benchmark full-universe generation separately from paginated cached reads; tune budgets after measuring rather than promising untested latency.
- Evaluate future 6/12/36-month total returns, drawdowns and thesis failures against SPY and sector benchmarks using point-in-time membership and inputs, including delisted names. Paper evaluation only; do not claim historical predictive performance from retrospectively reconstructed fundamentals.

Launch gates: auditable source/date for every rating; no private data in public reports; no stale critical evidence in the opportunity shortlist; clearly labeled actual universe coverage; at least one demonstrated end-to-end non-watchlist discovery; no unsupported promises of future returns. Empty qualified output is valid and must explain why.

## References and limitations

- [User's Morningstar example](https://www.morningstar.com/stocks/25-newly-overvalued-stocks-this-week-2): direct access returned HTTP 403 on October 27; its exact article body and current list were not reviewed.
- [Morningstar accessible October shortlist](https://www.morningstar.com.au/stocks/10-best-us-companies-invest-now-2): September 28 data; used only for dated research examples above.
- [Fidelity RSI guide](https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/RSI): supports the conventional 70/30 momentum thresholds, not a fundamental valuation conclusion.
- [Existing intelligence report implementation](2026-10-03-intelligence-reports-implementation.md) and [historical fundamentals audit](../audits/2026-10-06-fundamentals-data-audit.md).

The original design work did not perform a live production audit, subscription entitlement verification or current-price validation. The subsequent local implementation is documented separately above. Provider coverage remains a validation dependency; no provider purchase is assumed.
