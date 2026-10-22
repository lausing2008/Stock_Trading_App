# AI Signals: horizon thresholds, return improvements and an intraday plan

Review date: October 21, 2026, America/Los_Angeles. Runtime inspection: October 21 at 19:18 PDT / October 22 at 02:18 UTC. Source checkout: `ed3205754134fc3a448ff1814c62c043c949c667`.

**Requested deliverable:** review every existing AI Signals horizon, recommend settings that better fit each horizon, and specify an additional intraday/day-trading horizon. The user explicitly chose documentation and an implementation plan first. This review makes no application, configuration, database, delivery or deployment changes.

## Recommendation

**Yes: settings should be specific to the horizon, market, direction and strategy—but the first improvement is consistent measurement and configuration, not simply higher entry thresholds.** The existing system already differentiates SHORT, SWING, LONG and GROWTH. Several later gates still use shared settings, the effective thresholds differ substantially from the defaults, and model prediction windows differ from the holding periods advertised to users.

Add `INTRADAY` as a fifth, explicitly same-session horizon. Give it completed 5-minute trigger bars, completed 15-minute context, session-relative entry/exit times, short freshness limits and an independent outcome ledger. Start in research/shadow mode with the concrete baseline in §7. Do not reuse daily-model probability or daily outcome calibration for it.

There is **no defensible “best settings” winner yet**. The inspected 180-day resolved history contains **24,434 outcomes and zero with `evidence_status='verified'`**. This is a price-basis evidence limitation, not proof that every stored return is wrong. It prevents treating their apparent hit rates as verified performance or selecting a production optimum from them. The recommended baseline is an experiment, and the promotion process below is how to establish better settings.

## 1. What was inspected and measured

Reviewed generation, fusion, dynamic threshold readers/writers, calibration, ML label construction, conviction alerts, freshness, game-plan defaults, paper management, signal persistence and outcomes. The scope is AI Signals and its downstream decision gates; the separate RZY signal family is relevant as reusable intraday infrastructure, not interchangeable performance evidence.

Production reads used allowlisted Redis keys and PostgreSQL `READ ONLY` aggregate queries with timeouts. Eight reviewed files across signal-engine, market-data and ml-prediction matched deployed SHA-256 hashes. No calibration endpoint was invoked.

| Evidence status in resolved last-180-day outcomes | Rows |
|---|---:|
| Verified | **0** |
| Provisional | 173 |
| Unverified | 2,741 |
| Unknown / legacy | 21,520 |

Seventeen resolved rows have missing or non-forward entry dates under the probe's basic entry-date check. That check is diagnostic; it is not a replacement for the application's exact next-session eligibility rule. Regime coverage is also uneven: bull 20,861; choppy 3,353; neutral 49; unknown 170; bear 1; no risk-off rows in this captured resolved cohort. Separate bear/risk-off fitted optima are not supportable from this sample.

The outcome artifact retains stored hurdle-hit rates and direction-adjusted mean stock returns by market/horizon/direction/evidence status. These are **diagnostic stored statistics**, not verified executable strategy returns. They pool historical policy versions, omit a faithful trade lifecycle and do not establish the current configuration's profitability. Recent signals also have unequal maturation across horizons.

Evidence:

- [Runtime keys, cohorts, observed weights and tuning history](evidence/2026-10-21-ai-signals-horizons/runtime.json)
- [Read-only capture script](evidence/2026-10-21-ai-signals-horizons/capture_runtime.py)
- [Configuration, numeric decision points and source hashes](evidence/2026-10-21-ai-signals-horizons/source-inventory.json)
- [Deployed source hashes](evidence/2026-10-21-ai-signals-horizons/deployed-source-hashes.json)
- [Offline executable witnesses](evidence/2026-10-21-ai-signals-horizons/probe-results.json), [inspection script](evidence/2026-10-21-ai-signals-horizons/inspect_thresholds.py)

## 2. The horizon contract needs to be explicit

| Current horizon | User-facing game-plan duration | ML target: daily bars ahead | Primary BUY outcome: calendar days | Primary SELL outcome: calendar days | Paper maximum hold: default days |
|---|---|---:|---:|---:|---:|
| SHORT | 1–5 days | 5 | 7 | 5 | 10 |
| SWING | 5–30 days | 10 | 14 | 7 | 20 |
| LONG | 1–12 months | 20 | 28 | 10 | 90 |
| GROWTH | 30–90 days | 15 | 14 | 7 | 60 |

Sources: `trainer.py:_HORIZON_BY_STYLE`, `signals_shared.py:_OUTCOME_HOLD_DAYS` / `_SELL_OUTCOME_HOLD_DAYS`, and scheduler/paper `_STYLE_PARAMS` / `_STYLE_OVERRIDES`. Portfolio configuration and tuned stop/hold artifacts can override paper defaults; this review did not export private portfolio settings.

These windows can legitimately differ, but must answer different, named questions. A 20-session directional forecast can support a longer managed position; it cannot be presented as a validated 12-month forecast. GROWTH is also a momentum strategy family as much as a duration. Its 15-bar model and 14-calendar-day primary outcome do not even approximate the same forecast window as closely as SHORT/SWING/LONG.

**Proposed contract:** store `prediction_window`, `evaluation_window`, `maximum_hold`, `bar_timeframe`, `market_calendar`, `entry_convention`, `direction`, `strategy_family`, and `policy_version` separately. Use exchange sessions for daily forecast evaluation and timestamps for intraday. Keep historical outcomes versioned under their original convention. Maintain a separate managed-trade outcome based on actual exits.

For the first aligned daily research baseline, use forward forecast evaluations at **5 / 10 / 20 / 15 trading sessions** for SHORT/SWING/LONG/GROWTH, matching their existing ML labels. Preserve shorter SELL studies as explicitly named tactical-exit studies. A SELL can mean exiting a held long; it must not automatically mean opening a short with assumed borrow availability.

## 3. Current threshold inventory

### 3.1 Score semantics and decision boundaries

`fused_prob` / `bullish_probability` is the final composite score after TA, ML, adjustments and filters. It is not automatically a calibrated probability of making money. Displayed confidence is `abs(fused - 0.5) × 200`, a distance-from-neutral strength measure. For example, BUY score 0.72 corresponds to strength 44, not 72% or 44% expected win rate.

Decision order in `_decide_style` is precise:

1. `fused > buy_threshold` → BUY.
2. Otherwise `fused > hold_threshold` → HOLD.
3. Otherwise `fused >= sell_threshold` → WAIT.
4. Otherwise → SELL.

Equality with the BUY cutoff is **not BUY**. The probe confirms a captured SHORT cutoff of 0.72 returns HOLD at 0.72 and BUY just above it. Calibration's `>=` BUY / `<=` SELL selections should be reconciled with production's strict BUY / SELL boundaries before claiming equivalent replay.

Published confidence-calibration bands are [0,40), [40,55), [55,70), [70,85), and [85,101), with a 30-observation minimum, a 180-day history window and a one-hour cache. Buckets distinguish horizon/direction/market and may fall back to pooled markets with disclosure. Sample count alone does not override evidence eligibility or establish a precise probability.

### 3.2 BUY thresholds: defaults versus captured effective settings

The reader prioritizes **watchdog → calibrated threshold → code default**. An override represents the bull baseline; other regimes receive the delta from that baseline and are clamped to [0.55, 0.85]. The values below are computed by executing the deployed-matching reader with captured keys.

| Horizon | Default bull | Default neutral / unknown | Default choppy | Default bear / risk-off | Captured bull | Captured neutral / unknown | Captured choppy | Captured bear / risk-off |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| SHORT | .63 | .62 | .65 | .68 | **.72** | .71 | .74 | .77 |
| SWING | .72 | .72 | .74 | .76 | **.81** | .81 | .83 | .85 |
| LONG | .60 | .62 | .65 | .70 | **.63** | .65 | .68 | .73 |
| GROWTH | .60 | .60 | .65 | .68 | **.71** | .71 | .76 | .79 |

The underlying calibrated BUY keys are SHORT .57, SWING .61, LONG .55 and GROWTH .65. Watchdog keys .72/.81/.63/.71 currently take precedence. Those are large differences, especially SWING .61 versus .81; a report of only the code defaults or only the calibration keys would be misleading.

The legacy `high_vol` profile entries equal the choppy entries, but `_decide_style` accepts the canonical regimes and maps an unrecognized value to `unknown`. Do not introduce a new caller that assumes passing the legacy string applies the choppy decision tier.

### 3.3 HOLD, WAIT and SELL

| Horizon | HOLD cutoff bull | HOLD neutral / unknown | HOLD choppy | HOLD bear / risk-off | SELL default | Captured SELL |
|---|---:|---:|---:|---:|---:|---:|
| SHORT | .46 | .47 | .50 | .52 | .35 | **.29** |
| SWING | .50 | .50 | .54 | .56 | .35 | .35 |
| LONG | .46 | .46 | .50 | .54 | .35 | .35 |
| GROWTH | .45 | .45 | .50 | .52 | .35 | **.29** |

WAIT occupies the interval from the SELL threshold through the HOLD threshold, subject to decision ordering. Dynamic SELL bounds are [.15, .45]; SELL is horizon-specific when calibrated, but currently has no regime-specific reader. HK SHORT SELL is explicitly changed to HOLD after decision. HOLD is a signal label, not permission to override a held position's stop or risk exit.

### 3.4 ML fusion and model thresholds

| Horizon | Default ML cap | ML floor at AUC ≥.60 | Captured per-style cap | US precision target | HK precision target |
|---|---:|---:|---:|---:|---:|
| SHORT | .30 | .10 | **.40** | .73 | .78 |
| SWING | .65 | .15 | absent; global key is **0** | .63 | .70 |
| LONG | .45 | .12 | **.15** | .53 | .60 |
| GROWTH | .60 | .20 | **.65** | .63 | .70 |

Precision targets are training-selection targets, not delivered alert win rates. The current binary ML labels exclude small moves: dead zone = `0.5 × median rolling-20-day volatility × sqrt(horizon)`, clipped to 0.5–3% for US and 0.5–5% for HK, with 1% fallback. Consequently, a model trained on retained directional examples is not directly estimating unconditional probability of a profitable trade among all candidates.

Fusion details:

- Model probability is clipped to [.05, .95]. AUC <.50 receives zero weight; AUC .50–.55 ramps weight 0–.20; .55–.70 ramps .20–.75, capped thereafter.
- A per-style tuned cap overrides the global cap; the global cap overrides the profile default. The global value is loaded in-process, so future observability should expose that resolved in-memory value as well as Redis.
- The AUC-scaled floor is applied **after** the cap ([signals.py:2116-2122](../../services/signal-engine/src/generators/signals.py#L2116-L2122)). That ordering is deliberate: the profile comment reads "global cap cannot push ML weight below this", which shields each style's model from a single global cap tuned on all styles at once. The offline SWING witness produces weight .15 with global cap 0. Stored latest SWING rows show positive weights in 106 of 197 symbols, up to .15. The stored row is not a capture of every input used at generation.
- **Calibration/runtime mismatch.** The global cap is written by `calibrate_ml_weight`, whose sweep scores each candidate as `fused = w × ml + (1 − w) × ta` ([calibration.py:545-550](../../services/signal-engine/src/api/calibration.py#L545-L550); `/ml-weight-validation` uses the same simplification at line 341). Production instead treats the value as a **cap** on an AUC-derived weight, then applies the AUC-scaled floor and the ML/TA-disagreement cut. The calibrator validated "weight 0" for a calculation that production never runs; production runs SWING at up to .15. Two further mismatches follow from the same simplification. The sweep pools every horizon, but the resulting global cap only takes effect for styles without a per-style cap (currently SWING alone). And it counts a candidate as firing at `fused > 0.5`, not at that style's effective BUY threshold.
- ML/TA disagreement >.25 cuts ML weight by 25%; >.35 increases the reduction progressively to 50% at gap .65. SWING also compresses by .85 when ML >.90 and TA <.75.

**Fix before tuning: make calibration evaluate the production weight calculation, and keep the floor.** Factor the runtime path (AUC ramp → effective cap → AUC-scaled floor → disagreement cut → fusion) into one shared function. Have `calibrate_ml_weight` and `/ml-weight-validation` call it for every candidate cap. The stored signal already carries `ml_probability`, `ta_score` and `ml_test_auc` in `reasons`, and its horizon is on the row. Exclude rows whose AUC was unmeasured, with counts, rather than assuming one. Calibrate only over the styles the global cap actually governs (those without a per-style cap at generation time), or calibrate each style's cap separately, and judge firing against the effective BUY threshold rather than 0.5. Do **not** make the cap override the floor: that would let a pooled global tune switch off a style's ML without ever testing that style.

A deliberate per-style "ML off" remains a legitimate need, but it is a **separate feature**: an explicit per-style `ml_enabled` flag that an operator sets on purpose, not an inference from a cap value of 0. Validate every runtime value and expose effective source/version. Evaluate style models on their own horizon, including no-edge examples, costs and base-rate calibration. Do not infer that higher precision targets or a larger ML share improve returns.

### 3.5 Horizon-specific filter profiles

Compression means `0.5 + (score - 0.5) × multiplier`; a smaller multiplier pulls more strongly toward neutral. These are score transforms, not necessarily hard entry vetoes.

| Setting | SHORT | SWING | LONG | GROWTH |
|---|---:|---:|---:|---:|
| ADX minimum: default / captured override | 27 / **10** | 15 | off | 12 |
| ADX compression | .85 | .90 | off | .92 |
| Choppy/high-vol/risk-off compression | .92 | .85 | .90 | .88 |
| Breadth <40% compression | .90 | .90 | .92 | .95 |
| Weekly alignment multiplier: aligned / opposed | 1.08 / .93 | 1.12 / .85 | 1.18 / .80 | 1.08 / .92 |
| Relative-strength compression at rank <.70 | .90 | .85 | .80 | off |
| Minimum bullish pillars before stronger penalty | 2 | 3 | 3 | 2 |
| Minimum bearish pillars: default / captured | 0 / **3** | 0 | 0 | 0 / **3** |
| Minimum retained pre-filter distance, before later hard filters | .70 | .55 | .65 | .60 |
| News sentiment <25 / <35 compression | off | .75 / .85 | off | .80 / .90 |
| Declared earnings ≤2 / ≤5 / ≤10-day compression | separate rule | .65 / .85 / .95 | off | .60 / .80 / .92 |

**Active versus declared settings matters:** `_EARNINGS_COMPRESSION_ENABLED=False` disables the listed SWING/GROWTH proximity multipliers. SHORT's separate imminent-event guard remains active: ≤2 days uses .40 with beat-rate/regime scaling. A bull-market earnings-beater bonus of +.03 for beat rate ≥.70 remains active in the SWING/GROWTH branch when an earnings date is known; that bonus is outside the ≤10-day branch. Document this separately and test whether event timing actually falls inside the intended holding window.

Important shared rules and exceptions:

| Layer | Current rule |
|---|---|
| Pillars | Bullish pillars <2: ×.85; below style requirement: ×.70; four bullish pillars: +.03. Tuned bearish minimum: ×.70 when unmet |
| Weekly structural gate | SWING/LONG, RSI ≤38 and downtrend: ×.65 for <5 consecutive low-RSI weeks, tapering to ×.40 at ≥20; daily recovery can exempt it |
| Weekly overbought | SWING/LONG, RSI >75 with uptrend: ×.85; GROWTH skips these structural gates |
| Relative-strength exception | Do not compress a bullish score for rank <.70 when 20-day return >5%, or RSI 28–45 with stochastic cross-up |
| Pullback recovery | 5–25% below prior 20-day high and ≥2 rising closes; +.07 with volume >1.10× prior average, otherwise +.04, subject to pillar/recovery eligibility |
| Pattern adjustment | Recency over 20 bars, aggregate clipped ±.15; confirmed double-top breakdown additionally ×.35 |
| Ordinary news / hot news | Negative-news transforms are direction-aware; material negative hot news uses .70 initially, .85 after one hour, .92 for delayed disclosure; LONG is exempt from hot-news compression |
| Short interest | SWING/GROWTH bullish scores: ≥20% float +.02; ≥30% +.04, subject to upstream evidence availability |
| Analyst changes | Except SHORT: positive net upgrades +.02 or up to +.05; negative changes −.03 or up to −.08 |
| K-Score | LONG boost: ≥55 +.04; ≥70 +.08; <35 −.06 |
| Data/model quality | Price age >3 UTC calendar days: ×.60; history <50 daily bars: ×.50; model out-of-sample suppression: ×.60. These score penalties are distinct from downstream freshness/eligibility vetoes |
| HK-specific | HSI bearish / southbound / low-liquidity gates remain market-specific; HK SHORT SELL is disabled |
| Options | Composition is contextual; current options adjustment is zero. Do not reintroduce call-volume-as-bullish scoring while tuning horizons |

The compression cap can restore some earlier score reductions; weekly structural gates occur afterward. A filter tag therefore does not prove a candidate was blocked. Measure the complete before/after decision and preserve the actual score path.

The source inventory includes the complete style dictionaries, common TA weight defaults, numeric generator conditions and calibration function parameters. Core TA is shared daily-bar logic: RSI14, stochastic RSI, SMA20/50/200, MACD12/26/9, Bollinger20/2, OBV10/30, ADX, prior-20-day volume and price context. The field named `price_above_vwap` uses a rolling daily volume-weighted reference; it is not a session VWAP suitable for day trading.

Global calibrated TA weights also exist in Redis; their full captured values are in the runtime artifact. Examples are `rsi_mild_overbought=.3204`, `price_above_vwap=.2920`, `above_sma50=.2241`, and zero weights for `macd_strong`, `bullish_trend` and `obv_trend_bullish`. These are global weighting inputs, not independently optimized horizon thresholds. The calibrated blend also depends on an in-process/file activation flag, which this read-only capture did not inspect; do not claim that every captured weight is active merely because its Redis key exists. Record activation and resolved weights in future signal policy snapshots.

### 3.6 Alert and trade-management thresholds

BUY alerts pass a separate conviction gate: K-Score ≥55; price/SMA trend structure (GROWTH relaxes the golden-cross condition; confirmed double bottoms can override it); RSI 45–72 or GROWTH 50–85; positive/rising MACD; bullish OBV; and `adx_trending`, which is computed at **ADX >25**. ML evidence must exceed .65 in bull, .70 in neutral/unknown, or .78 in choppy/bear/risk-off when ML is present and weighted. Missing/zero-weight ML soft-passes.

Full conviction means no failed layers; near conviction permits one soft failure, currently drawn from OBV, ADX, ML and MACD, with dynamically determined softening also possible. Thus the shared ADX condition is not an unconditional hard veto. It still differs from the horizon-specific fusion ADX setting, and a tuning experiment must measure both.

The additional softening rule uses calibrated layer edge <2 percentage points. The captured trend-above-SMA50 edge is 2.6, so it does not soften the Uptrend layer in this snapshot. These edge estimates share the outcome-quality limitations above.

Overextension checks include stochastic RSI >.80 now or on the preceding cycle, price within 3% of the prior 20-day high with RSI >65, and 10-day ROC ≥10%. The last condition is a shared entry disqualifier, including for GROWTH. Investigate the cost of suppressing momentum breakouts separately from avoiding late entries.

Non-BUY bullish transitions use analyst agreement plus strength floors 58/60/68 by regime; these floors are not the BUY conviction rule. AI alert freshness is a shared **72 hours** and cooldown **2 hours**, with reversal exceptions. Optional consensus requires two horizons agreeing. These settings cannot be inherited unchanged by intraday.

| Game-plan / paper default | SHORT | SWING | LONG | GROWTH |
|---|---:|---:|---:|---:|
| First / second entry discount | 0.5% / 1.5% | 1.5% / 3.5% | 2% / 5% | 2.5% / 6% |
| Breakout premium | 1% | 2% | 3% | 3.5% |
| Fallback stop distance | 3% | 5.5% | 10% | 12% |
| Fallback target gain | 5% | 12% | 25% | 35% |
| Paper ATR stop multiplier | 2 | 2 | 2 | 3 |
| Paper minimum strength | 45 | 50 | 40 | 45 |
| Paper minimum K-Score | 48 | 52 | 50 | 48 |
| Paper minimum reward:risk | 2 | 2 | 2 | 2 |
| Paper trailing activation | 5% | 3% | 6% | 7% |
| Paper breakeven activation | 3% | 1.5% | 4% | 4% |
| First partial-profit level | 10% | 10% | 15% | 15% |

These are static defaults, not a production portfolio export. ATR/expected-move inputs, tuned artifacts, market overrides, portfolio choices and decision-engine gates can alter actual plans. The scheduler can also substitute an analyst target; its target horizon is not proven to match the trade horizon. SHORT's 5% fallback target versus 10% partial-profit setting illustrates why exits should be reviewed as a coherent lifecycle instead of independently tuned percentages.

Common paper defaults include 1% risk/trade, 10% maximum position value, six positions, 25% sector exposure, 4% daily-loss brake, 20% portfolio drawdown brake, three entries/day, 12% maximum open risk, and 0.1% entry slippage. These are existing software defaults, not recommended intraday values; actual effective portfolio policy must be resolved before comparing returns. The full defaults and style/market overrides are preserved in the source inventory.

## 4. Problems to address before claiming better win rates

**A. Calibration evidence differs between readers and writers (evidence-population mismatch).** Published confidence calibration keeps only `evidence_status == "verified"` outcomes ([signals_shared.py:279](../../services/signal-engine/src/api/signals_shared.py#L279)). `outcomes_calibrate_apply` selects resolved BUY outcomes by date, `is_correct` and direction only ([calibration.py:1446-1452](../../services/signal-engine/src/api/calibration.py#L1446-L1452)), with no evidence filter. `calibrate_ml_weight` uses a third population: it reads `Signal` and `Price` directly, applying its own split-adjusted-where-evidenced return basis, and never consults `evidence_status`. Writers can therefore tune from observations that the reporting layer refuses to publish as measured. With zero verified resolved outcomes in the captured window, reporting publishes nothing while fitting proceeds on the rest. Apply one eligibility contract to display, fitting, promotion and post-promotion evaluation.

**B. Threshold tightening is not a demonstrated return improvement.** The watchdog reacts to a <38% hurdle-hit rate with ≥15 samples, raises BUY by .03, has a three-tightening guard and a seven-day key TTL; it can relax .02 after seven days without signals. It monitors horizon-matured windows, but does not perform a counterfactual strategy validation for each nudge. The captured history shows multiple watchdog tightenings soon after model/style changes. Evaluate joint changes and avoid daily feedback on overlapping cohorts whose outcomes predate the policy being judged.

**C. Some promotion comparisons use the wrong baseline.** `outcomes_calibrate_apply` compares a candidate against the hardcoded bull threshold, not necessarily the currently effective watchdog/calibrated policy. Other tuning functions have their own semantics. Freeze and compare against the effective incumbent, including all overrides, costs, exits and gates. A positive lift over an obsolete baseline is insufficient.

**D. Existing splits do not establish independence.** The threshold sweep uses an older-70% / newer-30% split sorted by signal date. Repeated symbols, overlapping holding windows and same-date rows across the boundary require grouping and purging. Threshold application defaults require 50 selected observations on each slice and a .1-percentage-point validation lift; a nominal 100 total is not enough to guarantee 50 in the 30% slice. `tune_strategy` searches 31 BUY cutoffs ×13 ML caps with a 15-observation-per-slice floor. Multiple testing and repeated reuse of validation dates remain relevant.

**E. Generated-only outcomes constrain what can be learned.** A sweep on historical BUY outcomes cannot reliably judge lowering the threshold to admit candidates that were never BUY and have no captured outcomes. Reconstruct point-in-time candidates or collect a shadow candidate universe, including rejected candidates, before loosening gates.

**F. Score strength is not profit probability.** A fixed strength floor or higher fused score need not improve expectancy. ML dead-zone labels, calibration buckets and conditional delivery selection all change the population. Use reliability plots and cost-aware outcomes for each market/horizon/direction; publish unmeasured when evidence is insufficient.

**G. Tuning records mix metric units.** Watchdog writes `win_rate_14d` as a fraction into `validation_ev_pct`; other writers use that field for percentage return. The captured .2101 SHORT watchdog value means a 21.01% stored hurdle-hit rate, not +0.2101% validated expected return. Introduce typed `metric_name`, `value`, `unit`, `cohort` and `evaluation_version` before comparing tuning histories.

**H. Execution quality can dominate an intraday edge.** The Oct 21 [457/458 investigation](../2026-10-21/od-01-od-02-session-analysis.md) established bar-source and delivery-latency problems. Fixes have been deployed and further measurement is scheduled; this review does not claim the next session's results already exist. Use the new evidence to establish achievable signal age and replay assumptions before enabling intraday delivery.

## 5. Improving returns: tune the complete strategy

Optimize **net expectancy and portfolio returns subject to risk constraints**, and report win rate alongside them. For a fixed-risk example:

`expectancy_R = win_probability × mean_win_R − loss_probability × mean_loss_R − mean_cost_R`.

A 45% win rate with 2R wins and 1R losses gives +.35R before costs. A 70% win rate with .3R wins and 1R losses gives −.09R. Raising hit rate by taking tiny profits while leaving large losses can reduce returns. These are arithmetic illustrations, not platform measurements.

Measure, for each horizon/market/direction/policy version:

- Net expectancy per trade, average/median win and loss, profit factor and cost sensitivity.
- Actual position-sizing portfolio return, drawdown, exposure, turnover, capacity and correlation—not a compound curve of overlapping fully funded trades.
- Stop/target order, adverse/favorable excursion, time in trade and overnight gap exposure.
- Candidate → eligible → delivered → executable → entered → closed funnel, with suppressed, stale, rejected, unfilled and unresolved observations retained.
- Performance by regime, liquidity, time-of-day for intraday, and event proximity. Display uncertainty and coverage for thin groups.

Separate LONG forecast accuracy from 90-day managed-position returns; separate SELL exit usefulness from short-sale profitability; separate fixed-horizon stock returns from option P&L. Use market/sector benchmarks and a simple same-horizon baseline.

Searching many trading configurations on limited history creates false discoveries; retain the entire search history and a final untouched evaluation set. [Bailey et al., *The Probability of Backtest Overfitting*](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf)

## 6. Horizon-specific improvement plan

Keep the captured effective settings as the comparison baseline; **do not bulk replace them with the defaults or the candidates below**. Resolve the ML calibration/runtime mismatch and outcome eligibility first, then evaluate one limited hypothesis family at a time.

| Horizon | Recommended research emphasis | Small initial comparisons | Promotion question |
|---|---|---|---|
| SHORT | 5-session forecast, fresh entry, liquidity, ATR/structure stop, event overlap | Captured ADX 10 versus 20/27; ML cap .20/.30/.40 versus ML disabled (via the separate `ml_enabled` flag), once calibration simulates the production calculation; evaluate BUY cutoffs near incumbent, then legacy .63 separately | Does selectivity improve net 5-session/managed-trade returns without losing too many timely entries? |
| SWING | 10-session forecast, pullback vs breakout families, sector context, overnight gaps | Incumbent .81 versus .78/.75; active weekly gate versus a predefined recovery exception; ML zero versus .15/.30 with valid models | Is .81 suppressing useful setups, or correctly avoiding losses? Measure all rejected candidates |
| LONG | Explicit 20-session forecast plus separately managed 90-day position | Incumbent .63 versus .60/.66; weekly structure, fundamentals and risk-scaled exits; preserve dividend/action basis | Does the model help over 20 sessions, and does the longer trade-management policy add value separately? |
| GROWTH | 15-session momentum forecast, volatility/liquidity-aware risk | Incumbent .71 versus .68/.74; separate breakout and pullback rules; assess the 10-day-ROC entry veto; ML zero/.30/.60/.65 | Is the shared anti-chase rule filtering late entries or eliminating the intended momentum opportunity? |

These are bounded candidate comparisons, not a Cartesian product or optimized recommendations. Tightening and loosening both require eligible candidate evidence. Do not fit a separate setting for every ticker; use horizon × market × strategy family first, with regime-specific changes only where evidence supports them. Prefer a stable range of good settings over one isolated maximum.

Replace loosely coupled constants with a versioned policy registry. Each signal should expose the resolved BUY/HOLD/SELL boundaries, ML enabled/weight/cap, gate thresholds, source of each override, source bar close, feature cutoff, model ID, policy ID and outcome convention. Snapshot that policy when a candidate is generated so future tuning can reproduce it.

## 7. New horizon: `INTRADAY` / “Intraday · same session”

### 7.1 Recommended starting profile

This is a **research baseline for liquid US equities/ETFs**, not an empirically optimal or live-executable configuration. HK, extended hours, options and short selling require separate data/market/permission work. The first setup is a long trend-pullback continuation, which avoids combining incompatible reversal and breakout entry rules into one score.

| Setting | Proposed initial value / exact meaning |
|---|---|
| Prediction and position horizon | 30–120 minutes; maximum 120 minutes after entry, with mandatory session-end exit attempt earlier when required |
| Trigger bars | Completed authoritative 5-minute regular-session bars |
| Context bars | Completed 15-minute bars available as of the trigger close; never use a forming or later context bar |
| Session schedule | No entries until open +15 minutes; stop new entries at close −60 minutes; begin mandatory flattening at close −10 minutes. Use exchange calendar, including early closes/DST |
| Initial universe | Listed US common equities/ordinary ETFs; price ≥$5, prior-20-session average dollar volume ≥$50M; exclude instruments with unresolved corporate actions or unsupported leveraged/inverse behavior |
| Data warm-up | At least 50 completed trigger bars and 150 completed context bars, with valid contiguous indicator inputs; use prior regular sessions for EMA/ATR warm-up |
| Session VWAP | Reset each regular session; use only observed completed regular-session bars through the feature cutoff. Keep it distinct from existing daily `price_above_vwap` |
| Trend | 5m EMA9 > EMA21; price above session VWAP; completed 15m EMA20 > EMA50; 15m +DI > −DI |
| ADX / momentum | 15m ADX14 ≥20; 5m RSI14 between 50 and 70 inclusive. These are setup-admission hypotheses |
| Pullback trigger | Previous completed 5m low touches/breaches its EMA9 while its close remains ≥EMA21; current completed close breaks above that previous bar's high |
| Relative volume | Current-session cumulative volume ≥1.5× median cumulative volume at the same elapsed session minute over prior 20 sessions; require at least 10 valid prior sessions; unknown does not pass |
| Current liquidity | Trigger-bar dollar turnover ≥$500k; executable quote spread ≤10 basis points **and** ≤10% of planned per-share risk |
| Quote freshness | Two-sided timestamped quote ≤5 seconds old for executable status; missing quote/size remains research-only |
| Signal lifetime | Expires at trigger close +5 minutes, or when invalidated, whichever comes first. Target close-to-provider-acceptance p95 ≤120s; never extend expiry to hide processing delay |
| Entry | First eligible quote after computation/delivery, using an explicit adverse fill assumption. Reject a chase >.25R above planned entry. Limit-order nonfills remain nonfills |
| Initial stop | Lowest low of the last three completed trigger bars minus .10×ATR14(5m), then ensure distance is at least .75×ATR. Reject if resulting distance >1.5×ATR. Define stop from actual candidate entry and freeze it; never widen a held stop |
| Target | 2R gross objective, admitted only if expected net reward:risk after modeled costs ≥1.5. Risk here is entry-to-stop distance; scenario losses can exceed it on gaps/halts |
| Exit baseline | Initial protective stop, target, 120-minute timeout or session flattening. No automatic averaging down, partial exits, breakeven or trailing in the first experiment |
| Research risk defaults | .25% simulated equity per trade; .50% aggregate initial open risk; at most two positions and one per symbol; at most three entries/session; stop new entries at .75% session loss, including open P&L |
| Re-entry | Fresh setup required; 30-minute symbol cooldown after a stop, in addition to event-level send-once deduplication |
| Events / market state | No executable status during a halt, stale/rebuilding data, unknown session state or failed eligibility. Require coverage of scheduled events through the planned holding window |
| ML | **Disabled initially**. Daily SHORT/SWING/LONG/GROWTH models are ineligible. Any later intraday model gets its own labels, costs, held-out calibration and version |
| BUY/WAIT/HOLD/SELL semantics | BUY = admitted new-entry setup; WAIT = no admitted entry; HOLD = valid existing position; SELL = held-position exit/invalidation. A broken stop takes precedence over model wording |

Quantity is unavailable until an account/risk budget and buying-power constraints are known. For simulation, bind position size to the risk budget, maximum position value and executable liquidity; do not size from the displayed strength score. Standard ATR calculation and cross-session warm-up must be specified consistently in replay and live calculation; tag overnight gaps and do not mix extended-hours bars into the regular-session baseline.

Do not invent a fused-score cutoff such as “BUY at .70” for this new horizon. The first profile has explicit deterministic admission rules and an **unmeasured probability**. If the UI requires a numeric field, use a nullable value and a rule-based status rather than fabricated confidence.

Limit prices do not establish that an order filled. Model fills, nonfills and latency explicitly; a touched price in a 5-minute candle does not guarantee an executable fill. [SEC limit-order explanation](https://www.sec.gov/answers/limit.htm) Frequent trading costs can materially erode small-move returns, and account settlement/margin permissions must be resolved from the broker/account rather than inherited from generic defaults. [FINRA intraday trading guidance](https://syndication.finra.org/content/frequent-intraday-trading-understanding-basics)

### 7.2 Finding a better intraday profile

Freeze the baseline first. Compare **5m trigger/15m context versus 15m trigger/1h context** if measured processing delay prevents the 5m profile from being timely. A slower profile requires its own frozen expiry/management policy; it is not a license to send expired 5m signals.

Evaluate limited candidate families separately:

| Hypothesis | Candidate values | Keep fixed during this comparison |
|---|---|---|
| Trend selectivity | ADX 15 / 20 / 25 | Setup definition, risk, costs and exits |
| Activity filter | Same-time RVOL 1.2 / 1.5 / 2.0 | Liquidity universe and trigger |
| Exit economics | Target 1.5R / 2R / 2.5R; compare timeouts 60/120 minutes in a later test | Entry policy and sizing |
| Opportunity window | Opening continuation versus later session, evaluated as separate cohorts | No post-hoc deletion of losing time blocks |

Spread, timestamp validity, account eligibility and stop integrity are quality/risk constraints, not knobs to relax to improve a backtest. Include spread, slippage, fees and impact assumptions in the candidate fingerprint. Stress costs and delay; record whether improvement survives. Keep breakouts, mean reversion and news-event trading as later separately evaluated strategy families.

## 8. Implementation plan

### Stage 1 — Correct configuration and research contracts

1. Make ML-weight calibration evaluate the production calculation. Extract the runtime AUC ramp → cap → floor → disagreement-cut fusion into one shared function and use it in `calibrate_ml_weight` and `/ml-weight-validation`. Keep the floor-after-cap ordering. Scope the sweep to the styles the global cap governs, and fire at the effective BUY threshold. Validate per-style override types/ranges and threshold ordering. A per-style `ml_enabled` flag for deliberate disabling is a separate, later feature, not part of this fix.
2. Unify outcome eligibility for all calibration writers/readers; preserve evidence-status exclusions. Introduce typed tuning metrics and effective-incumbent comparisons.
3. Define the horizon contract and versioned policy registry. Persist immutable policy and feature-cutoff references for prospective candidates, including rejections.
4. Reconcile BUY/SELL boundary equality and daily session windows. Keep older outcome versions intact.

Owners: `services/signal-engine/src/generators/signals.py`, `api/calibration.py`, `api/signals_shared.py`, outcome eligibility, and ML label/calibration modules. Existing threshold values remain comparison baselines until validated changes are selected.

### Stage 2 — Add intraday data and immutable candidate storage

1. Extend `SignalHorizon` and API schemas with `INTRADAY` using an explicit PostgreSQL enum migration and compatible rollout. Do not rely on `create_all()` to alter an existing enum.
2. Keep the existing daily signal row as a display snapshot if useful, but add immutable intraday occurrences keyed by symbol, horizon, setup, trigger-bar close and policy version. Current daily upserts and first-BUY/SELL snapshots are insufficient for multiple same-session setups.
3. Add an outcome schema with timestamped entry/exit, session ID, policy/model version, planned and actual fill basis, costs, barriers, invalidation and unresolved reasons. Do not encode “intraday” as `hold_days=0` in the daily evaluator.
4. Reuse authoritative bar provenance, completed-bar preparation, session calendars and measured scan-phase infrastructure. A provider correction supersedes evidence through a recorded revision, not a silent rewrite of an already-delivered candidate.

Owners: `shared/db/models.py`, migrations, signal persistence/routes, `rzy_data.py` / bar-authority infrastructure. Reuse reusable infrastructure while keeping RZY and AI Signals identities and outcomes separate.

### Stage 3 — Generate and show research-only intraday signals

Implement the baseline as a separate evaluator operating only when a completed trigger bar arrives. Cache shared context; avoid adding per-symbol external requests every minute. Record missing/invalid inputs and all eligibility results.

Add Intraday to the stock-detail AI Signals selector and relevant scan/watchlist views, with “same session”, trigger close, expiry, context timeframe, status and evidence quality. Audit every hardcoded SHORT/SWING/LONG/GROWTH list. Unknown styles must return a validation error, not silently fall back to SWING. Keep intraday out of daily ML, daily outcome and generic consensus calculations unless the consumer explicitly supports it.

### Stage 4 — Replay, shadow capture and delivery

Use executable quotes where available. If only OHLCV exists, label results bar-based approximations and preserve ambiguous same-bar stop/target paths. Use finer data or report conservative/bounded outcomes; do not assume target-first. Replay must observe revision and availability timestamps, otherwise corrected historical bars can leak information unavailable live.

Run shadow capture before alerts. Reuse the independent outbox pattern, with fresh checks at send time: exact candidate identity/policy, expiry, cancellation, current setup/position eligibility, opt-out and send-once behavior. Position exits outrank new entries. A missed session-end exit due to a halt remains unresolved/overnight exposure; never claim a guaranteed flat close.

Live broker routing is a later capability. Research signals and email delivery do not prove an executable or filled trade.

### Stage 5 — Promote only after measured validation

Use whole-session chronological splits; purge samples whose realized holding windows overlap a boundary and embargo according to the maximum horizon. For daily models, fold lengths must support the longest evaluated holding window. For intraday, split by dates and retain multiple market conditions; account for correlated symbols on the same date.

Suggested initial operational floors: at least **200 completed eligible intraday research trades across 60 sessions and 20 symbols**, plus a final untouched period and subsequent forward shadow evidence. These are screening floors, not a guarantee of statistical sufficiency. Require positive net expectancy on held-out and forward samples, improvement against the frozen incumbent/simple baseline, and an uncertainty estimate clustered by session. Reject a winner dependent on one symbol, one day or optimistic fills. Stress costs at 1.5× and observed delay tails; disclose any regime without adequate coverage.

A win-rate target alone is not a promotion rule. Require acceptable drawdown, tail loss, trade frequency, liquidity capacity and data completeness. Risk limits must be fixed before evaluating candidates. Preserve rejected experiments and avoid retuning on the untouched period.

## 9. Acceptance checks and rollout gates

| Area | Required evidence |
|---|---|
| Existing horizon regression | Default/override precedence, equality boundaries, canonical regimes, BUY/HOLD/WAIT/SELL ordering and invalid override inputs. Calibration parity: for the same stored inputs and candidate cap, the calibrator's weight equals the runtime weight (including a 0 cap with a positive-AUC model, which must still yield the floor) |
| Calibration integrity | Same eligible cohort for fitting/reporting; policy/model/price-basis versions; fixed incumbent; missing outcomes excluded with counts; split by session without holding-window overlap |
| Intraday time contract | Completed 5m/15m alignment, DST/holiday/early close, session VWAP reset, warm-up, future/late quotes, expiry and timestamped outcome resolution |
| Position state | New entry versus held position, stop integrity, no widening, session flattening, halted/unfilled exits and account constraints |
| Execution/replay | Costs, gaps, nonfills, stale quotes, ambiguous same-bar barriers, corrections observed after the original signal and survivorship coverage |
| Delivery | Concurrent senders, cancellation/expiry races, retries, opt-out and exact candidate identity; no daily 72-hour TTL or two-hour cooldown inherited by accident |
| UI/API | New selector, stable symbol changes, visible unavailable/research states, no invented win probability, no fallback to SWING, existing horizon behavior preserved |
| Performance | Close → ingest → evaluation → queue → provider acceptance timings; p95/p99 and missed candidates in the denominator; provider acceptance distinguished from inbox delivery |

Keep the first implementation in shadow mode until the post-fix 457/458 measurements show that the proposed freshness window can be met. Deployments must respect scheduled evidence captures. The previously unexplained 43 disappearing RZY signals remain an investigation item; this plan does not declare them resolved or attribute them to AI threshold tuning.

## 10. What this review establishes

- Four current horizons are already parameterized, but the live overrides, shared downstream gates and inconsistent window/metric meanings need consolidation.
- Runtime thresholds and eight deployed source files were inspected read-only. The resolved evidence basis currently does not support a verified optimum.
- The offline witness reproduces the zero-cap/floor interaction and confirms strict decision boundaries. The interaction is a calibration/runtime mismatch: the floor is intended, but the calibrator never simulated it. **123 focused signal-engine tests passed** (original report); their passing does not cover or repair the finding.
- An explicit intraday baseline, candidate tests, data/schema/API/UI dependencies and promotion criteria are specified here. **No intraday implementation or threshold change has been made.**

Reproduce the offline inspection after a captured `runtime.json` is present:

```bash
python3 docs/audits/evidence/2026-10-21-ai-signals-horizons/inspect_thresholds.py
```

The runtime capture script is intended to execute on the existing Docker host with read access. Its SQL is aggregate-only and uses read-only transactions. The saved capture is a timestamped observation, not a promise that Redis overrides remain unchanged.

## Review addendum (October 21, 2026)

An independent source review of this report changed two things. It found no other disagreement with the report's claims.

1. **The zero-cap finding is reclassified** from a precedence defect to a calibration/runtime mismatch (§3.4). The floor-after-cap ordering is deliberate, and the proposed fix ("honor zero; enforce weight ≤ cap after floors") would have removed the protection it provides. The chosen Stage 1 fix instead makes calibration evaluate the production calculation (§8 Stage 1, item 1). Deliberate per-style disabling is recorded as a separate `ml_enabled` feature.
2. **The evidence-population mismatch is recorded with sources** (§4 A), including the third population used by `calibrate_ml_weight`.

Re-verified by the review: the cap/floor ordering and the calibrator's simplified fusion, at the lines cited; the missing evidence filter in `outcomes_calibrate_apply`; the strict BUY boundary; the offline inspection output (reproduced: SWING weight .15 at global cap 0, and the §3.2 effective thresholds). **Not re-verified, and attributed to the original report:** the 123 passing tests, the eight deployed-source hash matches, and all production counts (24,434 resolved outcomes, the evidence-status and regime breakdowns, and the 106 of 197 SWING rows).
