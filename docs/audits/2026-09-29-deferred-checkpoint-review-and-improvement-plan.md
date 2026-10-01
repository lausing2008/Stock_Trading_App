# Deferred checkpoint review and improvement plan

**Review date:** 2026-09-29 (local workspace date). **Source:** the supplied Claude transcript and [checkpoint dated 2026-09-30](2026-09-30-three-week-deferred-checkpoint.md). This date difference is retained, not silently normalized.

**Scope:** review the reported measurements, their extraction queries and relevant local consumers/evaluators. Production counts were not independently re-queried. No trading rules, feature flags, production data, CLAUDE.md or prior reports were changed. Recommendations below are proposals, not deployed changes.

**Production follow-up:** the authorized read-only run is now documented in [Production checkpoint verification](2026-09-29-production-checkpoint-verification.md), with executable probes and returned evidence. It materially qualifies the original confidence, flow and GEX interpretations. The statements below describe the earlier review unless superseded there.

## Assessment

The checkpoint makes useful progress: it distinguishes inadequate samples from specification and instrumentation gaps, and resists threshold tuning merely to generate more activity. Keep calibration feedback unpromoted; do not automatically invert bearish flow; do not loosen squeeze or prebreakout rules on these results.

Several conclusions exceed the measurements. The most important are a mismatch between the confidence query and its production consumer, a five-day versus ten-day mismatch for options-flow calibration, and the treatment of correlated option contracts as separate evidence. These warrant a bounded analytical follow-up before a scoring redesign. “Closed: do not promote now” is defensible; “proved permanently useless” is not.

## 1. Confidence: keep feedback off, but measure the actual policy

The supplied query groups all `signal_outcomes` with non-null confidence and five-day return into ten-point bands, counting `is_correct_5d`. It has no market/direction/horizon split, no signal-date cutoff and no model-version boundary. The reported band counts sum to 19,256.

In contrast, `services/signal-engine/src/api/signals_shared.py::_build_confidence_calibration` uses:

- Primary `is_correct`, not universally `is_correct_5d`.
- A 180-day signal-date window.
- Configured `_CONF_BANDS`, not the checkpoint's ten-point buckets.
- Horizon, direction and market slices, plus a pooled-market fallback.

`services/market-data/src/services/paper_trading_engine.py` applies the optional feedback to the resulting slice win rate: +1 at ≥55%, −1 at ≤35%, otherwise no change. Consequently, “a ±1 adjustment adds noise” is a hypothesis about the policy, not a demonstrated result of this table. Every reported pooled band lies between those thresholds; if those were the actual inputs, the adjustment would be zero. The actual slices could behave differently.

**Recommendation:** leave `calibration_feedback_enabled` off. Reproduce the production cohorts and report which eligible entries would actually change score, eligibility or size. Separate post-remediation/model-version cohorts; do not blend old definitions with current behavior. Show per-slice support, unique symbols/sessions and coverage of the pooled fallback. Analyze only predeclared slices with sufficient support; sparse ones remain unavailable rather than “good” or “bad.”

The pooled table shows weak, non-monotonic association, but it neither proves the inversion fix nor establishes the correct sign in every slice. The report's range is **7.4 percentage points**, not “about 5.” A `100–109` histogram bucket can contain only exact-100 values; inspect min/max before calling this an out-of-range defect.

Distinguish score discrimination from probability calibration. Calibration compares predicted probability with observed frequency; a well-calibrated near-base-rate forecast can still have little ranking power. A raw score must not be presented as a probability without an evaluated mapping. Evaluate both against a constant base-rate forecast on a time-separated holdout. [Scikit-learn probability calibration documentation](https://scikit-learn.org/stable/modules/calibration.html)

Do not rebuild the confidence formula based on this review. First determine whether the existing optional adjustment has incremental value over the frozen baseline.

## 2. Options flow: downgrade the claim before considering inversion

Reported evidence: bullish 528/957 successful five-day outcomes, bearish 286/984, with average underlying returns of +3.05% and +4.00%, respectively. These are concerning for interpreting the bearish label as an outright downward forecast. They do not establish a profitable inverse strategy or demonstrate that bullish flow is useful above a matched baseline.

Local code establishes important qualifications:

| Detail | Consequence |
|---|---|
| `_record_options_flow_alert_outcome` deduplicates by option chain and fired date | Many contracts on one underlying/session can reuse essentially the same stock return. Approximately 1,000 rows is not approximately 1,000 independent bets. |
| `evaluate_options_flow_alert_outcomes` scores bullish return >+0.5%, bearish return <−0.5% | Failure includes the neutral zone. A 29.1% bearish success rate does **not** imply a 70.9% profitable bullish/inverted rate. Count positive, neutral and negative outcomes explicitly. |
| Returns are underlying-price returns | These are not observed option-premium P&Ls or executed trading returns. |
| Entry uses the first available daily close after the fire date; horizons add calendar days with a subsequent-bar lookup | “5d” is not five trading bars or an immediate executable alert fill. Record actual entry/exit dates and delays, and label the measure correctly. |
| `_build_options_flow_alert_calibration` reads `is_correct_10d` and requires ≥5 distinct fired dates, as well as ≥30 outcomes | Five-day totals alone do not establish that the actual ten-day consumer's gates are satisfied. |
| The checkpoint aggregates all available history | It does not isolate recent fixes, regimes or a fresh untouched cohort. |

The statement that market drift cannot explain the 26-point directional success gap is too strong. Bullish and bearish success use opposite return signs; a broad upward market naturally favors one and penalizes the other. Cohort composition can add another difference. Neither comparing to a fair coin nor comparing the two direction-conditioned hit rates isolates alpha.

**Priority analytical job:**

1. Group by symbol/session (and preserve option-chain-level diagnostics separately). Report distinct dates, names, concentration and overlapping horizons by direction, both 5d and the production 10d target.
2. Split call-at-ask, call-at-bid, put-at-ask and put-at-bid; also record unknown side, spread/multileg ambiguity, expiry/moneyness, sweep and source/version. Do not tune dozens of subgroups retrospectively and announce the winner.
3. Compare underlying returns with same-period market/sector returns and a contemporaneous eligible-universe baseline matched on momentum/volatility/liquidity. Use identical entry/exit timestamps. Report absolute and excess return, median/tails, and a flat/no-position benchmark for the trading policy.
4. Compare frozen baseline, baseline without the bearish contribution, and an exploratory inverse policy in shadow/paper. Select on development data; evaluate the selected policy prospectively or on an untouched chronological holdout. Account for costs, opportunity cost and portfolio risk.
5. Use session/time-block and symbol/event-aware uncertainty, not IID contract-row confidence intervals. Include unresolved/censored coverage and do not drop difficult outcomes silently.

**Immediate proposed product posture:** label flow as an observation with an inferred direction and unproven predictive value. Keep bearish candidates for research; do not market them as validated SELL instructions. Before changing any trading contribution, enumerate its consumers and run the ablation. Do not disable unrelated protective exits.

Options may be used for hedging and income as well as directional speculation. Consequently, selling calls need not express a forecast of an outright fall, and buying puts need not reveal an unhedged bearish portfolio. Transaction-side classification is evidence, not proof of investor intent. [FINRA options overview](https://www.finra.org/investors/investing/investment-products/options)

## 3. Prebreakout: diversity is necessary, not sufficient

Keep it experimental. The report distinguishes 8 total names from 7 with resolved five-day outcomes; retain that distinction. The “independent sample is ~7” phrasing is only a warning shorthand: effective sample size depends on within-name, time and event dependence, not symbol count alone.

There are two small reporting corrections: the table's ten-day average rounds to 0.0%, so “negative at every horizon beyond 3d” is not established from the displayed numbers; RGTI's +0.43% means POET is not literally the only positive five-day name. Neither correction makes the result attractive.

Use ≥20 names and ≤15% from one name as provisional diversity checks, not promotion criteria. Add distinct market sessions, event clusters and forward net economic evidence. Report equal-symbol-weighted and event-weighted results, leave-one-symbol-out sensitivity, and a same-universe baseline. Hold eligibility rules fixed while collecting data; do not loosen them simply to hit the diversity target. Do not revive the deferred live-bar or ingestion refactors on this evidence.

## 4. Dark pool: define three rates instead of arguing over one

The supplied query counts outcome rows per day but counts covered symbols across the entire stored print history. Thus neither reported ratio is necessarily the percentage of eligible names firing on that day. The 71 names with ≥20 lifetime prints also does not by itself establish readiness for a rolling-window relative threshold; verify the consumer's actual time window and quality requirements.

Specify and publish separately:

- **Coverage:** distinct eligible symbols with usable contemporaneous data / tracked eligible symbols that session.
- **Conditional alert rate:** distinct alerting symbols / symbols with sufficient fresh data and required lookback that same session.
- **User alert load:** delivered notifications per recipient/session, including repeat/cooldown statistics. Keep raw candidate count separate.

Treat missing coverage as unknown, not zero activity. If the original 5–10% was intended as a selectivity target, define it against session-level eligible coverage. If it was an attention-budget target, specify notifications per user instead. Neither is a profitability target.

Choose relative-size/persistence/liquidity thresholds through held-out outcome and alert-load comparisons, not by forcing a chosen percentage. Separate activity observations from inferred direction. Audit the claim that the old and new fire rates have comparable universes/windows before claiming a quantified improvement.

## 5. GEX and squeeze: avoid both premature promotion and premature dismissal

A feature true 91% of the time can still be a useful safety filter if the remaining 9% contains disproportionate losses. Prevalence alone does not establish discrimination or economic value. The n=13 comparison is insufficient, and the NULL pre-instrumentation cohort is not a valid concurrent control.

Do not build the GEX gate yet. Examine false-arm net returns and tail losses by alert subtype and contemporaneous market conditions; measure opportunity cost from rejecting those alerts. Thirty false-arm outcomes is a checkpoint, not a proof threshold. Require independent sessions/events and prospective evidence for the actual target strategy.

Keep squeeze subtypes separate. More gamma-unwind rows do not validate short-squeeze or ignition logic. Six ignition observations and 16 short-squeeze events do not justify loosening or inversion. Report medians/tails and symbol sensitivity alongside means; one large loss can dominate n=6. Use a predeclared research budget and revisit date to prevent indefinite maintenance, with any retirement decision framed as an operating-cost choice rather than proof of no edge.

## 6. Anti-chase: instrument a funnel, not a headline percentage

Claude correctly distinguishes exposure from realized rejection. This merits a small, separately scoped observability task; it is not a reason to rebuild the strategy during a measurement checkpoint.

Proposed immutable decision fields: decision ID, timestamp, portfolio/strategy/version, symbol/horizon, input snapshot, each evaluated gate/result, not-evaluated reason, first rejection, ROC value, anti-chase result, final action and linked execution. Record candidate identity once per decision, with idempotent writes.

Publish three denominators: all eligible BUY opportunities, candidates actually reaching anti-chase, and candidates that would pass every other gate. The last requires a side-effect-free shadow evaluation of the remaining gates; a first-rejection log alone cannot establish incremental impact. Then measure vetoed candidates' counterfactual outcomes separately from real executions. Keep failed data and unfilled orders visible. A 30.1% exposure rate and a predicted 17% incremental block rate are not directly comparable.

## 7. Improvement sequence and acceptance criteria

| Priority | Bounded next deliverable | Acceptance criterion |
|---|---|---|
| P0 analytical | Frozen evidence extract and report for confidence/flow using the real consumer definitions | Exact cutoff, query/code version, horizon units, cohorts, row/name/session counts, exclusions, missing data and signed/unsigned return conventions can be reproduced. |
| P0 product review | Consumer map for bearish flow and raw confidence | Identify each email, scoring, sizing and execution use; separate observation labels from supported predictive claims. No blanket disabling of SELL exits. |
| P1 analytical | Matched, clustered baseline/no-bearish/inverse comparison | Out-of-sample incremental net returns and risk reported, including weak/negative results. Inversion remains experimental. |
| P1 observability | Anti-chase decision funnel and session-level dark-pool coverage | Denominators come from the actual eligible population; decisions link to outcomes without duplicate counting. |
| P2 prospective | One controlled paired-paper trial at a time | Frozen policy, equal capital/risk assumptions, realistic costs/fills, full equity and abstention accounting, predeclared analysis/stopping rules. |
| Deferred | GEX, squeeze and prebreakout promotion | Diversity plus relevant outcomes and operational quality, not an arbitrary row total alone. |

Use existing outcome/decision records where their grain and provenance suffice; extend only the missing fields. Do not introduce another large engine to solve a denominator problem. The [Jev experiment design](../features/2026-09-29-jev-integration-and-ab-testing-design.md) provides a reusable paired-trial structure, but first establish an honest baseline. Do not change Jev, confidence, flow direction and exit policy simultaneously: attribution would be lost.

Regime evidence remains limited. Historical multi-regime replay and explicit stress scenarios can expose weaknesses before another bear market arrives, but cannot manufacture prospective live evidence or justify automatic promotion. Similarly, broker lifecycle failure simulations can improve engineering confidence without pretending to be observed live trading results.

## Decision summary

Keep non-promotion decisions, correct the overclaims, and replace broad historical hit rates with consumer-aligned, point-in-time, clustered economic comparisons. The next useful output is a reproducible analytical comparison and a small observability plan—not an inverted SELL rule, wider thresholds, or another confidence formula. No result here establishes a profitable stock or option strategy.
