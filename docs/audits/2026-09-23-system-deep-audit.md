# System Deep Audit — Trading Logic, Model Validation, Accounting, and Architecture

**Date:** September 23, 2026, America/Los_Angeles.  
**Reviewed revision:** local `92868c2`; production checkout `92868c2d`.  
**Production aggregate snapshot:** September 24, 2026, 05:40:03 UTC / September 23, 22:40:03 PDT.  
**Deliverables:** this report, [offline reproductions](evidence/2026-09-23-deep-audit-reproductions.py), and [sanitized evidence](evidence/2026-09-23-deep-audit-evidence.json).

## 1. Assessment

**I found 12 implementation or measurement issues requiring action, including eight high-priority findings.** The most consequential problems are in how the platform establishes predictive validity, combines model outputs, settles and values options, authorizes model changes, and maintains news risk state. These are stronger reasons to delay wider automation than a particular confidence or R:R threshold.

The platform has useful coverage of market data, signals, research, alerts, paper portfolios, and broker adapters. Recent changes address several earlier findings correctly. However, passing unit tests and deployed code do not yet establish that the complete decision-and-execution system produces reliable, achievable net returns.

This was a broad source review across the service boundaries, with deeper investigation of the paths described below. It is **not a claim that every line, endpoint, strategy, dependency, or deployment setting is defect-free**. The coverage table identifies where source review, deterministic reproduction, and production verification differ.

The broker account remains sandbox according to the user's clarification. The current database has no broker order IDs on equity paper trades. No real-money loss or exploit was demonstrated. All production operations in this review were read-only; no training, recalibration, orders, emails, deployments, or application/configuration edits were performed. `CLAUDE.md` was not changed.

### Finding register

P1 means high priority for trustworthy decisions, accounting, access control, or before enabling the affected automation. P2 means a bounded correctness/measurement problem that should be fixed before relying on the affected report or workflow. These are impact-based priorities, not assertions of current live-money exposure.

| ID | Priority | Finding | Evidence |
|---|---|---|---|
| DA-01 | P1 | Meta-model validation is grouped by symbol, not globally chronological | Actual-source ordering probe; deployed source matches |
| DA-02 | P1 | Meta-model inference supplies different feature meanings from training | Actual ensemble function with captured inference arguments; deployed source matches |
| DA-03 | P1 | Base-model validation silently removes embargoes for shorter datasets | Actual split statements executed; deployed source matches |
| DA-04 | P2 | Backtest terminal exits charge costs in trades but omit them from equity | Actual accounting code with controlled prices; deployed source matches |
| DA-05 | P1 | Manual options run can settle an expiry using an unfinished daily bar | Actual settlement read/math with fake database; deployed source matches |
| DA-06 | P1 | An arbitrarily old option ask overrides current intrinsic exposure | Actual liability function plus query trace; deployed source matches |
| DA-07 | P1 | Options execution lock fails open and can delete another worker's lease | Actual lock wrapper with simulated lease replacement; deployed source matches |
| DA-08 | P1 | Ordinary authenticated users can invoke global model mutation endpoints | Actual train handler with ordinary identity; gateway/route source verified |
| DA-09 | P1 | Unrelated minor news can erase an active material-negative risk flag | Actual storage function with fake classification/database; local source verified |
| DA-10 | P2 | Mixed broker fill batches can discard confirmation flags on session close | Actual exit poller with controlled broker/session; deployed source matches |
| DA-11 | P2 | Optimizer metrics describe a fully invested sleeve, not returned weights/cash | Actual allocation/metric code; deployed source matches |
| DA-12 | P2 | Put “annualized yield” uses underlying price, not reserved collateral | Actual contract-scoring function; deployed source matches |

“Reproduced” means the named code behavior was observed under stated inputs. It does not mean the failure was observed in a production trade. The evidence script deliberately isolates infrastructure dependencies; its assertions record existing defects and should be inverted/replaced with desired-behavior regression tests when fixes land.

## 2. Current evidence and coverage

### What production establishes

| Observation | Result | Interpretation |
|---|---|---|
| Equity paper trades | GROWTH 64 closed / 1 open; SWING 62 closed / 1 open; LONG 0 closed / 2 open; no SHORT rows | 126 closed and four open; still no resolved LONG/SHORT paper evidence |
| Broker IDs on paper trades | Zero entry IDs; zero exit IDs | No stored broker-backed paper execution case to evaluate; not a query of the actual broker account |
| Options income | Nine open cash-secured puts; no closed positions | First expiry September 25; latest October 30; no realized options-income win rate yet |
| Resolved signal outcomes in the meta query's capped population | 18,561; signal dates May 25–September 17 | More signal labels than paper trades, but they measure different outcomes and are not independent trials |
| Persistent scan-log schema | `paper_entry_scan_logs` includes timestamp, portfolio gate/reason, candidate count, skip tally | The earlier four-hour-only persistence limitation has a schema/code remedy; this audit did not aggregate its actual rejection history |
| Runtime source files | Ten selected files matched local hashes across six containers | Confirms the reviewed core implementations are deployed; not a fleet-wide drift certification |
| Meta artifact | File exists, 58,048 bytes; filesystem mtime July 8, 2026, 17:54:43 UTC | Investigate promotion/refresh history. Mtime is not proof of training date, AUC, schema version, or whether inference currently accepts it |

The ten files are the paper engine, options-income engine/API, ML trainer/meta-trainer/API, strategy backtester, optimizer methods, gateway proxy, and congressional report implementation. Full hashes are in the evidence JSON. News storage and shared authorization helpers were source-reviewed locally, not separately runtime-hashed.

The nine put positions versus zero covered calls is an observation, not a finding that strategy selection is incorrect. Explain the candidate-to-entry funnel, concentration limits, available capital, and configured strategies before changing that mix.

### Review coverage

| Area | Work performed | Important limit |
|---|---|---|
| Market-data / equity paper / brokers | Entry configuration, sizing, frozen exits, scan persistence, broker poll/close paths, targeted tests and runtime hashes | No broker API requests, partial-fill integration run, or full cash-ledger reconstruction |
| Options income | Ranking economics, opening, settlement, liability marks, locks, API triggers; settlement, marking, locking, and yield probes | No real executable contract quotes/fills obtained; archived bids remain research inputs |
| ML prediction | Feature builder, splits, meta records, training/inference contract, promotion logic, API authorization | No retraining; model artifact not deserialized; no claim of measured AUC inflation magnitude |
| Strategy backtesting | Fill timing, costs, final liquidation, reported metrics, full service test suite | Not a new historical performance study |
| Portfolio optimization | Return inputs, allocation constraints, cash scaling, metrics, API/frontend presentation | No new optimized portfolio or investment recommendation |
| Signal / Decision Engine | Prior-fix comparison, outcome/feature semantics, news modifier, primary/fallback policy interfaces | No new complete signal replay or alert-accuracy estimate |
| News / event intelligence | Classification/storage, hot-flag lifecycle, congressional/institutional entry conventions, recent session findings | No remeasurement of all disclosure or earnings studies |
| Ranking / technical analysis / research | Scoring inputs, missing-data treatment, indicator/feature interfaces, research sizing and proxy surface | Source-level spot checks; no full numerical certification or new P1 claim for these services |
| Gateway / shared auth / frontend | JWT versus authorization boundaries, route mapping, role enforcement, relevant metric presentation | No penetration test, load test, dependency CVE scan, or security-group audit |
| Operations / documentation | Compose exposure, deployment notes, recent incident and implementation reports, selective runtime hashes | No disaster-recovery restore drill or full image provenance audit |

The service source inventory contains roughly 93,000 Python lines across 12 services, excluding shared/frontend code and tests. That scope makes precise evidence and coverage labels preferable to declaring the entire project “fully verified.”

## 3. Detailed findings and proposed fixes

### DA-01 — The meta-model's “chronological” validation is not chronological

**P1.** [meta_trainer.py](../../services/ml-prediction/src/training/meta_trainer.py), particularly lines 155–167, 244–277, 311–351, and 374–386.

Outcomes are fetched in descending signal-date order, grouped by symbol, sorted ascending **within each symbol**, and appended to one `records` list. An 80/20 array split then divides that list. Dates are discarded from the feature/label tuples before splitting, so there is no subsequent global time ordering.

The reproduction uses five symbols with the same September 1–4 observations. Training contains observations through September 4; validation begins September 1 and consists of the last symbol. The reported AUC therefore does not establish future-period performance. It mixes a partial symbol holdout with overlapping historical dates.

Additional weaknesses in this same validation path: the nonconstant-feature selector sees the full dataset; the validation set is also the early-stopping `eval_set`; and promotion compares a new AUC with the previous bundle's historical AUC rather than evaluating both models on the same current holdout. A harder current cohort can reject a better model; an easier cohort can promote a worse one.

**Solution:** retain `stock_id`, signal timestamp, label window end, and label availability with every example. Globally sort and split by time, group same-session observations, and purge unavailable/overlapping training labels. Keep separate early-stop, calibration, and final comparison windows. Fit feature selection on training only. Evaluate incumbent and challenger on the same untouched cohort and report date/symbol-clustered uncertainty. A separate leave-symbol-out study can test cold-start generalization, but should be named as such.

**Acceptance test:** interleaved multi-symbol inputs must preserve `max(training_label_available_at) < validation_start`. Changing a validation-only feature must not change training feature selection. Early-stop and final promotion observations must be disjoint.

### DA-02 — Meta-model features change meaning between training and inference

**P1.** [meta_trainer.py](../../services/ml-prediction/src/training/meta_trainer.py), lines 260–277 and `predict_meta`; [trainer.py](../../services/ml-prediction/src/training/trainer.py), lines 1367–1407.

Training appends the actual signal outcome's confidence, fused score, and TA score. The ensemble caller instead sends XGBoost confidence, XGBoost bullish probability as `fused_prob`, and the weighted ML ensemble probability as `ta_score`. No TA measurement is supplied. It also omits `direction`, leaving the meta function's default BUY interpretation.

With controlled model probabilities 0.7/0.6/0.5, the actual ensemble function supplies `ta_score=0.605` and `fused_prob=0.7`. The first number is the weighted ML result, not technical analysis. Valid types and column counts conceal the semantic mismatch.

The target contract is also different: the meta model learns `SignalOutcome.is_correct` conditional on signal direction/horizon, while base models learn their own forward-return label. Treating both outputs as the same bullish probability and blending them 85/15 requires validation; equality of range `[0,1]` is not equality of meaning. Defaulting to BUY does not make the forecast event identical to every base-model label.

**Solution:** choose one explicit design. Either move a signal-success meta model after signal fusion, feeding the actual frozen signal features and using it as a calibrated setup-quality estimate; or retrain a stacking model on out-of-fold base-model probabilities with an identical future-return target. Do not manufacture missing TA input from another probability. Version the feature schema **including definitions, units, target, horizon, and availability**, not only column order.

**Acceptance test:** one frozen feature envelope must produce identical training/inference vectors. Test BUY and SELL separately. Verify the event represented by each probability before blending. Until that contract is validated, exclude the meta contribution from promotion claims; any runtime switch should be a separately reviewed configuration change, not an untracked audit side effect.

### DA-03 — Smaller datasets silently lose temporal separation

**P1.** [trainer.py](../../services/ml-prediction/src/training/trainer.py), lines 755–774.

The four-way train/early-stop/calibration/test split uses a horizon-sized gap only when the next slice exceeds `3 × horizon`. Otherwise it sets that gap to zero. This is reachable above the function's 200-row minimum.

For 200 dense observations and LONG's 20-bar horizon, calibration includes row 179, whose forward label uses row 199; the test begins at row 180. The reproduction executes the actual split assignments and confirms a zero gap. The separate `TimeSeriesSplit(gap=horizon)` cross-validation does not repair this final fit/calibration/test path.

**Solution:** never trade leakage for sample size silently. Purge by actual label-end/availability timestamps, then require viable disjoint slices. If data is insufficient, return `insufficient_data` and retain the incumbent or use a separately validated fallback. Include the actual splits in model metadata. Time-series evaluation must preserve temporal ordering; scikit-learn explicitly provides a gap mechanism for separation. [TimeSeriesSplit documentation](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html).

**Acceptance test:** exercise the minimum supported sample count for every horizon, plus irregular/dead-zone-filtered dates. No training, early-stop, or calibration label may use a price from the later evaluation period. Refusal to train is a valid expected result.

### DA-04 — Terminal liquidation costs disappear from the backtest equity curve

**P2.** [backtest/engine.py](../../services/strategy-engine/src/backtest/engine.py), lines 69–89.

The final open trade is marked closed at the last price with fee/slippage deducted, but its `position` remains one. The separate equity calculation detects exits only from a transition to zero, so the forced terminal exit's cost is omitted. An entry on the final bar is even clearer: its round trip appears in the trade list while equity stays flat.

At constant $100 prices with default 5 bps fee plus 2 bps slippage per side:

| Case | Trade return | Equity-curve return |
|---|---:|---:|
| Earlier entry, forced final exit | −0.139902% | −0.069951% |
| Entry and forced exit on last bar | −0.139902% | 0% |

This affects total return and path-derived risk statistics. It does not mean all ordinary next-bar fill timing is wrong; existing tests verify useful parts of that mechanism.

**Solution:** derive fills, cash, holdings, equity, and trade P&L from a single event ledger. At minimum, explicitly book terminal liquidation and both costs, including same-bar entry/exit. Report whether terminal positions are liquidated or marked open; do not mix conventions.

**Acceptance test:** terminal equity must reconcile to compounded trade returns under the engine's full-allocation assumptions for flat, rising, falling, last-bar-entry, and ordinary-exit cases, with nonzero costs. The existing final-close test uses zero fees/slippage and checks only that trade fields exist, which misses this failure.

### DA-05 — Options can settle on an unfinished expiry-day price

**P1; prioritize before September 25's first paper expiries.** [options_income_engine.py](../../services/market-data/src/services/options_income_engine.py), `_settlement_close` at line 467, `settle_expired_positions` at 505, `_run_options_income_step_locked` at 808; [options_income.py](../../services/market-data/src/api/options_income.py), manual `/run-step` at line 233.

Settlement selects positions with `expiry <= today`. `_settlement_close()` requires the correct daily-bar date, but does not require the exchange session to have ended or the bar to be finalized. The normal evening schedule is appropriate; the admin manual route is callable intraday and reaches the same logic. This repository already handles mutable intraday D1 bars in other decision paths.

The reproduction presents an expiry-day D1 close of $101 for a $100 put with $100 collected premium. The code accepts that price and would settle for +$100. If the actual session closes at $90, the modeled result is −$900. Once closed, the position is excluded from later settlement retries.

**Solution:** require both the expected settlement session **and finality/availability after its exchange close**, including early-close calendars and ingestion delay. An intraday manual run may update marks or open eligible positions but must defer settlement. Persist settlement price, session, source, observed/available times, and finality. Missing final data should leave a visible `pending_settlement` state.

**Acceptance test:** expiry morning, regular close before final ingestion, early close, holiday-shifted expiry, and final data arriving later. Only a confirmed final bar may close the position.

The current synthetic assignment/liquidation convention is suitable only as a clearly labeled research model. Actual assignment is not deterministically guaranteed by the closing-price comparison; exercise instructions and assignment processes matter. [OIC options assignment guidance](https://www.optionseducation.org/referencelibrary/faq/options-assignment).

### DA-06 — Stale archived asks can hide current short-option exposure

**P1.** [options_income_engine.py](../../services/market-data/src/services/options_income_engine.py), `short_option_liability` at 669, `_latest_option_ask` at 697, `_snapshot_income_equity_curve` at 707.

The ask query returns the most recent non-null ask without returning its date or checking age/as-of. Any nonnegative ask, including zero, overrides the intrinsic fallback. The snapshot combines that possibly old quote with a current underlying price. It discards the returned mark-source string `_src`; the persisted equity row therefore cannot explain this mixed-time valuation.

For one $100 short put, current stock $80, and a stale $2 ask, the function reports a $200 liability. With no ask it reports $2,000 intrinsic. The stale quote therefore raises reported equity by $1,800 relative to that fallback. This is a controlled example, not an assertion that one of the nine current positions has that error.

**Solution:** return a quote envelope containing price, source, timestamps, contract identity, and quality. Require age/session consistency with the underlying mark, reject invalid/crossed/suspect quotes, and preserve mark provenance. If quotes are unavailable, show an explicit uncertainty state and the limitations of intrinsic-only valuation; remaining time value is missing. A current-liability sanity check must not silently treat an old low ask as an executable close.

**Acceptance test:** old ask after a large underlying move, zero ask, future-dated archive row, missing quote, and stale underlying. Both API and equity history must carry freshness/source status. A historical snapshot must never fetch an unconstrained “latest” future quote.

### DA-07 — The options-income lock is not safe under lease expiry or Redis failure

**P1.** [options_income_engine.py](../../services/market-data/src/services/options_income_engine.py), lines 763–800.

The wrapper acquires `SET key "1" NX EX 1800`, proceeds after a Redis exception, and unconditionally deletes the key in `finally`. If worker A exceeds the TTL and B acquires a new lease, A deletes B's lock on completion. The reproduction replaces the lease during the actual work callback and confirms that A deletes it. If acquisition fails transiently and Redis recovers, the fail-open run can also delete another worker's lease.

The underlying code updates portfolio cash and positions from snapshots; the lock was specifically added to prevent concurrent over-opening. Failing open defeats that protection. A 30-minute TTL reduces ordinary overlap but is not a correctness guarantee.

**Solution:** use an unpredictable ownership token and atomic compare-and-delete release, with monitored renewal for long jobs. For financial state transitions, also lock/serialize the portfolio in the database and use idempotent trade-intent keys. If neither lock nor database serialization is available, skip the mutation and surface an operational error. Redis documents ownership-checked release explicitly. [Redis distributed-lock guidance](https://redis.io/docs/latest/develop/clients/patterns/distributed-locks/).

**Acceptance test:** lease expiry/reacquisition, Redis down during acquire, recovery before release, concurrent admin/scheduled calls, and process death. Neither duplicate positions nor a negative cash balance should be possible. Audit sibling scheduler locks for this pattern rather than copying a fix only into one function.

### DA-08 — Authentication is being used as authorization for global model changes

**P1.** [ML routes](../../services/ml-prediction/src/api/routes.py), `/train` at 71, `/train_meta` at 262, `/resweep_suppression` at 657 and related train/tune endpoints; [gateway proxy](../../services/api-gateway/src/api/proxy.py), `_require_auth`; [shared JWT helper](../../shared/common/jwt_auth.py), `get_current_username`.

These model mutation endpoints require a valid username token but do not require an admin role or a service capability. The gateway only adds an admin-role check for the `/admin` prefix, not `/ml`. Consequently a normal authenticated account can request global training/tuning and `resweep_suppression?dry_run=false`. The controlled actual-handler call with an ordinary identity schedules one training task; no task was executed and no real endpoint was called.

There is a related deactivation inconsistency: market-data's `get_current_user()` checks the live user row, while the shared JWT helper does not. `toggle_user()` changes `is_active` without invalidating all existing tokens. An existing, otherwise valid token can therefore remain usable on shared-helper routes until expiry/revocation. This is source-traced; no real disabled account was exercised.

**Solution:** define a capability matrix: prediction/read access, research generation, training, calibration promotion, suppression changes, trading administration. Enforce privileged actions server-side using current role/account state and narrowly scoped service identities. Preserve scheduler access by granting its principal the required capability, not by trusting all signed tokens. Queue expensive jobs with deduplication, quotas, bounded parameters, and an audit record. Enforce revocation/account-state changes consistently across services. [OWASP authorization guidance](https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html).

**Acceptance test:** real gateway-to-service requests using basic, advanced, admin, service, expired, revoked, and disabled identities. Ordinary users must not mutate shared model state. Hiding buttons is not an authorization control.

### DA-09 — Minor unrelated news can clear a material-negative signal brake

**P1.** [news storage](../../services/news-intelligence/src/services/storage.py), `persist_news_items` around lines 246–254; [signal generator](../../services/signal-engine/src/generators/signals.py), lines 2505–2529.

The new clear path is intended to accommodate corrections/retractions. Its actual condition is any inserted, classified, non-material, non-macro story for a symbol whose current hot flag is negative. It does not require positive/neutral sentiment, correction semantics, a link to the original event, or a publication time later than the flagged event.

The reproduction inserts “Unrelated minor negative story,” classified negative and non-material, and observes `_clear_hot("AAPL")`. The signal engine uses a material-negative flag to compress bullish fused scores, so removing it changes trading evidence. An old article ingested late can produce the same transition. Merely restricting the clear branch to neutral/positive stories would still let unrelated news erase an unresolved adverse event.

**Solution:** store event IDs, publication/availability time, materiality, supersession/retraction relationships, and expiry. Resolve or retract the specific event only when evidence supports it. Aggregate active material events by symbol instead of maintaining a single last-writer-wins flag. Natural TTL expiry should remain distinct from an explicit correction. Validate LLM field types rather than coercing strings such as `"false"` with Python `bool()`.

**Acceptance test:** unrelated positive, unrelated negative, older delayed, macro, genuine linked correction, and two concurrent material events. Only a qualifying correction or expiry should remove the relevant negative event. Typed classifier validation is a hardening recommendation; the reproduced finding is the incorrect clear transition.

### DA-10 — Broker fill confirmation can be lost in a mixed batch

**P2; currently latent in the inspected paper data.** [paper engine](../../services/market-data/src/services/paper_trading_engine.py), entry polling lines 325–401 and exit polling 404–491; [scheduler](../../services/market-data/src/services/scheduler.py), standalone exit poll call near 1609.

The exit poller commits immediately when the broker fill price differs, incrementing `updated`. An unchanged-price fill only sets `broker_exit_fill_confirmed=True`. At the end it commits those flags **only if `updated == 0`**. If a changed-price trade is processed first and an unchanged-price trade later, the later flag is never committed before the owned session closes.

The reproduction's two trades end in memory as `[confirmed, confirmed]`, but the last commit captured `[confirmed, unconfirmed]`. The entry poller has the same conditional-commit shape. This can cause repeated polls and stale status; this specific defect does not by itself prove duplicate cash credit. An externally supplied session may later be committed by its caller; the reproduced path owns/closes its session, as the scheduled call does.

**Solution:** always commit all successful flag updates under an explicit transaction strategy, or commit each independently handled trade consistently. Distinguish “price reconciled count” from “rows changed count.” Preserve the larger lifecycle work for idempotency, partial fills, cancellation/rejection, and account reconciliation.

**Acceptance test:** changed-price then unchanged-price, reversed order, unchanged-only, failure between successes, and a fresh database session reading the resulting flags. Mock assertions on in-memory booleans alone are insufficient.

### DA-11 — Returned optimizer risk/return metrics ignore its cash allocation

**P2.** [optimizer methods](../../services/portfolio-optimizer/src/optimizers/methods.py), `ai_allocation` at 311, especially lines 379–388; [portfolio UI](../../frontend/src/pages/portfolio.tsx), expected-return/volatility cards near 336.

The function returns `w_scaled = w × (1−cash_floor)` and the cash allocation, but computes metrics using unscaled `w`. Its comment deliberately favors comparability of fully invested sleeves. The UI labels the results as portfolio expected return/volatility without that distinction.

With the actual default 5% cash buffer and one retained asset having 20% annualized volatility, the returned portfolio is 95% asset / 5% cash, yet reported volatility is 20% instead of 19% under the model's constant-cash assumption. A sleeve expected return of 8% corresponds to 7.6% before cash interest, not the displayed 8% portfolio return. This is not proof the optimizer's expected returns are accurate forecasts; it is an internal allocation/metric mismatch.

**Solution:** calculate portfolio metrics on returned weights with an explicit cash return/rate and base currency. If sleeve metrics are useful, return separately named fields. Compute drawdown and Sharpe from the same total-portfolio return path. Continue surfacing infeasible weight-cap fallbacks; a warning does not make a constraint-violating fallback suitable for automatic execution.

**Acceptance test:** zero, default, and larger cash allocations; all-cash result; portfolio weights plus cash sum to one; independently recomputed variance and return match the response/UI.

### DA-12 — Option yield and collateral use different denominators

**P2, metric-definition/ranking concern.** [options-income scoring](../../services/market-data/src/services/options_income_engine.py), `_score_contract` at 224; [options-income UI](../../frontend/src/pages/options-income.tsx), annualized yield near lines 513/582.

Both strategy types report `premium_bid / current_stock_price × 365/DTE`. However, a cash-secured put reserves `strike × 100`, while a buy-write reserves underlying price × 100. The ranking and minimum-yield filter therefore do not compare premium income per unit of reserved capital on the same basis.

For spot $100, strike $90, premium $1, 30 DTE, the displayed annualized figure is 12.17%. Annualized premium on the $9,000 reserved cash is 13.52%. The existing value is mathematically a premium-on-spot yield; it must not be presented or used as if it were return on reserved collateral.

**Solution:** preserve the original feature under an explicit name if it is part of the historical ranking study. Add strategy-specific `premium_yield_on_collateral`, net of modeled costs, and keep expected total strategy return separate from premium yield. Version and re-evaluate ranking weights if changing the denominator; do not silently reuse weights calibrated on the old definition. Annualization does not guarantee repeated reinvestment at that yield or account for assignment losses.

**Acceptance test:** covered calls and puts with differing spot/strike, multiple contract counts, and costs. UI, filter, ranking, and realized-return denominator must either agree or identify their different definitions.

## 4. Previous findings: credit fixes, preserve remaining scope

| Earlier item | Current disposition from this review |
|---|---|
| PT-H01: masked base R:R default | **Remediation present:** explicit `resolve_min_rr_ratio` / `resolve_regime_min_rr_ratio` and manual/calibrated mode. The earlier “all portfolios secretly use calibrated 2.25” diagnosis remains incorrect. Do not reopen the old bug unchanged. |
| PT-H03: entry/exit config mismatch | **Remediation present for new trades:** `exit_config_snapshot` is captured at entry and monitoring uses `_trade_cfg`; legacy positions intentionally retain prior behavior. Full migration/integration coverage was not rerun here. |
| PT-H08: diagnostics disappear after four hours | **Persistence added:** `PaperEntryScanLog`, nested transaction write, deployed table. It still does not replace an immutable per-candidate feature/policy/outcome history. |
| A01–A03: broker entry/exit basics | Preflight/exit ID/poll/manual-close work has landed per source and implementation reports. The durable order lifecycle remains larger than those patches; DA-10 finds a new commit-boundary gap inside the added polling. |
| E09: failed flow/dark-pool send consumes cooldown | Source now releases cooldowns claimed by the failed attempt. A transactional delivery ledger/outbox is still separate work. |
| E07: pooled AI email accuracy badge | Recent cohort-label work is recorded and should receive credit. It does not establish paper-trade profitability or options P&L. This audit did not independently recompute every email cohort. |
| Options equity omitted short liability | Liability subtraction exists. DA-06 addresses stale quote selection/provenance, not absence of the liability term. |
| Options settlement used wrong-session fallback | Expected-session lookup exists. DA-05 concerns finality within the correct session, a different failure. |
| PT-H04–H07 and broader UW roadmap | Experiment isolation, horizon-specific UW targets, exit reachability, calibration methodology, data provenance and executable fills still require the broader validation work; do not infer closure from unrelated fixes. |

Relevant implementation records: [September 19 batch](2026-09-19-four-audit-implementation-batch.md), [September 21 deferred items](2026-09-21-deferred-audit-items-batch.md), [horizon audit review](2026-09-19-paper-trading-horizon-audit-review.md), and [September 23 session index](2026-09-23-session-index.md).

## 5. Trading and measurement recommendations

**Optimize achievable net expectancy and controlled drawdown, not a headline win rate.** A more accurate direction classifier can still lose money when fills, payoff size, costs, and exit timing are unfavorable. A high-premium short option is not automatically a high-quality trade. These recommendations are a research/engineering program, not a promise of returns.

1. **Separate four claims in every dashboard and email:** direction prediction, setup reaching a target before invalidation, realized simulated trade P&L, and broker-executed net P&L. For options, separate underlying direction from contract P&L. Include horizon, entry convention, costs, policy version, sample count, distinct entry sessions, and unresolved observations.
2. **Preserve point-in-time availability.** Congressional/13F filing-date entry is an improvement over trade/report date, but a calendar date does not establish availability before that day's closing auction. Store actual disclosure/ingestion time and use the next executable event where timing is unknown. Do not describe all filing-day-close returns as reachable without that check. This is a design limitation, not a new quantified alpha claim.
3. **Use matched controlled experiments.** Candidate stream, session, symbol universe, risk budget, fills and costs must match. Separate entry-threshold experiments from exit-management experiments. Shared cross-portfolio symbol caps must not let one experimental arm censor another.
4. **Treat UW as measurable incremental evidence.** Compare the same policy with and without timestamped UW features; evaluate effects on net results, adverse excursion, turnover, and drawdown. Distinguish raw trade-flow evidence from estimates derived from chain volume. Freshness/missingness must be features or explicit eligibility states, not silent neutral values.
5. **Require distinct option playbooks.** Directional debit options, spreads, covered calls, and puts have different capital/payoff/expiry mechanics. Include quote spread, liquidity, contract multiplier, expiration, early assignment, dividends, event risk, and portfolio exposure. The current synthetic close-at-expiry income engine is not a full options broker simulator.
6. **Re-evaluate by code/policy cohort.** Separate pre-fix data, post-fix entries, and merely post-fix exits. Today's 126 closed equity trades still do not support a universal “best” threshold, and nine unresolved put positions do not support a realized options win rate.

A congressional query lead was checked and not promoted to a current-production finding: its candidate disclosures are not bounded to the price CTE's 500-day start, which could shift sufficiently old entries to the first available bar. The production query found **zero** qualifying purchases older than that window. Add a bounded-entry invariant before the dataset ages into it; do not claim this explains current leaderboard returns.

## 6. Architecture changes with the highest payoff

The recurring problem is that multiple functions implement different versions of the same contract. The proposed architecture should make those contracts explicit rather than adding another score or layer of gating.

| Contract | Proposed owner/representation | Required invariant |
|---|---|---|
| Evidence availability | Versioned feature envelope: source, observation time, availability time, session, finality, missingness | A historical decision consumes only information available then |
| Signal/model semantics | Feature/target schema registry and promotion manifest | Training and inference use identical definitions; probabilities identify their event/horizon |
| Trading policy | Shared pure policy functions, immutable decision snapshot | Main, fallback, replay, UI and alerts resolve the same policy for the same inputs |
| Orders and cash | Durable intent → submitted → partial/filled/cancelled/rejected state, fill ledger, reservations | Cash/holdings change once per fill, not once per request or polling attempt |
| Valuation | Timestamped marks with source/quality and total-portfolio accounting | Every equity number can be reconciled and its uncertainty explained |
| Alerts | Observation/setup/delivery records plus transactional outbox | A generated candidate, approved setup, attempted message, and delivered alert remain distinguishable |
| Authorization | Capability checks at each mutating boundary, scoped service identity | A valid token alone cannot change global trading/model state |
| Concurrency | Owned leases plus database serialization/idempotency | Lock loss or retries cannot create duplicate economic events |

Use a shared library for pure policy/financial calculations first; a large service rewrite is unnecessary to begin enforcing these invariants. Persist decision and execution events so later performance analysis can reconstruct exactly what happened. Version interfaces when economics change, including yield denominators and target definitions.

## 7. Repair sequence and acceptance gates

| Sequence | Work | Completion evidence |
|---|---|---|
| 1 — Before first option expiry | DA-05 settlement finality, DA-06 quote provenance, DA-07 concurrency | Expiry-day/time/holiday tests; stale-mark diagnostics; overlapping-worker tests; expected cash/position/equity reconciliation |
| 2 — Protect global state | DA-08 capabilities and deactivation consistency | Gateway-to-service role/account-state matrix; authorized scheduled jobs still function |
| 3 — Restore model evaluation validity | DA-01–03 data ordering, feature/target contract, nonoptional purging | Frozen datasets, explicit timestamps, disjoint evaluation, shared incumbent/challenger holdout, reproducible model manifest |
| 4 — Repair decision evidence | DA-09 news lifecycle; retain remaining UW/alert provenance work | Unrelated/older headlines cannot clear material events; every modifier has attributable evidence |
| 5 — Reconcile reports and execution | DA-04, DA-10–12 | Ledger-to-curve equality, persisted fill flags, allocation-to-metric equality, explicit yield definitions |
| 6 — Resume controlled strategy evaluation | One reference plus one challenger per market/style/playbook | Prospective net expectancy, risk/cost sensitivity, mature outcomes and confidence intervals; no silent threshold changes |

Do not enable real automatic trading merely because the new reproductions stop failing. The pre-existing durable broker lifecycle, partial-fill handling, failure/restart recovery, options execution support, and account reconciliation remain prerequisites. Sandbox is the appropriate place to verify those end-to-end behaviors.

## 8. Verification details and reproducibility

**Twelve offline reproductions completed successfully.** They demonstrate the audited behavior; they are not tests declaring that behavior desirable. Run:

```bash
python docs/audits/evidence/2026-09-23-deep-audit-reproductions.py
```

The script needs the project's numpy/pandas dependencies. It executes selected actual AST nodes/functions with controlled infrastructure and market inputs. It makes no network/database calls, runs no background training tasks, and writes no files. Examples of the output are retained in the evidence JSON.

**239 existing tests passed**, as follows:

| Test scope | Passed |
|---|---:|
| Strategy-engine suite | 55 |
| Selected ML meta/ensemble tests | 23 |
| Selected market-data income/broker-poll/scan-log tests | 76 |
| Optimizer and endpoint tests | 27 |
| News hot-flag/macro/classification tests | 25 |
| Gateway synchronous proxy/auth/route tests | 33 |

Run service tests from the service directory with `PYTHONPATH=.:../../shared`; initial root-directory invocations failed collection because `src` was not importable, and were corrected. The full gateway run stopped making progress and was interrupted; the bounded synchronous rerun passed with three async tests deselected. **The complete gateway/async/WebSocket suite is not certified green by this audit.** No all-repository test-pass claim is made.

The passing tests miss the reproduced boundaries: nonzero terminal liquidation costs, global chronology across symbols, semantic rather than numeric feature equality, small-data embargo removal, unfinished daily bars, stale mark dates, lost lock ownership, mixed commit branches, and unrelated-news supersession.

Production SQL used read-only transactions and eight-second statement timeouts. Runtime verification read hashes and filesystem metadata. Automatic approval review rejected a proposed `joblib` model load because deserialization can execute code; that operation was not performed. The safe alternative established file existence/hash/mtime and source deployment, but **the bundle's AUC, training metadata, and current contribution to real predictions remain unverified**.

The evidence JSON contains aggregate counts, selected source hashes, artifact filesystem metadata, reproduction outputs, and test totals. It contains no credentials, account identifiers, email addresses, or individual user data. Earlier audit documents and application code remain unchanged.
