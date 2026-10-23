# Options, off-exchange activity and AI Signals: second audit

Date: October 22, 2026 (America/Los_Angeles). Reviewed checkout: `994e967be802ee17c9d0e6046ef8e74129974d5e`.

> **Status update (later on October 22, 2026):** an independent review re-checked R01–R10 against HEAD `b997cadb` and confirmed all ten, with four corrections (R01, R03, R07 evidence, R10). R01–R07 are now **fixed locally and not deployed**; R08–R10 are open and R11–R14 are pending review. See [section 9](#9-review-corrections-and-resolution-status). Sections 1–8 are kept as originally written.

The recent fixes materially improve the system, but they do not yet establish complete data visibility, safe automatic calibration, or an executable intraday strategy. **Prioritize failure visibility, contract identity and calibration evidence before tuning for higher win rates.** Keep the intraday horizon in research/shadow mode until replay and delivery evidence meet the gates below.

This is a fresh source and behavioural audit, not another implementation pass. No application settings, trading rules, databases, services or production deployments were changed. Pre-existing changes to deployment/backup scripts and their proposal were left untouched.

## 1. Scope and evidence boundaries

Reviewed the stock detail Options Flow card, Market Pressure/off-exchange panel, chain adaptation, session/expiry scope, premium/turnover/whale accounting, snapshots, premarket composition text, game-plan/strategy/calculator interfaces, AI Signals profiles and threshold precedence, ML fusion and calibration promotion, and the proposed intraday horizon. The RZY bar-authority and sender work is a dependency for intraday delivery; this audit does not replace the pending session comparison or independently re-audit every RZY state transition.

Evidence in this audit:

- Current source and targeted behavioural tests, including the Phase B changes already committed locally.
- [Executable offline witnesses](evidence/2026-10-22-feature-reaudit/probe.py), [results and source hashes](evidence/2026-10-22-feature-reaudit/probe-results.json), [UI/calculator witnesses](evidence/2026-10-22-feature-reaudit/ui-witness.test.tsx).
- [Fresh static threshold inventory](evidence/2026-10-22-feature-reaudit/threshold-inventory.json). This includes literal configuration from generation, calibration, options routes, alert scheduling, paper trading, ML training and outcome horizons. It is not a snapshot of effective production overrides.
- Prior [options audit](2026-10-21-options-dark-pool-and-dominance-review.md), [Phase A–C plan](../2026-10-21/options-f2-f8-dominance-plan.md), and [horizon review, including its corrections and ML dry-run addendum](2026-10-21-ai-signals-horizon-thresholds-and-intraday-plan.md).

**Production boundary:** no fresh production request, database query, browser session, release hash check or capture-job inspection was performed in this audit. Phase A's live status and Phase B's held status are attributed to the recorded rollout notes. Historical runtime keys, outcome counts and dry runs are dated evidence, not asserted to be today's live configuration. No actual loss, newly occurring outage or profitability improvement is established by synthetic witnesses.

The review covers correctness, research methodology and product behaviour. It is not a penetration test, infrastructure capacity audit, independent reviewer approval, or a complete broker execution certification.

## 2. What is fixed, and what is still incomplete

| Area | Re-audit result |
|---|---|
| F1 null-premium outage | Fixed in current source. Ranking does not sort null premiums; failed reads of every expiry return a failure state. Regression tests pass. |
| F2/F3 directional wording and rounded composition | Stock card uses exact volumes and descriptive labels. Phase B adds the exact backend classification and versioned snapshot wording. Legacy `sentiment`/`cp_ratio` remain intentionally compatible; downstream consumers must distinguish them. |
| F4 source session and expiry scope | Reads pin each slice to one source session. Historical/current expiry lists are separated and time-dependent scope is applied after cache lookup. Product-specific expiry rules and browser ageing remain gaps. |
| F5 selection and pressure | Counts/rules and measured pressure denominator are disclosed. Call/put callouts are symmetric. Selection ties, mixed-time pressure inputs and ratio symmetry need further work. |
| F6 premium/turnover | Chain marks no longer become paid premium or whales. Adjusted-root turnover is withheld on the flow path. Identity protection does not extend through the calculator/game-plan path. |
| F7 print times and side | Future and timezone-ambiguous prints are quarantined from alert/persistence evidence. UI shows time status and labels NBBO side as estimated. Fetch failures and ageing remain incomplete. |
| F8 relative baseline | Query failure, insufficient history and valid baseline are distinguished. Population is explicitly captured prints. First/last timestamps do not prove ingestion continuity. |
| Phase C dominance summary | Still absent. Volume composition exists; one consistent volume/OI/traded-premium/aggressor summary does not. |
| Game-plan time-dependent test | Fixed-clock change is in HEAD; all five component tests pass in this audit. |
| ML weight replay | Recent fix shares the AUC-ramp/cap/protective-floor/disagreement calculation with production. GET preview and applying calibrator share the sweep. This closes the earlier blend-vs-cap mismatch, not the remaining evaluation limitations. |
| Zero ML cap | Protective floor is deliberate, not a newly discovered precedence bug. Probe still yields SWING weight 0.15 at cap 0 with adequate AUC. An explicit `ml_enabled` switch would be a separate feature. |
| Intraday horizon | Specification only. `SignalHorizon` still contains SHORT, SWING, LONG and GROWTH. No fifth horizon was implemented by this review. |

## 3. Findings requiring action

P1 means fix before relying on the affected path for production calibration or actionable trade calculations. P2 means a material correctness, visibility or research-quality gap. No P0 incident is established by this local audit. Findings are deliberately narrower than a claim that every affected input is present in production.

### R01 — P1: upstream dark-pool failures still disappear as empty activity

**Evidence:** [unusual_whales.py](../../services/market-data/src/services/unusual_whales.py), `get_dark_pool_prints` at line 1779, catches upstream exceptions and returns `[]`. [routes.py](../../services/market-data/src/api/routes.py), `get_dark_pool_prints_route` at line 4506, maps that result to `available:false, reason:no_data`. [MarketPressurePanel.tsx](../../frontend/src/components/MarketPressurePanel.tsx) returns null if every request completed without usable data. Its existing test explicitly requires that disappearance.

The offline probe injects an upstream 503 exception into the real provider function and receives `[]`. This failure will not reach SWR's error path as an HTTP request failure. A working gateway therefore does not establish a working feed. Disabled, empty, rate-limited, malformed and failed are not distinguishable at this interface. Partial unavailable components also disappear when another component succeeds.

**Improve:** typed provider result with `status`, `reason`, `fetched_at`, `source_as_of` and `retry_after`; preserve those states through the route and render each panel's state. Keep a valid cached sample only with an explicit stale/error banner. Update the existing empty-panel test to the intended visible-state contract.

**Acceptance:** provider timeout, 429, 503, disabled entitlement, successful empty response and malformed payload produce different visible states; other panels remain usable; retry targets the failed component. Test the full provider → route → UI boundary, not only a rejected frontend promise.

### R02 — P2: cached results suppress failures, and displayed freshness freezes

**Evidence:** [OptionsFlowCard.tsx](../../frontend/src/components/OptionsFlowCard.tsx) checks `error` only inside `!flow`. Its saved UI witness renders valid cached flow plus a 503 and finds neither error text nor Retry. The flow request in [stock/[symbol].tsx](../../frontend/src/pages/stock/[symbol].tsx) and Market Pressure requests disable focus revalidation and specify no periodic refresh. [optionsFlowStatus.ts](../../frontend/src/lib/optionsFlowStatus.ts), `printTimeText`, formats the response's fixed `age_minutes`; it does not derive elapsed age from the execution timestamp. Backend cache expiry does not push a new payload into an already-open browser.

Consequences: a long-open tab can retain pre-expiry eligibility, stale NOPE readings under “updates roughly every minute,” and an age such as “1 min ago” indefinitely. Reopening/remounting may refresh; that is not continuous freshness.

**Improve:** render error independently of cached data; label last successful retrieval; refresh according to each feed's cadence while the panel is visible; compute age with a client clock and enforce freshness separately from display. Re-evaluate contract eligibility at the last-trading instant even before the next network response.

**Acceptance:** success → failed refresh retains data with a warning and Retry; advancing a fake clock changes age and expiry eligibility; unchanged data does not claim a new source time; no old-symbol data leaks after navigation.

### R03 — P2: unknown moneyness is displayed as OTM

**Evidence:** [uw_option_chain.py](../../services/market-data/src/services/uw_option_chain.py), `_to_chain_row` at line 82, initializes `itm=False` and calculates it only if `spot` exists. The flow route calls `_uw_option_chain(..., as_of=source_session)` without spot. The card maps false to OTM. Probe: a strike-100 call is false without spot and true with spot 120.

This is a definite missing-input path, not proof that every displayed contract is actually in the money. It presents an unmeasured field as measured.

**Improve:** nullable moneyness with underlying reference price and timestamp. For a settled-session card, use the same session's reference spot or state unknown. Do not silently mix today's spot with historical IV/volume without identifying that distinction.

**Acceptance:** missing spot renders “unknown”; calls and puts on both sides of strike and exactly at strike are tested against a stated reference time.

### R04 — P2: missing volume, OI and IV become measured zeros

**Evidence:** the same adapter uses `int(value or 0)` for volume/OI and `0.0` for missing IV; route frames also use `fillna(0)`. Probe confirms nulls become zeros. Thus “100% calls,” low-OI activity and 0% IV can include unknowns. Archive pagination completeness does not establish field-level completeness.

**Improve:** carry nullable measurements and validity counters through aggregation. A side share is a complete-scope statement only when its required volume population is measured. Otherwise show an observed subtotal and missing-field coverage. OI needs its own as-of date. Unknown IV is not zero volatility; missing OI is not a new listing.

**Acceptance:** actual zero, missing, malformed and negative values remain distinct; removing put volume cannot silently turn a mixed chain into a complete all-call reading. Record valid/total contract counts for each metric.

### R05 — P1: adjusted-contract protection ends before strategy calculations

**Evidence:** `contract_multiplier_status`/`unusual_premium_fields` protect flow turnover, but [routes.py](../../services/market-data/src/api/routes.py), `_options_chain_rows` at line 3911, discards `option_symbol` and multiplier/deliverable identity. Probe confirms that an adjusted-root row loses those fields. [options_strategies.py](../../services/market-data/src/services/options_strategies.py) uses `_CONTRACT_MULTIPLIER=100`; [optionsGainLoss.ts](../../frontend/src/lib/optionsGainLoss.ts) also uses a fixed contract size and accepts no deliverable identity.

If an adjusted contract reaches these paths, displayed payoff, collateral or coverage can be wrong. The flow card withholding turnover does not protect the adjacent strategy calculator. Root matching itself is an assumption, not an authoritative deliverable record. This audit did not measure how many current archived contracts are adjusted.

**Improve:** retain canonical contract ID, premium multiplier, settlement type and deliverable definition end-to-end. Treat adjusted/unknown products as unsupported for automated payoff/coverage until their terms are resolved; a manually supplied standard-contract calculator must state that assumption. Do not treat “100-share deliverable” and “premium multiplier 100” as interchangeable concepts for all products.

**Acceptance:** adjusted-root fixture with usable bid/ask is refused by all automatic strategy/calculator entry points unless explicit supported terms are supplied. Standard equity contracts retain existing results. Test stock-plus-cash deliverables and covered-call share requirements separately.

### R06 — P2: a recorded held fill disappears when its quote is missing

**Evidence:** `buildGainLossRows` in [optionsGainLoss.ts](../../frontend/src/lib/optionsGainLoss.ts) rejects `quoted.premium <= 0` before checking the user's fill. Saved witness: strike 100, valid paid premium 5, no quote, target 120 returns no row. This can hide a held position precisely when feed data is unavailable.

**Improve:** resolve the held fill first. Entry-basis expiry scenarios can use a valid fill without a current quote; current mark-to-market must remain unavailable. Include an explicitly entered held contract even if absent from the current chain, with contract identity and expiry verified.

**Acceptance:** missing quote + known fill retains expiry P/L; missing quote + no fill is unpriced; no manufactured current value; missing chain rows and expired held contracts are covered separately.

### R07 — P2: the unusual-contract shortlist is still order-dependent on ties

**Evidence:** flow first performs `df[mask].sort_values('volume', ascending=False).head(3)` for each side/expiry, then `rank_unusual` adds the deterministic order. The latter cannot recover a tied row discarded earlier. Probe: four equally active strikes yield 100/101/102 in one input order and 101/102/103 in the reverse order.

**Improve:** apply the complete stable ordering before every truncation, using unrounded volume/OI and a canonical contract ID as the final tie-break. The current displayed `vol_oi` is rounded, so it should not be the ranking source of truth.

**Acceptance:** permuting provider rows leaves eligible/kept/displayed IDs unchanged, including ties at both the per-side and overall cutoff.

### R08 — P2: pressure points mix time scopes and are not fully side-symmetric

**Evidence:** `get_options_flow` combines settled-session volumes with `_options_flow_gex_component`, which reads GEX and cached live prices without retaining their individual times in the pressure payload. `compute_options_pressure_score` gives ratio 2.0 ten ratio points and reciprocal 0.5 twenty-five points. Its asymmetry fix equalizes only the extreme endpoints, not reciprocal ratios throughout the scale. Probe confirms those values.

The UI correctly says points, not probability, and exposes the measured component set. Those improvements do not make different observation windows comparable or establish the predictive value of the weights. `pressureShare` still uses the variable denominator for badge emphasis.

**Improve:** separate chain activity intensity from current gamma proximity, or require a documented time-alignment policy with per-component timestamps. If the intended metric is side-neutral imbalance, use absolute share deviation or a symmetric bounded log-ratio, with explicit zero-side rules. Version the changed score and evaluate it before using it in trading decisions. Show measured components visibly, not only on hover.

**Acceptance:** reciprocal ratios receive equal imbalance points; historical volume plus stale/live GEX cannot appear as a same-time measurement; comparison requires both matching components and compatible windows. Do not advertise the score as conviction or a validated return predictor.

### R09 — P2, conditional on supported product: expiry scope assumes one equity calendar

**Evidence:** [options_scope.py](../../services/market-data/src/services/options_scope.py), `last_trading_instant`, accepts only an expiry date and uses NYSE regular-session close. It cannot distinguish AM settlement, PM settlement, product-specific hours or last-trading dates. Its comment that the rule can never admit an expired index contract is too broad. The code explicitly recognizes index roots elsewhere, but this audit did not establish that any such product is currently exposed to users.

**Improve:** either explicitly restrict automatic candidates to verified supported equity-option products, or key last-trading/settlement rules by contract class. Unknown settlement must not default to current eligibility. Keep historical evidence available.

**Acceptance:** regular equity, early-close day, holiday, DST, AM/PM index classes and unsupported product fixtures. An AM-settled contract cannot remain a new candidate until that afternoon. Use current exchange specifications; do not encode an old Thursday-only rule for every AM series.

Exchange documentation distinguishes AM and PM settlement and product-specific trading schedules: [Cboe SPX](https://www.cboe.com/tradable_products/sp_500/spx_options?db3id=5baf3e5f209db-ba88b624-eef), [Cboe AM-settled weekly notice](https://www.cboe.com/notices/content/?id=61537). The latter describes a proposed November 9, 2026 rollout subject to regulatory review; it is not assumed active on this audit date.

### R10 — P2: a sufficient captured-print baseline can hide ingestion gaps

**Evidence:** `_dark_pool_baseline` in [scheduler.py](../../services/market-data/src/services/scheduler.py) returns count, median, window and first/last timestamps. `baseline_status` accepts sufficient count plus positive median. Neither measures missing polling intervals or truncation of the last-50-print provider window. The plan's “track ingestion gaps” acceptance is therefore only partially implemented.

**Improve:** persist poll success/failure, returned count, oldest/newest event time, pagination/limit saturation and event deduplication/correction identity. Expose sampling quality independently of sample count. A high print count from a few busy bursts is not continuous tape coverage.

**Acceptance:** a synthetic multi-hour outage with many prints before/after retains `baseline_status` but also shows degraded sampling; query failure remains distinct; re-delivery/corrections do not inflate counts. Exact first/last timestamps alone must not pass continuity checks.

### R11 — P1: first ML calibration can promote negative validation returns

**Evidence:** [calibration.py](../../services/signal-engine/src/api/calibration.py), `_ml_weight_sweep`, validates when candidate fired count is at least 15, accuracy exists, and `(prev_cap is None or candidate_ev > baseline_ev)`. The no-incumbent branch bypasses return improvement entirely. The actual applying helper acts on that decision.

The offline witness executes the complete pure sweep and real weight helper with synthetic observations: 30 validation signals, **0% hit rate, −10% mean return, `would_apply:true`**, with no prior cap. No production calibration was invoked. An existing incumbent avoids this particular bypass, but merely outperforming a negative incumbent still does not establish positive net returns.

**Improve:** a first run is research-only until it passes the same explicit evidence/return gates as later runs. Require an executable net-return objective, uncertainty bounds and risk limits; compare with actual effective default policy and a no-trade/cash reference where relevant. Keep an explicit manual experimental mode separate from automatic promotion.

**Acceptance:** the saved negative-return witness must become unpromotable; absence of a Redis key cannot weaken quality gates; costs can invalidate an apparent gross gain; no write occurs when evidence is insufficient.

### R12 — P1: row-level chronological splitting is not independent validation

**Evidence:** the sweep sorts observations by signal date and splits at `int(n * 0.7)`. The witness's 100 different symbols all share one signal date: 70 become train and 30 validation, with identical date windows. Multi-day label intervals are not purged across the boundary. Related threshold searches use similar splitting.

**Improve:** split by whole sessions; retain each observation's actual entry and exit timestamps; purge training labels overlapping validation and embargo where needed. Group uncertainty by trading day and account for correlated symbols. Freeze a final untouched period, track all candidate trials and then run forward shadow capture. This also matters when “optimal” merely wins among many tested settings.

**Acceptance:** train/validation dates are disjoint; no training outcome reaches into validation; duplicate/correlated observations do not masquerade as independent sessions; insufficient independent history returns no selection. A 70/30 row count alone is not sufficient evidence.

### R13 — P1: tuning writers still use inconsistent evidence populations

**Evidence:** `outcomes_calibrate_apply` selects resolved BUY outcomes without an `evidence_status` eligibility predicate. The watchdog also reads resolved outcomes directly. The ML sweep separately permits raw returns with unverified action completeness, reports that count, and does not make it a promotion blocker. The witness promotes with all 100 synthetic observations classified in that unverified category.

This re-confirms the prior audit's population mismatch; the fusion replay fix does not close it. The previous capture's zero verified outcomes is historical evidence, not a new count from this audit. Unverified is not synonymous with false, but it must not silently satisfy a stricter published-evidence claim.

**Improve:** one versioned eligibility policy for display, preview, threshold calibration, ML tuning and watchdog actions. Record rejected counts/reasons. Separate exploratory provisional/raw studies from production promotion. Validate forward entry timing, corporate actions, market calendar, costs, policy version and outcome maturity.

**Acceptance:** identical records produce identical eligibility decisions across readers/writers; an ineligible row cannot influence an applied parameter; an all-unverified dataset can produce diagnostics but cannot be labelled verified validation.

### R14 — P2: the tuning objective and metric names still do not match strategy returns

**Evidence:** the ML sweep chooses its train winner by hit rate, then gates on mean gross price return. It has no minimum fired count for choosing the train winner. It sees historical BUY rows, not all candidates, and explicitly excludes later adjustments/gates. A cap that would create new BUY signals cannot be fully evaluated from a BUY-only sample. `outcomes_calibrate_apply` compares against code defaults instead of all resolved live override precedence. The watchdog records `win_rate_14d` as a fraction in `validation_ev_pct` (around line 2844).

**Improve:** immutable all-candidate snapshots and complete replay from signal generation through eligibility, fill, exits and costs. Tune a preregistered net-expectancy/risk objective with adequate train and validation activity; record trade frequency and capital usage. Use typed fields such as `hit_rate_fraction`, `mean_gross_return_pct`, `mean_net_return_pct`, `evaluation_window`, `policy_id` and `evidence_status`. Compare against the effective incumbent and log its provenance.

**Acceptance:** metric units cannot be confused; one lucky train trade cannot win a search; non-BUY observations are available for threshold changes; improving hit rate while reducing net expectancy fails the selected objective. The recent v2 dry run's rejection remains a reason to leave settings unchanged, not proof that the incumbent is best.

## 4. Call/put dominance: specification for every stock page

The question “which side dominates?” needs a stated measure and universe. **Volume dominance does not determine whether investors bought or sold the options, nor whether they opened or closed positions.** OCC explains why volume and open interest cannot be substituted for each other: [OIC general information](https://www.optionseducation.org/referencelibrary/faq/general-information).

Keep the agreed descriptive cutoffs: exact `calls / puts >= 1.3` → call-heavy; `<= 0.8` → put-heavy; otherwise mixed. These asymmetric cutoffs are an accepted product convention, not a measured trading edge. For all calls use “100% calls in observed volume; C/P undefined”; for all puts use “100% puts”; for zero measured activity use unmeasured. Missing one side is incomplete, not automatically all of the other side.

Build `options_dominance_v1` with separate rows:

| Row | Required population | Display and restrictions |
|---|---|---|
| Contract volume | One source session and selected expiry set, with field coverage | Call/put totals, shares, exact classification and scope. Show “nearest four expirations” rather than suggesting all listed options. |
| Open interest | Same contract scope, own documented OI date | OI totals and composition; no intraday-position-opening claim. |
| Traded premium | Deduplicated event price × size × verified premium multiplier in one window | Dollar shares and classified coverage. Chain midpoint/last-price turnover never fills this row. |
| Aggressor estimate | Comparable events plus quote/condition quality and a known denominator | Ask-call/bid-put versus bid-call/ask-put estimates, unknown share and multi-leg exclusions; never describe the estimate as known investor intent. |
| Estimated turnover | Optional separate research row, compatible chain scope | Clearly marked approximation; unsupported deliverables withheld. |

Every row needs `status`, `source`, `window_start/end`, `as_of`, `contract_scope`, `coverage`, `known/total`, `method_version` and freshness. A shared scope ID prevents joining unrelated sessions. Show disagreements instead of collapsing them into a bullish/bearish verdict. Retain `directional_intent:not_established` and `actionability:research_only` unless an independently validated model explicitly adds a different result.

For example, a stock can be call-heavy by volume and put-heavy by OI. That is a valid two-measure result. Call-heavy volume can include call selling; put-heavy volume can include put selling. Keep these fixtures in API and UI acceptance tests.

Do not invent a current MU/any-stock dominance report from old screenshots: this audit did not fetch a current market sample. The implementation must work for any supported stock and honestly show unsupported/unavailable cases.

## 5. AI Signals thresholds and horizon improvements

The fresh static inventory confirms the existing profiles. These are **code defaults**, not current resolved runtime settings:

| Horizon | BUY bull / neutral / choppy / bear | HOLD bull / neutral / choppy / bear | Default ML cap / protective floor | Default ADX minimum |
|---|---|---|---|---|
| SHORT | .63 / .62 / .65 / .68 | .46 / .47 / .50 / .52 | .30 / .10 | 27 |
| SWING | .72 / .72 / .74 / .76 | .50 / .50 / .54 / .56 | .65 / .15 | 15 |
| LONG | .60 / .62 / .65 / .70 | .46 / .46 / .50 / .54 | .45 / .12 | off |
| GROWTH | .60 / .60 / .65 / .68 | .45 / .45 / .50 / .52 | .60 / .20 | 12 |

Unknown uses neutral; risk-off uses bear. Default SELL is .35. BUY and SELL boundaries are strict (`>` BUY, `<` SELL); WAIT includes equality at SELL. BUY precedence remains watchdog → calibrated bull-baseline delta → defaults, bounded .55–.85. Per-style ML cap overrides global cap, then profile default; protective floor remains deliberate. Dynamic SELL bounds remain .15–.45. The old `high_vol` vocabulary must not bypass canonical regime handling.

The previous report inventories the remaining numeric gates: pillar counts, weekly alignment, breadth/high-volatility compression, RSI/stoch/ROC extension, earnings/news, confidence calibration bins, conviction alerts, paper stops/targets/partials and risk limits. The new machine-readable inventory preserves current literal defaults for comparison; values inside logic require the cited source and prior detailed table, not an assumption that this summary is every threshold.

**Yes, settings should be horizon-specific—but the entire lifecycle must be specific, not only the BUY cutoff.**

| Horizon | Improvement to validate | Evidence required |
|---|---|---|
| SHORT | Align short forecast, alert freshness, entry opportunity and exit timing; inspect the shared alert ADX >25 gate against generation's effective ADX | Next-session executable entries, costs, stale rejection, forecast at five sessions and separate actual exit outcomes |
| SWING | Reconcile effective threshold with ML floor/overconfidence/weekly filters; optimize net R per trade and capital-day, not hit rate alone | Ten-session forecast study plus complete swing lifecycle; sufficient independent regimes |
| LONG | Separate twenty-session forecast from longer portfolio management; avoid labeling it a validated twelve-month forecast | Longer holding outcomes, corporate-action and dividend treatment, drawdown/exposure, explicit rebalancing costs |
| GROWTH | Treat momentum strategy family separately from duration; preserve its special TA input in replay | Fifteen-session forecast, gap/earnings exposure, liquidity/slippage and faithful growth-specific fusion inputs |

Existing primary BUY outcomes use 7/14/28/14 calendar days while ML labels use 5/10/20/15 daily bars; user-facing and managed holds differ again. Store these as separate versioned contracts. Do not relabel historical outcomes when changing evaluation windows. SELL may mean exiting a held long, not an executable new short.

A higher hit rate alone does not imply better returns. With wins +0.5R, losses −2R and 70% wins, gross expectancy is `0.7×0.5 − 0.3×2 = −0.25R` before costs. Optimize net expectancy with drawdown, exposure, turnover, frequency and uncertainty reported. Scores and distance-from-neutral “confidence” are not calibrated win probabilities.

## 6. Intraday horizon: retain a bounded starting experiment

No evidence establishes “best” intraday parameters. Retain the prior plan's explicit initial candidate; do not inherit daily outcome timing or the 72-hour alert freshness allowance.

| Dimension | Initial research/shadow policy |
|---|---|
| Universe | US liquid ordinary equities/ETFs, price ≥$5, prior 20-session dollar-volume average ≥$50M; exclude unsupported products/actions |
| Bars | Completed 5-minute trigger and completed 15-minute context, aligned as-of; 50 trigger/150 context bars of warm-up |
| Session | Entries open+15 minutes through close−60; maximum hold 120 minutes; flatten attempt close−10; exchange calendar handles early closes/DST |
| Trend | 5m EMA9 > EMA21 and price above session-reset VWAP; 15m EMA20 > EMA50, +DI > −DI, ADX14 ≥20 |
| Setup | Prior bar touches EMA9 and closes at/above EMA21; trigger closes above prior high; RSI14 50–70 |
| Activity | Same-elapsed-session relative volume ≥1.5 against prior sessions, at least ten valid comparisons; trigger dollar turnover ≥$500K |
| Entry quality | Two-sided quote ≤5 seconds old; spread ≤10 bps and ≤0.1R; no chase beyond 0.25R; missing quote = research-only |
| Freshness | Candle-close-to-entry eligibility under five minutes; target close-to-provider-acceptance p95 ≤120 seconds, measured end to end |
| Stop/target | Prior three-bar low minus 0.1 ATR; minimum risk distance 0.75 ATR, reject above 1.5 ATR; 2R gross target, net reward:risk ≥1.5 after costs |
| Risk simulation | 0.25% equity per trade; 0.5% total open risk; two positions, one per symbol; three entries/session; 0.75% daily loss including open P/L |
| Exit semantics | Stop overrides HOLD; target/time/session exits; no partial/trailing variation in baseline; 30-minute post-stop cooldown plus new setup |
| ML | Disabled for initial candidate; no invented ML score cutoff or published accuracy |

Require explicit enum/schema/API/UI changes; reject unknown horizon instead of silently defaulting to SWING. Store immutable timestamped candidates and multiple occurrences per symbol/session. Do not represent intraday by `hold_days=0` or the daily upsert key. Version setup, thresholds, inputs, rejected reason, quote/fill, costs and outcome.

Replay same-bar stop/target ambiguity conservatively, retain non-fills and late signals, and test halts, missing bars, split corrections, holidays and early closes. Full replay and incremental evaluation must agree. Reuse proven outbox deduplication, but maintain a separate AI Signals strategy identity from RZY.

Only then test a small predeclared set of alternatives (ADX 15/20/25, relative volume 1.2/1.5/2.0; later target/timeout variants). Avoid a large Cartesian search. If measured delivery cannot support five-minute entries, test a distinct slower-bar strategy rather than extending the stale-signal allowance. Two hundred eligible trades across sixty sessions and twenty symbols are proposed operational floors, not proof of an edge; use day-clustered uncertainty, held-out and forward net returns, cost stress and drawdown limits.

## 7. Implementation order and industry-grade extensions

| Order | Work package | Release gate |
|---|---|---|
| 1 | Close R01–R06: provider/UI states, freshness, nullable measurements, contract terms and held-fill preservation | Behavioural route/UI witnesses now assert the corrected result; no false zero/OTM or unsupported payoff |
| 2 | Close R11–R14: evidence eligibility, first-run promotion, grouped/purged validation, typed metrics and full candidate capture | Read-only preview rejects negative/unsupported experiments; no automatic settings write until review |
| 3 | Finish Phase B residuals R07–R10 and build Phase C dominance | Single-session scope, deterministic selection, product-aware expiry, sampling coverage and consistent rows |
| 4 | Add intraday shadow pipeline and replay | Correct timing, complete candidate ledger, no overnight residual hidden by a flatten request, independent sender evidence |
| 5 | Calibrate daily/intraday strategies through held-out and forward studies | Net return/risk improvement with uncertainty and realistic costs; separately reviewed promotion |

Keep market-data restarts coordinated with the actual capture completion marker. This report does not assert the October 22 capture has completed or authorize replacing the operational measurement with old evidence. Diagnose the previously unexplained 43 disappeared RZY signals before treating rebuild invariance as proven.

Useful next features, in dependency order:

1. **Data-quality dashboard:** entitlement, source and event clocks, exchange-session age, poll gaps, chain-field coverage, feed failures, duplicates/corrections, release and policy identity. This makes errors distinguishable from quiet markets.
2. **Contract master and quote-quality layer:** settlement/deliverable metadata, fresh executable quote qualification, spread/size checks and explicit historical-research mode. Share this across flow, calculators, game plans and portfolio risk.
3. **Event-level options timeline:** consistent windows, aggressor-estimate coverage, multi-leg grouping and correction-aware premium totals. Do not infer one whale from many small trades summed to $500K; future `event_premium` wiring needs event identity and grouping semantics.
4. **Off-exchange price zones:** deduplicated captured events, age and capture coverage, estimated side and uncertainty. Prefer “off-exchange” unless venue identity is verified: off-exchange execution includes ATS and non-ATS activity ([FINRA](https://syndication.finra.org/content/look-over-counter-equities-trading)).
5. **Position-aware risk/scenarios:** known fills, shares and deliverables; expiry payoff versus pre-expiry scenario valuation clearly separated; portfolio Greeks only after the model/quote basis is defensible; earnings/dividend/assignment exposure explicitly represented.
6. **Research-to-production registry:** immutable candidates, policy versions, all trials, walk-forward results, deployment gate and rollback criteria. Add calibrated probability reporting only after observed out-of-sample reliability is measurable.

## 8. Validation and remaining uncertainty

Fresh checks at this checkout:

| Check | Result |
|---|---:|
| Market-data scope/premium/pressure/dark-pool/snapshot/premarket tests (9 files) | 133 passed |
| Market-data game-plan/strategies/options-alert tests (9 files) | 125 passed |
| Signal-engine generation/fusion/calibration/eligibility/watchdog/tuning tests (9 files) | 429 passed |
| Frontend cards/status/review tests (6 files) | 40 passed |
| Frontend calculator/chart tests (3 files) | 134 passed |
| Additional saved UI/calculator witnesses | 3 passed, confirming current gaps |
| Offline Python witnesses | All assertions passed; results saved |

Total: **861 existing tests plus three additional UI witnesses = 864 passing tests**, with additional Python witness assertions. These are selected relevant tests, not the complete repository suite. Existing deprecation warnings concern Pydantic config, FastAPI regex, Pandas fillna downcasting and Vite CJS. No new application code was changed; no build, fresh production browser check or new PostgreSQL integration run was claimed.

The witness tests deliberately pass when the present defect is observable. A fix needs inverted expected behaviour in the maintained suite. Some legacy tests inspect source strings; those establish wiring text, not runtime correctness. The provider failure and full sweep probes execute function bodies with explicit fake boundaries; the UI witnesses render real components. The Python sweep probe's coverage verdict is injected and clearly marked synthetic.

Current production impact remains to be measured for missing fields, adjusted contracts, provider failures, stale browser sessions and calibration eligibility. No parameter change or performance promise follows from the passing tests. The next review should verify corrected acceptance cases and deployed source identity, then measure forward outcomes under the unchanged or explicitly versioned policy.

## 9. Review corrections and resolution status

Added after an independent review of R01–R10 on the same day. That review read the source at HEAD `b997cadb`, eleven commits after the audited checkout. Only `uw_option_chain.py` (provider exclusions) and `scheduler.py` (the capture job) changed among the audited files, and neither change affects a finding. R11–R14 were not part of that review and **remain pending review**.

### 9.1 Corrections to the findings

| Finding | Correction |
|---|---|
| R01 | The panel does show an HTTP failure when another section has loaded (`MarketPressurePanel.tsx`, the `failed` banner in the populated branch). What disappears is a section answered with HTTP 200 and `available:false`, which is how every provider failure arrives. The finding stands with that narrower wording. Practical exposure: [the October 2 counter reading](2026-10-02-runtime-counter-reading.md) recorded 119 Unusual Whales 429s in 48 hours, not attributed to an endpoint. |
| R03 | Wider than reported. The full chain endpoint `get_options_chain` also read chains without any reference price, so the stock page's chain table never tinted an in-the-money strike. The game plan did the opposite: it passed *today's* price against a settled session's chain, which is the mixing that R03's improvement warns about. |
| R04 | Population context: the `OptionChainHistory` model notes record volume and OI 100% populated, and IV null for 52.4% of contracts (untraded ones), in a measured AAPL 2026-06-02 sample. The visible defect was therefore mostly IV: about half of a chain displayed `0%`. Malformed provider values are already coerced to NULL at capture (`_opthist_i`/`_opthist_f`), so at read time "malformed" and "missing" cannot be told apart. Only negative values are separately detectable. |
| R07 evidence | The saved probe re-implements the old `sort_values(...).head(3)` itself, so it reproduces the defect regardless of the route and cannot observe a fix. The rerun exercises the real route body. |
| R10 | **Exact re-delivery is already deduplicated.** `uq_dark_pool_print` covers (symbol, executed_at, price, size), and `_persist_dark_pool_prints` (scheduler.py) inserts with `ON CONFLICT DO NOTHING`. Re-polling the same provider window therefore does not inflate counts. Two problems remain: **(a) corrections.** A print the provider later amends (price, size or time) inserts a second row and the original stays; no cancel/correction state exists, so both count toward the baseline. **(b) identity.** The parser keeps 8 fields and no provider trade identifier, so two genuinely separate prints that share symbol, timestamp, price and size collapse into one row. That undercounts, at a rate set by timestamp precision. R10's acceptance should read: corrections and cancellations do not double-count, and distinct prints with identical fields are not merged, once a provider identifier is available. The gap-tracking part of R10 stands as written. |

### 9.2 Resolution status

Local working tree only. **Nothing has been deployed**, and deployment is held for approval.

| Finding | Status | What changed | Regression evidence |
|---|---|---|---|
| R06 | Fixed locally | `buildGainLossRows` resolves a matching fill before rejecting an unquoted strike. The row's expiry figures come from the fill, and `quotedPremium` is `null`, so the current mark stays unavailable (`unrealized()` returns null). The table says "current market value unavailable — no quote for this strike". A fill for a strike absent from the chain is still ignored, because nothing confirms that contract exists at the expiry. | `optionsGainLoss.test.ts` (7 cases), `gain-loss-held-unquoted.test.tsx` (2 renders) |
| R07 | Fixed locally | One `unusual_order_key` (volume, exact unrounded vol/OI, expiry, side, strike, contract symbol) is applied before the per-side cut and in `rank_unusual`. Unusual rows now carry `option_symbol`, and the stated rule names the tie-break. | `test_options_flow_selection_order.py`: forward/reversed, 25 shuffles across 4 expiries with both cutoffs binding, rounded-ratio ties, symbol tie-break. Sabotage: restoring the volume-only cut fails 3 of 4. |
| R03 | Fixed locally, with R04 | Moneyness is measured only against the underlying's daily close **for the chain's own session** (`session_reference_close`: exact `DATE(ts) = as_of`, no earlier bar, no live quote). Callers can no longer pass a spot (`spot` removed from `get_chain` and `_uw_option_chain`, including the game-plan calls). Rows carry `moneyness` (ITM/ATM/OTM, with ATM at exactly the strike) and nullable `itm`. Responses carry `moneyness_reference`. The flow card shows "unknown" and the chain table tints nothing without that price, and both name the session close when it exists. | `test_options_missing_inputs.py`, `options-missing-inputs.test.tsx` (flow card and the extracted `OptionsChainTable`) |
| R04 | Fixed locally | The adapter keeps volume/OI/IV as `None` when missing or negative, with per-field `field_status` and per-chain `field_coverage`. `fillna(0)` is removed from the flow route, the chain rows and the expirations rollup. The flow route reports `volume_coverage` and `composition_scope` (`complete` or `observed_subtotal`) plus `unusual_unmeasured`. A side with no measured volume is `null` (`cp_ratio`, `sentiment` and the pressure score are then null), and an all-unmeasured chain is `volume_unmeasured`, distinct from `no_volume`. Missing OI is excluded from both the unusual and the low-OI ("new listing") buckets. Max pain excludes unknown OI and reports `oi_coverage`. The snapshot job skips a chain with an unmeasured side (`volume_side_unmeasured`) instead of persisting a ratio. Cache keys were bumped (`options_flow:v5`, `options_chain:v3`, `options_expirations:v3`). | Same files. Includes "removing put volume cannot make an all-call reading", zero vs missing vs invalid, and no NaN in the JSON. Sabotage: restoring `fillna(0)` fails 5 tests; forcing OTM fails 2. |
| R01 | Fixed locally | `fetch_dark_pool_prints` returns a typed outcome: `ok`, `empty`, `disabled`, `rate_limited`, `unauthorized`, `timeout`, `upstream_error` or `malformed` (all rows unparseable or a non-list payload), with `fetched_at` (provider answer time, kept on a cache hit), `source_as_of` (newest print), `http_status` and malformed-row count. Failures are never cached. A last good sample (24 h) is returned only *beside* a failure as `stale: true`, never as the current answer. The route carries all of this; `reason` keeps `no_data` for a genuine empty answer only. `get_dark_pool_prints` stays as the scanner's wrapper (`[]` for every non-ok outcome). The panel shows every state, including a genuine empty answer and "not configured", and gives each failed component its own Retry. | `test_dark_pool_provider_states.py` (16: six outcomes pairwise distinct through the real provider and route, cache keeps fetch time, failures not cached, stale sample only beside a failure); `options-feed-states.test.tsx` (seven visible states, stale sample labelled, other panels usable, Retry targets only the failed component, no previous-symbol data after navigation) |
| R02 | Fixed locally | The flow card shows a refresh failure *with* cached data ("Latest refresh failed … Showing the data computed HH:MM ET", plus Retry). The flow payload carries `computed_at`, stored inside the cache so a cache hit never restamps it. A ticking clock (`useNow`) recomputes print ages from the execution time, the flow's computed age, and expiry eligibility from each expiry's `last_trading_instant` without a new response. Each feed refreshes at its backend cadence while visible (flow 15 min, dark pool 15 min, GEX/NOPE 1 min, expirations 60 min). NOPE shows its own reading time and age instead of "updates roughly every minute". | `options-feed-states.test.tsx` (failed refresh keeps data with warning and Retry; fake clock advances age and expires a contract; elapsed print age; NOPE time). Sabotage: removing the banner or returning to the frozen age fails the matching test. |
| R05 | Fixed locally | New `services/options_contract.py` gives each row `contract_terms`: status, `premium_multiplier` and `deliverable_shares` as separate fields, both null unless supported, and `automatic_payoff_supported`. Only a standard root (matching the underlying) is supported, and that is still labelled an assumption, not a deliverable record. Chain rows keep `option_symbol` and terms. The game plan, the strategy matrix (`_leg`), the options-income engine (and its backtest, which calls the same selector) and the chain-driven gain/loss calculator all refuse adjusted (`nonstandard_root_deliverable_unknown`) and unidentified rows, and report the refusals. The hand-built calculator states its standard-contract assumption. | `test_options_contract_terms.py` (21: identity cases, rows keep identity, adjusted contract with a usable quote refused by the game plan, matrix and income engine, mixed chain prices only the standard one, stock-plus-cash refused, explicit supported terms priced, wrong multiplier refused, covered-call coverage per 100-share deliverable); `options-contract-terms.test.tsx` (7). Sabotage: removing the game-plan, leg or income-engine guard fails 3, 2 and 1 tests respectively. |
| R08–R10 | Open | — | — |
| R11–R14 | Pending review | Not reviewed in this pass. | — |

Remaining limits of the local fixes:

- **R05 is strict by design.** A contract with no readable symbol is refused just as an adjusted one is. A held position on such a contract is kept on record with the reason (section 9.3), never priced. Existing strategy tests now state standard terms explicitly in their fixtures. The options-income backtest uses the same selector, so a rerun would exclude any adjusted contracts in the archive; how many exist was not measured.
- **R01's last-good sample** is held for 24 hours. Past that, a failure shows the failure alone. `retry_after` is not reported, because the provider's per-minute reset header is not interpreted here.
- **R02 refresh cadence** adds roughly one request per minute per open stock page for the GEX/NOPE route. GEX itself stays cached for 15 minutes server-side; only NOPE's 1-minute cache expires that often.

- **The session close is the `prices` table's `close` for that date.** If a later split causes historical closes to be re-ingested split-adjusted, moneyness for older sessions would compare an adjusted price with unadjusted strikes. That was not verified. With no daily bar for the session, moneyness is unknown.
- **API shape changed.** `volume`, `oi`, `iv` and `itm` can now be null, and `call_volume`/`put_volume`/`cp_ratio`/`sentiment` can be null for an unmeasured side. Signal-engine's reader already treats a null `sentiment`/`cp_ratio` the same as unavailable. The ML options features read the snapshot table, which no longer receives a ratio for an unmeasured side.
- **Prospective impact is not measured.** No production query was run. In the one measured sample, volume/OI nulls were absent, so the composition and snapshot changes should rarely trigger; the IV display change affects about half of each chain.

### 9.3 Pre-commit checks (October 23, 2026)

**Build and typecheck reconciliation.** The earlier "clean typecheck" was produced with `src/pages/improvements.tsx` excluded through a temporary config, because that file did not parse. It was clean for every other file, not for the tree. The parse error was an unescaped apostrophe (`provider's` inside a single-quoted string, line 123) from commit `7a63bd47`, and it would have failed the next frontend build. With it escaped, the final working tree passes `tsc --noEmit -p tsconfig.json` with nothing excluded (exit 0), `next build` (compiled successfully, 76 pages, exit 0) and the full Vitest suite.

**R03 price basis closed.** Several ingest paths download daily bars with `auto_adjust=True`, so a session's stored close can be split- (and dividend-) adjusted after the fact while the archived strikes stay in that session's terms. The close is now used for moneyness only when the chain itself confirms its basis: put-call parity on the three two-sided call/put pairs nearest the money (S ≈ K + C − P) must agree with the close within 7.5%. A 5-for-4 split is a 25% difference. Otherwise moneyness is unknown and `moneyness_unknown_reason` says why: no stored close, no two-sided pair (unverifiable), or the two prices with their ratio (different bases). Regression tests cover a 2-for-1 split, a 1-for-10 reverse split, a 5-for-4 split, a close within tolerance, an unverifiable chain, a missing close, and the reason reaching the flow route and the card. Disabling the gate fails 4 of them.

**R05 exclusions measured** (read-only production query, about 06:30 UTC on October 23; each symbol's latest archived session, which the live paths read):

| Class | Contracts | Symbols |
|---|---:|---:|
| Standard root | 285,643 | 150 |
| Adjusted (nonstandard root) | 0 | 0 |
| Missing symbol | 0 | 0 |
| Unreadable symbol | 0 | 0 |

On current data, R05 excludes nothing. **Not measured:** the archive-wide breakdown (which the income backtest replays) and whether any *open* options-income position is on a non-standard contract. The second production read was refused by this session's permission policy, so both remain open questions rather than zeros.

**Held positions kept, with a reason.** A held position on an unsupported contract is preserved, never priced:
- The gain/loss table shows "kept on record, payoff not computed: …" with the specific reason (adjusted, or unidentifiable).
- An options-income position on such a contract is left OPEN at settlement, with `options_income.settlement_blocked_unsupported_contract` logged, the same pattern as a missing settlement close. Its cash outcome is not computed.
- Its daily equity mark is kept, because dropping the liability would overstate equity, but it is counted as an approximate mark of quality `unsupported_contract_terms`.
- The positions API returns `contract_terms` and `payoff_blocked_reason` for every position.

**R02 polling checked and corrected.** All polled feeds now go through `usePolledSWR`:
- Hidden tabs do not poll (`refreshWhenHidden: false`).
- Same-key requests are shared within SWR's deduping window.
- Consecutive rate limits, whether an HTTP 429 or a provider `rate_limited` answer, double the interval up to 16×, and the first ordinary answer restores the base cadence.

Testing found that a plain `refreshInterval` had no backoff at all. It also found that SWR skips polling while its cache holds an error, so recovery after a failure depends entirely on the error retry. A 429 is therefore retried once, at the backed-off interval; other errors retry with doubling delays, never longer than the base cadence. One lag is known: the first refresh timer is armed at mount, so a rate limit on the first answer is followed by one poll at the base interval. Tests use fake timers, and sabotage of hidden-tab polling or the backoff fails the matching test.

**R06 regression made unambiguous.** The held-fill-without-quote tests now state standard contract terms explicitly in their fixture. Unsupported identity (adjusted, or no terms) is a separate test that expects no priced row and a "kept on record" notice. With standard fixtures, restoring the old drop-when-unquoted line fails 5 tests.

### 9.4 Review follow-up (October 23, 2026)

**R05 exclusions, archive-wide and open positions** (read-only production queries, each statement bounded, aggregates only, no mutations; evidence in the rerun directory):

| Population | Contracts / positions | Classified standard | Adjusted | Missing symbol | Unreadable |
|---|---:|---:|---:|---:|---:|
| Archive, 133 symbols (one bounded query each) | 15,081,256 rows | all | 0 | 0 | 0 |
| Archive, the 17 largest symbols (bounded per quarter after hitting the 60 s per-symbol limit) | 37,445,586 rows | all | 0 | 0 | 0 |
| **Archive total** | **52,526,842 rows** | all | 0 | 0 | 0 |
| Options-income positions, open | 7 (7 symbols) | 7 | 0 | 0 | 0 |
| Options-income positions, closed | 7 (6 symbols) | 7 | 0 | 0 | 0 |

The archive spans 150 symbols, sessions 2024-08-09 to 2026-10-22. The single full-table query timed out at 300 s and returned nothing; the per-symbol and per-quarter queries replaced it and all completed.

**Result: zero exclusions under the current R05 classifier**, archive-wide and among held positions. That is a statement about the classifier, not independent verification: it checks only that each readable OCC root matches its underlying. It does not establish any contract's legal deliverable, and it is not proof that root matching detects every adjustment (an adjustment that kept the original root would pass).

**R03 is an estimate, labelled as one.** Put-call parity within 7.5% is a plausibility check, not proof of a matching price basis. Nothing in this code base establishes the close's provenance, so every moneyness reading that passes is `basis_confidence: "estimated"`: "ITM (est.)" in the flow card, with the reference line saying so, and "estimated to be in the money" in the chain table. A new test pins the limit: a 21-for-20 split moves the close 4.8%, passes the check, and misreads a truly in-the-money 115 call as OTM. That is exactly why the result is never presented as measured.

**Unresolved settlement is visible.** The positions API returns `settlement_state` (`open`, `settlement_pending` for an expired standard position awaiting its exact close, `settlement_unresolved` for an expired position on unsupported terms, `closed`) with a reason. The options-income page shows a SETTLEMENT UNRESOLVED badge with the reason on the position. The approximate valuation stays identifiable: each equity row's evidence carries `unsupported_contract_marks`; equity-curve points return it; and the portfolio summary returns `equity_unsupported_contract_marks` and `settlement_unresolved`, shown on the page as "≈ … settlement unresolved … return includes only their approximate mark, not a settled outcome". The equity curve says how many points rest on approximate and unsupported marks.

**Polling: two defects in `d52518bf`, both fixed with tests.** Both fail against the committed hook:
- **The data-level lag was real.** A provider `rate_limited` answer delivered with HTTP 200 on the first response was followed by one poll at the base interval. A first-response HTTP 429 already backed off, because SWR suspends polling while the cache holds an error, and it now has its own regression test.
- **The committed hook never polled under re-renders.** Its inline-function `refreshInterval` gave SWR a new dependency on every render, which restarted the countdown each time, so a component re-rendering faster than its interval never polled. The Market Pressure panel re-renders every 30 s, so its 60-second GEX/NOPE poll and 15-minute dark-pool poll would never have fired. Measured: 1 request in 3 minutes with re-renders every 15 s, against 4 with the fix.

The interval is now a number held in state. It changes only when the backoff step changes, and that change re-arms the timer at once.

**Hidden tabs still executed error retries** (found by the follow-up review, [2026-10-23-followup-review.md](evidence/2026-10-22-feature-reaudit-rerun/2026-10-23-followup-review.md)). `refreshWhenHidden: false` paused normal polling, but the custom `onErrorRetry` scheduled an unconditional timer, so after an HTTP 503 a hidden tab made 6 requests in 3 minutes instead of 1. Retries now go through one scheduler per hook instance:
- At most one timer; each retry has a deadline (429: the backed-off interval; other errors: doubling from 5 s, capped at the base cadence).
- A retry that comes due while hidden or offline is held, not run.
- On becoming visible and online, an overdue retry runs once immediately, and one not yet due is re-armed for its remaining time, so a rate-limit deadline is never shortened.
- Success, a key change or unmount clears it. SWR's own reconnect revalidation is off, so the scheduler is the single recovery path.

It is not simply dropped while hidden, because SWR skips normal polling while an error is cached and focus revalidation is off here. Tests cover 503 and 429 while hidden, hide-before-deadline, showing before and after a 429 deadline, repeated wake events, repeated failures (exact count, no burst), offline/online, unmount and key change. The review's witness passes unmodified. Removing the visibility/online gate fails 5 tests. Removing clean-up fails the unmount test, which compares leftover timers with a never-failed control because SWR leaves one internal timer either way. The key-change test alone would not catch missing clean-up (SWR's bound revalidate does nothing after the key changes); it shares the unmount clean-up path.

Rerun evidence is saved separately in [evidence/2026-10-22-feature-reaudit-rerun/](evidence/2026-10-22-feature-reaudit-rerun/README.md). The original evidence directory is unchanged.

