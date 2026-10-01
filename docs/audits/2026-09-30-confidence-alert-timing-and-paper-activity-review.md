# Confidence, alert timing, paper activity and improvement plan

**Date:** 2026-09-30. **Performance scope:** September-origin signals/trades, through the measured snapshot; current settings and surviving logs separately identified. This is a read-only production investigation and local code review. No rules, parameters, Redis state, broker state or alerts were changed.

## Main answer

Low activity is partly intentional selectivity, partly losses triggering protection, and partly engineering/policy coupling that should be corrected. More emails or trades is not the objective. The objective is timely, independently justified decisions with positive net expectancy and controlled downside. Current evidence does not justify broadly lowering thresholds or trusting a higher raw confidence value.

A concrete recovery-grant ordering defect and a stale/coupled conviction-cache design deserve priority over another model or a threshold search. We also have enough existing scan telemetry to rank broad blockers; the remaining instrumentation should fill specific gaps rather than duplicate it.

## Evidence and limits

- [September database probe](evidence/2026-09-30-signal-alert-paper-funnel-probe.py) and [results](evidence/2026-09-30-signal-alert-paper-funnel.json).
- [Detailed rejection probe](evidence/2026-09-30-funnel-detail-probe.py) and [results](evidence/2026-09-30-funnel-details.json).
- [Bounded log reader](evidence/2026-09-30-alert-paper-log-probe.py) and [sanitized aggregate](evidence/2026-09-30-alert-paper-log-summary.json).
- [Runtime hash/marker reader](evidence/2026-09-30-funnel-runtime-probe.py) and [results](evidence/2026-09-30-funnel-runtime.json).
- [September-only outcome analysis](2026-09-29-september-only-production-verification.md).

Database probes use read-only repeatable-read transactions, statement/lock timeouts and rollback. One initial rejection query could not expand JSON null as an object; the corrected query explicitly handles non-object values. The failure is retained in the evidence. Aggregates contain no recipient addresses.

Durable no-entry logs exist in this extract from September 22 onward, not all September. They are written for portfolio gate blocks and scans with **zero entries**, so they are not a complete denominator for all scans. Counts repeat candidates across scans and portfolios. Do not report them as unique lost opportunities or causal percentages.

The bounded Docker log read returned 5,260 JSON messages spanning **September 30 06:30:28–07:03:28 UTC**. This is a short HK-session window, not a full-month or US-session delivery audit. No observed `email.sent` in that window establishes only zero recorded acceptances there. An SMTP/provider acceptance is not confirmed inbox delivery.

Running paper engine hash `e466179887de94e5159cddd0c5bd28a90e29bac2b5d6cf05395f4367676ee890` and scheduler hash `d37f73ebc6d6d19145cd2c515e655e88c5c0b855c63d03f798852573a2dc822f` match the inspected local files. This is not a full-container drift audit.

### Focused local verification

[Executable gate witnesses](evidence/2026-09-30-paper-gate-reproductions.py) and [captured output](evidence/2026-09-30-paper-gate-reproductions.json) execute the exact AST-extracted recovery and conviction branches from the production-matching paper engine. With simulated dependencies, the first recovery check marks the grant; if no downstream trade is opened, the next check blocks. The cache check rejects an old failed BUY record and passes that gate when the cache is absent, without requiring a current signal identity.

These are bounded defect witnesses, not full-engine integration tests or a replay proving every historical rejection wrong. Their assertions deliberately describe the defects and must be replaced with corrected-behavior acceptance tests when fixes are implemented. They performed no network/database/Redis operations. Both witnesses completed successfully; document links and whitespace were checked.

## 1. Why paper trading is usually inactive

### It is running, but few entries survive

All 11 stock paper portfolios are active. September entries total **15**, across seven portfolios; **13 have closed, only one with positive stored P&L**, and two LONG positions remain open. Portfolio 2, 9, 10 and 891 have no September entries in this extract. The latest recorded September entry is portfolio 6 on September 23.

This is a small, entry-cohort-specific sample, not a stable estimate of future win rate. It excludes positions entered before September, and the two open trades are not losses or wins yet. Do not sum HK and US currency P&L or treat current cash as investment return.

There are thousands of persisted scans, so “the engine is not running” is not the main explanation. Only portfolio 8 currently holds positions (two); overall inactivity is not explained by all portfolios being full or short of cash.

### Measured blockers

Across available September no-entry tallies:

| Recorded reason | Repeated candidate checks |
|---|---:|
| Not on that style's watchlist | 12,649 |
| Alert conviction gate rejected BUY | 5,333 |
| Already open; scale-in path only | 4,070 |
| Price drift since signal | 2,683 |
| Declining confidence | 2,479 |
| Entry qualifier rejected / score below threshold label | 2,057 |
| Restricted symbol | 1,805 |
| K-score below threshold | 732 |
| No ranking | 257 |
| TA gate | 194 |

Total: **32,259 recorded checks**, not distinct trades. A first failing gate hides later possible failures; removing the largest gate would not convert that many checks into entries. Some labels also combine causes: `entry_score_below_threshold` is incremented for any false entry qualifier result, including a DE hard rejection, so it does not prove score alone is responsible.

Portfolio-level blocks add **1,529 consecutive-loss records** and **176 index-trend records**. Portfolio 2 has 775 records citing four losses/recovery already used; portfolio 5 has 754 citing ten losses/recovery already used. These are actual observed reasons, not suspected confidence-threshold failures.

### Finding P1: recovery grant is consumed before an entry exists

In `paper_trading_engine.py::_scan_for_entries`, around lines 5788–5823, the no-open-position recovery branch calls `_mark_recovery_grant()` immediately, before downstream portfolio gates, candidate checks or `_open_paper_trade`. `_mark_recovery_grant` stores the streak in Redis for seven days. Subsequent scans see the marker and return before considering candidates.

**Failure path:** losing streak → no open trades → mark recovery used → all candidates rejected (or no candidates) → no entry → next scan blocked as though recovery was traded. The code records permission to attempt as consumption of an actual entry.

Production supports the exposure: portfolios 2 and 5 have no open positions; their marker values are 4 and 10. Remaining TTLs at the probe were 584,763 and 23,432 seconds. Portfolio 2 has a zero-candidate scan on September 30 and no September trades, yet its recovery marker has nearly a week remaining. This is consistent with the ordering defect. Historical claim creation was not captured, so not every old blocked scan can be attributed to it individually.

**Solution:** model recovery as available → reserved → consumed. Reserve atomically only for a viable candidate/order intent; consume against a durable opened trade or accepted execution intent with reconciliation. Release failed/no-entry attempts, preserving ownership checks and concurrency safety. A database rollback, retry or killed worker must neither consume an unused grant nor create duplicate recovery positions. Repeated recovery losses and expiry need an explicit reviewed risk policy; do not blindly reset streaks or disable the brake.

**Acceptance tests:** zero candidates; later gate rejects; insufficient cash; broker rejection/ambiguous submission; database rollback; duplicate concurrent scan; process restart; successful entry; winning and losing resolution. Only a successful authorized execution lifecycle should exhaust the grant. This review identifies the defect; it does not deploy a fix.

### Finding P1: email conviction cache influences entry eligibility

`_scan_for_entries` around lines 6824–6846 reads `conv_gate:{symbol}:{style}`. A BUY record with `sent=False` blocks entry; missing/broken Redis falls through. The producer `_store_conviction` uses a 24-hour TTL and stores wall-clock `ts`, but the entry reader does not bind the record to the current signal ID/input version or check age against that decision. “sent” here represents gate status, not unambiguously successful email delivery.

Consequences: trading eligibility can depend on whether the alert path previously evaluated a symbol, and a failed old evaluation can affect a newer plan. The **5,333 conviction-gate checks** establish that the path matters, not that all those decisions were stale or wrong.

**Solution:** evaluate one shared policy over a versioned signal/plan and consume the resulting decision in both email and paper paths. Cache by decision/input identity and policy version, with explicit expiry. Distinguish `gate_passed` from delivery state. Removing a stale cache must not bypass genuine risk checks; recompute the policy. Test same-input parity and new-input invalidation across direct DE, alerts and paper paths.

### Watchlist and threshold changes

The watchlist is the largest observed filter. Establish whether each excluded candidate is intentionally out of mandate, belongs to another horizon, or is absent due to stale membership/ranking. Then run a separate shadow universe without the watchlist restriction, subject to identical liquidity/risk rules. Measure incremental executed net returns, not extra candidates.

Keep price-drift protection, but investigate signal age and plan-relative distance in ATR/risk units rather than assuming a universal percentage is right for all horizons. Treat declining raw confidence as another feature to ablate, not a calibrated deterioration in win probability. Preserve restricted-symbol and portfolio risk rules during experiments.

## 2. Why relatively few emails are sent

The AI signal sender is **transition-based**: unchanged state normally produces no new email. It also applies user preferences, recognized transition rules, optional consensus, conviction checks and a decision-engine gate for BUY. The current subscription snapshot has 362 rows, all mode `all` and none requiring consensus. Thus buy-only mode and consensus are not the primary current subscription-level explanation.

Seven subscription rows have no explicit email; the sender also has an effective-recipient path, so this is not proof all seven are undeliverable. There are **137 subscription rows whose latest send timestamp falls in September**. This does not count September emails: each row retains only its last timestamp and could have sent many times.

In the short retained log window:

- 1,638 skips were `analyst_or_confidence` (the lighter rule for bullish improvements that are not BUY).
- 63 skips were `conviction_layers_failed`.
- 35 BUY checks were rejected by the decision-engine gate.
- 34 `conviction_met` events were recorded; counts are not joined one-to-one by immutable decision ID.
- Seven `skipped_stale` messages occurred.

Do not conflate the 1,638 non-BUY-improvement skips with 1,638 rejected actionable BUYs. The observed retained window supports filtering as a current explanation, but cannot quantify the whole month's lost/delayed mail. The earlier known sending outage and consumed transitions remain separate historical delivery defects; they are not reconstructed again here.

**Improvement:** separate low-frequency “watch / setup forming” observations from actionable entry alerts, and risk/exit notifications from both. A watch digest can show near-misses without weakening action gates or implying they are trades. Preserve unsubscribe controls, event deduplication, explicit historical timestamps and delivery retries. Build the immutable event/outbox chain so future reports can show generated → eligible → queued → accepted → delivered/bounced, including failure reasons.

## 3. Are alerts timely?

**Not yet demonstrated end to end.** Code confirms the scheduling path; retained evidence does not establish event-to-inbox latency or price slippage when received.

The standard `_refresh_market` path ingests, refreshes rankings/signals, then calls `check_signal_alerts`, followed by paper work. Its actual schedule is mostly every five minutes during market windows. Comments claiming “5×/day” are stale. US full refresh has visible gaps from **09:45 to 10:00** and **15:00 to 15:30 ET**. Separate five-minute ingestion and paper checks continue on their own schedule; that alone does not refresh/send standard AI signals through the full path. Other specialized alert jobs have their own cadences.

Alerts can therefore see scheduled delay plus ingestion/ranking/signal work, locks, budget-limited symbol processing and provider latency. The current general price-freshness check permits a four-day bar age to accommodate weekends; it does not establish the freshness of the stored signal, every input or the executable plan. This is inadequate evidence for calling an alert “real-time,” even when a job ran successfully.

**Recommended timing contract:** store source event time, receipt time, input as-of/version, signal creation time, policy evaluation time, queue time, provider acceptance time and delivery callback time when available. Track p50/p95 latency by alert family/horizon and market, plus entry-price drift, fraction expired before send, and max favorable/adverse excursion after realistic receipt. Where no delivery callback exists, report acceptance latency only.

Proposed initial objectives—not measured achieved service levels—are actionable intraday delivery acceptance within one completed five-minute bar after validated inputs, with an explicit plan expiry, and scheduled-session evaluation for swing/growth. UW event alerts need their own provider-lag-aware objective. Protective position management must remain independent of email.

Before speeding up any job, measure which stage dominates latency and whether earlier publication uses incomplete bars. If full-refresh gaps demonstrably miss actionable transitions, address those gaps or trigger evaluation from a committed new decision. Do not simply poll faster against unchanged/stale signals.

## 4. How to improve confidence and actual signal accuracy

`services/signal-engine/src/generators/signals.py` computes:

```python
confidence = round(abs(fused - 0.5) * 200, 2)
```

This measures distance from neutral. It is a **signal-strength score**, not the probability of making money. Raising it can select strong but wrong signals; existing September results do not establish reliable monotonicity. Keep the raw field for reproducibility, label it correctly, and introduce a separately named calibrated probability only after validation.

A calibrated 70% probability should correspond to approximately 70% observed positives for its defined target. Calibration and discrimination are distinct; improving a calibration curve cannot by itself create predictive information. Use reliability diagrams alongside Brier/log loss and ranking metrics, with a constant base-rate benchmark. [Scikit-learn calibration documentation](https://scikit-learn.org/stable/modules/calibration.html)

Recommended bounded sequence:

1. **Define the target.** Market, horizon in bars/sessions, direction, executable entry delay, target/stop/time exit, costs and label-availability time. A fixed-horizon stock direction label cannot stand in for options P&L or a stop/target strategy.
2. **Build leakage-safe evaluation.** Immutable input snapshots and only matured labels available at each training cutoff. Separate chronological train/calibration/test windows; purge overlap and group duplicate symbol/event observations. Keep September as the reporting cohort; any older development data must be clearly labeled and cannot be presented as September validation.
3. **Establish simple baselines.** Base-rate/no-trade and a basic market/sector/momentum strategy, using the same universe and timing. Quantify whether ML, flow, news and confidence add value one at a time.
4. **Calibrate conservatively.** Compare a simple sigmoid mapping to the base-rate model; use more flexible mappings only with enough independent data. Fit per relevant cohort or use explicit shrinkage/partial pooling when sparse. Report unavailable probability rather than fabricating precision. Do not promote a noisy mapping based on a minimum row count alone.
5. **Separate selection from risk sizing.** Do not increase size solely because raw confidence is high. Estimate expected payoff and downside separately; cap exposure deterministically. Validate sizing as its own policy intervention.
6. **Run one prospective paired paper experiment.** Same baseline candidates, independent account state and identical costs/fill rules. Include skipped opportunities, outages and open positions. Freeze parameters before the comparison; no after-the-fact horizon selection.

Jev can later contribute event-specific adverse-news evidence within this framework. Adding it before the baseline and blocked-decision records are trustworthy would make attribution harder, not fix confidence automatically.

## 5. Improve returns rather than optimize the win-rate display

A useful decision objective is net expectancy:

`P(win) × average win − P(loss) × average loss − expected execution costs`.

Define win/loss and costs consistently; if average win/loss already include costs, do not subtract them twice. A high hit rate can coexist with poor returns when losses are large, while a lower hit rate can work with favorable payoff asymmetry. Estimated target/stop R:R is not realized payoff and cannot guarantee either outcome.

Report portfolio net return on starting capital, maximum drawdown, tail losses, exposure, turnover, cost, open-position marks and uncertainty. Separate US/HK currencies and independently evaluate options using contract fills, spreads, IV/theta, assignment and collateral. The 13 closed September paper entries with one winner argue for diagnosing selection, execution and exits before increasing trade count, not for bypassing risk limits.

For exits, record entry-to-stop/target excursions, time-to-target, exit slippage and post-exit opportunity cost. Test one exit rule at a time on the same entries. Avoid tuning stops and targets to the same small set of losses or letting a better hit rate hide worse drawdown.

## 6. Priority implementation plan

| Priority | Work | Acceptance criterion |
|---|---|---|
| P0 | Fix recovery-grant lifecycle | No-trade scans cannot consume a recovery entry; duplicate/restarted workers cannot exceed the allowance; risk policy stays explicit. |
| P0 | Separate versioned conviction decisions from alert-delivery state | Same inputs produce the same decision across alert/paper/DE; stale cache cannot block or authorize a newer plan. |
| P1 | Extend existing scan logs into an event-level funnel | Unique decision IDs, all gate results/reasons and execution links; retain present tallies, add successful scans and precise hard-reject reasons. |
| P1 | Alert outbox and latency evidence | Actual event/queue/send states and input age visible; distinguish provider acceptance from delivery. No duplicate or silently consumed transitions. |
| P1 | Watchlist/declining-confidence ablation | Measured incremental expectancy and risk versus frozen control, not a promise that removing a gate increases returns. |
| P2 | Confidence calibration experiment | Out-of-sample calibration and discrimination beat the relevant baseline with adequate independent evidence; no automatic activation. |
| P2 | Timing and exit experiments | Measured improvement after realistic receipt/fills, costs and risk; parameters frozen before forward testing. |

These are reviewable recommendations. This session completed measurement and documentation, not implementation or deployment. Existing scan telemetry already answers broad inactivity questions; outcome-linked, stage-specific evidence is still required to decide which gates should change.
