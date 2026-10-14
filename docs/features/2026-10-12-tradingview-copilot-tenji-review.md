# TradingView AI Copilot and Tenji: review and StockAI implementation plan

Reviewed: October 12, 2026. Status: research and proposed implementation; no product code changed.

## Recommendation

Add two complementary capabilities to StockAI: an evidence-linked assistant that operates existing workflows, and a research feed that connects events to affected companies. Start with read-only assistance and a small, reviewed relationship catalogue. Expand to chart actions and alert creation after the shared evidence and authorization contracts are tested.

StockAI already has chart overlays, stock chat, screening, strategy rules, research reports, event ingestion, and notifications. The opportunity is to connect those capabilities and improve their evidence trail. A wholesale chart replacement or another independent signal engine is unnecessary for the first release.

This review uses official product pages and selected local source files. It is not a hands-on comparison of authenticated accounts, a production audit, or verification of either vendor's accuracy or returns. Existing uncommitted risk-alert work was present during review; source presence below does not establish deployment or production readiness.

## 1. Verified product descriptions

### TradingView AI Copilot

TradingView describes a native Supercharts assistant that reads active chart context and its market data. It can change chart settings, manage indicators and drawings, analyze technicals/fundamentals/news, screen securities, and manage watchlists and alerts. Access requires a paid TradingView plan plus a monthly add-on, with usage limits; web and desktop are supported, while mobile apps are excluded in the reviewed documentation. Exact checkout pricing was not verified. These are vendor-described capabilities, not independently measured reliability. [Getting started](https://www.tradingview.com/support/solutions/43000795131-getting-started-with-ai-copilot/)

Its strategy workflow generates and checks Pine scripts, applies them to charts, changes parameters, and explains backtest results. This is a useful model for shortening the path from an idea to a testable strategy. [Strategy documentation](https://www.tradingview.com/support/solutions/43000795135-create-indicators-and-strategies-with-ai-copilot/)

Chats are separate, searchable conversations with one-year history retention and one active request at a time. [Chat documentation](https://www.tradingview.com/support/solutions/43000795133-chats-and-history-in-ai-copilot/)

The current native product should not be confused with older browser-extension beta descriptions or third-party products using similar names. The official launch describes the paid-plan add-on. [Launch announcement](https://www.tradingview.com/blog/en/ai-copilot-now-on-tradingview-61231/)

### Tenji

Tenji's public page describes event research built around first-, second-, and third-order effects. Each connection includes affected names, directional pressure, a horizon, reasoning, a contrarian case, and a falsifier. Additional features include historical analogues, earnings read-throughs to adjacent companies, and macro basket alerts based on unusual credit-spread or yield-curve moves.

It advertises four daily processing runs, a web feed and email digest, confidence/importance scores, and Actionable, Speculative, and Deep Thesis tiers. Coverage is limited to constituents of nine major indices, including US indices and Hang Seng. The public page currently says access is free with Google sign-in.

Tenji deliberately does not provide entry, stop, target, or sizing instructions. It records publication prices for later scoring and advertises a public outcome record. These claims do not establish profitable performance; the authenticated feed, scoring methodology, and completeness of that record were not inspected. [Tenji public product page and FAQ](https://tenji.io/login)

### Assessment

TradingView's strongest transferable idea is reducing interaction cost: the assistant can act on the same objects the user sees. Tenji's strongest idea is explaining indirect exposure and defining what would disprove an interpretation. For StockAI, the combined workflow should be: inspect an event, understand supported connections, evaluate the affected stock with existing tools, then optionally save a watch or alert.

Neither polished prose nor a plausible economic story establishes a trading edge. Measure workflow utility separately from predictive outcomes.

## 2. Local capability and gap map

The references below are repository inspection evidence, not claims that all paths have passed runtime acceptance.

| Area | Existing StockAI foundation | Addition required |
| --- | --- | --- |
| Stock assistant | [Stock detail](../../frontend/src/pages/stock/[symbol].tsx), `sendAiMessage`, passes price, signal, ranking and five headlines; [AI client](../../frontend/src/lib/ai.ts) returns text | Server-built evidence packet, chart context, typed tool calls, citations and persistence |
| AI backend | [AI proxy](../../services/research-engine/src/api/ai_proxy.py) exposes authenticated `/chat` and text responses | Bounded orchestration, validated arguments, result provenance, cancellation and usage accounting; current inspected contract has no tool execution loop |
| Chart operations | [PriceChart](../../frontend/src/components/PriceChart.tsx) already has indicator controls, levels and manual horizontal/trendline drawings | Controlled command interface and chart-state snapshots; preserve existing manual controls |
| Screening | [Market screener](../../frontend/src/pages/market-screener.tsx), [screener](../../frontend/src/pages/screener.tsx), [ranking routes](../../services/ranking-engine/src/api/routes.py) | Translate language to supported filters, show the interpreted query and actual universe/coverage |
| Strategy assistance | [DSL evaluator](../../services/strategy-engine/src/dsl/evaluator.py), [strategy API](../../services/strategy-engine/src/api/routes.py), [backtester](../../services/strategy-engine/src/backtest/engine.py) | Generate validated DSL and explain results; reject unsupported rules before saving or running |
| Evidence-based research | [Report interpretation](../../services/research-engine/src/intel_reports/interpretation.py) has evidence requirements and counterarguments; [report API](../../services/research-engine/src/api/intelligence_routes.py) supports history/comparison | Reuse evidence conventions for chat and event connections; inspect visibility rules before exposing reports through user-scoped tools |
| Company event connections | [News impact watcher](../../services/market-data/src/services/news_impact_watch.py) contains one bounded product-event rule with reviewed company roles | General event/relationship catalogue, time-valid edges and per-hop evidence; this is partial support, not a general causal engine |
| Earnings and macro | [Issuer documents](../../services/event-intelligence/src/services/issuer_documents.py), [macro reactions](../../services/event-intelligence/src/services/macro_reaction.py), [prospective capture](../../services/research-engine/src/intel_reports/prospective_capture.py) | Link disclosed metrics to documented exposure; add explicit regime calculations where needed; never substitute prior values for missing consensus |
| Delivery and outcomes | [Shared models](../../shared/db/models.py) include news-impact receipts, notification outbox, intelligence observations/outcomes and LLM budgets | Extend compatible records after schema review; avoid a second email or outcome pipeline |

Related work: [intelligence reports](2026-10-03-intelligence-reports-implementation.md), [evidence-grounded narration](2026-10-03-evidence-grounded-report-narration-design.md), and [market/company/risk alert design](2026-10-12-market-company-risk-alert-design.md). Coordinate with the last item before modifying notification schemas or scheduler jobs; its design and the evolving working tree may differ.

## 3. Proposed StockAI copilot

### User experience

Extend stock chat into a reusable side panel on stock detail, stock intelligence, and screener pages. Initial prompts: “Explain this signal using its evidence,” “What changed since the previous report?” and “Which watched stocks meet these filters?” Responses contain a short answer, timestamped evidence links, missing-data notes where relevant, and optional action cards.

Add chart commands in the next increment: show a supported indicator, display sourced levels, or change the visible range. Draft alerts and watchlist edits should show their exact parameters before saving. A chart command must report success only after the UI acknowledges the resulting state; TradingView itself documents cases where actions may need state verification. [Troubleshooting](https://www.tradingview.com/support/solutions/43000795140-troubleshooting-ai-copilot/)

### Implementation contract — proposed, not existing APIs

- Frontend context: `symbol`, `market`, `currency`, `timeframe`, `visible_range`, `enabled_indicators`, `chart_version`. Derive user identity from the authenticated session, never from model arguments.
- Research-engine orchestration: fetch canonical evidence through narrowly scoped adapters, then call the configured model. Read tools could include `get_stock_evidence`, `compare_reports`, `screen_universe`, and `get_existing_alerts`.
- Response: `answer`, `evidence_refs`, `data_as_of`, `limitations`, `proposed_actions`, `request_id`. Each quantitative claim resolves to a source field with units, timestamp and transformation; calculations run in code.
- Action draft: `kind`, validated `arguments`, `expected_state_version`, `expires_at`, `idempotency_key`. Execute through existing application APIs with ownership checks. Separate UI-only commands from durable writes.
- Add bounded turn/tool/token limits, request cancellation, per-user quotas and audit records. Reuse [LLM budgeting](../../shared/common/llm_budget.py) and [usage logging](../../shared/common/llm_usage.py) after checking their compatibility with interactive calls.
- Persist conversations and action receipts under the authenticated owner. Define export/deletion/retention explicitly; do not automatically copy a competitor's retention period.

Treat retrieved articles and filings as data, not tool instructions. Restrict tool names and arguments server-side, keep secrets out of model context, and enforce access checks on every read and write. A generated answer cannot change signal scores, enable a trading policy, or place an order through this initial tool set.

For strategy creation, use the existing JSON DSL. Its evaluator supports comparisons, crossings, and boolean trees over a finite feature set. Add strict recursive schema validation, feature allowlists and complexity limits: the inspected create endpoint's `entry` presence check is insufficient for generated strategies. Preview the normalized rules before persistence; the current ad-hoc backtest contract can auto-save a strategy, so a “preview” must not accidentally invoke that write path.

## 4. Proposed event-connections research

### Start with auditable relationships

Begin with 20–30 reviewed US/HK relationships in one or two sectors. Each relationship needs issuer identities, relationship type, primary-source evidence, effective dates and a last-verification date. Keep economic dependency separate from price correlation. Use PostgreSQL tables/JSON initially; the small catalogue does not justify a new graph database.

Suggested new concepts, subject to existing-model reuse:

| Record | Minimum contract |
| --- | --- |
| Relationship | Source/destination issuer, supplier/customer/competitor/substitute relation, evidence IDs, valid interval, verification status; exposure amount only when sourced |
| Connection thesis | Canonical event ID, publication/cutoff time, version, linked symbols, per-hop fact/inference labels, expected mechanism, horizon, counterevidence, observable invalidation, review status |
| Outcome | Frozen thesis version, evaluation horizon, price basis, benchmark, eligibility/coverage, result and resolution timestamp |

Suggested processing sequence:

1. Reuse ingested news and issuer documents; normalize event identity and remove duplicate coverage.
2. Retrieve only relationships valid when the event became known. Extract a specific disclosed fact, including period, units and source.
3. Draft implications using those edges. Mark each causal hop as an inference unless directly established. A missing edge blocks publication as a supported connection.
4. Require a mechanism-specific counterargument and measurable falsifier. An independent critique pass may help, but is not independent factual verification.
5. Apply deterministic gates for evidence resolution, freshness, issuer identity, liquidity/coverage and duplicate events. Start with human review of every published thesis.
6. Publish an in-app research card. Reuse notification preferences/outbox for opted-in summaries; preserve separate states for queued, provider-accepted and delivered where available.

An illustrative template, not a current market claim: a buyer discloses a capex change → an identified supplier has documented exposure → demand may change over the next two reporting periods. The card should also test offsets such as competing suppliers, product mix and inventory. Without sourced exposure, report an unresolved research question rather than inventing revenue sensitivity.

Keep these cards distinct from BUY/SELL signals. The user may open the existing stock analysis, save a watch, or draft an alert. Signal confidence, evidence quality and thesis importance are different quantities; none should be labeled a profit probability without prospective calibration.

### Later extensions

Add historical analogues only after event timestamps and relationship history are reliable. Freeze retrieval criteria before looking at forward returns; show sample size, dispersion, benchmark-relative results and rejected/missing cases. Do not cherry-pick attractive precedents or use today's index membership as historical membership.

For macro regime research, define the instrument, tenor, frequency, trailing window, minimum coverage and revision policy before testing thresholds. A standardized deviation can identify an unusual observation; it does not establish that a basket is mispriced. Reuse existing macro reactions and current risk-alert work, while treating regime detection as a separately validated addition.

## 5. Build versus integrate

Build the application workflow on StockAI's own data and services. TradingView's chart libraries do not supply market data, and Lightweight Charts requires application-provided indicators. Upgrading to Advanced Charts would not provide Pine, server-side alerts or the strategy tester. Do not assume that licensing a chart grants access to Copilot. [Library comparison](https://www.tradingview.com/charting-library-docs/latest/product-comparison/), [library FAQ](https://www.tradingview.com/charting-library-docs/latest/resources/Frequently-Asked-Questions/)

No embeddable Copilot contract or Tenji API/data-export agreement was verified in this review. This is an unresolved integration question, not proof that no API exists. Vendor integration would require a documented API, authentication model, usage terms, redistribution permission, and cost/coverage assessment before becoming a dependency. A personal subscription alone is not a verified platform integration path.

Retain existing providers for the first prototype. Inventory whether their actual coverage includes necessary filings, currencies, exchange sessions and historical observations. Manually source the initial relationships; evaluate licensed relationship data only if maintaining the catalogue becomes the bottleneck. Do not scrape authenticated competitor output as a substitute for an integration agreement.

## 6. Delivery priorities and acceptance gates

Estimates are planning ranges for one engineer familiar with this repository, including focused tests and UI integration; they exclude data procurement, unrelated remediation and elapsed observation periods. They are not delivery commitments.

| Order | Deliverable | Effort | Release gate |
| --- | --- | --- | --- |
| 0 | Evidence/context contract, permission inventory, evaluation fixtures | 3–5 engineer-days | Trace a representative answer to canonical fields; verify user isolation |
| 1 | Read-only copilot with citations and report comparison | 5–8 days | No unsupported numeric claims in the reviewed release set; explicit stale/missing-data behavior |
| 2 | Chart commands, screening, watchlist/alert drafts | 5–8 days | Accurate state acknowledgment, undo for chart changes, idempotent saves, no cross-user effects |
| 3 | Reviewed event-connections pilot and opted-in digest | 8–12 days | Every edge sourced; every card has falsifier and cutoff; duplicate/retry tests pass |
| 4 | Natural-language DSL builder and backtest explanations | 4–7 days | Reject unsupported rules; reconcile explained metrics with engine output; separate tuning and holdout periods |
| 5 | Historical analogue and macro-regime expansion | 10–15 days | Point-in-time evaluation, adequate declared samples and verified coverage |

Orders 0–3 form the recommended first scope: approximately 21–33 engineer-days. If company-news coverage is the immediate product priority, move the reviewed connections pilot ahead of action tooling after the evidence contract is ready. Avoid adding concurrent email paths while the current risk-alert work is being reconciled.

Required evaluation cases: incorrect symbol/currency, unsupported timeframe, stale prices, missing consensus, malicious instructions inside news, cross-user report/alert access, duplicate requests, a changed chart during generation, model timeout, revised disclosures, invalidated relationships, and events outside exchange hours. For strategy tests include costs, slippage and out-of-sample evaluation; narrating a good in-sample result must not imply predictive validity.

Track grounded-claim rate, action correctness, task completion time, p50/p95 latency, tool failures and cost per completed task. For connections, separately track reviewer rejection rate, unsupported-edge rate, duplicate rate, coverage and falsifier resolution. Evaluate directional outcomes at predeclared horizons with benchmarks; paper outcomes and a short pilot do not establish live profitability.

Run the connections pipeline in shadow mode before opt-in publication. Start with two weeks to inspect operational quality, then continue prospective outcome collection for the full declared horizons. A two-week operations review cannot validate a multi-month thesis.

Budget from measured usage: chat requests × tokens per request, plus candidate events × bounded analysis/critique calls, plus data costs. Cache shared evidence by source version/cutoff, keep user context isolated, filter duplicates before model calls and impose a daily budget. No dollar estimate is defensible until model choice, traffic and vendor entitlements are known.

## 7. Review limitations and completion record

Official sources were accessed on the review date and linked next to the claims they support. Tenji findings are public vendor descriptions; TradingView findings are official documentation rather than authenticated acceptance tests. Pricing beyond the stated access model, vendor APIs, data licenses, model quality and claimed performance remain unverified.

Local review inspected the relevant chat, chart, strategy, report, event and shared-model paths. No production systems were queried and no application behavior was changed. Documentation validation checks local reference targets and whitespace; application tests are unnecessary for this document-only addition.
