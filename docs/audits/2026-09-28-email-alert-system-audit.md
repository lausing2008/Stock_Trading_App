# Email alert system audit and remediation verification — September 28, 2026

**The email findings are still open at `5509dd76`. The referenced email reproduction file demonstrates defects; its successful exit means those defects reproduced, not that the application passed a regression test.** The new commits fix two separate settlement/training findings. They do not change the email scheduler or renderer.

Review date uses America/Los_Angeles. This report completes the ongoing email review and answers the request to check Claude's latest changes. It supersedes no historical evidence: earlier observations remain dated snapshots.

## What changed, and what did not

| Item | Independently checked result |
|---|---|
| `2026-09-28-email-alert-reproductions.py` | Added to git by `fa6095b8`. Still explicitly asserts defective behavior. All seven probe groups run successfully against current source. Six groups exercise function behavior; the preference inventory is static AST analysis. |
| Email application changes | No diff from `b5332a3` to `5509dd76` in `scheduler.py`, `email_service.py`, or `shared/common/alert_prefs.py`. Committing the reproduction file did not repair those paths. |
| Settlement read-order defect, PV-01 | The specific counterexample is fixed: initial 99.90, atomic refresh to 100.10 plus successor bar, two corroboration reads, returned 100.10. Independently reproduced with actual functions and temporary SQLite tables. Broader finality limitations remain below. |
| Estimated label availability, PV-02 | Actual trainer availability block returns October 1 for the sparse ten-bar example, drops missing/unprinted targets, and preserves a later October 5 observed exit. Actual meta availability block also uses October 1 and drops an unprinted target. The estimate helper is deleted. |
| Targeted remediation suites | **90 passed** in market-data's income/concurrency suites; **70 passed** in the R01/R03 ML suites. |
| Earlier email-related suite run during this review | **921 passed, 1 skipped** across 56 files at `b5332a3`. This predates `5509dd76`; the audited email implementation is unchanged. These passing tests did not catch the defects reproduced below. |
| Deployment / nine sabotages / served tiers | Claude-reported for the latest fixes. This continuation did not independently rerun deployment drift, the mutation campaign, or browser rendering. |

Evidence: [results, source hashes and production aggregates](evidence/2026-09-28-email-alert-evidence.json), [original email defect probes](evidence/2026-09-28-email-alert-reproductions.py), [new remediation checks](evidence/2026-09-28-email-review-remediation-checks.py).

The original reproduction files are historical defect witnesses, not release gates. The older post-deployment script references the now-deleted `label_end_date()` and assumes a single corroboration call; running it unchanged against the fix is not a sound closure test. The new check permits a legitimate second read and executes current availability logic.

No application code, original evidence, or `CLAUDE.md` was edited in this review. No emails, trading actions, deployment, or production writes were performed.

## Scope and evidence strength

The review traced scheduler triggers, recipient selection, preference enforcement, cooldowns, rendering, outcome accounting, and SMTP/SES transport. It also followed conditional-order and paper-exit callers outside the scheduler, and the UW adapters and options-plan snapshots. This is source review plus selected deterministic behavioral checks, not a claim that every provider response or email client was integration-tested.

The six behavioral email probes execute actual extracted functions with controlled dependencies. Database query results and providers are mocked; these are not full PostgreSQL/provider integration tests. The new settlement probe uses real repository tables on SQLite, which verifies the read sequence but not PostgreSQL row locking. Label checks execute the actual availability blocks, not a complete model training run. No inbox or browser rendering was inspected.

Read-only production aggregates were captured earlier in this same review at **2026-09-28 23:55 UTC**. They are not a new post-`fa6095b8` deployment attestation. The bounded log sample contained 50,001 lines from `--since 24h --tail 50000`; it is not necessarily the complete preceding day. It contained 62 `email.sent` events and no captured signal-alert send/error events. **The AI Signal failure below is deterministically reproduced locally, not independently observed in that log sample.**

## Findings and remedies

### EA-01 — P1: AI Signal transitions fail before reaching the sender, then can be discarded

**Source:** [scheduler.py](../../services/market-data/src/services/scheduler.py), `check_signal_alerts`, lines 7550–7561 and the subsequent retry-limit branch.

The call supplies `new_signal=current`, but another argument evaluates `_signal_cohort_stats(session, new_signal, style)`. A keyword argument does not bind a local variable. `new_signal` is undefined in the scheduler, so Python raises before calling the email function.

The exception is treated as a delivery failure. After five non-quota failures, the job advances `last_signal` to the current signal even though no message was sent. The reproduction runs an eligible BUY→SELL transition five times: **five NameErrors, zero sender calls, `last_signal=SELL`, `last_sent_at=None`**. The same argument evaluation affects other transitions that reach this call. Webhook/push handling downstream of successful email is also bypassed.

**Remedy:** pass `current`; make optional cohort enrichment independently fallible; persist a pending notification separately from the observed signal. Never consume an unsent transition because a programming error reached a generic retry limit. Escalate repeated failures to an operator-visible dead-letter state.

**Acceptance:** run the whole job for BUY and exit transitions with real rendering and a fake transport. Force enrichment failure, transport failure, quota rejection, and process restart. Confirm intended notifications remain retryable and no duplicate trading action is introduced.

### EA-02 — P1: a bullish stock playbook can recommend taking profit below its stop

**Source:** [scheduler.py](../../services/market-data/src/services/scheduler.py), `_build_game_plan`, lines 1408–1418.

The analyst target passes when it exceeds `current_price * min(1.03, default_tp_multiplier * 0.8)`. For SWING, the multiplier is 1.12, making the acceptance threshold 89.6% of the current price. At current price 100 and analyst target 90, the real function produces entries **98.5 / 96.5**, stop **94.5**, and take-profit **90**. This is a valid-input counterexample, not an observed production recommendation.

The helper also supports squeeze-family plans, so repairing EA-01 alone does not remove this risk. Fixed percentage levels are described as support/resistance even when those levels were not measured from price structure.

**Remedy:** create one validated plan contract. For a long trade, require finite positive values and `stop < selected entry < target` after rounding. Validate breakout and pullback entries separately and compute their separate reward/risk. Reject unsuitable analyst targets; an analyst's long-term estimate is not automatically a short-horizon target. Label percentage-derived levels as such, or derive actual technical levels and retain their evidence.

**Acceptance:** targets below price, below stop, equal to entry, missing, nonfinite, and across each horizon and tick size. A plan that fails geometry must not render as actionable.

### EA-03 — P1: earnings today become a “clean runway”; missing evidence becomes bullish rationale

**Source:** [scheduler.py](../../services/market-data/src/services/scheduler.py), `_build_game_plan`, around line 1442 and its catalyst/risk fallbacks.

`days_to_earnings or 99` replaces a valid zero with 99; `days_to_earnings or "?"` replaces it with unknown. The actual function labels an earnings event today **“No earnings until 2026-09-28 (?d) — clean runway”**. The separate truthiness-based risk check also misses zero.

With no reasons or fundamentals, the same helper invents “AI signal + analyst consensus aligned,” “Technical structure improving,” and “Volume trend supporting move.” These are assertions of evidence the helper did not receive.

**Remedy:** distinguish `None`, zero, positive and overdue values explicitly, using exchange-local event time where available. Preserve unknown event timing as unknown. Generate rationale only from supplied, timestamped evidence; show an unavailable-data state instead of optimistic fallback prose.

**Acceptance:** earnings before/after market today, tomorrow, missing, overdue and timezone boundaries; empty input must never produce claims of analyst or volume confirmation.

### EA-04 — P1: technical triggers render as a different, sometimes opposite, event

**Source:** [scheduler.py](../../services/market-data/src/services/scheduler.py), `check_technical_alerts`; [email_service.py](../../services/market-data/src/services/email_service.py), `send_price_alert_email`, line 2999.

The technical scheduler passes a descriptive condition such as “MACD Bullish Cross …”. The price template recognizes only the exact string `above`; every other string becomes “fallen below.” The reproduction renders a bullish MACD condition as **“Price Alert: TEST has fallen below 0.0”**, compares a stock price with an indicator threshold, and removes MACD from the body. Its footer also says the alert will not fire again even when the caller's technical alert is recurring.

**Remedy:** use a typed event containing condition code, display label, observed indicator value, threshold, units and recurrence policy. Keep price-crossing and indicator descriptions distinct.

**Acceptance:** trigger-to-render tests for EMA crosses, MACD, RSI, new highs/lows, volume and recurring alerts; verify direction and units in both HTML and plain text.

### EA-05 — P1: many unsubscribe controls are not enforced by their sending jobs

**Source:** [alert_prefs.py](../../shared/common/alert_prefs.py), [email_service.py](../../services/market-data/src/services/email_service.py) lines 75–190, and scheduler callers.

The registry advertises 23 manageable types. Eleven scheduler families call `_filter_by_alert_pref`. Twelve advertised types lack that enforcement in their delivery paths: **signal, morning_digest, premarket_brief, squeeze_watch_revert, sr_watch, value_area, earnings_reminder, portfolio_digest, post_open_digest, theme_forecast, trade_coach, trade_exit**. An unsubscribe footer does not enforce a preference; neither `_with_unsub` nor `send_email` does so. Paper-exit delivery is an additional caller outside the static scheduler inventory.

The flow digest directly selects every user with a nonempty email, without an active-account predicate or preference check, and lacks a manageable registry type. The controlled job attempts delivery to an inactive recipient. Several direct earnings/macro emails similarly bypass the manageable-template path. Even filtered jobs fail open if the preference query raises.

Production currently had one active emailed account and no preference rows. That does **not** demonstrate an ignored production opt-out. It explains why ordinary happy-path usage may not expose this bug.

**Remedy:** enforce recipient identity, active status and typed preference at a common delivery boundary, rechecking queued mail before dispatch. Queue optional mail when preference state cannot be read. Preserve the documented essential exceptions—user-created price alerts, conditional-order confirmations, broker reauthorization and operator notices—under explicit policies. Register the additional informational families. Avoid reconstructing identity solely from an email address.

**Acceptance:** enabled/disabled/missing preference, inactive account, changed preference after enqueue, failed lookup, and every registered type. Exercise dispatch, not just footer generation or the presence of a helper name.

### EA-06 — P1: firing an alert consumes its notification before successful delivery

**Source:** [scheduler.py](../../services/market-data/src/services/scheduler.py), `check_price_alerts` around 7949 and `check_technical_alerts` around 8337.

A price alert is marked triggered and committed before the mail result is known. The reproduction makes transport return false, then runs the job again: the alert remains triggered and the sender was called only once. One-shot technical alerts have the same separation problem; recurring alerts can consume their daily send timestamp before failure.

**Remedy:** atomically record the trigger and an outbox notification. The trigger should remain true—the market event happened—but notification delivery should retain its own retryable state. Do not repair this by re-running an order or resetting a completed trade whenever mail fails.

**Acceptance:** failed send, crash before/after provider acceptance, restart and multiple workers; one economic event, recoverable notification, bounded duplicate risk.

### EA-07 — P2: global composition changes suppress recipient retries

**Source:** [scheduler.py](../../services/market-data/src/services/scheduler.py), `check_top3_conviction`, lines 7003–7011; analogous ordering in `check_sector_rotation_alerts`.

Top-3 composition is written globally before recipient delivery. If every delivery fails, an unchanged next scan exits before retrying anyone. The real job reproduction attempts a failed send once across two runs and retains `TEST:BUY` as the consumed composition. A composition change and a recipient's successful receipt are different facts.

**Remedy:** retain the global content version for computation, but track delivery per user/content version. Queue recipient notifications before advancing dispatch state; retry only failed recipients.

**Acceptance:** one success and one failure in the same batch, unchanged subsequent picks, process restart and preference changes.

### EA-08 — P1 for actionable BUY mail: freshness and decision checks still fail open

**Source:** [scheduler.py](../../services/market-data/src/services/scheduler.py), `check_signal_alerts`, around 7162–7205 and 7465–7505.

The stale-universe bug was partially fixed: a query returning only stale bars now suppresses the run. However, a failed query or entirely missing price history still assumes freshness. A recent price in any timeframe is not proof that the stored signal, fundamentals or technical inputs are fresh. Decision-engine errors/non-success responses also permit continuation without the unavailable verdict being treated as a failed BUY gate.

**Remedy:** timestamp and version the actual decision inputs. Treat freshness and safety-veto availability as explicit gates for new actionable BUY setups. A risk/exit observation can still be sent with a conspicuous degraded-data explanation; do not falsely label it a freshly confirmed trade. Use exchange-session freshness instead of one universal four-calendar-day allowance.

**Acceptance:** fresh price with old signal, missing history, query failure, unavailable decision engine, weekend/holiday and differing US/HK sessions.

### EA-09 — P2: UW direction is more certain in the email than in the evidence

**Source:** [scheduler.py](../../services/market-data/src/services/scheduler.py), options-flow candidate construction around 5130; [email_service.py](../../services/market-data/src/services/email_service.py), lines 2043 and 2250–2270.

Equal positive ask-side and bid-side premium is classified as ask-dominant through `ask >= bid`. A missing side becomes zero. No minimum classified fraction of total premium or meaningful dominance is required here. The template calls this “aggressive BUYING/SELLING.” The dark-pool template goes further and calls inferred aggressor side a measured fact.

The print itself and its price are observations; quote-side classification is an inference. It does not establish opening versus closing intent, an unhedged directional bet, or who the participant is. FINRA reporting also covers off-exchange venues other than dark-pool ATSs. [FINRA execution-venue explanation](https://www.finra.org/investors/insights/where-do-stocks-trade).

**Remedy:** represent neutral/unknown side explicitly; show classified coverage and imbalance separately; distinguish reported fields from inference. Preserve multi-leg/opening indicators and event identifiers when actually available in the subscribed feed. UW documents such fields in its streaming schema; verify the REST schema and entitlement separately rather than assuming identical coverage. [UW FlowAlert schema](https://api.unusualwhales.com/docs/kafka/types/FlowAlert).

**Acceptance:** balanced premium, missing side, mostly unclassified premium, multi-leg flows and midpoint prints. Neutral evidence must not produce a directional certainty claim.

### EA-10 — P2: event age, contract deduplication and execution readiness are conflated

**Source:** [unusual_whales.py](../../services/market-data/src/services/unusual_whales.py), `_FLOW_ALERT_MAX_AGE_HOURS=48` and adapters; scheduler `check_options_flow_alerts` and `check_dark_pool_alerts`.

The options emails now preserve provider event timestamps and no longer universally say “right now”—a real prior fix. The adapter still admits a 48-hour observation window. That is reasonable for a watch/digest but insufficient to imply a current option entry. Dark-pool data is cached for 15 minutes and the alert freshness window extends to 90 minutes.

Options candidates are stored by contract using unconditional assignment. Multiple events for the same contract therefore choose whichever row is processed last, without explicit event-time ordering. Cooldowns/seen sets also suppress events rather than preserve a durable pending event queue. Premium ranking after some suppression decisions cannot guarantee the globally largest available opportunities are selected.

**Remedy:** retain immutable event IDs and source/ingest times; select latest or aggregate explicitly. Define separate watch and actionable TTLs. Revalidate current executable bid/ask, spread, liquidity, expiry and underlying context immediately before presenting an entry. Record reasons for cooldown/cap omissions; aggregate important new flow during cooldown rather than silently treating it as already delivered.

**Acceptance:** reversed provider ordering, repeated contract with a newer/larger print, new events during cooldown, expired contracts in cache, market reopen, stale chain with fresh underlying.

### EA-11 — P2: performance badges do not establish email or options-strategy accuracy

**Source:** scheduler `_signal_cohort_stats`, `_flow_hit_rate`, outcome recording/evaluation; email signal table around line 418 and options calibration around 2082.

The per-symbol “90d signal accuracy” mixes more contexts than the specific signal being emailed. The new direction/horizon cohort uses legacy `signal_outcomes`, whose multi-window rows are selected by primary-outcome maturity; it does not use the independent horizon-resolution table. The cohort query is all-history, not a 90-day estimate. In isolation its “averaged” wording is preferable to a forecast, but selection and window definitions must remain visible.

Flow outcomes record detected candidates before recipient-delivery decisions. Options calibration measures the **underlying's** favorable move from next-session close to a +10-calendar-day target. The email now explicitly says this is not option P&L; credit that fix. Dark-pool `is_correct` measures an absolute move exceeding its hurdle, not correct BUY/SELL direction. Digest “next-day” wording obscures that its reference is the next-session-close entry, not necessarily the day after the alert.

**Remedy:** separate candidate diagnostics, delivered-alert outcomes, executed paper trades and broker fills. Freeze the actual sent plan and cohort definition. Show direction, horizon unit, signal-date range, resolved/pending counts and unique sessions. Use independently matured windows and evaluate the chosen strategy after realistic execution costs. Directional correctness alone is insufficient for options because option value also depends on time and volatility. [OIC options pricing](https://www.optionseducation.org/optionsoverview/options-pricing).

**Acceptance:** Friday/holiday alerts, unresolved long-horizon rows, repeated correlated contracts, unsent candidates and a stock-direction winner whose option loses value.

### EA-12 — P2: transport and logs overstate delivery reliability

**Source:** [email_service.py](../../services/market-data/src/services/email_service.py), `_send_smtp` / `_send_ses`; [paper_trading_engine.py](../../services/market-data/src/services/paper_trading_engine.py), `_send_exit_emails`, around 7209.

SMTP has no explicit timeout. A blocked transport can outlive a scheduler's lock lease. Several jobs use constant lock values, fail open on Redis exceptions, and delete leases without checking ownership; these locks do not provide an exactly-once delivery guarantee.

Paper-exit delivery ignores the sender's boolean and logs `paper.exit_email_sent` even when it returned false. Conditional-order/broker-notification paths also need delivery state independent of already-committed business state. SES message identifiers are not retained here. Provider acceptance alone is not evidence of inbox delivery.

**Remedy:** bounded transport timeouts; transactional outbox; unique lease ownership with checked release; explicit attempted/provider-accepted/bounced/failed states. Persist provider identifiers where available. Alert on backlog age and failed critical notifications, not merely scheduler completion. Preserve trading state while retrying its notification.

**Acceptance:** SMTP stall, negative return, recipient rejection, worker crash after provider acceptance, lease expiry and mixed-recipient outcomes.

## Coverage across all email families

This table records source coverage and principal conclusions, not a blanket pass for rows without a dedicated reproduction.

| Family | Reviewed paths and assessment |
|---|---|
| AI Signal changes and delisting notices | Transition, conviction, freshness, plan, cohort, retries, renderer; EA-01/02/03/05/08/11. Delisting notice also reserves its dedup key before successful sending. |
| Price and technical alerts | Compound conditions, trigger state, indicator descriptions and recurrence; EA-04/06. |
| Top conviction and sector rotation | Ranking, composition/global state, recipient filters and templates; EA-07. |
| Volume anomaly, short squeeze, ignition, pre-breakout | Candidate filters, recipient/cooldown paths, shared plans and calibration. Preference helper is present. Shared plan and outcome limitations remain; a small resolved cohort is not a validated edge. |
| Options expiry/gamma watch | OI/watch framing and historical calibration. Watch wording is improved; do not treat call/put OI alone as a measured dealer position or option profit forecast. |
| UW unusual options flow and dark-pool activity | Adapter timing, side inference, candidates, ranking, cooldown, seen sets, templates and outcome evaluation; EA-09/10/11. |
| Squeeze-watch revert, support/resistance watch, value-area breakdown | User/watch state, recipient flow and rendering. Manageable preference enforcement is missing; EA-05. |
| Earnings reaction, macro reaction, earnings impact, early earnings news | Direct-mail paths and action/playbook context. Register their preference policy; label heuristic/event risk and timestamp. Earnings actions based on global paper positions should name that portfolio rather than imply the recipient holds the stock. |
| Earnings beat screener and reminder | Screener has preference filtering; reminder does not. Distinguish reported surprise from future price prediction. |
| Morning, premarket and post-open digests | Input aggregation, snapshots, recipient path and templates. Missing preference checks; mixed timestamps should be explicit. |
| Flow digest | Stored candidate aggregation, hit-rate formula, audience and direct sender. Inactive recipients are not excluded; no manageable type; misleading timing shorthand. |
| Paper portfolio digest, drawdown and trade exits | Shared paper-account scope, drawdown policy, exits and delivery. Drawdown filter exists; portfolio digest/exit preference gap and exit false-success log remain. |
| Conditional order and broker reauthorization | Essential transactional-notification policy; separate committed business action from retryable notification. |
| Data quality and LLM usage spikes | Operator audience and essential-mail policy; data-quality path handles unsuccessful sends better than several trading paths. External delivery still depends on common transport. |
| Theme forecast and trade coach | Summary/LLM commentary, inputs and audience. Neither checks manageable preferences. Coach computes shared paper-trade statistics but describes them as “this account's own”; label shared scope accurately. |

Paper portfolios are intentionally global in the current model. This audit does **not** classify shared paper data as a demonstrated tenant-data breach. It flags misleading personalization where emails imply a recipient-specific account or holding.

## What recent production data actually supports

At the recorded September 28 snapshot, there were **362 signal subscriptions**, with **14** having a last-send timestamp within seven days. The maximum last-send timestamp was **September 24, 03:40:45 UTC**. These rows measure each subscription's last send, not total messages, successful inbox delivery, or the cause of a quiet period.

The legacy September signal cohorts had the following underlying five-day results. These are **maturity-selected diagnostics**, not the accuracy of emails that passed all delivery/conviction gates, and not fair cross-horizon rankings:

| Horizon / direction | Resolved legacy rows | Stored five-day correctness rate | Mean underlying return |
|---|---:|---:|---:|
| SHORT BUY | 588 | 33.67% | -1.28% |
| SHORT SELL | 307 | 51.47% | +0.52% |
| SWING BUY | 200 | 29.00% | -2.85% |
| SWING SELL | 384 | 45.83% | +1.55% |
| LONG SELL | 426 | 40.85% | +2.16% |
| GROWTH BUY | 318 | 29.87% | -2.23% |
| GROWTH SELL | 191 | 41.36% | +1.75% |

No LONG BUY row appeared in this legacy September cohort. Do not interpret that absence as zero performance or no signals; primary-resolution selection affects which rows exist. SELL return signs above remain raw underlying returns, not short-trade P&L. Use independent resolved horizon rows before drawing a September-versus-earlier improvement conclusion.

| Flow/watch candidate cohort | Resolved count | Observed metric |
|---|---:|---|
| Bullish options flow | 818 | 59.78% favorable underlying move at the +10-calendar-day target |
| Bearish options flow | 891 | 43.21% favorable underlying move at that target |
| Dark-pool prints | 742 | 40.70% absolute underlying move beyond the one-day hurdle; **not directional accuracy** |
| Gamma calls watch | 45 | 48.89% stored ten-day correctness |
| Gamma puts watch | 102 | 42.16% stored ten-day correctness |
| Short squeeze | 4 | 75%; too few resolved events to infer a reliable advantage |
| Squeeze ignition | 3 | 66.67%; too few resolved events to infer a reliable advantage |

The options pool covered 21 fired dates, with 1,031 bullish and 1,059 bearish candidates; the unresolved remainder must stay visible. Repeated contracts/symbols are correlated observations. The flow query returned September rows only, so it provides no older comparison cohort. These measurements do not establish option returns, best thresholds, or improvement caused by a particular fix.

## Implementable design for more useful UW alerts

1. **Separate observation, setup and actionable plan.** A large print can be emailed as an observation with event time and uncertainty. A setup requires independent price/regime confirmation. A plan additionally requires current executable quotes, valid geometry and an explicit risk budget. Missing data downgrades the class rather than receiving optimistic prose.
2. **Preserve the event and decision evidence.** Store provider event identity, event/ingest/decision times, classified premium coverage, underlying and option quote timestamps, model version, gate results and the exact rendered plan. Version the candidate separately from each recipient's notification.
3. **Use flow as corroboration, not an automatic order.** Test incremental value of repeated fresh flow, price confirmation, relative volume, event risk and sector context against a baseline without UW. Keep multi-leg/unknown opening intent explicit. Do not infer institutional conviction from premium size alone.
4. **Generate strategy-specific plans.** A directional stock setup, a long option and an income strategy need different entry, exit and risk definitions. Each option plan needs exact contract/legs, expiry, limit price, spread/liquidity checks, maximum loss, invalidation, time exit and earnings/assignment context. Refresh it when opened; email is not a live quote surface.
5. **Measure actual notification-to-trade behavior.** Join candidate → gates → recipient queue → provider acceptance → paper entry/exit. Model long-option entry/exit with conservative executable sides and costs; model multi-leg fills explicitly. Never substitute favorable stock movement for option profitability.
6. **Promote only after forward evidence.** Evaluate net expectancy, payoff ratio, drawdown, turnover, coverage and calibration, stratified by horizon/direction/regime. Use date-separated validation and clustered uncertainty; log threshold/version changes. Run alternatives in shadow paper cohorts before changing all alerts. This audit cannot honestly identify a universally profitable threshold from the available candidate counts.

## Remaining limits on the separate settlement/training closure

The settlement re-read closes the reproduced stale-value race. It does not prove every expiry bar was finalized: the corroborator still infers finality from a successor bar, and a requested re-fetch window does not prove the provider returned that particular earlier session. The latest-session branch still accepts a nearby quote within 0.5% without persisting exact-session quote provenance. Keep the earlier recommendation for explicit per-session finalization/revision evidence; do not reopen the fixed read-order counterexample as though it still fails.

Trainer and meta availability now use actual bar positions. Closure here concerns the estimated-endpoint defect, not every remaining feature parity, incumbent promotion or label-publication issue. Meta source comments still describe a business-day fallback that executable code no longer uses; correct that stale explanation during the next implementation change. Earlier lease, schema-readiness, event-linked news and disabled-meta-blend limitations remain outside this email audit's closure claims.

## Recommended implementation order

1. Repair EA-01 and validate the full AI Signal dispatch path. In the same release, reject invalid plans and correct same-day earnings and technical rendering (EA-02–04). These affect whether an alert arrives and whether its instructions mean what they say.
2. Centralize recipient/preference enforcement and persist notification delivery independently of trigger state (EA-05–07, EA-12). Include operator-visible backlog/failure status.
3. Enforce input freshness and rewrite side/accuracy claims to match evidence (EA-08–11). Preserve the prior improvements rather than replacing their honest labels with a generic confidence score.
4. Add versioned UW setup/plan evaluation and a forward paper trial. Measure the incremental result after fills and costs before increasing automated execution scope.

The current sandbox status limits money at risk; it does not make missing, reversed or unsupported email instructions technically correct. The immediate task is reliable and truthful alerting, followed by evidence that the delivered strategies improve net outcomes.
