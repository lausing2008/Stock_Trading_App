# HK SWING: why zero BUY signals, and what the data can and cannot settle

Date: October 2, 2026. Read-only trace against production (SELECT only, repeatable-read),
plus one observational instrumentation change deployed with it. **No threshold was moved, no
portfolio setting touched, nothing enabled.**

The question came from the inactivity follow-up: HK SWING portfolio 9 had 383 zero-candidate
scans. The supply trace showed the cause is upstream — HK SWING produced **zero BUY signals in
three days**, later confirmed as zero over seven. This document traces those signals through
the generator to separate three explanations that call for opposite responses:

| | Meaning | Right response |
|---|---|---|
| `no_opportunity` | the evidence was never near the bar | nothing — the generator is working |
| `suppression_bound` | the score cleared the bar and a gate pushed it under | examine the gate |
| `stale_or_missing` | a required input was absent | fix the input, distrust the score |

## What the window contains

211 HK SWING signals, 7 days, 42 symbols. **Every one of them in the `choppy` regime.**

SWING's BUY bar in choppy is **0.74** — the second-highest in the whole table, behind only
bear's 0.76, and well above SHORT's 0.63. It reached 0.74 through four successive raises
(0.62 → 0.65 → 0.67 → 0.72 bull, with choppy at 0.74).

| | |
|---|---:|
| Signals | 211 (WAIT 134, SELL 48, HOLD 29, **BUY 0**) |
| Distinct symbols | 42 |
| Fused score — median | 0.4394 |
| Fused score — p90 | 0.5561 |
| Fused score — **max** | **0.7060** |
| Scores above 0.50 | 54 |
| Within 0.02 of the bar | **0** |
| Within 0.05 of the bar | 3 |

The best signal produced in seven days across 42 symbols was **0.706 against a bar of 0.74** —
short by 0.034. One symbol, `0992.HK`, accounts for the top of the distribution.

## How much of this is the bar?

Counterfactuals on the **same scores**, changing only the threshold:

| Bar | Would have been BUY |
|---|---:|
| 0.74 — SWING in choppy, the live rule | **0** |
| 0.67 — SWING's own value before the SA-32 raise | 10 |
| 0.62 — SHORT's bull bar | 12 |
| 0.60 — GROWTH's bull bar | 16 |

The bar is decisive: SWING's own threshold from before its most recent raise would have
produced ten BUY signals from this identical evidence.

**This does not establish that 0.74 is wrong — and the standing outcome data leans the other
way.** The deploy-boundary baseline captured the same day records, across all resolved
`signal_outcomes` to date:

| Direction / horizon | Resolved | Mean return |
|---|---:|---:|
| BUY SHORT | 4,118 | −1.34% |
| **BUY SWING** | **3,453** | **−2.57%** |
| BUY LONG | 2,343 | −3.47% |
| BUY GROWTH | 3,978 | −2.68% |
| SELL SHORT | 1,420 | +0.62% |
| SELL SWING | 1,539 | +0.40% |

Every BUY horizon has a negative mean return and every SELL a positive one — the inversion
this platform's own September audit already recorded. A SWING BUY bar that emits nothing in a
choppy regime is therefore not obviously malfunctioning; on this evidence it is declining to
produce the worst-performing signal class in the least favourable conditions. The raises were
made against measured win rates — SA-31 recorded SWING BUY at confidence 65–79 winning
13.3%. What the counterfactual
establishes is that the zero is a **threshold-and-regime** outcome, not a broken generator and
not a portfolio setting. It locates the decision; it does not make it.

## Separating suppression from absence, without a stored pre-score

Until today the generator recorded the final fused score and the gate flags but never the
score the compression chain started from, so "never near the bar" and "compressed under the
bar" were indistinguishable after the fact.

They can still be bounded, from the compression cap's own arithmetic. The cap restores an
over-compressed signal to `0.5 + orig_dist × max_ratio`. A signal whose pre-compression score
was at the bar has `orig_dist = 0.24`, so it cannot finish below `0.5 + 0.24 × 0.55 = 0.632`.
Therefore **any final score below 0.632 is proof the signal was never at the bar**:

- **200 of 211 — provably `no_opportunity`.** Their final scores are below the floor a
  bar-clearing signal could not fall through.
- **11 of 211 — unresolved.** Four symbols (`0992`, `6951`, `0939`, `6809`). The cap did not
  fire on any of them, which bounds their pre-compression score but does not pin it.

So suppression can account for **at most 11 of 211 observations**, and is ruled out for the
other 200. The newly recorded `fused_pre_compression` resolves the remaining 11 from the next
generation cycle onward.

## Two findings worth separate attention

**The compression cap fires on 108 of 211 HK SWING signals — 51%.** The code's own measured
fleet-wide figure is 27% (1,129 of 4,120). This cohort sits at roughly double that, meaning
stacked compression routinely over-suppresses here and the cap routinely undoes it. There is a
structural consequence: when the cap fires, the final score *is* `0.5 + orig_dist × 0.55`, so
clearing a 0.74 bar requires a pre-compression score of **0.936** — near the top of the
possible range. For half this cohort the effective bar is therefore far higher than 0.74.

**`breadth_pct` is absent on 126 of 211 signals — 60%.** This is the input to the breadth
compression. A missing input is not a neutral one: it means that gate's contribution to those
scores is unknown rather than zero, and it is the `stale_or_missing` category the
classification exists to keep separate. No other required input was missing: `ta_score`,
`calibrated_ta_score`, `adx`, `market_regime`, `rsi`, `macd_hist` and `ml_test_auc` are present
on all 211.

For context on whether the evidence itself was ever strong: raw `ta_score` has a median of
**0.34** (max 0.82) and `calibrated_ta_score` a median of 0.542. `adx` is below SWING's
minimum of 15 on 109 of 211. The underlying technical read across this cohort is weak-to-
neutral, which is consistent with 200 signals provably never approaching the bar.

## The instrumentation added

`reasons` now carries `fused_pre_compression`, `fused_post_compression`,
`compression_total_ratio` and — when the cap fires — `fused_post_cap`. Every one is a write
into `reasons`; a test asserts that each added line writes only there, that the cap's restore
formula and sign guard are unchanged, that the snapshot point was not moved, and that no
threshold changed (parsed as values, per the T401 rule). Four sabotages confirm it bites,
including one that makes the instrumentation mutate `fused`.

## What this does NOT establish

- **Not that 0.74 is too high.** The zero is explained; it is not thereby shown to be wrong,
  and the standing outcome table points the other way. Answering it needs forward outcome
  evidence for the signals a lower bar would have admitted — which does not exist and cannot
  be produced by rerunning the generator over the same history.
- **Not that the BUY-side inversion is caused by anything here.** The negative BUY means
  above are the pre-existing, separately-recorded finding; this trace neither explains nor
  worsens them. It is quoted only because it bears on whether a high SWING bar is a defect.
- **Not that the 11 unresolved observations were suppressed.** They are unresolved. Their
  pre-scores will be recorded from the next cycle.
- **Not a judgement about HK coverage.** 42 symbols produced these signals; whether that
  universe is right is a separate question this trace did not ask.
- **Not applicable beyond the window.** Seven days, one regime (`choppy` on 100% of rows). A
  trace confined to a single regime cannot describe behaviour in the other six, and the bar
  differs in each.
- **No portfolio threshold was lowered**, per the standing instruction. The finding is in the
  generator's threshold/regime interaction, and lowering a portfolio gate would have masked it
  while changing nothing about candidate supply.

## Artifacts

- [Generation trace](evidence/2026-10-02-hk-swing-generation-trace.py) and
  [results](evidence/2026-10-02-hk-swing-generation-trace.json)
- [Suppression bound](evidence/2026-10-02-hk-swing-suppression-bound.py) and
  [results](evidence/2026-10-02-hk-swing-suppression-bound.json)
