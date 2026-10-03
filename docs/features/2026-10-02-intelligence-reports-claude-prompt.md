# Claude Code implementation prompt — intelligence reports

Date: October 2, 2026. This is a prompt for a future implementation session, not authorization to deploy, send reports or trade from the document-writing session.

Run Claude Code from `/home/lausing/Documents/MyProjects/Stock_Trading_App`. Paste the prompt below. It prioritizes the requested reports over a second intelligence platform.

---

You are extending this EXISTING AI Stock Trading Platform. Implement a report-first Market, Stock and Earnings Intelligence feature using existing services and data contracts.

## Product outcome

I must be able to generate, inspect, save and compare:

1. **Market Outlook:** observed market trend and conditional direction across distinct horizons, with breadth, leadership, volatility, macro, liquidity, catalysts and contrary evidence.
2. **Stock Outlook:** market/industry context, company condition, earnings/guidance, valuation, technical structure, relative strength, news and options context; separate outlooks by horizon.
3. **Pre-Earnings Outlook:** frozen expectations, prior guidance, positioning, key questions, bull/base/bear conditions and event risks.
4. **Post-Earnings Analysis:** actuals versus the frozen baseline, guidance, management commentary, observed reaction, revised thesis and later follow-through evaluation.

Follow `docs/features/2026-10-02-intelligence-report-templates.md` as the report contract. Missing optional data must yield a useful partial report, not fabricated content or an endless implementation blocker. Do not require a new prediction model to deliver factual reports and conditional scenarios.

## First inspect, then reuse

Read applicable repository instructions without modifying `.claude/CLAUDE.md`. Inspect current code and status; do not overwrite, stage or commit another agent's work.

Inspect these existing areas and produce a concise capability-to-file/API map:

- `services/research-engine/`: durable research generation, report storage and API.
- `services/news-intelligence/`: ingestion, source links, event classifications and Jev capabilities/flags.
- `services/event-intelligence/`: earnings, economic calendar, filings, valuation and catalysts.
- `services/market-data/`: provider adapters, UW/yfinance usage, scheduler, option plans, alert delivery/outbox.
- `services/technical-analysis/`, `signal-engine/`, `ml-prediction/`, `decision-engine/`: existing features, forecasts, runtime availability and authoritative decisions.
- `shared/metrics/`, shared DB models, provenance, event IDs and experiments.
- Existing gateway routes and frontend intelligence, research and stock pages.
- `docker/docker-compose.yml` and actual dependencies/scheduler configuration.

Use current source to settle stale documentation conflicts. Read the measurement/Jev designs and the latest options/signal audit, including `docs/audits/2026-10-02-system-followup-audit-and-feature-roadmap.md`. Do not consume an unvalidated options plan as execution-ready.

Do not create a replacement news service, gateway, frontend app, broker path or prediction engine. Prefer report orchestration within the existing research capability, with source-domain services owning their data; document a different placement if current code demonstrates a better fit. Reuse current database, queue/cache and scheduler. Do not introduce Airflow, Snowflake, dbt or Kafka by default. Verify provider entitlements, fields, latency and quotas instead of assuming named sources are accessible.

Create `docs/features/YYYY-MM-DD-intelligence-reports-implementation.md` with reuse map, gaps, ownership, schema/API changes and staged acceptance. Then implement the bounded first slice below.

## Bounded first implementation slice

Deliver all four report **types** with available existing data, rather than completing every proposed analytics engine first:

1. A versioned, validated structured report schema shared by deterministic generation, optional LLM narration, API, UI and export.
2. Adapters over existing market/stock/earnings/news/technical inputs. Unsupported dimensions explicitly report their status and reason.
3. Immutable report snapshots and evidence references. Reuse/extend existing report persistence; do not create duplicate issuer, earnings-event, signal or price stores. Include content/input fingerprints and report-policy version for idempotent generation.
4. Frozen pre-earnings baseline and linked post-earnings versions. A missing pre-report or missing historical consensus must be represented honestly, never reconstructed with post-release information.
5. Existing-UI integration for on-demand generation, history, before/after comparison, source inspection and printable/Markdown output. Use deterministic tables when an LLM is unavailable.
6. Evaluation fields and joins from the first saved report. Do not build a new backtesting engine just to store report outcomes.

Reuse authentication and user-scoped authorization. Any report containing portfolio information is private to its authorized owner. Public market context may be cached separately; never share portfolio-bearing cache entries across users. Rate-limit expensive generation and deduplicate concurrent requests.

## Non-negotiable report behavior

- Every material fact has evidence identity and time. Keep publication, observation/period, first availability, retrieval, generation and cutoff distinct.
- Use exchange sessions/timezones and source-confirmed fiscal periods. Never derive fiscal quarter from release month.
- Preserve actual/estimate accounting basis, currency, units and period. Source conflicts remain visible; extraction confidence does not mean market confidence.
- Prevent look-ahead: pre-release snapshots cannot contain later information. A historical reconstruction is labeled as such and excluded from prospective results.
- Observed trend, conditional outlook, evidence quality and forecast probability are separate fields. An existing model name, meta score or backtest is not sufficient evidence of calibrated probability. Never enable a disabled model/blend to fill a field.
- A citation does not establish causality. Prefer “reported catalyst,” “coincided with,” and clearly labeled hypotheses where causal attribution is uncertain.
- Deterministic code calculates indicators, comparisons, surprises, returns and payoffs. LLMs summarize supplied evidence and must not invent sources or execute actions from retrieved text. Validate their structured output; fall back to deterministic reporting on failure.
- Use per-leg option timestamps, deliverables and quote quality. Archived/last-only prices remain research context, not current executable quotes. Do not label a one-share position covered for a standard call.
- No personalized sizing without current account data and existing risk checks. No naked-option recommendation by default. No LLM-to-order connection.
- Generation and persistence are independent of email success. Respect preferences and existing durable delivery semantics when scheduling is later enabled.

## Scheduling and rollout design

Build the on-demand path first. Specify, but do not activate during this slice, per-market pre-open/post-close briefings, pre-earnings refreshes, result/guidance/call updates and follow-through reviews.

Reuse the admin flag convention if rollout gating is needed. Separate report availability, automatic generation and notification delivery controls. Disabling report generation must not alter signal thresholds, paper trading, broker intent or exits. Existing Jev/provider flags keep their meaning.

Do not deploy, activate report emails, replay historical alerts, change trading policies or enable broker execution as part of implementing reports. Prepare the concrete release checklist and report any configuration that needs a separate activation decision. Do not block ordinary local implementation and testing on that later decision.

## Acceptance tests

Execute real generation/persistence/API behavior with controlled fixtures, not source-string assertions:

- Market/stock partial reports with stale, missing and conflicting inputs.
- Timezone/midnight/holiday boundaries and incomplete-session bars.
- Fiscal year-end unlike calendar year, full-year results, rescheduled earnings and ambiguous event identity.
- Negative/zero EPS estimates, mixed GAAP/adjusted data, margin units, guidance ranges and non-comparable periods.
- Frozen pre-report survives later consensus/source revisions; early release cannot become a retrospective pre-report.
- Duplicate events and concurrent requests do not duplicate an identical report; a genuine revision creates a linked version.
- Preview, results, guidance and call information bind to the right issuer/event and do not erase earlier versions.
- After-hours stock reaction with unavailable option quotes; stale option evidence does not become a fresh P&L claim.
- Citation identifiers resolve, numerical summaries agree with structured facts, LLM/provider outage degrades gracefully.
- Cross-user authorization/cache isolation and generation cost/rate limits.
- Outcomes remain pending until their exchange-session horizon matures; generated forecasts, hypothetical plans, accepted emails and actual fills remain distinct populations.
- Actual frontend rendering of each report type and before/after comparison, using available browser tooling; state explicitly if browser verification cannot be performed.

Use the repository's required checks appropriate to touched code. Use PostgreSQL for guarantees requiring its transaction/concurrency behavior. Record exact tests and limitations rather than summarizing skipped tests as verified.

## Completion and subsequent phases

Finish this vertical slice end to end. Do not claim that a schema-only implementation or a new blank dashboard satisfies the request. Report implemented/partial/unavailable for each template section, files changed, migrations, API/UI behavior, tests, data limitations and release steps.

Subsequent bounded phases: scheduled/report-event delivery with measured latency; historical consensus and richer event reconciliation; prospective report/decision evaluation; optional new-data/UW/Jev interventions under existing experiment contracts. Do not add these merely to enlarge the first delivery, and do not claim reports improve profitability before forward outcomes support that conclusion.
