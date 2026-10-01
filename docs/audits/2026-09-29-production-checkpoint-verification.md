# Production verification of deferred checkpoint

**Scope update:** the user requested September only. Use [September-only production verification](2026-09-29-september-only-production-verification.md) for the current assessment; this broader-period report is retained as historical evidence.

**Run:** 2026-09-30 06:50–06:53 UTC / 2026-09-29 local Pacific date.  
**Scope:** authorized read-only measurements; no deploy, flags, data mutations, scheduled job invocation, provider calls or notifications.  
**Conclusion:** headline counts reproduce, but weighting, horizon and direction conventions materially alter interpretation. Keep feedback unpromoted and do not invert flow or build the GEX gate on this checkpoint.

## Evidence and execution

Each database probe used a PostgreSQL **REPEATABLE READ, READ ONLY** transaction, a 25-second statement timeout and two-second lock timeout; each ended with rollback. Scripts were streamed through SSH to Python stdin in the running market-data container, without uploading files. Queries and returned rows are in the JSON artifacts; the three probes are separate snapshots, not a single combined atomic snapshot.

- [Main probe](evidence/2026-09-29-production-checkpoint-probe.py) and [results](evidence/2026-09-29-production-checkpoint-results.json).
- [Supplement](evidence/2026-09-29-production-checkpoint-supplement.py) and [results](evidence/2026-09-29-production-checkpoint-supplement.json).
- [Benchmark/config probe](evidence/2026-09-29-production-checkpoint-benchmark.py) and [results](evidence/2026-09-29-production-checkpoint-benchmark.json).
- [Prior interpretation review](2026-09-29-deferred-checkpoint-review-and-improvement-plan.md).

Production checkout: `5896dd8d7acb5760b3bf859f6d39cbdec51b5c48`. The running signal calibration module, market-data scheduler and shared models matched local SHA-256 hashes:

| File | SHA-256 |
|---|---|
| `services/signal-engine/src/api/signals_shared.py` | `ff13abf7a610a772e8614b7be7019ddd0317c207e999b064749c26f72f3b677e` |
| `services/market-data/src/services/scheduler.py` | `d37f73ebc6d6d19145cd2c515e655e88c5c0b855c63d03f798852573a2dc822f` |
| `shared/db/models.py` | `d43da8245c494cd78b4febe615e559639beebffc8302c6a6c9f026f8e29d43e8` |

This is a targeted provenance check, not a full deployment-drift certification. Two optional portfolio-summary queries initially referenced nonexistent columns (`style`, then `trading_style`); their errors are preserved. Savepoints isolated those failures. Reading the model established that portfolio configuration lives in JSON; the final query succeeded without either column. No failed query was counted as a passed check.

## 1. Confidence: the pooled result reproduces; actual slices differ

The five-day ten-point histogram reproduces 19,256 resolved rows. It is not the production calibration's target/cohort. Using its primary `is_correct`, 180-day window, configured bands and market/horizon/direction splits yields **76 populated slices, 59 with at least 30 rows**. Of those 59, **2 exceed/equal 55%** and **16 are at/below 35%**. These are counts of supported buckets, not counts of affected trades. Pooled-market fallback data are also saved separately.

Selected BUY slices show why aggregation matters:

| Horizon / market | Band | n | Primary success |
|---|---|---:|---:|
| LONG / US | 0–40 | 528 | 53.0% |
| LONG / US | 85+ | 131 | 35.9% |
| GROWTH / HK | 40–55 | 230 | 40.9% |
| GROWTH / HK | 85+ | 58 | 13.8% |
| SWING / HK | 55–70 | 85 | 16.5% |
| GROWTH / US | 55–70 | 753 | 45.2% |

These historical differences do not establish a safe new scoring rule; they refute the idea that the pooled table settles all production slices. The since-September-1 diagnostic has only **23 supported slices out of 48**, with different composition. It is a calendar cohort, not a verified post-fix/model-version cohort, and unresolved longer horizons introduce maturity selection.

**Live configuration:** all **11 active paper portfolios** have `calibration_feedback_enabled` absent/null. Under the current default-false gate, feedback is off. No flag was changed.

**Decision:** keep it off. Evaluate the existing ±1 policy on immutable entry decisions with point-in-time calibration history before any promotion. Bucket measurements alone cannot establish how many entries would change, how their portfolio paths would differ, or whether score/sizing changes improve net returns. Do not describe the inverse association as fixed in every slice merely because two pooled endpoint bands have the expected order.

## 2. Options flow: clustering changes the headline

### Contract-row results

| Horizon | Direction | Resolved rows | Success | Up >+0.5% | Down <−0.5% | Neutral | Mean underlying return |
|---|---|---:|---:|---:|---:|---:|---:|
| 5 calendar days | Bullish | 957 | 55.2% | 528 | 316 | 113 | +3.05% |
| 5 calendar days | Bearish | 984 | 29.1% | 598 | 286 | 100 | +4.00% |
| 10 calendar days | Bullish | 818 | 59.8% | 489 | 311 | 18 | +2.97% |
| 10 calendar days | Bearish | 891 | 43.2% | 484 | 385 | 22 | +2.41% |

Five-day resolved rows span **18 fire dates** per direction. Ten-day rows span **14 dates** per direction. Thus both directions meet the actual calibration consumer's minimum 30 resolved ten-day outcomes and five-date gate. Passing that publication gate does not establish independent sample size or trading edge.

At five days, inversion's observed up-move fraction for bearish rows is **598/984 = 60.8%**, not 70.9%; 100 rows are neutral. This still is not a profitable option strategy, nor an independent out-of-sample result.

### Give each symbol/fire-date group equal weight

Average contract outcomes within each direction/symbol/fire-date, then equally weight those groups. This removes contract-multiplicity weighting, but does not remove serial, cross-symbol or overlapping-window dependence.

| Horizon | Direction | Symbol/date groups | Mean group success fraction | Mean underlying return |
|---|---|---:|---:|---:|
| 5d | Bullish | 156 | 42.3% | +1.01% |
| 5d | Bearish | 174 | 47.1% | +1.08% |
| 10d | Bullish | 122 | 60.7% | +3.41% |
| 10d | Bearish | 136 | 35.3% | +2.65% |

One bearish symbol/day accounts for **58 contracts**; the bullish maximum is **36**. **116 symbol/date groups appear in both directions at five days** (93 at ten days). Opposite labels often point at the same underlying move. The five-day “opposite in quality” conclusion is not robust to this basic weighting change.

The four flow-side cells are preserved in the evidence. At five days, bearish call/bid rows have 125/501 successes and +4.72% mean underlying return; bearish put/ask rows have 161/483 and +3.25%. These are descriptive slices selected for understanding the existing taxonomy, not promoted strategies.

### Same-date SPY comparison: partial coverage, exploratory only

Available daily SPY prices permit a limited market comparison. For each symbol/date cluster, reconstruct the evaluator's next eligible exit bar (target calendar date plus at most the existing ten-day grace), require that the current underlying close matches the stored exit price within 0.0001, then match SPY closes on the same entry/exit dates. This is an ex-post benchmark, not a point-in-time feature.

| Horizon | Direction | Matched / all groups | Underlying | SPY | Excess over SPY |
|---|---|---:|---:|---:|---:|
| 5d | Bullish | 125 / 156 | +1.72% | −0.13% | +1.85 pp |
| 5d | Bearish | 135 / 174 | +1.76% | −0.08% | +1.84 pp |
| 10d | Bullish | 48 / 122 | +6.97% | +0.74% | +6.23 pp |
| 10d | Bearish | 53 / 136 | +5.12% | +0.77% | +4.35 pp |

The five-day matched subsets have almost identical excess returns despite opposite labels. This is consistent with both labels identifying similar active/rising names; it does not isolate a causal effect or risk-adjusted alpha. The ten-day match rate is particularly poor and results must not represent the full cohort. This probe does not partition exclusions into missing benchmark bars, missing underlying bars and revised/mismatched exit prices; that reconciliation remains required before using these comparisons for selection. No sector/beta/momentum matching, dependence-adjusted confidence interval, costs or executable options P&L was computed.

**Decision:** no inversion or new short/long rule. The next bounded analysis should reconcile benchmark exclusions, build matched eligible-universe controls and compare baseline/no-flow/inverse policies on untouched data with realistic fills/costs. Keep delivery outcomes and contract-level trade P&L distinct from these stock-return diagnostics.

## 3. GEX: signed outcomes reverse the apparent advantage

The checkpoint pooled raw stock returns across bullish `gamma_unwind_calls` and bearish `gamma_unwind_puts`. Production evaluator code explicitly scores puts as a bearish thesis. For an interpretable directional comparison, negate puts' raw return (a diagnostic, not realized short P&L).

| Alert type | Corroborated | Resolved 5d | Success | Thesis-signed mean return |
|---|---|---:|---:|---:|
| gamma_unwind_calls | True | 45 | 15/45 | −1.20% |
| gamma_unwind_calls | False | 9 | 2/9 | +0.54% |
| gamma_unwind_puts | True | 64 | 31/64 | −2.34% |
| gamma_unwind_puts | False | 4 | 2/4 | +6.18% |

Weighted across those subtypes, corroborated outcomes are approximately **−1.87%**, versus **+2.28%** without corroboration. The prior +0.88% versus −1.53% raw-return comparison is not evidence of a beneficial directional filter. Neither is this tiny false arm proof the filter is harmful: the false groups contain only five call symbols and three put symbols, with different composition and no randomized allocation.

**Decision:** do not build the gate. Use subtype-specific signed economics, matched conditions and adequate independent observations in any follow-up. Do not invert GEX based on these numbers either.

## 4. Dark pool: persistence confirmed, denominator still operationally incomplete

Production contains **375,411 prints across 78 symbols**, matching the checkpoint. But 78 is historical coverage, not daily coverage. Using execution timestamps interpreted as UTC and converted to Eastern session dates:

| Session | Observed print symbols | Distinct alert symbols | Alert symbols / observed symbols |
|---|---:|---:|---:|
| Sep 23 | 39 | 34 | 87.2% |
| Sep 24 | 38 | 32 | 84.2% |
| Sep 25 | 40 | 35 | 87.5% |
| Sep 28 | 41 | 34 | 82.9% |
| Sep 29 | 48 | 39 | 81.3% |

These descriptive ratios are far higher than 35/78. They are still **not a certified eligible-universe fire rate**: persisted prints do not prove which symbols were polled, had fresh qualifying inputs, met history requirements or were available at the scan cutoff. Alert rows are candidate outcomes, not recipient delivery counts. Date-level aggregation also does not prove each alert references a same-session print; the current recency rule and scan schedule must be respected.

**Decision:** record each scan's bounded eligible universe and source/coverage status before adjusting selectivity. Define a conditional alert rate on that same population, plus a separate delivery-load metric. Do not tune to 5–10% against historical coverage or all tracked names.

## 5. Remaining limits and next steps

Prebreakout remains eight total names with concentrated five-day support; the per-symbol extract is saved. GEX/squeeze subtype counts also reproduce the small short-squeeze/ignition samples. This run did not change or instrument anti-chase; its realized incremental block rate is still unavailable from the inspected records.

Priority next work:

1. Correct checkpoint interpretations using these measured definitions; retain prior evidence rather than overwriting it.
2. Reconcile stored-return/benchmark matching and missing outcomes; freeze cohorts by code/model version and availability before strategy comparison.
3. Evaluate flow contribution with equal symbol/date weighting, matched controls and time/event dependence accounted for. A new prospective paired trial is required for promotion, not merely another summary table.
4. Add the narrowly scoped anti-chase funnel and dark-pool scan coverage ledger in a separate implementation task.
5. Preserve disabled calibration feedback and experimental status for GEX/squeeze/prebreakout. No production behavior was changed by this verification.
