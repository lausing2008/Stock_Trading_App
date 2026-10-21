# Little RZY email review — 2026-10-21

Scope: current `rzy_email.py`, `rzy_workflow.py` eligibility/delivery checks, `rzy_data.py` execution flags, and recorded evaluation/operational results. Read-only code review; no deployment, real email, model calls or production re-verification. Renderer and real SQLite workflow suites: 25 passed.

## Can it be used now?

For research alerts and chart inspection, yes, subject to the displayed data limitations. It is not validated as an executable entry/exit service. The adapter still marks bars `execution_eligible=False`; paper entry is cancelled. The corrected historical baseline is 692 trades, mean −0.093R after costs, with edge versus control +0.001R and a symbol-clustered interval spanning zero. Clearer presentation cannot change that result. Operational trackers 457/458 require current-session evidence; prior latency measurements are historical, not a measurement of today's delivery.

## Current strengths

Readable bullish/bearish/watch labels, separate reference levels, before-cost reward:risk, Eastern timestamp, research positioning and explicit blocked-paper/exit expectations. HTML escapes dynamic content. Send-time checks cover consent, watchlist/timeframe membership, stream pause, current generation and changed figures. Current code uses actual processing time for lateness and supports arbitrary generations. No reopening of previously fixed generation-129 defect.

## Improvements recommended, in order

1. **Put status and freshness first.** Subject: `RZY research | ANF | bearish trigger | 5m`. First panel: `Research trigger — paper execution blocked`, candle close, evaluation time, rendered/sent time, age at that stage, and explicit data verification status. Do not call send time inbox arrival. An email remains a historical snapshot after delivery; it cannot update itself.
2. **Give one specific next step for each event.** A watch explains which measured event caused it and what confirmation is still missing; a trigger explains which candle crossed which prior-bar level, touched which trendline, and passed which R/R check. Translate rule identifiers into readable measured conditions. Do not invent missing measurements or a win probability.
3. **Separate levels from execution permission.** Show quote currency, entry reference, structural stop, target, risk distance in currency and percent, gross R/R and cost assumptions. Add an actual-fill acceptable range only after validated code computes it using costs and direction. Never imply a high R/R means high probability or that nominal stop risk caps gap losses.
4. **Make each email event-specific.** Watches currently receive generic entry/stop rows that can say “not available”; show watched level and missing confirmation instead. Exit emails should lead with actual fill/exit, reason and remaining quantity, with original setup levels secondary. Keep technical IDs in a compact footer.
5. **Add a stock-chart link carrying timeframe and event identity.** The app should show current validity, revised/superseded status and the archived email event distinctly. Do not claim the email itself is current when opened later.
6. **Define research lifecycle separately from paper fills.** Current emailable types include trigger, reversal/exhaustion watch, exit-pending, paper-exit and selected cancellations, but not every structural invalidation/expiry, nor PAPER_FILL. A blocked trigger therefore does not guarantee an invalidation or exit email. If adding research follow-up, track the structure after paper cancellation, deduplicate updates and label them `setup invalidated/expired`, never a filled-trade exit. A future eligible paper workflow should explicitly decide whether confirmed paper fills get mailed.

The scanner currently allows a new event to queue within timeframe + 3 minutes, and outbox expiry is candle close + timeframe + 5 minutes. For 5m that is an 8-minute enqueue window and 10-minute delivery expiry. These are operational limits, not promises that a setup remains tradable for that period. Show them as notification expiry; trading validity needs separate state/price checks.

## More specific versus more accurate

Specificity can improve now through measured trigger explanations, explicit age, clear level roles, event-specific next steps and a chart link. Data accuracy requires verified source/session provenance, stable completed bars and measured processing/delivery latency. Predictive accuracy requires new evidence: no filter should be promoted merely because it improves the already-examined sample.

Research candidates: liquidity/spread-to-risk limits, gap/entry-distance checks, cost-aware fill feasibility, and separately evaluated higher-timeframe context. Test one change at a time against the unchanged baseline on frozen data, then prospective sessions. Report net expectancy, sample sizes, uncertainty, drawdown, cancellation/fill rate and alert delay—not win rate alone. Keep RZY rule/config changes explicit and separate from email formatting.

## Acceptance before calling alerts execution-ready

- Completed-bar/source and symbol-level freshness evidence passes; pauses/stale streams clearly excluded.
- Prospective close-to-ingest/evaluate/queue/provider-acceptance timing meets a predeclared objective, with missed/suppressed signals in the denominator.
- Eligible paper fills and full entry/exit lifecycle are exercised using defensible spread/slippage/borrow inputs and a durable trade ledger.
- A representative forward evaluation supports the intended use after costs; current backtest does not establish an edge.

## Separate LLM hidden-rules experiment

Recommended protocol: the proposed seven cases paired shown/hidden (14 requests), same frozen input packet and model settings per pair, predeclared metrics, and stop at the first of 28 provider calls or USD 2. Include retries in both limits and reserve cost before launching each call. Stale/unsupported cases should be reported as preflight exclusions. Do not spend leftover budget expanding the matrix; do not infer significance from one answer per case. No production prompt change follows automatically. This experiment tests proposal behavior, not RZY profitability. No paid evaluation was run in this review.

## Implementation status (2026-10-21, commits `b6a55374`, `67e3adcc`)

Implemented (presentation only; eligibility, delivery and expiry unchanged):
- Recommendations 1–5: status and freshness first, subject `RZY research | SYM | <event> | <tf>`; measured trigger reasons from passed rules and recorded values, with absent values named "not recorded"; labelled levels with risk distance and %, gross R:R, and no-probability and gap notes; event-specific watch, exit and cancellation layouts; chart link.
- Notification expiry is shown as a delivery limit, not trading validity.
- "Follow-up emails are not guaranteed" replaces any implication that a trigger will be followed by an exit or invalidation email.

Not implemented (separate follow-up work): research lifecycle notifications (#6), the actual-fill acceptable range, and cost-assumption figures. Known gaps:
- Quote currency is not in the RZY payload ("currency not recorded").
- Prior-candle high/low and the trigger open are not recorded in events.
- The stock page does not read the link's `tf`/`rzy_event` parameters, so the email says to select the timeframe there.
