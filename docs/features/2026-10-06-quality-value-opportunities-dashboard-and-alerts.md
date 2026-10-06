# Quality & Value Opportunities: dashboard and email alerts

Date: October 6, 2026. Status: proposed product, implementation and integration design. User request: a “buy low, sell high” dashboard and email alerts for companies with good fundamentals, economic moats, underappreciated value and attractive entry conditions, plus other criteria worth considering.

This task documents the feature. It does not enable email, create subscriptions, purchase data or execute trades. Proposed defaults below are pilot hypotheses, not validated investment rules.

Companion: [fundamentals, moat and external-research integration plan](2026-10-05-fundamentals-moat-and-external-research-integration-plan.md), including free sources and evidence requirements. Also [interpretation design](2026-10-03-report-interpretation-and-decision-support-design.md), [measurement framework](2026-09-30-measurement-framework-and-improvement-backlog.md) and [narration design](2026-10-03-evidence-grounded-report-narration-design.md).

## 1. Recommended solution

Build a dedicated **Quality & Value Opportunities** page backed by the same frozen evidence as Stock Outlook. Use a quality-and-valuation research screen, followed by a separate entry-condition assessment and ongoing thesis monitoring. “Buy low, sell high” describes the objective; it cannot promise bottoms, tops or returns.

A low price versus last year's high, low RSI, analyst target, or BUY signal alone is insufficient. The central question is whether a financially resilient business with supported competitive advantages trades below a defensible estimated value, and whether observable conditions make further entry research timely.

Start with a research horizon of 6–24 months and a separate entry-monitoring window of 5–20 completed sessions, both explicit and configurable. Neither label claims validated forecast skill. Use daily close evaluation first; intraday operation is unnecessary for the initial thesis and multiplies data, notification and latency complexity.

Do not average all dimensions into an opaque conviction number. Required evidence gates cannot be compensated for by high technical scores. Provide ranked eligible candidates, watch-only names, and blocked/insufficient-evidence names separately. An empty eligible list is a valid result.

## 2. Criteria and evidence gates

| Dimension | Evidence to assess | Eligibility and interpretation |
|---|---|---|
| Business quality | Comparable revenue/profit history, margins, cash conversion, capex, debt/liquidity, dilution | Require current-enough comparable periods and provenance; do not infer missing share counts from incompatible EPS |
| Competitive durability | Sourced switching costs, cost advantages, networks, IP, distribution; counterevidence and deterioration triggers | “Supported”, “mixed”, “insufficient evidence”; quantitative persistence alone cannot prove a moat |
| Valuation | Bear/base/bull estimates, assumptions, sensitivity and appropriate peer/history context | Price below a model estimate is a hypothesis, not a fact about intrinsic value; analyst targets are attributed inputs |
| Entry condition | Completed-session price, liquidity/spread evidence, volatility, gap/extension and selected price-confirmation rule | Versioned rule with reference time; daily bars must not be described as executable quotes |
| Value-trap risk | Structural demand decline, leverage/refinancing, cash burn, dilution, accounting issues, customer concentration, commodity/cycle exposure | Critical risk blocks eligibility; missing critical evidence means unknown, not pass |
| Catalysts and expectations | Verifiable product/earnings/capital-allocation events and comparable estimates | A catalyst is optional context, not a fabricated deadline for price convergence |
| Portfolio fit | Existing holdings, sector/industry/FX exposure and liquidity needs, if authorized and available | Separate portfolio warning; no position-size suggestion without current holdings and risk inputs |

Initial universe: active ordinary equities covered by the existing platform. Exclude ETFs, delisted securities and unresolved identities; banks/insurers require dedicated valuation/quality templates before eligibility. Treat ADR currency and share-ratio mapping explicitly. Begin with MU plus a contrasting company such as MSFT, then missing-data and cyclical stress cases. Inclusion in the pilot is not a recommendation to buy.

Freshness policy is field-specific and versioned: price must be from the expected latest completed exchange session; a statement is not current merely because fetched today. Detect when a newer release exists but the annual series omits it. Unknown basis, conflicting fiscal identity, stale valuation or unresolved material restatement blocks an actionable entry alert while retaining a labelled research card.

## 3. Valuation and entry policy

Compute valuations in code from explicit, sourced assumptions. Select methods by business model: cash-flow scenarios where credible, normalized earnings for cyclicals, and appropriate peer multiples as a cross-check. Do not use peak-cycle earnings to manufacture a cheap P/E. Do not demand a DCF where inputs cannot support it.

For price P and positive per-share base valuation V, display discount as `(V - P) / V`; display potential upside as `(V - P) / P`. Name the different denominators. Bear/base/bull values are scenario estimates, not confidence intervals or calibrated probabilities. Show sensitivity to margins, reinvestment, discount rate, terminal assumptions and dilution.

Define research entry ceiling as `V * (1 - required_discount)`, using an uncertainty-dependent, versioned discount policy. Do not ship an arbitrary 20%/30% threshold as empirically proven. Select provisional thresholds in shadow mode and freeze them before prospective evaluation. Very high uncertainty or an unbounded downside case can block eligibility regardless of apparent discount.

Default entry design: quality + supported durability + required valuation discount + no critical risk + a separately satisfied price-stabilization rule. Compare this with a valuation-only baseline to measure whether timing adds value. Example candidate rule for testing: two completed closes above the recomputed 20-session average, with a liquidity gate and no excessive gap above the frozen entry ceiling. This is a testable heuristic, not a claimed optimum. Avoid combining many correlated indicators into false corroboration.

Recheck after the next tradable quote where available. If the stock has gapped outside the entry zone, mark the setup expired rather than retaining an old “ready” label. Optional staged-entry research may be added later; never automate averaging down after a thesis break.

## 4. States and “sell high” review

Persist evaluations and state transitions rather than recomputing alert status from scratch on each page view:

| State | Meaning | Notification |
|---|---|---|
| Insufficient evidence / blocked | Required data missing, stale, conflicting or a critical risk exists | Dashboard and optional subscribed thesis-status notice |
| Quality watch | Business/durability gates pass; valuation not attractive | Digest only on material change |
| Value candidate | Discount gate passes; entry confirmation pending | Optional digest inclusion |
| Entry review ready | All required gates pass at the stated cutoff | One new-state alert, subject to subscription and send-time revalidation |
| Entry expired | Price outside zone, evidence stale or event invalidates setup | Update/supersession, not a repeated buy prompt |
| Valuation review | Price approaches/exceeds a current value range | Review alert; not an instruction to sell |
| Thesis at risk / broken | Material deterioration, source correction or disqualifying risk | Subscribed thesis-change notification |

A price drop alone is neither proof of value nor a thesis break. A price rise alone is not a reason to exit a stronger business. Recompute valuation when the underlying evidence changes, and preserve the old estimate so users can see whether price or assumptions caused the transition. Where both change together, identify both rather than inventing a single cause.

For monitored holdings, support review events for valuation convergence, deteriorating fundamentals, excessive concentration and horizon expiry without catalyst realization. Do not emit personalized “sell” instructions unless a separate portfolio-aware advisory/execution design is authorized. Show applicable tax/transaction-cost uncertainty without estimating it from absent account data.

Use hysteresis, a configurable cooldown and an episode identifier to avoid oscillating around a boundary. Policy/version changes or historical backfills must not mass-email existing candidates; seed state in silent shadow mode first.

## 5. Dashboard design

Proposed route: `/quality-value`; not an existing route. Link each candidate to its Stock Outlook report and frozen evidence.

Top: as-of session, eligibility coverage, evidence health and counts by state. Separate “Entry review”, “Watch”, “Valuation/thesis review” and “Insufficient evidence”. Default scope is the user's selected watchlist; broad-universe discovery is a deliberate filter.

Table/card fields: company, state, quality findings, durability evidence status, current price/date, valuation range, model discount, entry zone/condition, strongest counterargument, next catalyst, next observable trigger, freshness and holdings exposure if available. No raw evidence hashes in the lead. Distinguish research confidence/evidence coverage from probability of a profitable trade.

Filters: market/currency, industry, watchlist/holdings, state, evidence completeness, liquidity and valuation uncertainty. Sort eligible candidates by transparent discount and quality criteria after gates, with stable tie-breaking; show why an item ranks ahead of another. Missing values never sort as zero-risk or greatest discount.

Detail view: three key reasons, strongest downside case, bear/base/bull assumptions, valuation sensitivity, business and durability cards, entry/exit review rules, source citations, history and corrections. Provide “watch”, “mute”, alert preferences and “why not eligible”. Mobile uses stacked cards and expandable evidence, not compressed multi-column financial tables.

## 6. Email design and delivery policy

Introduce an explicit opt-in alert type, proposed key `quality_value_opportunity`, with separate preferences for digest, entry transitions and thesis/valuation review. Existing preference code defaults missing rows to subscribed; this new feature must have its own positive subscription requirement rather than inheriting opt-in from an unrelated price alert.

Suggested pilot defaults: daily digest after each selected market's completed-session evaluation; transition alerts opt-in separately; timezone-aware quiet hours; configurable daily cap (proposed three transition emails, with overflow summarized). These defaults need user-facing controls and pilot validation. Watchlist/holding scope must be explicit; no automatic whole-universe mailing.

Email includes:

- Subject: “[Research watch] SYMBOL entered its quality/value review zone” (or thesis/valuation review).
- Snapshot price/time and report ID; evidence and valuation versions.
- Why it qualified: up to three sourced findings, required discount and entry condition.
- Strongest counterargument, event risk and what would invalidate the setup.
- What changed since the previous notification; current status rechecked before sending.
- Link to dashboard, evidence and scoped one-click unsubscribe.

Example uses synthetic SYMBOL and placeholders, not current MU prices. All numbers are rendered from the frozen evaluation, never copied by a model. Entry-ready is a research state, not an order recommendation.

Persist a transactional notification outbox with a unique `(user, symbol, episode, transition, channel)` identity. Worker claims and retries are durable; provider message IDs/idempotency are used where supported. Distinguish queued, sent, failed, suppressed and ambiguous outcomes. Never stamp suppressed as sent or promise exactly-once delivery across an email-provider timeout without provider support. Recheck authorization, preferences, evidence validity and expiry before delivery.

Development/test stacks must not send mail. Reuse the global alerting gate, recipient helpers, quota/quiet-hour controls and unsubscribe registry. User authorization here is to document the feature, not to send emails now.

## 7. Integration and proposed records

| Component | Integration |
|---|---|
| Research-engine `intel_reports` adapters/interpretation/generators | Quality, durability, valuation and entry findings from existing evidence; freeze evaluations |
| Shared intelligence contracts | Typed eligibility reasons and numeric calculations; preserve N1 claim guards |
| Market-data | Prices, statement refresh, corporate actions, session completeness, applicable liquidity evidence |
| Event-intelligence | Release/catalyst timing and critical event changes |
| Portfolio service | Optional authorized holdings/exposure read; never needed to display general research |
| Market-data scheduler and `email_service.py` | Evaluation dispatch or orchestration, outbox delivery, existing environment gate and quotas |
| `shared/common/alert_prefs.py` | New registered type and explicit opt-in semantics, unsubscribe handling |
| API gateway/frontend | Authenticated page, scope filters, evidence/history and preferences |

Proposed logical records: versioned opportunity policy, immutable evaluation (cutoff, input IDs, individual gate results, valuation assumptions, state and expiry), episode/transition history, explicit user subscription, notification outbox/delivery attempts and forward outcome observations. Reuse existing records where semantics match; do not overload PriceAlert as consent or turn every report regeneration into a new episode.

Proposed endpoints: list/filter evaluations, get evaluation/evidence/history, manage own subscriptions and muted symbols. Authorization follows existing user scoping. The scheduler evaluates once per completed exchange session and on material evidence correction; server-side cached evaluations serve dashboard reads. Page visits cause no model/provider calls.

First implementation uses deterministic calculations and templates; no new LLM is required. Optional source extraction follows existing document permissions, caching and PostgreSQL token admission. Never duplicate fundamentals ingestion or assume licensed moat ratings are freely available.

## 8. Implementation and rollout

1. **Inventory and contracts:** audit data readiness for the pilot, existing alert-consent behaviour, statement vintages and valuation inputs. Define policy, states, applicability and blocked reasons.
2. **Offline evaluator:** implement pure calculations and gate composition; unit tests plus real-store persistence/concurrency tests. No email and no live buy labels.
3. **Dashboard shadow:** display pilot research states and evidence; unknown moat/valuation remains unknown. Capture prospective evaluations before tuning thresholds against outcomes.
4. **Notification dry run:** create previews/outbox records with sending disabled; prove transition dedup, quiet hours, opt-in, expiry and replay suppression.
5. **Limited opt-in release:** approved recipients only, bounded universe, daily digest first, then optional transition alerts after delivery validation.
6. **Evaluate and expand:** add business-model templates, more markets and optional portfolio context only after their own acceptance gates. Do not imply a small pilot establishes an investing edge.

Dependencies: the companion fundamentals/moat/valuation work must supply evidence before entry-ready eligibility. The dashboard shell can ship with honest blocked states; inventing a moat or fair value to populate it cannot. Prospective estimate/macro capture can proceed alongside this work.

Additive migrations, disabled-by-default sending and separate evaluation/display/delivery switches enable rollback. Shared changes require rebuilding all affected backends; frontend is checked separately. Serialize deployments and verify actual generated payloads, authenticated rendering and scheduler lifespan, not just HTTP 200.

## 9. Acceptance and measurement

Test fiscal/basis mismatch, missing/newer statements, currency/share ratios, nonpositive denominators, dilution, split-adjusted prices, restatements, invalid valuation inputs, holiday calendars, forming bars, stale quotes, earnings-event risk, sector applicability and conflicting source evidence. Test that missing required gates cannot be outweighed by a technical score.

Use controlled-clock tests for expiry and hysteresis; real PostgreSQL concurrency for evaluation/outbox claims; provider stubs for ambiguous delivery; authenticated mobile/desktop browser tests. Confirm no default subscription, no live test mail, no historical backfill burst and no repeated email on unchanged data. Critical deterioration must withdraw eligibility and preserve a correction trail.

Measure eligible/blocked coverage, alerts per user, duplicate/suppressed delivery rates, cost per evaluation, explainability and whether a reader can state the downside case and next trigger. Separately evaluate prospective total returns, drawdown, adverse/favourable excursion, turnover and risk-adjusted results at predeclared horizons with fees/slippage, next-tradable-price execution and appropriate broad/industry benchmarks. Include delisted names and all original candidates; avoid survivorship and hindsight.

Compare quality-only, quality+value, and quality+value+entry versions on the same frozen population. Keep alert success distinct from investment success. Do not publish calibrated success probabilities until sufficient out-of-sample evidence supports them; include uncertainty and counts when reporting outcomes.

## 10. Research basis and open decisions

This is our proposed design, not a reproduction of a vendor rating. Morningstar's methodology separates competitive advantage, fair value and uncertainty; its discussion supports keeping those dimensions distinct, not importing its proprietary ratings without permission: [economic moat methodology](https://www.morningstar.com/business/insights/blog/equity-economic-moat-ratings), [uncertainty](https://www.morningstar.com/stocks/an-introduction-morningstar-uncertainty-rating).

Portfolio context should respect differing horizons/risk tolerance and concentration: [Investor.gov asset allocation and diversification](https://www.investor.gov/introduction-investing/getting-started/asset-allocation). Diversification is not a guarantee against losses.

Before implementation release, settle the starting market/universe, research horizon, provisional discount/uncertainty thresholds, liquidity requirements, event-risk policy, user alert cadence and budget. Before any live sending, require the feature's explicit subscription and validate actual recipients. Documentation and shadow evaluation can proceed using the proposed defaults without implying financial suitability for an individual.
