# Multi-horizon momentum reports and monitoring tasks

Date: October 3, 2026. Status: report-template and implementation-design addition. **No task, email subscription, production job or trading rule was activated by this document.**

Optional [LLM narration](2026-10-03-evidence-grounded-report-narration-design.md) may explain saved EMA/OI observations and contradictions. It cannot choose trigger thresholds, change task state, infer confirmed dealer/institutional intent, or control notification/trading eligibility. An unavailable narrative leaves the deterministic task/event intact.

Extends [the intelligence report templates](2026-10-02-intelligence-report-templates.md) and [Claude implementation prompt](2026-10-02-intelligence-reports-claude-prompt.md). Reuse existing technical analysis, market data/UW, report history, scheduler, preferences and durable alert delivery. This is not a second signal engine.

## What to keep and what to correct

The user's framework is useful as a set of observation windows and conditional setups. It is not yet an empirically validated prediction model.

| Original claim | Required interpretation |
|---|---|
| Short-term trading should focus entirely on technicals | Prioritize price/volume/execution timing, but retain earnings, guidance, macro events, corporate actions and news as relevant context and invalidation inputs. |
| Holding a strike forces dealer buying and a gamma squeeze | A strike/OI concentration alone establishes neither dealer inventory nor hedging direction. Estimate scenarios only with documented position-sign assumptions, Greeks, expiries and freshness. |
| A high-volume weekly recovery confirms institutional accumulation | It establishes an observable price/volume pattern. Investor identity and intention are not observed from candles. Label accumulation as a hypothesis, not confirmation. |
| Earnings hype has completely faded by day seven | Measure persistence and event age; seven sessions is a checkpoint, not a universal information-decay law. New guidance and revisions may extend the event. |
| Relative strength proves long-term capital validates fundamentals | It measures relative price behavior. It does not establish participant identity, holding horizon or causal belief. |
| RSI 50–60 and rising EMA21 make pullbacks healthy buys | These are candidate technical conditions, not a profitability guarantee. Require confirmation, risk checks, contradictory evidence and forward evaluation. |
| Max pain is a three-day price target | Treat as an expiry-specific descriptive calculation with assumptions, never a demonstrated attraction level without evaluation. |

Open interest is a count of outstanding contracts, updated through clearing; it is distinct from intraday volume. It does not reveal the owner or direction of every trade. [OIC open-interest explanation](https://prd-web.optionseducation.org/news/open-interest-why-it-matters). Gamma describes how delta changes; a dealer's net position matters for the hedge response. [OIC gamma explanation](https://prd-web.optionseducation.org/advancedconcepts/gamma).

## Shared horizon definition

Use **1, 3, 5, 7 and 14 exchange trading sessions**, measured from a saved report/decision cutoff. Fourteen sessions is not two calendar weeks. A rolling five-session window is not necessarily a Monday–Friday weekly candle. Chart interval and indicator lookback are separate: EMA(5) on hourly bars describes five hourly bars, not three days.

For earnings-anchored reports, record event publication time and which first session is counted. Intraday releases require an explicit partial-session policy. For regular daily forecasts, use the next completed session as horizon one; report generation/first-tradable time must precede the measured outcome. Holidays and HK lunch/session structure use existing calendars.

Keep the existing broad short/medium/long report horizons. Add an optional **Tactical Momentum** panel; do not silently remap stored forecasts or duplicate a single rule across five labels. A panel remains conditional/research-only until a separately versioned rule and forward outcomes support a forecast claim.

## Five-horizon template for both market and stock reports

| Horizon | Evidence and timeframe | Question | Confirming/contradicting observations |
|---|---|---|---|
| 1 session | Completed 30-minute opening range, regular-session VWAP, prior-session levels, current spread/volume | Is the opening move sustaining or reverting? | Break/hold/retest of the measured range; VWAP acceptance/rejection; invalidation below/above the defined setup boundary |
| 3 sessions | Completed hourly EMA5, daily structure, relative strength, dated OI changes and available flow | Is the initial move continuing or failing? | Successive structure/relative-strength changes; contradictory momentum or adverse news; OI is context rather than a directional verdict |
| 5 sessions | Rolling five-session return and volume; separately completed exchange-week candle | Is participation broadening and is the recovery persisting? | Close location, volume against a matched baseline, breadth/sector participation; avoid declaring institutional intent |
| 7 sessions | Cumulative and sector-relative returns, event/news revisions, volatility and participation | Is the event-associated move persisting? | Continued relative strength versus reversal; new catalysts; describe decay only if a defined measurement exists |
| 14 sessions | Daily EMA21 slope and defense, RSI14, support structure, sector/market context | Is medium-short-term structure intact? | Completed close/hold/reclaim around EMA21, higher/lower lows, opposing events and deterioration in relative strength |

For market reports, use an explicitly named index/ETF proxy, breadth denominator and sector participation. For stock reports, add issuer-specific events and sector/benchmark-relative returns. Do not infer index constituent breadth from an ingested watchlist. Prefer a suitable benchmark for the issuer rather than always using SPY. Missing hourly data means the hourly rule is unavailable.

### Per-horizon output

```text
Report/decision ID, subject, horizon, cutoff, valid until:
Observed structure and source/bar timestamps:
Conditional outlook: constructive / mixed / adverse / unavailable
Supporting evidence:
Contradictions and upcoming events:
Trigger and required confirmation:
Invalidation and expiry:
Data quality and unresolved assumptions:
Existing signal/risk assessment, separately attributed:
Forecast probability: omitted unless validated for this exact target
Change since previous report:
```

Opening-range signals cannot use the completed first 30 minutes before those minutes elapse. Compare intraday volume with the same elapsed session time, not a full-day average. A provisional intraday assessment is separate from the daily close confirmation.

## Task A — EMA21 defense, loss and reclaim

**Proposed initial subject:** MU. **Proposed lifespan:** 20 exchange sessions after activation, with explicit expiry displayed. These are configuration proposals, not active subscriptions. A saved task must specify recipient/channel before notifications are enabled.

Use the existing technical-analysis EMA implementation and record its seed, warm-up, adjustment basis and version. Maintain sufficient completed daily history; a 21-bar input without a defined seed is not automatically equivalent to the engine's EMA21. Never compare split-adjusted EMA with an unadjusted price.

Persist separate observed-position states (`above`, `in_band`, `below`, `unknown`) and confirmed events:

- **Approaching:** price enters a configured EMA proximity band; informational.
- **Defense observed:** with a rising EMA and an explicitly recorded approach/touch, a completed daily candle closes back above the configured boundary. Do not call every above-EMA day a successful defense.
- **Defense lost:** a fresh completed daily close breaks the configured lower boundary, subject to a declared confirmation policy.
- **Reclaimed:** after a recorded loss, a completed close recovers above the upper boundary.
- **Data unavailable:** bars/session status are insufficient; preserve prior valid state but do not claim another defense.

Use a band defined in versioned ATR units or basis points, plus hysteresis to avoid alternating alerts around one price. Exact width, slope lookback and number of confirmation closes are **experiment parameters**, not “best” values. Candidate shadow setup: one completed-close trigger; optionally compare two-close confirmation using the same opportunities. Test alternative bands before treating any as optimized.

Evaluate once after the market-data service confirms the session's final bar. Optional intraday approach alerts use a fresh quote versus the previous completed session's EMA and are labeled provisional; do not silently substitute a partial-day EMA.

Example alert format, placeholders only:

> MU — EMA21 defense lost, daily-close confirmation. Close [P] versus EMA21 [E], distance [basis points/ATR], slope [value]. Trigger [rule/version]; previous state [state/date]. Sector context [observation]. This is a technical state change, not an automatic sell or a guaranteed breakdown. Evidence and current plan: [links].

## Task B — OI build-up at tracked strikes

**Proposed initial subject/lifespan:** MU, same 20-session task window. Use authorized UW snapshots and the current provider-budget controls. **Poll for a new OI publication, not for an imagined real-time OI tick.** A distinct intraday volume/flow task may provide earlier observations without calling them confirmed OI changes.

Task configuration must include options universe, expiry window, call/put side, deliverables, fixed or dynamic strike policy and initial reference snapshot. Do not hardcode the example's $1,050 strike or historical spot price as current truth.

Track the **same contract** across comparable snapshots: issuer, expiry, right, strike, multiplier/deliverable and provider identity. For a dynamic nearest-strike watch, record membership changes separately from OI changes. Rolling from one expiry to another is not an OI spike in one contract.

Report:

```text
Contract / strike / right / expiry / DTE / spot timestamp:
Previous OI and as-of date → new OI and as-of date:
Absolute change / percentage change when the denominator is meaningful:
Baseline count and historical percentile, if supported:
Share of comparable expiry-side chain OI:
Distance from spot / quote quality / nearby event:
Intraday volume/flow, if separately available and clearly dated:
Direction: unestablished from OI alone
```

Define “heavy” with both an absolute change floor and a relative/statistical rule. Require a denominator floor so 1→10 contracts does not become a high-priority “900% institutional surge.” Select floors from observed MU/peer distributions and an alert-volume budget, not from an arbitrary universal number. Preserve raw measurements in shadow if there is insufficient history to calibrate a threshold. New contracts, expiry/assignment, missing snapshots and adjusted contracts receive distinct statuses.

Example alert:

> MU — confirmed OI build-up at [strike/right/expiry]. OI changed from [A] to [B] between [dates], exceeding [versioned rule]. Current spot distance [X]; data published [time]. This shows increased outstanding contracts; opening direction and dealer inventory are not established. View the chain and separate flow evidence: [links].

OI expansion at calls or puts is not automatically BUY or SELL. Optional gamma scenarios must state net-position assumptions and data limitations; never say dealers “must buy” based on a psychological strike holding.

## Integration, delivery and evaluation

- Reuse existing scheduler and technical-alert infrastructure after checking its capability gaps. Persist task owner, symbols, timezone, creation/cutoff, next evaluation, expiry, rules/policy version, last evaluated input identity, state and pause/cancel controls.
- Save monitored events independently of notifications. Durable identity includes task, rule version, input snapshot/contract and state transition. Do not deduplicate merely by symbol/day and suppress a later loss/reclaim event.
- Use existing preferences and an outbox path that is actually wired for this family. Do not assume the first M20 family automatically supports all new types. Defer on unreadable preferences, distinguish accepted from delivered and do not send stale backlogs as current advice.
- Add in-app task status, next expected data refresh, stale-data reason, last evaluation and event history. Evaluate after restart from saved state; do not create duplicate notifications. Suppress initial-state alerts unless the task explicitly requests an initial snapshot.
- No automatic order, risk-limit change or exit follows from these tasks. Trading decisions remain in the existing authoritative services. Task off/pause must not disable protective exits.
- Record all eligible monitoring observations, including non-fires. Measure detection latency, duplicates, alert rate, stale/missing coverage and subsequent 1/3/5/7/14-session returns from first executable observation time. Use fixed baselines and cluster overlapping events; compare EMA-only, OI-only and combined hypotheses without attributing profits to email delivery.
- Preserve market/stock/context distinctions and separate return forecasts from option-contract P&L. Test net-of-cost plans only when entry/exit and executable data are defined.

## Claude implementation acceptance

Implement as a bounded follow-on to existing reports: (1) tactical panel/schema and pure rule evaluation, (2) durable tasks/in-app events in shadow, (3) reviewed notification configuration and activation. Do not replace broad-horizon reports or silently enable emails from this design.

Behavioral tests must cover: insufficient warm-up; split adjustment; incomplete candles; ORB before completion; holidays/timezone/HK lunch; touch-without-defense; defense→loss→reclaim; duplicate/revised bars; delayed OI publication; unchanged snapshot; zero OI base; new contract; expiry/roll; call/put separation; dynamic strike membership; provider failure; restart; opt-out; task expiry; unavailable data; and notification failure that leaves the event intact. Verify the task and alert rendering in a browser.

The feature is useful if it consistently tells the user **what changed, when it changed, what evidence supports it and what would invalidate it**. More frequent alerts or technical terminology alone do not establish better predictions.
