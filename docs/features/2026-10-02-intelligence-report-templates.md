# Market, stock and earnings intelligence — report templates

Date: October 2, 2026. Status: design/templates, not implemented by this document.

## Recommended product

Extension: [1/3/5/7/14-session tactical momentum templates and EMA21/OI monitoring tasks](2026-10-03-momentum-horizons-and-monitoring-tasks.md). These add optional conditional observation panels; they do not replace or silently redefine the broad report horizons below.

Build three report families: **Market Outlook**, **Stock Outlook**, and **Earnings Before/After**. The earnings family contains a frozen pre-release report and independently versioned post-release updates. Each report starts with a short decision summary, followed by inspectable evidence. Full details belong below the summary, not in a wall of indicator values.

Use the [companion Claude implementation prompt](2026-10-02-intelligence-reports-claude-prompt.md) from the existing repository root. Preserve the original two brainstorming documents as references rather than treating all their proposed infrastructure as requirements.

## Review of the two existing prompts

| Keep | Correct before implementation |
|---|---|
| Separate horizons, evidence, contradictions and scenarios | The long prompt mixes a report product with rebuilding most of the trading platform. Ship reports first. |
| Reuse deterministic calculations and existing integrations | The phased prompt explicitly creates `news-engine`, `dashboard-api` and a separate frontend. Extend existing services/pages instead. |
| Provenance and missing-data states | Add **availability time**, report cutoff, revision history, units, currency and fiscal-period basis; a retrieval timestamp alone cannot prevent hindsight. |
| No invented probabilities | Existing meta-model output is not automatically calibrated or enabled. A backtest alone does not qualify a number as a probability. |
| Cited explanations | A news link establishes a reported fact, not causal proof that it moved the stock. Label interpretation and competing explanations. |
| Phased delivery | Do not introduce Airflow, Snowflake, dbt, Kafka or extra paid feeds merely because the prompt names them. |
| Options/flow context | UW is already integrated. Reuse accessible data now, label its actual latency, and defer unsupported fields rather than the whole integration. |
| Historical outcomes | Freeze reports and join outcomes from the first release; do not postpone the measurement contract to the final phase. |
| Earnings information | Add explicit pre-release expectations, result/guidance reconciliation, conference-call update and subsequent market reaction. |
| Portfolio context | Keep the report read-only. Account-qualified trade actions remain owned by existing decision/risk/execution services. |

Other corrections: sector ETF volume is activity, not measured net fund inflow; credit ETFs are proxies, not measured credit spreads; an equal-weight ratio is not constituent breadth. Validate instrument identifiers and classifications against provider metadata instead of hardcoding every ticker/name from the prompt. Keep leveraged funds separate from ordinary industry benchmarks. Use US/HK-specific sessions and benchmarks. Form 13F is due within 45 days after quarter-end; holdings age depends on the holding date and filing date, and is not a fixed 45 days. [SEC Form 13F](https://www.sec.gov/pdf/form13f.pdf)

## Shared report contract

All placeholders below are fields, not facts to invent. Empty fields must show `UNKNOWN`, `UNAVAILABLE`, `STALE`, `CONFLICTING` or `NOT_APPLICABLE` with a reason. Missing input must never silently become neutral evidence.

**Header for every report**

```text
Report type / report ID / version / supersedes:
Subject: market, issuer or canonical earnings event
Market / exchange / currency / timezone:
Generated at:
Information available through: [cutoff]
Price as of: [timestamp, session, source, adjusted/unadjusted basis]
Report status: complete / partial / preliminary / superseded
Data coverage: [available sources, stale sources, missing inputs, disagreements]
Method/policy version / model version if used:
Next scheduled refresh / event that invalidates this report:
```

**Evidence record:** ID, source URL or internal record ID, source publication time, first available-to-platform time, retrieved time, observation/period date, revision/version, value, units/currency, adjustment/accounting basis, quality status. Retain raw-source references and sufficient immutable inputs to reproduce derived values, within source licensing terms.

Every material conclusion links to evidence IDs. Classify statements as **observed fact**, **deterministic calculation**, **interpretation**, **conditional scenario**, or **model forecast**. Require deterministic checks for prices, arithmetic, fiscal periods, timestamps and units before publishing LLM prose. Treat retrieved content as data, never as instructions to tools.

Use distinct fields for:

- **Observed trend:** what the selected bars and features currently show.
- **Forward outlook:** conditional expectation for an explicitly defined future horizon.
- **Evidence quality:** freshness, coverage, source agreement and uncertainty; not chance of profit.
- **Forecast probability:** optional, only from an identified, suitable, calibrated model with evaluation evidence. Otherwise omit.
- **Execution status:** information-only / watch / conditional plan / eligible according to a referenced decision. A bullish report is not an order authorization.

Horizon labels should map to explicit exchange-session counts and target definitions. Do not silently map “1–4 weeks” to an existing `SWING` model unless its actual label horizon matches. Chart intervals (weekly/daily/hourly bars) are separate from forecast horizons.

## Template 1 — Market Outlook

### A. Thirty-second summary

```text
Headline: [one sentence describing the dominant condition]
Current market regime / risk regime:
Short horizon, 1–5 sessions: [outlook; key condition]
Medium horizon, approximately 1–4 weeks: [outlook; key condition]
Long horizon, approximately 1–3 months: [outlook; key condition]
Three strongest supporting observations:
Two strongest contradictions:
What changed since the previous report:
Most important upcoming catalyst:
What would change this view:
Evidence quality / critical limitations:
```

### B. Market evidence table

| Dimension | Required observations | Report conclusion |
|---|---|---|
| Price structure | Relevant benchmark returns over 1/5/20/63 sessions; trend slopes; support/resistance | Trend and transition conditions, not a vote for every correlated indicator |
| Breadth | Advance/decline and percentage above moving averages, with covered/eligible constituent counts | Broad participation or narrow leadership; explicitly label proxy-only analysis |
| Leadership | Sector/industry relative returns and changes in rank | Leaders, improving groups, laggards; separate semiconductors and subsectors where covered |
| Volatility | Available volatility indexes, realized volatility, term structure, timestamps | Risk conditions; implied volatility is not direction |
| Rates/credit/currency | Yields and changes, available credit spreads or labeled proxies, FX | Observed changes and conditional equity implications |
| Macro | Latest releases versus frozen expectations/prior vintage; next release times | Growth/inflation context, surprises and alternative interpretations |
| Liquidity | Available financial-condition measures and dated components | Context; do not present a liquidity formula as a proven market timing law |
| Positioning | Available options/flow/GEX and coverage/latency | Corroboration or conflict, with attribution assumptions |

### C. Forward scenarios

| Scenario | Required conditions | Confirmation | Invalidation | Relevant horizon |
|---|---|---|---|---|
| Bull | … | … | … | … |
| Base | … | … | … | … |
| Bear | … | … | … | … |

Do not force scenario probabilities. Distinguish observed price levels from analyst targets or model ranges. Finish with a dated catalyst calendar, exposure themes worth reviewing, and evidence references.

**Cadence:** per-market pre-open briefing, post-close review, and a material-event update when evidence changes. Intraday reports must identify partial-session bars. Propose refresh cadence and provider cost limits before enabling schedules; reuse cached unchanged evidence.

## Template 2 — Stock Outlook

### A. Decision summary

```text
Issuer / ticker / listing / currency:
Price and session as of:
Business in one sentence:
Observed trend versus conditional forward outlook, by horizon:
Thesis: [two sentences]
Strongest support / strongest contradiction:
What changed since the previous report:
Next earnings or other catalyst, date certainty and timing:
Status: WATCH / WAIT_FOR_CONFIRMATION / INFORMATION_INCOMPLETE
Existing decision-engine assessment, if available: [separate source, time, ID]
```

### B. Evidence table

| Area | Include | Interpretation requirement |
|---|---|---|
| Market/sector/industry | Linked market report, benchmark and peer-relative returns | Distinguish company strength from market beta |
| Company condition | Revenue, margins, cash flow, balance sheet, share count; comparable periods | Improvement/deterioration with source and accounting basis |
| Earnings/guidance | Latest event and guidance; link to earnings report | Do not conflate a beat with good guidance or a positive price reaction |
| Estimate revisions | Snapshot dates, contributor coverage, same fiscal period and basis | No historical revisions claimed from today's single consensus snapshot |
| Valuation | Applicable multiples, peer/historical context and assumptions | Unprofitable/negative denominators are not “cheap”; identify cycle dependence |
| Technical structure | Weekly/daily and supported intraday trend, volume, levels, relative strength | Incomplete or missing intraday data does not become daily evidence |
| Company events | Products, customers, regulation, competition, capital allocation | Verified facts, unresolved claims and possible impact separately |
| Options/positioning | Quote age, IV, expected-move method, liquidity, flow | Separate hedging/unknown activity from inferred directional interest |
| Risks | Earnings gaps, funding, dilution, concentration, FX, corporate actions | Company risk versus portfolio-specific risk |

### C. Outlook and conditional plans

Provide the same bull/base/bear table as the market report, plus a horizon table containing outlook, supporting evidence, contradiction, invalidation and evidence quality.

Optional plans must come from existing validated plan/decision code:

| Setup | Trigger | Entry zone | Invalidation/stop | Target basis | Expiry | Current eligibility |
|---|---|---|---|---|---|---|
| Pullback | … | … | … | … | … | … |
| Breakout | … | … | … | … | … | … |
| Bearish/risk reduction | … | … | … | … | … | … |

Distinguish reducing held stock from opening a short. Report sizing only when a current portfolio snapshot and existing risk checks are available. Otherwise show an illustrative assumption or omit. Never manufacture entry/stop/target numbers to fill the template. Present stock-versus-options alternatives only with contract identity, timestamped prices, deliverable, coverage/collateral and costs; unavailable execution evidence means research-only.

**Cadence:** on demand, after a completed session, and after material company events. A unchanged input snapshot should reuse an existing report rather than pay for a fresh LLM paraphrase.

## Template 3A — Pre-Earnings Outlook

### A. Event identity and frozen baseline

```text
Canonical issuer / event ID:
Fiscal period end / fiscal year / quarter or full-year:
Scheduled release date/time/timezone / certainty / source:
Conference-call time / source:
Frozen report cutoff / version:
Pre-event stock reference price and session:
Consensus snapshot source/time/contributor count/accounting basis:
Prior company guidance source/date/period:
```

Fiscal quarter comes from source evidence, not the calendar month of the release. If uncertain, show unknown. For a reschedule, preserve prior timing and link the revised event. Never silently change the frozen pre-report after results arrive.

### B. What the market expects

| Metric | Consensus | Range/dispersion if available | Prior comparable period | Previous company guidance | Key uncertainty |
|---|---|---|---|---|---|
| Revenue | … | … | … | … | … |
| EPS, explicitly GAAP or adjusted | … | … | … | … | … |
| Gross/operating margin | … | … | … | … | … |
| Free cash flow | … | … | … | … | … |
| Next-period guidance | … | … | … | … | … |
| Sector-specific KPIs | … | … | … | … | … |

For MU/memory, optional KPIs include memory pricing commentary, HBM demand/capacity, inventory, capex and margins **only when sourced**. For other industries use relevant operating KPIs instead. Label management guidance, analyst consensus and unverified “whisper” expectations separately; missing consensus stays missing.

### C. Positioning and risk into the release

- Price run-up/drawdown and relative performance into the event.
- Existing market/sector regime and competitor read-through, without assuming causation.
- Historical reactions: event count, dates, same return window, median/range and limitations. Do not mix premarket, after-hours and intraday baselines without normalization.
- Options expected move: spot, expiry after the release, quote time, method and units. If using an ATM straddle premium, label it as a premium-based proxy, not a calibrated probability interval. If using an IV formula, record time convention and assumptions.
- Gap risk, bid/ask spread, implied-volatility change risk and exposure concentration.

### D. “What would change the thesis?” scenario matrix

| Scenario | Results/guidance combination | Likely interpretation, conditional | Price confirmation required | Invalidation |
|---|---|---|---|---|
| Bull | … | … | … | … |
| Mixed/base | … | … | … | … |
| Bear | … | … | … | … |

Include three questions for management, an existing-position risk review, and a watch/no-new-position/conditional-post-release plan. A confident earnings-beat prediction is not required. Forecasts, if available, must name their model, target and evidence; leave unsupported probabilities absent.

**Cadence:** initial briefing around five exchange sessions before the event, refresh around the prior session, freeze the latest valid pre-release report before publication. Actual scheduling is configurable and release-time-aware. If the event happens early and no valid pre-report exists, record that absence rather than backfilling one.

## Template 3B — Post-Earnings Analysis and Follow-through

### A. Release status

```text
Event ID / link to frozen pre-report:
Stage: FIRST_FLASH / RECONCILED_RESULTS / CALL_UPDATE / SESSION_REVIEW
Source publication / first receipt / extraction / report timestamps:
Results confirmed? Guidance confirmed? Call/transcript available?
Source conflicts / unreconciled values:
Headline: [what changed, in one sentence]
```

### B. Actual versus the pre-event baseline

| Metric and period/basis | Frozen consensus | Actual | Absolute surprise | Percentage surprise when meaningful | Prior comparable result | Source/status |
|---|---|---|---|---|---|---|
| Revenue | … | … | … | … | … | … |
| EPS | … | … | … | … | … | … |
| Margins / cash flow / operating KPIs | … | … | … | … | … | … |

Use a documented convention such as `(actual - estimate) / abs(estimate)` only where meaningful; report absolute differences and a not-applicable reason for zero/near-zero or economically misleading denominators. GAAP actuals cannot be compared with adjusted estimates. Show margin changes in percentage points, not interchangeable percentage growth. Preserve revisions rather than rewriting the original surprise.

### C. Guidance and management interpretation

- New range/midpoint versus **prior company guidance** and separately versus **frozen analyst expectations**, for the same period/basis.
- Raised/maintained/lowered/first-issued/withdrawn/unknown guidance classification with evidence.
- Management comments: demand, pricing, costs, investment, customers and risks; source references for each.
- What improved, what deteriorated, what remains unanswered.
- Assessment: business result versus expectations, forward outlook, and market reaction are three separate verdicts.

### D. Observed market reaction

| Observation window | Stock return and baseline | Volume/liquidity | Benchmark-relative return | Options evidence | Interpretation |
|---|---|---|---|---|---|
| Initial available post-release window | … | … | … | unavailable unless fresh quotes exist | … |
| First regular session open/close | … | … | … | … | … |
| Subsequent 1/5/20 sessions, when mature | … | … | … | … | … |

Use exchange-aware timing. Do not describe unchanged option quotes after an after-hours stock move as current option P&L. Positive reported earnings can coexist with a negative price reaction; record the divergence and possible explanations without claiming a citation proves causation. Earnings-related IV can fall and hurt a long option despite a favorable stock move. [OIC explanation](https://prd-web.optionseducation.org/news/the-crush-is-real)

### E. Updated outlook and accountability

```text
Pre-release thesis: confirmed / mixed / contradicted / not evaluable
What changed versus the frozen report:
Updated outlook by horizon:
Supported confirmation levels / invalidation:
Existing decision-engine verdict and timestamp, if available:
Next action: wait for liquidity / watch confirmation / review held risk / no setup
Next update and unresolved questions:
```

Add a later scorecard: original scenario, actual event, first tradable decision-time quote, forward outcomes, forecast errors, missing data and report latency. Never score a newly generated post-release forecast as though it preceded the announcement.

**Cadence:** event-driven first flash, then update only when results/guidance/transcript adds material information; first regular-session review and mature outcome reviews. Target latency must distinguish publication→receipt, receipt→report and report→notification, and be set from actual provider/scheduler capability. Do not promise minute-level delivery from an unmeasured or closed-session scheduler.

## Rendering and measurement acceptance

The UI should expose the four views (market, stock, pre-earnings, post-earnings) through shared report components with compact summaries, evidence expansion, “changes since last report,” report history and printable/Markdown export. Display partial reports usefully; do not hide them behind a generic failure page. Email remains an optional delivery channel governed by existing preferences/outbox and does not decide whether a report exists.

Measure report coverage, stale/missing inputs, source agreement, citation correctness, revision frequency, latency by stage and cost per report. Evaluate directional forecasts by their exact horizon and frozen cutoff, grouped by market/regime and independent event clusters. Track option-plan results separately from stock returns, and executed-trade results separately from research scenarios. No result should claim higher win rates or returns until the relevant prospective outcomes exist.
