# Deep platform audit

Date: November 6, 2026, America/Los_Angeles. Source baseline: `49802ce516fe064e9356d90d1bd675a442097d4a`, including the pre-existing working-tree changes described in [evidence](evidence.md). Findings apply to inspected local source; deployment status is not inferred from it.

## Assessment against your five objectives

| Objective | Assessment | Highest-value change |
|---|---|---|
| Better signals | Many useful inputs and safeguards exist, but overlapping factors, different data conventions and several decision paths complicate interpretation | One versioned evidence record and entry policy; measure each feature's incremental contribution |
| Enter as a rally starts, avoid buying the top | Early-recovery logic, RZY no-chase logic, gap/extension gates and anti-chase counters already exist. They do not establish that the selected entries are better | Separate setup discovery from a completed-bar trigger and a still-valid entry window; test breakout/retest and first-pullback alternatives |
| Better prediction | Ensemble calibration, suppression and evaluation diagnostics exist. Final threshold/report label separation remains incomplete | Finish the existing F04 evaluation protocol; predict explicitly defined events over named horizons |
| Confidence and trust | Strength is not win probability; historical alert attribution and data qualification limit performance claims | Show strength, calibrated event probability, data quality and execution readiness separately |
| Better return | Historical RZY and options studies do not demonstrate a reliable net edge | Optimize net expectancy and portfolio drawdown at matched exposure, including nonfills and trading costs |

**Recommendation:** consolidate and validate before expanding. Keep the platform's research breadth, but make the actionable decision small: **what setup, why now, maximum entry price, invalidation, exit policy, evidence quality, and portfolio risk**. No indicator can identify the exact beginning of every rally or reliably exclude every top.

## What is already valuable

- AI Signals has trend/momentum/volume/structure scoring, early-recovery handling, regime-specific thresholds, overextension controls, model suppression, and explanatory reasons. Do not replace this with another unvalidated weighted score.
- The October ML-unavailable repair removes suppressed models' ML contribution. The final-split defect is a different unresolved issue; do not reopen the repaired fallback as if it were unchanged.
- Daily input provenance, immutable signal occurrence/opened/completed-close records, strict outcome eligibility, and a measurement registry are foundations to extend.
- Decision-engine is the default primary entry authority in current source. July architecture documentation describing it as only a secondary opinion is stale. A separate legacy fallback still exists.
- Little RZY uses deterministic completed-bar structure, confirmed pivots, explicit states, costs and no-chase rules. These are useful research properties even without demonstrated profitability.
- Intraday Stage C has separate contracts, records and read APIs. `INTRADAY_PRODUCTION_ENABLED=False`; it is not an active same-session prediction product.
- Options has contract-identity checks, unsupported-contract refusals, quote provenance, strategy comparisons and archived outcome research. Preserve these safeguards.
- News ingestion has deduplication, failure handling, classification accounting and correction handling. Extend this rather than creating a second news collector.

## Findings register

Priority definitions: **P1** materially affects a trading interpretation or evidence claim; repair before relying on that claim. **P2** improves research, resilience or usability. No production-loss incident is inferred from a source defect. “Reproduced” means a local witness against actual code; “source-confirmed” means directly traced code; “research” means a hypothesis requiring measurement.

### A01 — P1: RVOL means different things across paths

**Source-confirmed.** [`get_rvol`](../../services/market-data/src/api/routes.py) compares today's cumulative M5 volume with prior days at the same local clock time. [`scheduler._session_elapsed_rvol_thresholds`](../../services/market-data/src/services/scheduler.py) instead adjusts a full-day-volume threshold approximately linearly with elapsed session time, with a floor. Squeeze/volume alert paths divide cumulative volume by a full-day average. These are not interchangeable measures of “2× normal.”

The endpoint loads all stored M5 bars before selecting prior dates; days with any positive partial coverage can enter the denominator. It does not demonstrate complete matched-session coverage. The returned `minutes_since_open` is actually local minutes since midnight. The scheduler models HK lunch but uses a fixed 390-minute US day, so early-close normalization also needs an explicit calendar contract.

**Improve:** a shared volume snapshot with separate `same_time_rvol`, `full_day_volume_ratio`, and completed-bar `volume_z`; baseline sessions, coverage, source age, session type, and formula version. Use eligible completed bars and the same session segments in numerator and denominator. Missing coverage is unknown, never quietly low activity. Cache bounded per-symbol/session summaries. Do not simply copy the endpoint's current calculation everywhere.

**Acceptance:** US early close/DST, HK lunch, opening auction, partial current bar, missing historical intervals, zero volume, and mixed providers; equivalent inputs produce identical API/chart/alert values. Report changes in both early alerts and false positives before changing thresholds.

### A02 — P1: standard chart VWAP is range-anchored, not session-reset

**Source-confirmed.** [`PriceChart.computeVwap`](../../frontend/src/components/PriceChart.tsx) accumulates typical-price × volume over its entire input array. The standard overlay passes `activePrices`; it has no session reset. Anchored VWAP uses a selected slice of the same array. Loading multiple sessions therefore changes the meaning of the ordinary “VWAP” line.

**Improve:** name and separate **session VWAP**, **selected-range VWAP**, and **event-anchored VWAP**. Define regular/extended-session inclusion and venue timezone. Persist anchors by bar identity/time, not just an array index vulnerable to range changes. Freeze only known-to-date anchors in research. This is OHLCV typical-price approximation, not transaction-level VWAP.

**Acceptance:** two sessions with different price/volume distributions reset correctly; changing loaded history cannot change today's session VWAP; an explicit anchored calculation retains its selected anchor. Session VWAP should become an entry feature only after parity with the chart is established.

### A03 — P1: an FVG can remain unfilled after full cumulative coverage

**Reproduced.** [`detect_fair_value_gaps`](../../services/technical-analysis/src/indicators/trendlines.py) retains one contiguous covered interval and discards a disconnected overlap. For a gap `[100,110]`, subsequent ranges `[100,103]`, `[107,110]`, then `[103,107]` collectively cover it. The function still reports `filled=False`. Gaps between bars make disconnected overlaps possible; its continuity assumption does not hold for all OHLC paths.

**Impact:** a stale gap can remain eligible for chart planning. This is a status-computation defect, not proof that an actual trade lost money. Also, a three-candle FVG does not mean no trades ever occurred there: the middle candle may have traded through that range.

**Improve:** retain/merge the full interval union, or explicitly redefine mitigation as a different documented rule. Track formation-known time at the third candle's completion, first touch, partial coverage, full fill and invalidation separately. A historical chart's middle-candle index is not the first actionable time.

**Acceptance:** disconnected overlaps later bridged, interior-only overlap, gap-over without trades through, mirrored bearish case, and chronological replay. Existing 14 FVG tests pass; they do not catch this witness.

### A04 — P1: squeeze days-to-cover explanation conflicts with its formula

**Reproduced/source-confirmed.** [`compute_short_squeeze_score`](../../services/market-data/src/api/routes.py) awards 30 days-to-cover points at DTC 1 and zero at DTC 5, while describing lower DTC as shorts being unable to exit quietly. DTC is short interest divided by average daily share volume; larger values mean more average-volume days to cover. The code's inverse weighting may be a liquidity/ignition hypothesis, but it is not the described covering-difficulty measure. See [FINRA's definition](https://www.finra.org/sites/default/files/Equity_Short_Interest_Data_File_Download_API.pdf).

**Improve:** distinguish crowding/covering burden from current liquidity and ignition. Correct the explanation immediately in a future implementation; compare the current inverse rule, no DTC contribution, and a separately justified crowding rule before changing rankings. Historical percentiles alone do not establish predictive direction. Do not blindly invert the score and call it fixed alpha.

### A05 — P1: “utilization” uses an unqualified denominator

**Source-confirmed.** The same function computes `short_interest / short_shares_available`, clamps at 100%, and calls it utilization. A same-provider pair does not establish matching universe, timestamp or denominator. Reported short interest is not necessarily shares on loan, and available-to-borrow inventory is not necessarily total lendable inventory. [IBKR's definition](https://www.ibkrguides.com/clientportal/researchmenu/orbisa-securities-lending.htm) uses on-loan quantity divided by total inventory.

**Improve:** verify the actual provider field contract and coverage before treating this as utilization. Until qualified, label it an inventory-pressure proxy or omit its score contribution; do not invent a total inventory from incompatible quantities. Preserve uncapped raw values for diagnostics. Add field-level dates and source coverage. Provider-specific semantics are an open verification item in this audit.

### A06 — P2: HMM labeling, convergence and artifact reliability need tightening

**Reproduced:** [`_label_states`](../../services/market-data/src/services/hmm_regime.py) claims lower VIX breaks equal-return ties, but `np.lexsort((-vix_means, -ret_means))` puts higher VIX first. The witness returns `[1,0,2,3]` rather than `[0,1,2,3]`. This exact-tie edge case is real but is not presented as a major return driver.

**Source plus library documentation:** checking `monitor_.converged` alone does not prove tolerance convergence. The [hmmlearn API](https://hmmlearn.readthedocs.io/en/stable/api.html) also declares convergence when the iteration limit is reached. Local hmmlearn is unavailable; the installed production version was not inspected. The source writes directly to `/tmp/hmm_regime.pkl`; no atomic replacement or writer lock is present in this module. These are failure risks, not observed corrupt files.

The fit and inference sequences use different lookback lengths to initialize IWM's EMA200, creating a feature warmup inconsistency. The model always assigns bull/neutral/choppy/bear by return rank; rank alone does not establish that a state is “choppy.” Its observations are US market proxies, not HK-specific regime evidence.

**Improve:** align feature history and warmup, record data cutoff/age, examine likelihood history and degeneracy, use atomic publication plus locking, validate state occupancy/stability out of sample, expose transition uncertainty and unavailable/stale state. Compare with the existing simpler regime classifier. HMM posterior is state-membership probability, not next-trade success probability. For historical evaluation fit only on data then available; do not smooth an entire future sequence and backdate its states.

### A07 — P1: final ML threshold/report boundary remains unpurged

**Source-confirmed, previously identified F04.** [`trainer.py`](../../services/ml-prediction/src/training/trainer.py) splits final test rows at `_thresh_split` into adjacent threshold-selection and reporting slices without removing overlapping future-label windows. Earlier embargoes do not repair this last boundary. The code records metric denominators and evaluation mode, which should be preserved.

**Improve:** finish the existing [F04 versioning/rollout plan](../features/2026-10-27-f04-evaluation-version-comparison-and-rollout-plan.md), including label-maturity boundaries, version-aware incumbent retention, isolated candidate storage and publication fencing. Do not deploy a bare purge that freezes legacy “valid” incumbents or silently removes serving coverage. Separate algorithm correctness release from subsequent profitability qualification. More models or a more sophisticated ensemble cannot compensate for contaminated evaluation.

### A08 — P1: strength, probability, forecast accuracy and trade return remain distinct

**Source-confirmed.** [`signals._apply_style_signal`](../../services/signal-engine/src/generators/signals.py) derives confidence as `abs(fused - .5) * 200`; the fused score receives rule-based adjustments. This is strength, not calibrated win probability. Calibration eligibility has been tightened, but its outcome target is still a named directional/hurdle event, not necessarily a filled trade's target-before-stop result.

**Improve:** a score dictionary in every decision: raw strength, explicitly named calibrated event probability when qualified, sample/date coverage and uncertainty, data quality, and execution readiness. Calibrate the final published prediction separately from any component model. Missing verified evidence should display “not established.” AUC measures ranking; Brier/log loss combine several properties and should be accompanied by reliability curves and base-rate comparisons. [Calibration reference](https://scikit-learn.org/stable/modules/calibration.html).

### A09 — P1: exact delivered-alert performance is not yet established by daily-row attribution

**Source-confirmed remaining F03.** [`signals_shared.py`](../../services/signal-engine/src/api/signals_shared.py) still selects delivery state from `Signal` alongside first-fire outcome data and uses it for a sender-accepted subset. The new occurrence/snapshot tables exist, but that alone does not make the current calibration receipt-specific. One day's first SELL and later mailed BUY must not share an outcome identity.

**Improve:** finish immutable send snapshots/receipts and receipt-specific outcomes using the [existing occurrence design](../features/2026-10-27-f03-signal-occurrence-and-delivery-attribution-design.md). Preserve generated, close-confirmed, provider-accepted and filled cohorts separately. Provider acceptance is not inbox delivery or a trade fill. Do not reconstruct unavailable historical receipt identity.

### A10 — P1: fallback can change entry policy when decision-engine is unavailable

**Source-confirmed.** [`paper_trading_engine._scan_for_entries`](../../services/market-data/src/services/paper_trading_engine.py) defaults to decision-engine primary, but uses `_should_enter()` when the decision result is unavailable. Shadow comparisons and authoritative gate counters exist; the two implementations still represent policy drift risk.

**Improve:** extract one pure versioned policy evaluation, used locally and by decision-engine, with the same resolved parameters and snapshot. Define a degraded new-entry policy explicitly; required missing inputs should block new risk. Keep position management independent so an entry-service outage cannot stop risk-reducing exits. Replay both existing policies before consolidation, retaining intentional differences as named modes.

### A11 — P2: correlated evidence can look like independent confirmation

**Architectural assessment.** AI trend/momentum, K-Score momentum, RSI/MACD, price-breakout rules, squeeze daily change, and RZY price structure partly reuse the same underlying bars. VP/FVG/VWAP are also transformations of price/volume, not independent institutional confirmations. Counting five green indicators is not five independent bets.

**Improve:** group evidence by price structure, participation/liquidity, event, market context and valuation; log lineage. Run group ablations on identical candidates/dates/costs. Keep a feature only if it adds predictive, risk or explanation value beyond the baseline. Do not mechanically award one point per agreeing widget.

### A12 — P2: existing anti-chase gates need a unique-event counterfactual study

**Source-confirmed measurement limitation.** [`shared/metrics/anti_chase.py`](../../shared/metrics/anti_chase.py) correctly states that its rejection rates are repeated scan attempts, not unique opportunities, and that counts alone cannot establish benefit. Existing signal recovery logic already helps avoid an exclusively late-trend bias.

**Improve:** log each setup once with all gate decisions and track accepted and vetoed alternatives under the same ex-ante entry/exit model. Compare avoided drawdown with missed profitable continuation. Keep hard data/risk gates distinct from research filters. Replacing all extension controls with lower thresholds would defeat the user's objective.

### A13 — P2: Volume Profile variants need one auditable contract

**Source-confirmed.** [`volumeProfile.ts`](../../frontend/src/lib/volumeProfile.ts) and [`volume_area.py`](../../services/market-data/src/services/volume_area.py) distribute each OHLC bar's volume uniformly across its high–low range. Backend alert levels use a rolling daily range; the chart supports selected windows and fixed ranges. This is an approximation, not bid/ask order flow. Different ranges legitimately yield different POCs.

**Improve:** preserve the useful variants but standardize range identity, timeframe, bucket size/count, value-area fraction, price/volume adjustment basis and algorithm version. Share golden fixtures across Python/TypeScript. Label chart and alert range explicitly; avoid displaying a rolling-alert level as though it came from the user's selected range. Never choose research anchors using future swing endpoints.

### A14 — P1 for performance claims: Little RZY is a research setup, not a demonstrated edge

**Current code plus historical evidence.** [`little_rzy.py`](../../services/market-data/src/services/little_rzy.py) defaults to 7 right-confirmation bars, 1.5 ATR minimum impulse, 20–80% retracement, first/second preferred sequences, no-chase and structural stops. Seven right bars imply at least 35 minutes of pivot confirmation on 5m bars and 105 minutes on 15m bars; not every trigger has this total latency, but a historical pivot cannot be acted on at its original timestamp.

The [October 26 review](../audits/2026-10-26-little-rzy-ai-signals-full-review.md) cites the corrected October 20 study: 692 trades, −0.093R mean net, and no demonstrated lift over matched random timing. These are historical results under that study's assumptions, not a fresh result for today's source. Scan latency and readiness/fill causality were also concerns.

**Improve:** prioritize the existing causal forward ledger, as-observed versus rebuilt separation, availability timestamps and independent position management. Study first-pullback/sequence changes only after these foundations. Do not relabel a hindsight-confirmed pivot as an early live signal. Lowering pivot confirmation is a research tradeoff between delay and false structure, not a free improvement.

### A15 — P2: news needs surprise and price-response context, not sentiment alone

**Source-confirmed coverage; proposed research.** [`classify_headlines`](../../services/news-intelligence/src/services/classify.py) produces sentiment, materiality and category. [`storage.py`](../../services/news-intelligence/src/services/storage.py) implements deduplication and negative-flag correction rules. Event-intelligence separately supplies earnings/macro/filing context.

**Improve:** cluster syndicated versions into a canonical event; retain published, first-observed and processed times plus corrections. Add expectation-versus-actual fields only where timestamped expectations exist. Evaluate price/volume response after actual availability. A positive story with a failed breakout can indicate an exhausted trade; an old headline newly ingested is delayed information. Keep LLM extraction/narration separate from deterministic eligibility and arithmetic. Do not infer insider intent or price direction merely from a news category.

### A16 — P1 for return claims: options suitability is not selection skill

**Source-confirmed plus historical research.** [`options_strategies.py`](../../services/market-data/src/services/options_strategies.py) selects structures using objectives and IV context; prices may be quote midpoints or labeled last prints. [`options_contract.py`](../../services/market-data/src/services/options_contract.py) refuses unsupported terms, but standard-root 100-share terms remain an explicitly labeled assumption. The research lead is a bounded shortlist ordered by quote quality, not predicted return.

The [October 31 study](../audits/2026-10-31-options-candidate-study-results.md) found no reliable held-out profitability for its compared choices and no evidence supporting promotion as a “best pick.” The full 285,702 scenario rows are not that many independent trades. Its same-entry stock comparison also matters for covered strategies.

**Improve:** first choose a qualified underlying setup, then compare stock, no trade and supported option structures under common time/price/IV scenarios, executable spread assumptions and capital. High historical IV rank does not establish that premium is overpriced relative to future realized volatility/event risk. Add quote-age/size checks, assignment/dividend/expiry considerations, and actual deliverable verification where available. No new naked-short or complex-product automation is recommended. [OIC Greeks explanation](https://www.optionseducation.org/referencelibrary/faq/option-price-behavior).

### A17 — P2: learning and architecture text can overstate current capabilities

**Source-confirmed.** The existing [`/learn`](../../frontend/src/pages/learn.tsx) self-tuning tab claims continuous tuning with held-out improvement for every mechanism. Current [`calibration.py`](../../services/signal-engine/src/api/calibration.py) has research-only guards on legacy writers. The old architecture document also describes older decision authority and service inventory.

**Improve:** use the new [learning wiki](learning-guide.md) as reviewed content for an eventual `/learn` refresh. Separate “available,” “research,” “disabled,” and “validated”; source live capability status from a backend contract. Maintain distinct prediction window, outcome window, and managed hold duration rather than one ambiguous “days” label.

### A18 — P2: operational trust needs decision-level service quality

**Scoped inspection, not a full operational/security certification.** The gateway authenticates routed traffic, compose binds external app ports to loopback, and the Intraday routes require admin. Existing outbox, locks, metrics and ownership mechanisms are useful. Their existence does not certify every endpoint or deployment.

**Improve:** measure bar-close → available → processed → decision → send acceptance → order-ready latency; expected coverage and excluded observations; quote age and provider disagreement; unresolved positions; fallback activation. Prioritize open-position management over scans/research. Restore drills, source/image parity, per-user authorization and broker kill-switch/idempotency tests remain release checks. Inspect production read-only before claiming readiness. Do not add a new streaming infrastructure stack until measured bottlenecks justify it.

## Feature disposition

| Feature | Keep | Consolidate/improve | Avoid |
|---|---|---|---|
| AI Signal | Explanatory scoring, suppression, recovery and risk checks | Versioned score/prediction and policy snapshot | Calling strength a success probability |
| RVOL | Participation evidence | One coverage-aware same-time baseline | Comparing incompatible ratios |
| Volume Profile / Fixed Range VP | Auction/level context | Shared contract and fixtures, named anchors | Claims of exact institutional buying |
| VWAP | Session/anchor location | Explicit session reset and named anchors | Treating any VWAP reclaim as sufficient |
| HMM | Optional regime evidence | Stable features, uncertainty, validation | Market-state probability as trade probability |
| Little RZY | Deterministic setup research | Causal forward observations and early-sequence study | Live profitability claims from reconstructed patterns |
| News/events | Catalyst/risk context | Canonical event and observed-time response | Sentiment as an entry command |
| FVG | Conditional price zone | Correct lifecycle and known-at time | Guaranteed “gap fill” or automatic 1.5R edge |
| Short squeeze | Crowding watchlist and ignition context | Correct DTC/proxy semantics, freshness and late-move cap | Ranking the largest existing rally as the best new entry |
| Options | Objective/contract comparison | Underlying-first plan and scenario/cost analysis | Equating premium yield or IV rank with expected return |

The execution plan, acceptance tests and experiments are in [architecture.md](architecture.md). The next implementation should repair A01–A05 semantics, complete A07–A10 evidence/policy work, and preserve the already-tested safeguards while those changes are evaluated.
