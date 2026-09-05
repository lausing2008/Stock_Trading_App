# ENTRY TIMING — WHAT CAN ACTUALLY BE DONE

**Date:** 2026-09-05
**Question:** the placebo test showed entering 14 days earlier was ~6 points better, with the
cause being that every conviction pillar is a momentum measure. Can anything be done about it?

---

## What was tested

The diagnosis says the architecture needs a **non-momentum pillar** — something that predicts
returns without being derived from recent price. So: does such a signal already exist in the
data being collected?

`signals.reasons` already carries several genuinely non-momentum fields. Correlation against
direction-signed forward return, post-fix BUY signals (n≈4,770):

| Feature | Correlation | n |
|---|---|---|
| **insider_score** | **+0.164** | 4,767 |
| institutional_score | +0.013 | 4,764 |
| earnings_beat_rate | −0.011 | 3,600 |
| rs_score | −0.047 | 4,770 |
| analyst_momentum | (no numeric data) | 0 |

`insider_score` at +0.164 is **the strongest single predictor found anywhere in this session** —
for comparison, `confidence` itself correlates ~+0.07 post-fix. And the bucketed returns looked
genuinely exciting:

| Insider score | n | Avg return | Win rate |
|---|---|---|---|
| None (≤0) | 4,482 | −1.98% | 40.6% |
| Low (0–20) | 103 | −1.50% | 63.1% |
| Mid (20–50) | 98 | +0.03% | 52.0% |
| **High (50+)** | 84 | **+0.57%** | 45.2% |

Monotonic across all four buckets, and the top bucket is **profitable** — the only profitable
bucket produced by any feature tested today.

## Why it does not survive scrutiny

**Two independent checks kill it.**

### 1. The effect does not exist in the pre-fix era

| Era | All signals | Insider ≥20 | Insider ≤0 |
|---|---|---|---|
| Pre-fix (before 2026-08-04) | −2.50% | **−2.93%** | −2.52% |
| Post-fix | −1.89% | **+0.28%** | −1.98% |

In the pre-fix era, high insider scores performed *slightly worse* than no insider score. A real
insider effect should not switch on the day an unrelated signal-generation bug was fixed.

### 2. It is 6 stocks, not a factor

Only **6 distinct symbols** carry a meaningful insider score at all. The 182 "high insider" rows
are those same 6 names repeated across many signal-days:

| Symbol | Rows | Avg return |
|---|---|---|
| UUUU | 37 | +1.64% |
| GRNT | 48 | +1.21% |
| MGY | 26 | +0.43% |
| TSM | 36 | −0.28% |
| SOFI | 30 | −0.50% |
| INTC | 5 | **−10.73%** |

**Three positive, three negative.** The bucket average is carried by UUUU and GRNT happening to
rise last month. That is stock-picking luck across a handful of names, not an insider factor.

**Verdict: not actionable. Do not gate or weight signals on `insider_score` today.**

This is the same trap the squeeze-ignition band presented earlier: an encouraging first result
that dissolved when the sample was widened. Recording it as a win would have been wrong twice
in one day.

---

## What CAN be done, in order of confidence

### 1. Already shipped: the anti-chasing filter (AUD-CHASE-ROC10)

Blocks BUYs on stocks already up ≥10% in 10 days. Out-of-sample validated at ~+0.17 points, and
it directly targets late entry. Live now — let it accumulate.

### 2. Already shipped: the concentration fix (AUD-GLOBALSYMCAP-STALE)

Not an entry-timing fix, but the single largest realised loss driver (64% of net P&L). Fixed
today.

### 3. Highest-value untested idea: widen insider-data coverage

The insider hypothesis was not *disproven* — it was **untestable**, because only 6 of 193
tracked symbols have insider data at all. That is a data-coverage problem, not a signal problem.

If insider/institutional data were populated across the full universe, this becomes a real
experiment with real statistical power. As it stands the question cannot be answered. **This is
the highest-leverage next step**: it converts an unanswerable question into an answerable one,
which is exactly the pattern that has paid off elsewhere today.

### 4. Structural, and genuinely hard: an early-warning pillar

Every current pillar answers *"is this moving up?"*. None answers *"is this about to move?"*.
Candidate non-momentum precursors, none currently computed at signal time:

- **Volatility compression** (Bollinger squeeze, ATR contraction) — measures *coiling* before a
  move, not the move itself. Genuinely leading, and computable from data already stored.
- **Options-flow precedence** — aggressive call buying *before* price responds. The data now
  exists via UW.
- **Borrow-rate spikes** — real short-side stress, not currently ingested.

Of these, **volatility compression is the most promising**: it is computable from existing price
bars, needs no new data source, and is the one precursor that is mathematically *not* a momentum
measure.

**And it is ALREADY BUILT.** `check_prebreakout_alerts()` (scheduler.py:3856) uses
`detect_price_compression()` to fire while a stock is *still coiling*, explicitly framed as the
pre-move counterpart to the classic squeeze alert. It is the exact non-momentum, genuinely
leading pillar this analysis concluded was missing — it just has not been evaluated yet:

| Fired | Resolved (5d) | Avg 5d | Win rate |
|---|---|---|---|
| 26 | **10** | −0.042% | 0.0% |

10 resolved outcomes is far too thin to judge — 0% win on n=10 is as likely to be noise as
signal. **This changes the recommendation: the highest-value entry-timing work is not building
a new pillar, it is giving the existing one enough data to be evaluated.**

Concretely: let `check_prebreakout_alerts()` accumulate for 3–4 weeks, then compare its forward
returns against the classic squeeze alert and against no-signal on the same names — the same
comparison structure used throughout this session. If compression genuinely leads, it is the
missing pillar and can be promoted into signal generation. If it does not, that closes the
last untested structural idea and the honest answer becomes "this system is a research and
risk tool, not an entry engine."


### 5. What NOT to do

- **Do not tune thresholds.** Every looser variant tested today performed worse.
- **Do not gate on insider_score.** Six symbols is not a factor.
- **Do not rebuild the confidence formula.** It is correctly signed post-fix; that was already
  established and is not the problem.

## The honest summary

Entry timing has one shipped mitigation (anti-chasing, +0.17 pts validated) and one clear path
to a real answer (populate insider/institutional data, then re-test). The structural fix — a
genuine non-momentum pillar — is real work, and volatility compression is the best candidate
because it can be built from data already in hand.

What was *not* found today is a quick win. Two candidates looked like one (the ignition band,
insider score) and both dissolved under a wider sample. That is worth stating plainly: the
absence of a shortcut is itself a finding, and it is why the shipped fixes were deliberately
modest and measured rather than bold and unvalidated.
