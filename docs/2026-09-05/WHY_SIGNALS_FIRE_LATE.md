# WHY SIGNALS FIRE LATE — DIAGNOSIS

**Date:** 2026-09-05
**Follows:** `ENTRY_TIMING_PLACEBO_TEST.md`, which established that entry timing (not stock
selection) causes the −2% alpha intercept. This asks *why* entries are late.

**Two hypotheses tested. Answer: they are the same mechanism, not alternatives.**

---

## Finding 1 — the system fires on already-extended conditions

Comparing the feature values stored in `signals.reasons` at BUY-fire moments vs. the 17,663
evaluations that produced no signal (medians, DFNS excluded):

| Feature | BUY fires | No signal |
|---|---|---|
| RSI | **57.1** | 50.7 |
| % below 20-day high | **2.40%** | 5.93% |
| ROC(10) | **2.33%** | 0.72% |
| ROC(20) | **5.11%** | 2.69% |
| Bollinger %B | **0.687** | 0.559 |

The system fires when a stock is near its high, already rising, and in the upper third of its
Bollinger band. Momentum-weighting: **confirmed**.

## Finding 2 — confirmation-stacking IS momentum-chasing

BUY requires ~3.10 of 4 pillars active vs. 2.24 at baseline. The two hypotheses looked
independent, but they are not — **price extension rises monotonically with pillar count:**

| Pillars active | ROC(10) | % below 20d high | RSI |
|---|---|---|---|
| 0 | −8.94% | 14.22% | 38.0 |
| 1 | −5.63% | 11.67% | 42.8 |
| 2 | +2.74% | 6.28% | 50.1 |
| **3 (BUY threshold)** | **+3.14%** | **3.12%** | **54.8** |
| 4 | +2.35% | 3.17% | 58.7 |

The four pillars (trend, momentum, volume, structure) are **all momentum measures**. Requiring
3+ to agree mechanically guarantees entry only after a stock has already run ~3% and sits ~3%
from its high. **Confirmation-stacking and momentum-chasing are the same defect.** You cannot
relax one without relaxing the other.

## Finding 3 — but MORE pillars perform BETTER (do not relax the gate)

The obvious remedy — require fewer pillars — is wrong:

| Pillars at entry | n | Return |
|---|---|---|
| 1 | 78 | −2.30% |
| 2 | 1,370 | −3.28% |
| 3 | 2,600 | −1.60% |
| **4** | 722 | **−0.23%** |

Confirmation is doing real work. The problem is not *how much* confirmation is required, but
that every pillar measures the same underlying thing.

## Finding 4 — extension at entry predicts the loss

| Extension at entry | n | Return | RSI | ROC(10) |
|---|---|---|---|---|
| **At the high (<2% below)** | 2,203 | **−2.27%** | 62.9 | **+12.31%** |
| Near (2–5%) | 1,231 | **−0.73%** | 56.5 | +7.40% |
| Pullback (5–10%) | 810 | −1.56% | 52.1 | +5.45% |
| Deep (>10%) | 526 | −3.49% | 49.3 | +5.23% |

The worst bucket buys stocks that just ran **+12.31% in ten days**. The best entry is a modest
2–5% pullback — not at the high, not a deep decline (which is falling-knife territory).

---

## Candidate filter — real but modest, and mostly curve-fit

In-sample, an anti-chasing filter looked strong:

| Rule | n | Return |
|---|---|---|
| Current behavior | 4,770 | −1.89% |
| ROC(10) < 8 | 2,708 | −0.69% |
| Combined (>2% below high, RSI<60, ROC10<8) | 1,587 | −0.61% |
| **Combined + 3 pillars** | 1,188 | **−0.25%** |

**Out-of-sample validation (fit on ≤2026-08-17, tested after) deflates this substantially:**

| Period | Unfiltered | Filtered | Gain |
|---|---|---|---|
| Fit period | −2.22% | +0.01% | +2.23 |
| **Holdout** | −0.96% | **−0.63%** | **+0.33** |

The honest estimate is **~+0.33 points**, not the +1.6 the in-sample fit suggested. The
thresholds (2%, 60, 8) were chosen on the same data that produced the in-sample number — most
of that apparent gain was curve-fitting. The filter also discards ~60% of signals.

**It does not reach profitability.** −0.63% is still a loss.

## Conclusions

1. **Root cause identified:** the pillar/conviction architecture is built entirely from
   momentum indicators that peak *after* a move. Requiring several to agree guarantees late
   entry — this is structural, not a tuning error.
2. **Do not fix it by relaxing the gate.** Fewer pillars performs worse (Finding 3).
3. **The real fix is a different pillar, not a looser one.** Every current pillar answers "is
   this moving up?" None answers "is this cheap?" or "is this early?" A genuinely independent,
   non-momentum pillar (valuation, mean-reversion, catalyst-before-move) is what the
   architecture lacks — and adding one is a design change, not a threshold change.
4. **An anti-chasing filter is worth ~0.33 points out-of-sample** — real, cheap, and honest,
   but it does not make the system profitable and costs ~60% of signal volume.

## What this does not answer

Whether a non-momentum pillar would actually work. That cannot be tested from stored data,
because the features to build it (valuation vs. history, distance from fair value, catalyst
timing) are not currently computed at signal time. It would need to be built and forward-tested.
