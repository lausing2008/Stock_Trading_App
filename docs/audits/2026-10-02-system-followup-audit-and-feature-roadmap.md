# System follow-up audit and feature roadmap — October 2, 2026

The highest-value next step is to make a recommendation prove its eligibility, preserve its decision-time evidence, and measure its eventual result. More signals, higher displayed confidence, and more trades are not success criteria by themselves.

This review found **four actionable defects or presentation gaps and one additional defensive-validation gap**. Two repeat the system's recent failure pattern: a repaired consumer coexists with another consumer using different rules. The recommended work combines those fixes with a measurable path toward better stock and option decisions.

## Scope and evidence

- Local baseline: `f9b6edb36ef43ecbb365b4f7afd3f5372bbbbcc1`.
- During this review, concurrent work advanced HEAD to `9d4f5b91` and included the two newly written evidence files. This audit did not issue a commit. The probes were rerun successfully at that HEAD; the results below still reproduce.
- Reviewed recommendation construction and rendering, broker submission/close boundaries, recent signal and news remediation, training sample instrumentation, and the existing measurement/Jev roadmap. This is a broad follow-up with targeted execution, not proof that every service and branch is defect-free.
- Executed the [local probe](evidence/2026-10-02-system-followup-probes.py); its [captured results](evidence/2026-10-02-system-followup-results.json) reproduce the cases below. The script executes the real pure strategy module and bounded, AST-extracted route/broker functions. Its isolated SQLite schema models only the fields those broker functions use.
- **No fresh production interrogation, full-suite run, PostgreSQL concurrency run, browser inspection, deployment, flag change, broker contact, or email send occurred in this review.** Previous production measurements below are attributed to their reports, not independently reverified today.
- Concurrent edits to the signal generator, its tests, and the remediation report were present. New HK trace/baseline files appeared during the review. They were not modified; their conclusions are not treated as this audit's independently verified findings.
- Only this document and its two evidence files were added. `.claude/CLAUDE.md` is unchanged.

These probes are **defect witnesses**: they assert the observed broken behavior to preserve evidence. Once fixed, convert the scenario into a separate acceptance test asserting the desired behavior; a passing witness is not a release gate.

## Findings

| ID | Priority | Finding | Evidence and exposure |
|---|---|---|---|
| SF-01 | P1 before M25 activation | Broker claim does not atomically recheck that an intent remains eligible | Local database witness; deferred path reportedly disabled, no production incident demonstrated |
| SF-02 | P2 | Original options legs still accept prices rejected by the new matrix | Executed actual route calculation and matrix helper with the same crossed quote |
| SF-03 | P2 | Any positive share count enables a covered-call recommendation | Real matrix execution with one share; advisory output, not an actual order |
| SF-04 | P2 | Last-trade provenance disappears at the recommendation/UI boundary | Real last-only recommendation plus frontend/type inspection; no browser rendering test |
| SF-05 | P3 | Matrix accepts expired contracts | Real matrix witness; normal single-symbol route already filters expired dates upstream |

### SF-01 — Closed intents can still be claimed for broker submission

Locations: [broker_submission.py](../../services/market-data/src/services/broker_submission.py), `claimable`, `begin_submission`, `submit_pending`; [paper_portfolio.py](../../services/market-data/src/api/paper_portfolio.py), `_close_one_paper_trade`.

`claimable()` requires an open trade, no broker order ID, the deferred route, a retryable state, and attempts below the cap. `begin_submission()` atomically checks **only ID and retryable state**. The manual close helper changes `stage` to `closed` without cancelling a pending submission state.

**Witness:** select an open/pending intent; another session commits its closure; call the actual `begin_submission()` on the previously selected intent. It returns `True`, refreshes a row with `stage='closed'`, and records `broker_submission_state='submitting'`. `submit_pending()` proceeds from a successful claim to the provider callback without a further stage check. The probe stops before any provider call.

This sequential interleaving proves a missing predicate; it does not claim SQLite establishes PostgreSQL lock behavior. The other selection conditions can similarly become stale, although only the closed-stage case was executed here.

**Solution:** share the eligibility predicate between discovery and the atomic claim, including open stage, route, no existing order ID and attempt budget. Coordinate cancellation with the same transition: pending→cancelled may succeed only before dispatch owns the intent. Once submitting, an exit must reconcile or cancel broker exposure; a local paper close cannot establish broker cancellation. Also define an intent expiry and maximum permissible price drift before submission.

**Acceptance:** PostgreSQL race tests through the real dispatcher and close/cancel paths. If close wins before claim, provider calls must be zero. If claim wins, close must explicitly handle potentially submitted exposure. Test route changes, order-ID arrival and the attempt boundary. Do not enable M25 based only on the existing single-dispatcher test.

### SF-02 — Two option displays still disagree about whether a quote is usable

Locations: [routes.py](../../services/market-data/src/api/routes.py), `compute_options_game_plan`; [options_strategies.py](../../services/market-data/src/services/options_strategies.py), `_mid`; [OptionsGamePlanCard.tsx](../../frontend/src/components/OptionsGamePlanCard.tsx).

The new matrix rejects a crossed quote. The original protective-put/covered-call calculation still independently uses `(bid + ask) / 2` if either side is nonzero.

**Witness:** bid **12**, ask **2** produces a covered-call `mid_price` of **7** in the original route calculation. The matrix's `_mid()` returns `None` for the same input. The original card displays the route's number as credit. This is a residual in the sibling path, not evidence that the matrix fix itself failed.

The original calculation also has no equivalent finite/two-sided validation. A one-sided quote is mechanically averaged with zero. Those additional cases are visible in the expression but were not separately executed in this probe.

**Solution:** one quote-validation/valuation contract consumed by both old and new cards, snapshot jobs, and any email that uses these outputs. Invalid quotes should yield an unavailable reason rather than a numerical payoff. Preserve whether a valid number is an archived quote, live quote, last trade or scenario assumption.

**Acceptance:** test the complete response with crossed, missing-side, zero, non-finite and valid quotes; old legs and matrix must agree on admissibility. Verify the resulting UI and any downstream snapshot output, not just the pricing helper.

### SF-03 — One share is treated as a covered position

Location: `options_strategies.py`, `build_strategy_matrix` passes `holds_shares=bool(shares and shares > 0)` to `_recommend`.

**Witness:** `shares=1`, normal positive call prices and rich IV selects `covered_call`, accompanied by “You hold shares, so covered calls and collars are available.” The payoff is still per standard 100-share contract. The frontend separately says 100 shares are required, so the detailed warning and the personalized recommendation disagree.

This is an advisory eligibility defect; the card explicitly does not place trades. It is not evidence that the broker accepted an uncovered order. The normal endpoint also omits IV rank, so the rich-IV witness exercises the matrix contract rather than claiming that exact endpoint configuration occurs today.

**Solution:** distinguish an illustrative strategy from one feasible for this account. Use deliverable units, contract multiplier, available unencumbered shares and existing short-call coverage to compute the maximum covered contract count. For standard contracts, one share cannot cover one call. Require a selected account/position snapshot before presenting an account-qualified recommendation. A buy-write that acquires the missing shares is a separate funded plan.

**Acceptance:** 0, 1, 99, 100, 150 and 200 shares; shares reserved for existing calls; adjusted deliverables; stale holdings; concurrent coverage reservation. Render an explicit “requires additional shares” result where appropriate. OIC's [covered-call description](https://www.optionseducation.org/strategies/all-strategies/covered-call-buy-write) describes equivalent stock coverage and illustrates 100 shares for a standard call.

### SF-04 — Recording price provenance does not ensure the user sees it

Locations: `_mid`, `_leg`, `_recommend`; [OptionStrategyMatrix.tsx](../../frontend/src/components/OptionStrategyMatrix.tsx); [api.ts](../../frontend/src/lib/api.ts), `OptionStrategyLeg`; `get_options_game_plan` in `routes.py`.

**Witness:** no bid or ask, last price **2.10**, yields a primary covered-call recommendation with `price_source='last_trade'` and `spread_pct=None`. The frontend leg type omits `price_source`; the component does not render it. Its spread warning cannot reveal the problem because a missing spread is not greater than the warning threshold. The primary heading says “Best fit right now.”

There is broader chain-source/as-of labeling elsewhere; this finding does not claim the whole page conceals archived data. The specific gap is that the new leg-level provenance never reaches the rendered distinction. A two-sided archived quote is also not made live simply by naming it `quote_mid`.

The route additionally assigns the same `chain_as_of` variable after both independently fetched chains. If their timestamps differ, the final response does not retain both timestamps. That is a source-traced provenance limitation, not an independently reproduced production stale-chain incident.

**Solution:** preserve quote/trade timestamps and provenance on every leg, plus underlying-price time. Separate `illustrative`, `quote_validated`, and `eligible_for_execution` states. Last-only or undated inputs may support clearly labeled research scenarios, not a current executable estimate. Show conservative buy-at-ask/sell-at-bid scenarios alongside midpoint illustrations; include fees and slippage assumptions. Validate timestamps again immediately before an order.

**Acceptance:** rendered tests for last-only, archived and live data; mixed-age legs; missing timestamps; wide spreads. Missing evidence must not produce a reassuring empty badge. OIC's [liquidity explanation](https://www.optionseducation.org/referencelibrary/faq/general-information) supports treating spread and execution quality as material, rather than inferring fillability from a last price.

### SF-05 — Expiry validity remains an upstream assumption

Location: `_leg` checks a nonempty expiry but does not reject negative/unknown DTE.

**Witness:** on October 2, an October 1 call remains the matrix's primary recommendation with DTE **−1**. The ordinary `get_options_game_plan` path uses `_nearest_expiry_in_dte_window`, which already skips past expiries. Therefore this is a defensive contract gap for alternate callers, replay or stale inputs, not proof that the normal endpoint currently serves expired options.

**Solution:** validate expiry inside the recommendation boundary too. Separately define same-day expiry eligibility using exchange/session and last-trading-time rules; do not treat DTE zero as sufficient proof of tradability. Test malformed dates and expired cached inputs. Retain historical plans as explicitly historical records rather than turning them into current recommendations.

## Current work that should not be restarted or overclaimed

| Area | What the current work establishes | What remains |
|---|---|---|
| SR-01/02 | Shared conviction interpretation and timestamp parsing are implemented | Runtime contract coverage and malformed/missing evidence policies; no demonstrated profitability gain |
| SR-03/04 | Revised flow send accounting and event identity are reported implemented | Observe scheduler cycles and real delivery reconciliation; email acceptance is not an executed trade |
| SR-06/07 | Matrix payoff validation and expiry-consistent collar construction exist | SF-02–05 and account/quote eligibility |
| M13 | Base training ledger is built; selection stages have a documented read-only production measurement | First complete instrumented fit, independent observations, purge/calibration/promotion lineage |
| M14 | Inventory executes and checks consistency with today's suppression rule | Actual runtime selection/weight trace and forward quality; unsuppressed directory counts are not serving-model counts |
| M15 | Atomic exposure reservation and mixed-writer tests are reported | Fresh-mark policy, pending broker exposure, FX and assignment representation; inspect actual deployed state before changing policy |
| M20 | Outbox components and scheduler wiring are reported built | First-family activation/cutover, runtime acceptance evidence, other families and delivery callbacks |
| M25 | Durable intent path exists behind a disabled flag | SF-01, sandbox lifecycle evidence and explicit activation; flag-off retains the old boundary |
| News/earnings | Ingestion, phase classification and attribution protections have progressed | Event-linked resolution, retrospective attribution, committed actuals reconciliation and real-release observation |
| Jev | Design and experiment dependencies are recorded | Provider evaluation and durable paired decisions/outcomes; email delivery is not a prerequisite for a decision-only shadow experiment |

Recovery markers and recovery digests remain separate decisions. Do not infer their current existence from older TTL estimates, or replay historic signals as current trade advice.

## Improving signal accuracy and confidence

### Measure the prediction actually consumed

Each published metric must name market, horizon, direction, policy/model version, opportunity population, availability cutoff, outcome definition and weighting. Keep September-only results separate from October post-fix cohorts; fixes deployed later cannot be credited with September outcomes.

Create one immutable opportunity record before gates, and retain rejected opportunities for **shadow evaluation**. Link the selected decision, plan, notification, order, fills and outcomes through IDs. Report separate populations:

1. Eligible opportunities and their directional outcomes.
2. Recommended plans and their modeled executable outcomes.
3. Accepted notifications and their timing.
4. Filled trades and realized portfolio results.

An underlying price increase is not proof that a purchased call made money. A bearish signal need not be an authorized short. An exit recommendation reduces an existing position; evaluate it against holding that position under a declared policy.

### Repair evidence limits before changing thresholds

The [base-ledger report](2026-10-02-base-training-ledger.md) now documents 755 bars → 503 feature-complete bars for its measured symbols. The 252-bar feature warm-up is material. The MU/GROWTH stored split counts reconcile arithmetically to 14 reporting rows, but the report correctly distinguishes that calculation from replaying the original fit.

Next experiments, after the first full instrumented fit:

- Compare longer point-in-time history with the existing history, checking corporate-action handling and historical availability.
- Compare the current feature set with a shorter-lookback version. Do not silently shorten features or fill missing values just to retain rows.
- Compare separate per-symbol models with a pooled model that preserves market/horizon distinctions, using both time-held-out and symbol-held-out evaluation. More pooled rows do not automatically supply independent evidence.
- Evaluate on the full eligible opportunity population, including small moves excluded by dead-zone training. Either model move occurrence and direction separately or compare a return-distribution target. A classifier conditioned on a future large move does not by itself estimate an unconditional chance of profit.
- Show `n_metric_rows`, positive/negative support, effective event/session clusters, validation status and runtime contribution on each model evidence card. Retain “unknown” when diagnostics are unavailable.

Treat the existing fused score as a score until calibrated to a defined outcome. Use out-of-time calibration with reliability plots, Brier score, log loss, coverage and uncertainty by supported slice. Calibration cannot create predictive information that is absent. Scikit-learn's [calibration guidance](https://scikit-learn.org/stable/modules/calibration.html) also emphasizes unbiased calibration data; Brier/log loss reflect more than calibration alone, so do not interpret one scalar as proof of reliability.

## Better options alerts and entry timing

**An option alert should express a thesis and a feasible plan separately.** Preserve observed flow, inferred direction, corroboration, quote time, suggested structure, eligibility and cancellation conditions. UW flow can represent hedges or multi-leg activity; do not convert a call/put label into a standalone trade direction. Count distinct symbol/session opportunities as well as contracts, and preserve conflicting labels.

Rank feasible plans by expected after-cost result and downside under declared scenarios, not by IV rank or premium collected alone. Include underlying move, time passage and volatility-change scenarios; probability of profit, expected value and maximum loss are different quantities. Cash-secured puts retain equity downside and covered calls cap upside.

Run a **paired prospective entry-timing experiment** before choosing a universal “best” trigger:

- Freeze the same eligible signal, data snapshot, exits and risk budget for both arms.
- Baseline: the next realistically executable quote after the decision. Challenger: one predefined trigger, such as a pullback/reclaim, with an expiry and invalidation rule.
- Record triggered, expired without entry, invalidated and unavailable-price outcomes. A no-fill is not a losing trade, but it still affects opportunity coverage and portfolio return.
- Use quote availability and latency realistically; never select the best intrabar price after seeing the future. If stop and target both fall within a bar and sequence is unknown, use an explicit conservative rule or mark the result ambiguous.
- Measure net expectancy, drawdown, turnover, capital use, fill rate, adverse/favorable excursion and time to entry. Keep horizon, direction and market slices separate.

Promote a challenger only after a predeclared forward window and independent-event requirement, with clustered uncertainty and drawdown/operational guardrails. A higher win rate with worse average loss or lower net return is not an improvement.

## Faster and more useful news intelligence

Reuse the existing news-source integrations before buying more feeds. Separate these clocks: provider publication, first receipt, normalized event, decision ready, queued, provider accepted, delivery callback and first executable market quote. Alert on the slow stage, not just total latency.

Build a durable issuer/event timeline: canonical issuer identity, report/event identity, phase, source evidence, revision history and unresolved impact. A positive article may resolve an adverse event only through an explicit linkage or reviewed resolution rule. Delivery reliability cannot solve event attribution.

For earnings, issue staged outputs: scheduled briefing; confirmed release with sourced actuals; reconciled surprise/guidance; observed price/volume response; conditional plan. Preserve fiscal-period and GAAP/non-GAAP distinctions. Avoid a direction forecast if the actuals, expectations or relevant market quotes are missing. “Release received, analysis pending” is useful information when honestly labeled.

Evaluate reaction predictions from the time the system could act, with after-hours spreads and the next session treated explicitly. Do not score against the pre-release close if the strategy could not enter there. Show raw return and benchmark-adjusted return separately; industry sympathy moves should not be mistaken for issuer-specific skill.

## Recommended product features and implementation order

| Order | Feature | Concrete behavior | Acceptance / primary measure |
|---|---|---|---|
| 1 | Shared option eligibility and valuation | Old/new cards, snapshots and alerts use one contract; show research versus account-feasible plans | SF-02–05 cases agree end to end; no undated quote silently appears execution-ready |
| 1 | Broker intent reconciliation console | Shows pending, submitting, unknown and observed fills, evidence and reserved exposure | SF-01 race passes on PostgreSQL; no resolution frees potentially live exposure without evidence |
| 2 | “Why no trade?” portfolio view | Signal supply → entry gates → risk reservation → order → fill, with no-opportunity distinguished from missing data | Every scan count reconciles; display first rejection and separately sampled all-gate diagnostics |
| 2 | Opportunity and outcome ledger | Decision-time snapshots and durable joins across signals/plans/orders | Counts reconcile, revisions remain inspectable, no accepted-email row is labeled a fill |
| 3 | Model evidence cards | Runtime artifact/version/weight plus real metric denominators and unavailable diagnostics | Can trace one displayed signal to the actual loaded artifact and contribution |
| 3 | Earnings/news event timeline | Release phases, source facts, unresolved impacts, revisions and latency | A real release can be traced source→event→decision→notification; unrelated good news cannot clear a brake |
| 4 | Entry-timing experiment lab | Predeclared baseline/challenger, shared snapshots, explicit no-fill accounting | Forward after-cost comparison with uncertainty and equal risk budgets |
| 4 | Option scenario and execution-quality panel | Move/time/IV scenarios, bid/ask assumptions, actual fill slippage | Option outcomes use the contract, deliverable and executable prices, not underlying direction alone |
| 5 | Intervention registry for UW/Jev/calibration | Feature flags, deterministic assignments, policy versions and rollback | One intervention per experiment; providers unavailable produces a declared fallback, not a hidden treatment change |
| 5 | Capability and data-quality monitor | Per-operation readiness, stale data, missing inputs and growing unknown queues | Failure inhibits only the dependent action; liveness and trading eligibility stay distinct |

Orders are priorities, not calendar promises. Features already partially built should be extended rather than replaced.

## Measurement contract for every experiment

Store an immutable specification before collecting outcomes: owner, hypothesis, baseline version, challenger version, cohort dates, eligibility rule, assignment unit, outcome horizon, primary metric, costs, minimum independent observations, stopping rule and promotion limits. Use symbol/session clusters where observations overlap; a thousand contracts need not be a thousand independent bets.

For decision experiments, primary metrics should include net return on a declared capital base and drawdown; report win rate, average win/loss, expectancy, turnover and exposure alongside them. For calibration use the defined target and reliability metrics. For delivery use event coverage, latency percentiles, suppress/defer reasons and unknown counts. Do not mix their denominators.

For stock and option portfolios, compute equity from cash plus consistently valued positions and realized/unrealized P&L, with costs, FX, assignments and corporate actions handled explicitly. Label mark quality. Compare with cash and a suitable market/risk benchmark. Publish missingness and reconciliation failures beside each result, and do not promote from an incomplete result just because its visible rows look good.

Recommended sequence: fix the confirmed boundary defects → validate one complete decision/outcome lineage → observe the first instrumented fit and post-fix cohort → run a bounded entry-timing experiment → add UW, Jev and calibration challengers separately. Existing operating fixes improve correctness; none of this review establishes an earned trading edge or guarantees better returns.

## Verification record

Executed successfully:

```bash
python docs/audits/evidence/2026-10-02-system-followup-probes.py
```

Captured: one-share covered-call recommendation; last-only primary with missing spread; expired primary; legacy crossed-quote price versus matrix rejection; closed trade successfully transitioned to submitting. No application tests were changed or claimed green. Production occurrence, browser behavior and concurrent PostgreSQL lifecycle tests remain explicitly unverified in this pass.
