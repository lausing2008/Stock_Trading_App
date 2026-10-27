# Intraday horizon and signal-performance implementation plan

Date: **October 26, 2026**. Status: **proposal; no implementation or activation authorized by this document**. Requested by the user as documentation/planning first. Companion: [full Little RZY / AI Signals review](../audits/2026-10-26-little-rzy-ai-signals-full-review.md). This updates the [October 21 horizon plan](../audits/2026-10-21-ai-signals-horizon-thresholds-and-intraday-plan.md), retaining its separate-horizon design while incorporating the repaired tuning guard and newly identified defects.

## Product decisions

1. Add **Intraday · same session** as a fifth horizon beside **Short-term**, Swing, Long and Growth. SHORT remains its existing daily-horizon identity; “short” position direction is a separate field.
2. Intraday uses **completed 5m trigger bars and completed 15m context**, with a regular-session clock. The default research forecast window is **60 minutes**, with separately reported 30/120-minute diagnostics. Managed positions have a maximum **120-minute hold**, subject to an earlier session-end exit. Forecast outcomes and managed-trade outcomes are distinct.
3. Keep sources explicit: `AI_INTRADAY` and `LITTLE_RZY_INSPIRED_V1` are separate strategy identities. RZY can be an optional context/independent candidate later; agreeing signals do not add independent evidence. RZY's existing 5m/15m/1h streams are not relabelled as the new AI horizon.
4. Support bullish and bearish research candidates. First execution-grade paper phase may be long-only while borrow is unavailable; short research remains visible as unavailable for execution. Existing SELL for a held long never automatically becomes OPEN_SHORT.
5. Show **WAIT / NO SETUP / DATA UNAVAILABLE / EXPIRED** as legitimate states. A numeric strength is optional. Show a win probability only when the precise current cohort has supported calibration; never manufacture one from distance to neutral or target R/R.
6. Start with a transparent deterministic Intraday baseline, labelled **rules baseline, ML not active**. A dedicated intraday ML model is a later stage, with its own labels and tests. Daily SHORT models are not eligible replacements.

## Contracts before implementation

### Horizon and occurrence registry

One registry declares horizon identity, display name, market/calendar, feature/trigger timeframe, forecast windows and units, entry convention, maximum hold, end-of-session policy, allowed directions, eligibility version and supported consumers. Every generator, API, UI, alert, export and evaluator reads that contract or explicitly rejects unsupported horizons.

Use an additive immutable occurrence store alongside the existing daily display snapshots. Suggested records:

| Record | Required content |
|---|---|
| Candidate snapshot | occurrence ID, stock/market, venue session ID, strategy/source, horizon, setup/direction, trigger bar close, context cutoff, first-observed/detected time, data-availability time, input snapshot hash, data lineage/basis, eligibility/rejection reasons |
| Policy snapshot | complete resolved thresholds/filters, model/artifact hash, feature and label versions, policy hash, override source and expiry, cost model, risk/portfolio policy; no inference from today's Redis |
| Trade proposal | entry condition and valid range, stop, target(s), net R/R at proposed and actual fill, invalidation, latest entry time, max hold and flatten deadline, sizing constraints |
| Delivery receipt | occurrence/snapshot/recipient/channel identity, queued/suppressed/accepted timestamps and reasons, idempotency key; provider acceptance separate from inbox confirmation |
| Execution lifecycle | decision/order-ready time, submit/ack/partial fill/cancel/replace/reject/fill/exit times, price, quantity, fees, spread/slippage basis, borrow/locate when needed, remaining risk and unresolved state |
| Outcome | prediction-window result separately from managed-trade net result; initial fill-to-stop R, MAE/MFE, realized and marked-to-market P&L, capital/time exposure, status and evidence version |
| Correction | old/new input generation, known-at times, impacted decisions, replay version and reason; original observed orders/fills remain immutable |

Forecast windows that cannot complete before the session closes are explicitly unavailable/censored for that window; do not silently shorten a 120-minute label or carry it overnight. Managed-trade outcomes still use the actual earlier timed/session exit.

Unique identities distinguish multiple setups in one symbol/session. Keep all rejected candidates for coverage and counterfactual research. Retention/archive costs must be estimated before rollout; large repeated bar arrays should be stored once by hash, with required availability retention, not embedded in every event.

### Data and clock requirements

- Select completed bars from the authoritative source with exchange timestamps, known availability and adjustment basis. Keep event time, provider publication/receipt time, database observation time and decision time separate. A database write after close does not establish first availability or provider finality.
- Context must have been complete **and available** by decision time. Warm-up may cross regular sessions; session VWAP and same-day counters reset at the venue open. Use the venue calendar for DST, holidays, shortened sessions and, in a future HK phase, lunch/auction boundaries.
- Missing/invalid/future bars, unresolved corporate actions, incomplete quote evidence and source changes produce explicit exclusions. Never synthesize no-trade candles without a provider-supported convention and separate flag.
- Shared infrastructure may reuse the RZY grid/gap/provenance code, but the current two-minute settlement rule and best-effort telemetry cannot establish low-latency executable prices by themselves.
- Proposed actionable latency target: **p95 close-to-detection ≤60 seconds, p99 ≤90 seconds**, with no “enter now” notification more than **90 seconds** after the trigger. These are product acceptance targets, not current capability. The existing two-minute settlement floor makes them unreachable without a separately validated lower-latency finality path. Until then show delayed research only. Do not reduce settlement blindly or relax freshness to manufacture a pass.
- Evaluate end-to-end eligibility coverage alongside latency; excluding slow symbols must not create an apparently fast but tiny service. Preserve rejected/late populations by symbol, session and cause.

## Initial Intraday research baseline

These values are a **preregistered starting hypothesis**, not proven optimal settings or a recommendation to trade them. Reuse the same pure rule function in replay and live research. Baseline changes create a new policy version.

| Component | Proposed rule |
|---|---|
| Universe | User watchlist, US listed common equities and ordinary unleveraged ETFs, price ≥$5 and prior-20-session average dollar volume ≥$50M. Explicitly exclude OTC/unsupported instruments and uncertain corporate-action windows. Freeze daily membership as known before the session. |
| Warm-up | At least 50 completed 5m bars and 150 completed 15m bars with valid indicators. Record gaps/availability; old indicator warm-up does not make a missing latest bar current. |
| Entry session | First eligible entry at open +15 minutes; last new entry at close −60 minutes. All times relative to the actual session, including early closes. |
| Long context | 15m EMA20 > EMA50, +DI > −DI, ADX14 ≥20; 5m EMA9 > EMA21 and close above completed-bar session VWAP. |
| Long trigger | After the previous bar closes at/below its EMA9, a completed 5m bar touches EMA9, closes above its EMA9 and above the previous bar high, with close > open; RSI14 in [50,70]. All referenced values must be available at the decision. |
| Short mirror | Reverse context/trend/close conditions; RSI14 in [30,50]. Requires separate shortability/borrow verification before any execution-grade paper fill. Do not assume a short mirror has the same expectancy as a long setup. |
| Liquidity | Current executable quote age ≤15 seconds; spread ≤10 bps; proposed quantity ≤1% of the last completed 5m bar's volume. Compare trigger volume with the same regular-session slot over prior sessions; missing coverage means that filter is unmeasured, not zero volume. Initial relative-volume threshold 1.0 is an ablation candidate. |
| Entry and expiry | A fresh executable observation **after** detection/order readiness must cross the trigger high (long) or low (short). Use a capped price instruction in simulation; expire an unfilled setup after one 5m bar or the session cutoff, whichever comes first. Quote movement beyond the entry cap is a nonfill, not a forced fill. |
| Stop | Long: minimum low of the last three completed 5m bars, minus .25 ATR14; short: mirror high plus .25 ATR. Initial directional risk must be positive. Freeze initial stop; later protective changes may tighten but never loosen it automatically. |
| Target | First baseline takes the full position at 2R, calculated from the actual eligible fill and frozen initial stop. Skip if a known opposing structural level prevents a 2R plan. Confirm **net** planned R/R ≥1.5 after estimated round-trip costs; skip if no fill within the capped range meets it. Target feasibility is a hypothesis, not target-hit probability. |
| Chase ceiling | No more than .25 ATR beyond the trigger breakout level, and never beyond the price allowed by the net-R/R gate. Recompute at actual quote/fill. |
| Other exits | Protective stop, target, 120 minutes since fill, or start flattening at close −10 minutes, whichever comes first. Start flattening does not mean a confirmed fill; remaining exposure is explicit. Initial baseline has no partial profits or discretionary model-written exits. |
| Event risk | Exclude new entries when known earnings fall inside the intended holding window or when a halt/quote discontinuity is unresolved. Unknown event data is visible. Test macro-event exclusions as a separate registered experiment, using point-in-time calendars. |
| Re-entry | One active proposal/position per symbol across sources; no same-setup re-entry. Require a new pullback and occurrence after a closed trade. Initial research cap: three entries per symbol/session. |
| Portfolio research policy | Starting scenario: risk .25% of simulated equity per position, aggregate open initial-stop risk ≤.75%, at most three positions and no new entries after 1% realized+unrealized daily loss. Enforce capital, concentration and broker/account constraints separately. These are simulation scenarios; compare with fixed-risk trade statistics and disclose gaps beyond stops. |

The 2R target, 1.5 net R/R floor and .25 ATR chase cap interact. The implementation must solve the permitted entry range and refuse contradictory or empty ranges; it must not independently render a target and entry that cannot satisfy the stated rule. With partial fills, recompute remaining cash/risk and quantity-weighted outcomes.

Execution prices can differ from last trade or stop prices; limit instructions can remain unfilled. The simulator must model these states explicitly, consistent with [SEC Investor.gov's order descriptions](https://www.investor.gov/introduction-investing/investing-basics/how-stock-markets-work/types-orders).

## UI and alert behavior

On stock detail, add **Intraday** to the horizon selector and a compact card:

- Direction and status; strategy/source; trigger close, detected time, data age and expiry.
- Entry range/condition, stop, target, net planned R/R and maximum hold; one short reason and one invalidation condition.
- Three separate indicators: **setup/rule strength**, **data/execution readiness**, **measured net performance**. A strong-looking setup with unknown execution evidence remains research-only.
- Expandable evidence: indicator values, rejected rules, source/availability, cost assumptions, exact policy/model and study cohort.
- Distinct selection/save for each strategy's exact snapshot. Chart overlays follow the selected plan. Saving cannot turn an expired/blocked proposal into a current trade; a saved plan remains historical until explicitly revalidated into a new occurrence.

RZY and AI Intraday can be compared side by side, but must not share a blended success percentage. Cross-source agreement is contextual, not an untested confidence bonus.

Alert subscriptions remain explicit and separate from existing daily AI/RZY consent. Proposed lifecycle: setup/watch → actionable trigger, when eligible → paper/order acknowledgement → fill → protective modification → exit/expiry/cancel. Research invalidation is not a trade exit; an accepted email is not an accepted broker order. Dedup per occurrence and recipient. Exits and position-risk monitoring must not wait for a universe-wide setup scan or a rebuild. Email is a notification channel, not an order-execution clock; execution-grade activation requires the actual order/fill contract.

Performance page filters: source/strategy, horizon, market, direction, policy/model, session range, liquidity/regime, generated/delivered/executed and as-observed/corrected replay. Show trade count, distinct sessions/symbols, nonfills/cancellations/unresolved, net expectancy R, profit factor, win/loss size, drawdown, exposure, uncertainty and benchmark. “No measurable evidence” stays distinct from 0% wins.

## Implementation work packages and acceptance

| Stage | Work | Acceptance before moving on |
|---|---|---|
| **A — repair evidence** | F01 projection and real-query tests; F02 completed/provisional input contract; F03 immutable delivery/outcome attribution; F04 final ML split purge. Preserve current promotion guard. | SQL-backed tests publish only valid cohorts; no same-day transition misattribution; no incomplete daily bar enters completed-close features; all selection/reporting boundaries respect label maturity. Old metrics remain labelled under old versions. |
| **B — correct forward simulation and measurement** | F05 causal order-ready fills; F07 immutable forward ledger plus corrected replay; F09 data-availability and latency capture; verified price-basis coverage and execution/borrow capability. | No fill predates decision/order readiness. Correction/restart/gap cannot erase actual risk or duplicate execution. Unknown timing/borrow blocks the relevant action. Full-session opportunities, not just delivered alerts, form denominators. |
| **C — add Intraday contracts** | Central registry; additive occurrence/outcome/receipt stores; PostgreSQL enum migration; API schemas and explicit invalid-horizon handling; consumer capability audit. | Multiple same-session setups coexist without overwriting daily snapshots. Unknown styles return an error. Existing four horizons retain their behavior; Intraday cannot leak into daily calibration/models/consensus by fallback. |
| **D — research generator and UI** | Pure baseline generator; event/bar-driven scheduler; shadow persistence; stock card, overlays, save snapshots, performance view and opt-in notification drafts. | Live/replay parity on frozen inputs; each feature known at decision; expired plans cannot be saved as actionable; exact save/reload prices/reasons/version; research state clearly rendered. No broker activation. |
| **E — bounded experiments** | F08/F10/F11 corrected controls, explicit windows, whole-policy ablations, cost/latency stress, portfolio replay and experiment registry. | Preregister hypotheses; frozen datasets/universe; purged session splits; untouched final period; trial accounting and clustered uncertainty; complete exclusion/coverage ledger. |
| **F — prospective validation** | New unseen sessions using the frozen candidate and incumbent; execution-grade paper only for supported directions. | Independent operational, execution and economic gates below all pass. No automatic promotion from a test count or a high win rate. |

Suggested code ownership boundaries for later work:

- Shared: horizon registry, timestamps/availability, identity, policy schema, outcome units, database migrations.
- Signal-engine: daily feature contract, occurrence generation, evidence query/eligibility and calibration publication.
- ML-prediction: labels/availability, all split boundaries, model artifact validation and intraday-specific training later.
- Market-data: authoritative bars/quotes, finality, incremental invalidation, execution-aware RZY simulation, scheduling/outbox and receipts.
- Research/portfolio: complete trade/portfolio replay, cost/borrow/nonfill assumptions, immutable studies and promotion review.
- Frontend: five-horizon selector, distinct strategy plans, clocks/readiness, immutable saves and correctly scoped metrics.

Rollout sequence: additive schema and enum migration → compatible readers/consumer capability flags → shadow producers → UI → separately approved optional delivery. Do not depend on ORM `create_all()` to alter an existing PostgreSQL enum. Keep old binaries from reading new enum rows until compatible; define rollback as disabling producers/visibility, not deleting the enum value or historical records. No simultaneous startup DDL convoy. Validate SQLite behavior and required PostgreSQL 16 paths, plus affected service suites and frontend tests.

## Research protocol for higher returns

Primary objective: **after-cost expectancy with acceptable drawdown and capital use**, with win rate as a secondary diagnostic. Improvements must be compared at comparable risk/exposure. A larger average win obtained by holding much more overnight risk is not a free improvement.

1. Freeze baseline/code/data/policy/universe, label availability, costs and test allocation. Record every attempted variant, including failed variants; do not repeatedly inspect a reserved final window.
2. First measure the causal execution baseline, including rejected, missed, delayed, cancelled and unresolved opportunities. Do not select only symbols that happened to have complete or profitable outcomes after the fact.
3. Run a small staged set: baseline → one liquidity/cost filter → one regime/context filter → one confirmation variant → one exit variant. AI ablations use identical opportunities. Avoid a full parameter Cartesian search; predeclare which tests can advance.
4. Split on whole chronological sessions and purge every outcome/feature-label dependency crossing a boundary. Random controls must stay within their assigned folds and use ex-ante holding rules. Test sensitivity to common session shocks and symbol dependence.
5. Use the existing retrospective RZY snapshot for development only. Final unseen evaluation starts after a new freeze date, not merely after the October 20 snapshot if those later dates have since been inspected or used to choose rules.
6. Report paired improvement versus incumbent and simple baselines, net R and portfolio return, risk/exposure and opportunity coverage. Estimate uncertainty by session blocks with symbol/multiway sensitivity; account for tested variants. Stress observed delay tails, 1.5× and 2× costs, gaps, partial fills, halts, shorts and data revisions.
7. Require stability across folds and leave-one-symbol/session-out checks. Sparse regimes remain unvalidated; do not extrapolate a bull-heavy sample into a bear-market claim. Reducing trades to a tiny favourable subset is not accepted without coverage and sufficient independent evidence.

Backtest selection can overfit even when each individual statistic looks strong. This plan's frozen comparisons, trial registry and final untouched period address that concern; they do not guarantee profitability. [Bailey et al., The Probability of Backtest Overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf).

## Promotion gates

All three gates are independent. Failure or inconclusive evidence keeps the strategy in research.

**Operational:** target session/calendar correct; no unlabelled forming/stale inputs; every expected eligible trigger evaluated or explicitly excluded; no duplicate events/orders; observed end-to-end latency meets the chosen contract across complete sessions, including worst-order symbols. Capture manifests/hash checks pass. A sample count, healthy container or fast median alone is insufficient.

**Execution:** verified actual availability/quotes, causal order timeline, attainable fill/cost assumptions, paper reconciliation, risk limits, short borrow when applicable, and restart/rebuild/partial-fill/end-of-day tests. Session-end failed exits remain visible as exposure. No automatic real-order enablement.

**Economic:** suggested screening floor **200 completed eligible paper/research trades across at least 60 sessions and 20 symbols**, followed by a genuinely untouched period and subsequent forward confirmation. These counts are not proof of adequacy. Require positive after-cost expectancy with a predeclared uncertainty criterion, improvement versus the frozen incumbent, tolerable drawdown/concentration, and no dependence on optimistic fills or a single symbol/day. A smaller sample is “inconclusive,” not a failed strategy or a reason to loosen the gate. If practical data cannot resolve the effect, continue observation or stop the hypothesis.

Before any broker use, review actual broker/account permissions, execution contracts and jurisdiction-specific requirements with current evidence. This plan does not assume an account supports day trading or shorting.

## Regression matrix and deliverables

Required cases include US/HK daily completion, weekend/holiday/DST/early close, missing/future/revised bars, quote/stored timestamp disagreement, context look-ahead, insufficient warm-up, gap through stop, both target/stop in one bar, unknown spread/borrow, partial fills, late arrivals, session-end halt/nonfill, restart with open position, duplicate receipts, changing same-day signal direction, watchlist removal, policy changes mid-session and revised historical candles.

Each stage produces: implementation/design delta, source/data hashes, focused behavioral and PostgreSQL evidence, exclusions and sample accounting, tracker status, rollout/rollback notes and remaining acceptance. Targeted source-spelling tests are insufficient for the missing-projection and delivery-identity classes; exercise the actual query/producer/consumer path.

**First implementation slice after this plan is reviewed:** A, beginning with F01's actual SQL projection and tests, then the daily feature/delivery contracts and final ML split. It is deliberately premature to tune RZY targets or publish an Intraday win probability before those measurement contracts and causal simulator checks are complete.
