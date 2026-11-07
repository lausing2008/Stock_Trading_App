# User experience specification: a simpler professional workstation

**Design proposal, not implemented UI.** Based on the current navigation/source and saved sample desktop workspace, not a fresh usability study. See [consolidation](consolidation.md) for existing-route mappings and [learning wiki](learning-guide.md) for current feature education.

## Experience goals

The user should answer five questions without assembling separate dashboards: **What changed? What deserves attention? What would make this trade valid? What can I lose? What actually happened?**

Professional depth should be available without making the default screen dense. Preserve the existing focus mode, layout preferences, chart controls and specialist analyses; improve defaults before adding customization controls.

Six primary workspaces:

```text
Today | Discover | Research | Trade | Portfolio | Review

Global: instrument search • account/environment • market/session • inbox • Learn
Secondary: Settings • Operations (permission-dependent)
```

These are six navigation destinations, not six new disconnected applications. Selecting an instrument, account and horizon persists across compatible tabs. When context cannot carry across, explain why rather than silently substituting SWING, a different account or another market.

## Shared application shell

### Persistent context

Always show selected account/book, **LIVE / SANDBOX / PAPER / MANUAL**, currency and relevant session. Use text plus distinct styling; never color alone. If a user changes account or environment, invalidate execution previews and recheck permissions, balances, risk and quote freshness. A saved paper plan cannot silently become a live order.

Instrument search should disambiguate issuer, exchange, currency and asset type. Search recent instruments, saved plans and research documents; do not treat a ticker string as a globally unique security identity. Existing keyboard search can be extended rather than replaced.

### Interaction conventions

- A primary button states the action: **Save plan**, **Create alert draft**, **Preview order**, **Submit order**. “Apply” and “Confirm” alone are insufficient for external effects.
- Reading, saving, arming a condition and submitting an order are separate actions with separate success states.
- Background updates show component as-of times without resetting user input or chart position.
- A stale/error state preserves the last known value with its timestamp and blocks actions that require freshness. It must not render as a current zero or empty successful result.
- Expensive refresh/research requests disclose the operation and cost scope; an ordinary view refresh should not imply retraining.
- Layout customization lives under **Customize workspace**; it should not compete with the trading task on every visit.
- Undo applies to local layout/drafts where reversible. Never promise undo for accepted broker orders or delivered notifications; provide cancel/reconcile actions with truthful status.

## Workspace 1: Today

**Purpose:** prioritize a day's decisions, especially held-position risk and changed plans.

Default order:

1. **Needs attention:** unknown broker orders, stale critical data, risk breaches, imminent events on held positions, expired/invalidated saved plans.
2. **My portfolio today:** reconciled value and exposure, session P&L with currency/basis, cash and open orders, coverage warnings.
3. **Market context:** concise regime/breadth/sector summary and changes since the previous session; show mixed or unavailable evidence.
4. **My opportunity queue:** a small, user-configurable set of watched/armed/ready candidates; no artificial “top pick” when evidence is weak.
5. **Event calendar and brief:** holdings first, watchlists second, broad market last.

Each attention item has owner/scope, first observed/last updated, impact and next action. An outage banner distinguishes “research delayed” from “execution state unknown.” Do not show a healthy overall badge if critical account truth cannot be established.

**Acceptance:** the user can locate the most urgent portfolio exception and its reason without changing workspaces; an empty queue explicitly distinguishes no issues from failed collection.

## Workspace 2: Discover

**Purpose:** find suitable candidates, not bypass entry planning.

One candidate table with saved presets: Early Rally, Quality & Value, Earnings Watch, Sector Leaders, Crowding/Squeeze and My Watchlists. Presets expose the filter definition and qualification status. External-market discovery and the locally covered universe remain distinct scopes.

Suggested default columns: instrument/venue, setup family, state, key change, entry extension, participation basis, upcoming event, data age and evidence status. K-Score/strength can be available columns; do not sort heterogeneous probabilities, heuristic scores and research flags as if they had a common numeric meaning.

Clicking a row opens a compact dossier preview with supporting and contradicting evidence, then **Open research** or **Save to watchlist**. Bulk actions can manage lists or research requests within limits; bulk order submission is not a discovery action.

Include a **Why excluded?** view for user-selected filters. It helps distinguish no qualifying candidates, incomplete data and overrestrictive filters. Unknown values should not be silently treated as false positives or false negatives.

**Acceptance:** identical saved filters yield consistent scope across refreshes; unavailable required data appears in exclusions; existing watchlist/alert enrollment side effects are previewed before saving.

## Workspace 3: Research

**Purpose:** maintain one investment dossier per instrument and a searchable report library.

Tabs: **Overview, Chart & Setups, Company & Valuation, Events, Positioning, Evidence**. Options can deep-link into Trade's options desk while preserving the underlying dossier. The report library filters by instrument, type, as-of date, author/model and qualification.

The overview answers: thesis, counter-thesis, time horizon, supporting facts, invalidation, catalysts and current knowledge gaps. A favorable long-term valuation can coexist with an unsuitable near-term entry. Show scenario ranges and assumption sensitivity instead of a single authoritative “fair price” when evidence only supports a scenario.

Each material claim links to a fact or explicitly labeled inference. Financial facts include unit, period, filing/source and availability date. Copilot narration cannot elevate a missing or conflicting fact to certainty. Archived reports remain archived; compare with current facts without rewriting the original text.

**Evidence drawer:** source, as-of/observed times, complete/forming basis, revisions, formula, uncertainty, applicable market/horizon and links to the saved decision. A compact badge summarizes this, but a numeric “trust score” should not conceal hard missing-data blockers.

**Acceptance:** a user can trace a quantitative claim to its inputs; scenario changes update derived values consistently; partial coverage cannot be labeled a completed issuer-wide review.

## Workspace 4: Trade

**Purpose:** turn a thesis into one explicit plan and, only for qualified capabilities, an order.

```text
Instrument + account + environment + horizon + evidence time
----------------------------------------------------------------
Chart with selected-plan levels       Selected plan
                                     State and decisive blocker
                                     Trigger / entry ceiling / expiry
                                     Stop / targets / exit policy
                                     Planned loss / gap stress / size
----------------------------------------------------------------
Evidence | Alternative setups | Options scenarios | Plan history
Saved plans / active conditions / order intents / broker orders
```

### Selected plan

Only one plan is selected for overlay, sizing and actions at a time. Deterministic, RZY, FVG and LLM alternative plans are comparable candidates, not concurrent execution authorities. Changing the selected plan resets dependent previews and preserves history.

Primary state vocabulary: **Watch, Waiting for trigger, Ready for review, Too extended, Blocked, Expired, Invalidated, In position**. “Ready for review” is a platform eligibility state, not a guarantee or an automatic order.

Separate the **forecast horizon**, **entry timeframe**, **entry expiry** and **managed exit policy**. A 5m chart does not turn a daily model into an intraday prediction. Show planned reward/risk beside structural target provenance; do not display a manufactured R-multiple target as a measured forecast.

### Order review for future qualified live capabilities

Show exact account, environment, instrument/contract, side, quantity/lot size, order type, price constraint, time-in-force, expected costs, buying-power effect, portfolio risk, data age, plan version and rejection reasons. Require revalidation on submit. A preview is expiring evidence, not durable authorization to send at any later price.

Submission success means the specific returned state: intent saved, submitted/accepted, partially filled, filled, rejected, cancel pending or unknown. Preserve the client/order identity through retries. When status is unknown, disable duplicate submission and route to reconciliation; “try again” is not a universal error action.

Keep stop/exit management separate from new-entry controls. Pausing new entries does not automatically cancel protective orders or liquidate positions. Explain the effect of each kill switch.

### Options desk

Begin with underlying plan and objective: directional exposure, protect a holding, acquire shares at a price, or sell upside on shares held. Compare **stock, supported option structure, no trade** under price/time/IV scenarios and executable quote assumptions. Show maximum contractual loss where valid, capital/collateral, assignment/expiry risk and unsupported contract terms.

Flow and dark-pool context stay in an evidence tab; they do not independently authorize a contract. A quote-quality shortlist is labeled as such. Saved option candidates retain exact symbol/expiry/strike/right/multiplier assumptions and snapshot time.

**Acceptance:** it is impossible to confuse saved plan with submitted order, paper with live, or stock-direction probability with option profit probability. Unsupported data/terms block the relevant action while allowing clearly labeled research.

## Workspace 5: Portfolio

**Purpose:** reconcile what is owned, what is committed and what can be lost.

Tabs: **Holdings, Risk, Orders & Activity, Accounts, Reconciliation**. Aggregate views retain drill-down by account, currency and environment; do not pool live and paper P&L.

Holdings show position source, broker-sync time, quantity/cost basis confidence, market-value coverage, realized/unrealized return convention and corporate-action exceptions. Risk includes signed net/gross exposure, concentration, common-factor overlap, liquidity, cash/FX and supported option scenarios. Missing assets remain visible in a coverage denominator.

Reconciliation lists local/broker differences with identifiers, evidence, age, exposure impact and permitted resolution. Manual corrections require a reason and append history; they do not erase broker events. A broker report failure must not appear as an empty account and remove holdings.

**Acceptance:** portfolio totals reconcile to defined sources within explicit tolerances; unresolved or stale holdings cannot disappear from risk; the system explains why analytical coverage is less than economic exposure.

## Workspace 6: Review

**Purpose:** distinguish a good process from a fortunate outcome and identify actual improvement.

Tabs: **Performance, Decision Journal, Execution Quality, Research Lab**. Every chart names its cohort, timeframe, account/environment, return basis, costs, benchmark and unresolved/excluded counts.

Default attribution separates idea selection, entry timing, execution costs, sizing and exit decisions. Provide replay of the evidence actually available at decision time. A corrected-data replay is a separately labeled view. Show missed/blocked opportunities under a declared counterfactual, not hindsight-perfect trades.

Research Lab records hypothesis, variant, data/universe, costs, training/validation/report windows, tested alternatives and qualification. Promotion is a controlled workflow outside a chart's “best backtest” button. A successful test run is not evidence of profitable strategy performance.

**Acceptance:** users cannot compare signal hit rate with broker P&L without a clear distinction; repeated scans do not inflate unique opportunities; an archived decision remains stable after a model or price revision.

## Copilot behavior

Keep the current bounded draft pattern: explain → propose → preview → explicit save through ordinary authorized APIs. Bind each response/draft to the selected instrument, account where relevant, evidence and plan version. Ask “Why is this entry blocked?”, “What changed?”, “Compare stock versus this call,” or “Explain this risk” against that context.

A Copilot answer should not create a competing score, fabricate unavailable inputs, or treat source documents as instructions. Retrieved text is evidence, never authorization. Switching symbols/accounts or updating evidence can invalidate a draft. Read-only questions must not produce hidden writes. Sending a broker order remains outside an explanatory chat response unless a separately designed and qualified execution workflow explicitly authorizes it.

## Learning and onboarding

Give all authenticated users basic education, definitions and risk explanations; advanced feature access remains separately controlled. Onboarding asks for preferred market/style, experience and intended use (research, paper, connected account), without implying suitability certification.

Suggested first session: choose a market → inspect a sample dossier → distinguish strength/probability/RR → create a paper plan → observe an expired/nonfilled outcome → review evidence. Use the [learning wiki](learning-guide.md), then link specialist guides rather than copying inconsistent explanations into every page.

For each tool, offer **What it measures / What it cannot establish / Worked example / Current data limits**. Definitions are available inline and searchable. A research-only tool retains a visible research label even for advanced users.

## Visual system and accessibility

Use reusable components for quantities, as-of times, evidence states, status transitions, risk messages, tables and action previews. Financial formatting must distinguish percent, percentage points, currency, shares, contracts and R. Negative values need signs/text in addition to color. Avoid mixing risk severity with bullish/bearish coloring.

Use readable default type, consistent spacing, restrained accent colors and tabular numerals. Dense tables can offer a compact mode; the default should remain legible. A chart needs a textual summary and a keyboard-accessible path to relevant values/actions. Mobile prioritizes attention, plans and risk, with full evidence accessible; do not squeeze every desktop widget into tiny columns.

Target [WCAG 2.2 AA](https://www.w3.org/TR/WCAG22/) through actual checks: keyboard operation, visible/unobscured focus, labels and errors, contrast, reflow and adequate target sizes. This is a proposed acceptance target, not a current compliance claim. Destructive/live actions must not be reachable by ambiguous shortcuts while typing in an input.

## Usability validation

Use representative beginner and experienced users, including keyboard/mobile workflows. Measure baseline before setting improvement targets; the following are proposed task criteria, not measured performance:

| Task | Pass condition |
|---|---|
| Find why an entry is blocked | Correct blocker and evidence source identified without visiting unrelated diagnostics |
| Distinguish score from probability | User correctly explains the named target and uncertainty or says probability is unavailable |
| Save a plan | Correct instrument/account/horizon, entry ceiling and expiry; no unintended order/subscription |
| Handle late alert | User sees expiry/extension and avoids interpreting it as a current actionable quote |
| Compare options | User identifies capital, spread and price/time/IV risks; no reliance on premium yield alone |
| Review account exception | Missing/stale position remains visible; user reaches supported reconciliation action |
| Recover failed request | Input preserved; no duplicate order or alert created |
| Use keyboard/mobile | Critical tasks work with visible focus and readable controls |

Record completion, errors, context switches and confidence in interpretation—not only satisfaction or click count. Fewer confirmations are desirable for harmless navigation; consequential order/account actions need clear review. Ease of use means reducing confusion while preserving control.
