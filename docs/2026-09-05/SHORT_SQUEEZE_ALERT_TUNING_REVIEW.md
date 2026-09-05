# CAN WE IMPROVE THE SHORT SQUEEZE ALERT?

**Date:** 2026-09-05
**Question:** the short-squeeze alert has fired only 11 times ever and nothing since
2026-08-24. Can it be improved?

---

## Answer: yes it can be tuned — but it should NOT be, and the reason matters

The alert is **not** broken, and it is **not** starved by a mis-set threshold. Loosening it
would make performance **worse**, and the measurement below shows why: the entry conditions do
not identify squeezes, they identify stocks that already popped and then mean-revert.

**Recommendation: change nothing. The honest fix is a different signal, not a different
threshold.**

---

## What was measured

### The universe is not the constraint

97 symbols have short-%-of-float data; **20 clear the 15% bar** (SOUN 39.9%, TMDX 35.5%,
AI 32.6%, QUBT 32.0%, UPST 29.1%, …). There is no shortage of candidates.

### Which condition actually gates

Across 288 stock-days on high-short-float names since 2026-08-01:

| Condition | Days passing |
|---|---|
| Move ≥3% | **66** (23%) |
| RVOL ≥2.2 | **5** (1.7%) |
| Both | **3** |

The RVOL gate does essentially all the filtering.

### And the RVOL bar is genuinely high

Real RVOL distribution on those names: **median 0.82, p75 1.03, p90 1.36, p95 1.68, max 3.19**.

A flat 2.2 sits **above the 95th percentile**. However — `_session_elapsed_rvol_thresholds()`
scales the bar by elapsed session and floors it at 1.5, so the *effective* intraday bar is:

| Time | Effective bar |
|---|---|
| 10:09am–12:45pm | **1.50** (floor) |
| 2:22pm | 1.65 |
| 4:00pm | 2.20 |

So the real gate is ≈1.5 for most of the session — around p92. Selective, not broken. That
scaling is well-designed and already accounts for the partial-day-volume bias.

## The decisive test: does loosening help?

Forward 5-day returns by signal strength, high-short-float names, since 2026-06-01:

| Bucket | n | Avg fwd 5d | % up |
|---|---|---|---|
| Move ≥3% + RVOL ≥2.2 | 9 | **−1.14%** | 44.4% |
| Move ≥3% + RVOL ≥1.5 | 15 | **−4.41%** | 33.3% |
| Move ≥3% only | 150 | **−1.92%** | 38.7% |
| **No signal** | 570 | **−0.54%** | **43.3%** |

**Every signal bucket underperforms doing nothing.** The no-signal bucket is the best of the
four. And loosening RVOL from 2.2 → 1.5 produces the *worst* bucket (−4.41%), so the obvious
"make it fire more" tuning would actively hurt.

### It is not a horizon problem either

If a genuine squeeze continuation existed, it should appear in the first day or two, before
mean reversion:

| Horizon | Avg return (n=24) |
|---|---|
| +1 day | +0.03% |
| +2 days | +0.22% |
| +3 days | −0.45% |

Flat, then negative. There is no continuation effect at any horizon tested.

## Why this happens

The alert requires a **≥3% move already in progress**. That is not "shorts are about to be
squeezed" — it is "this already moved." On high-short-float names, which are volatile and
heavily traded by momentum, a 3%+ pop on elevated volume is most often the *end* of a move
rather than the start.

This is the **same structural defect** documented in
[`WHY_SIGNALS_FIRE_LATE.md`](./WHY_SIGNALS_FIRE_LATE.md): every confirming condition is a
momentum measure, so requiring confirmation guarantees late entry. The short-squeeze alert has
it in a sharper form, because it demands the confirmation be large (3%) on a stock class that
mean-reverts hard.

The 11 real fired alerts agree: **9.1% win rate, −6.17% avg 5d.** Small sample, but pointing
the same direction as the 552-stock-day reconstruction above.

## What would actually improve it (none of it is threshold tuning)

1. **Fire BEFORE the move, not after.** The precondition for a squeeze is structural — high
   short % of float, high days-to-cover, low float — not a 3% pop. An alert on "this is
   structurally squeezable AND something just changed" (borrow-rate spike, unusual call
   buying, a catalyst) would at least be early. `check_squeeze_ignition_alerts()` already
   exists as the earlier-stage tier and is the better place to invest.
2. **Use days-to-cover as a primary gate, not an escalation flag.** `_SQUEEZE_CRITICAL_DAYS_TO_COVER`
   is already computed and calibrated (p50 = 4.65 days) but only decorates the email. Days-to-cover
   is the closest thing to a real "can shorts get out?" measure the app has.
3. **Add borrow-rate / hard-to-borrow status** if available. A spiking borrow fee is a genuine
   leading indicator of short-side stress. This app does not currently ingest it.
4. **Consider inverting the read.** The measurement above suggests high-short-float names that
   just popped 3%+ are, if anything, *short* candidates on a 5-day horizon (−4.41% at RVOL 1.5).
   That is a real, if uncomfortable, finding and is worth its own study before acting on.

## What NOT to do

- **Do not lower the RVOL bar.** Measured: makes performance worse (−1.14% → −4.41%).
- **Do not lower the 3% move bar.** Move-only is also negative (−1.92%) and would flood the
  inbox.
- **Do not lower the 15% short-float bar.** It is not the binding constraint.

## Caveats

- 552 stock-days over ~3 months on 16 hand-picked high-short-float symbols. Directionally
  consistent across every cut, but a single market regime.
- The reconstruction uses daily bars; the live alert is intraday, so its actual entry price
  differs from the daily close used here. The direction of the finding is unlikely to flip,
  but the magnitudes are approximate.
- The 11 real alerts are too few to be conclusive on their own — they are corroboration, not
  the primary evidence.
