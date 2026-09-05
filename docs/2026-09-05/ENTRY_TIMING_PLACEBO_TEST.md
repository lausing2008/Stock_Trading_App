# CONTROLLED TEST: THE DEFECT IS ENTRY TIMING, NOT STOCK SELECTION

**Date:** 2026-09-05
**Question carried over from `EDGE_SUBSET_ANALYSIS.md`:** the −2.02% alpha intercept was real,
but its cause was UNPROVEN. Inverted stock selection was the leading hypothesis and failed its
confirmation checks.

## ANSWER: it is entry timing. Stock selection is exonerated.

---

## Why a cross-sectional control was abandoned

The originally-planned test (signaled vs. unsignaled stocks, matched on sector/cap/volatility)
**cannot be built from this data:**

- The tracked universe is **193 stocks**, of which **158** received BUY signals — leaving ~35
  controls, and only **11** after removing index/sector ETFs.
- `stocks` has **no market-cap column** and no ETF flag, so matching on size is impossible and
  ETF exclusion is manual.

An 11-symbol control cannot support a conclusion. Instead: a **placebo-entry design**, where
each signal acts as its own control — same stock, same holding period, entry shifted earlier.
This removes stock selection from the comparison entirely, by construction.

## The test

For every BUY outcome, hold the **same stock** for the **same number of days**, but enter N
days earlier than the signal did.

| Entry point | n | Return |
|---|---|---|
| 7 days earlier | 4,770 | **+2.57%** |
| **14 days earlier** | 4,770 | **+6.67%** |
| 21 days earlier | 4,770 | **+6.03%** |
| 30 days earlier | 4,770 | +0.34% |
| 45 days earlier | 4,769 | −2.52% |
| **The actual signal** | 4,770 | **−1.89%** |

**Entering 14 days before the signal fires returns +6.67%. Using the signal returns −1.89% — an
8.5-point penalty for waiting for the signal.**

Same stocks. Same holding periods. The only variable is *when* the position is opened.

### Market-adjusted — the confound was checked, and the result survives

SPY rose from ~739 (late July) to ~773 (mid-August), so placebo entries 14–21 days earlier
landed near a market low and rode a real rally. Subtracting SPY's move over each **identical**
window:

| Entry point | Raw | SPY, same window | **Excess vs SPY** |
|---|---|---|---|
| **Signal (0)** | −1.90% | −0.42% | **−1.48%** |
| 7 days earlier | +2.57% | +0.65% | **+1.91%** |
| **14 days earlier** | +6.67% | +2.12% | **+4.55%** |
| 21 days earlier | +6.03% | +2.32% | **+3.71%** |
| 30 days earlier | +0.33% | +0.38% | −0.04% |

Market drift accounts for part of the raw magnitude but **not the pattern**. The
market-adjusted gap between entering 14 days early and using the signal is ~6 points, and the
signal is the only entry point with *negative* excess return.

## Mechanism: it buys after the move

| Metric | Value |
|---|---|
| Avg run-up in the 14 days **before** entry | **+7.67%** |
| Avg return **after** entry | **−1.89%** |
| n | 4,770 |

The system enters after a stock has already risen ~7.7%, then holds through the give-back. It
is **chasing momentum that has already happened** — buying tops.

This is the same pathology as `AUD232-BUY-FROM-TOP` (commit `aee6d17`, 2026-08-03). That fix
addressed the narrow single-tick stochastic-flicker case and did resolve the confidence
inversion, but **the broader buy-late behavior is still fully present in post-fix data.**

## Consequences

1. **Stock selection is exonerated.** The placebo holds the stock constant, so the −2.02%
   intercept cannot be a selection effect. The earlier "system picks stocks that go down"
   hypothesis is not merely unproven — it is now positively ruled out as the primary cause.
2. **This is the highest-value target in the system.** Unlike the diffuse "no edge anywhere"
   conclusion, this is one specific, measurable, addressable defect with a quantified cost of
   roughly 8.5 points versus an earlier entry on the same names.
3. **It is consistent with every prior finding**: the +4.66%/−8.07% win-loss asymmetry (buying
   extended, so downside is larger), losses scaling with holding time, and the 0.355 beta.

## Recommended next step (not yet done)

Determine *why* signals fire late. Two candidates, distinguishable by inspection:

- **Confirmation-stacking:** the pillar/conviction gates require so many indicators to align
  that by the time they all agree, the move is over. Testable by measuring the lag between the
  first pillar activating and the signal firing.
- **Momentum-weighted TA:** `_ta_score`'s weights favor already-extended conditions (price
  above SMAs, high RSI, MACD positive) — all of which peak *after* a run-up.

Both are investigable without changing production behavior.

## Data-integrity bug found while running this (separate issue)

**`DFNS` has an unadjusted split/reverse-split in its price history:** close jumps from **$0.042
to $4.26 (101x) on 2026-07-20**, with no adjustment applied to prior bars. Later bars show
further unadjusted jumps (3.0x on 07-27, 0.3x on 07-31).

- Any rolling feature, signal, or return computed on DFNS is corrupted. An uncleaned placebo
  run produced an +81% average return driven almost entirely by DFNS rows (one showed a
  +72,682% "return").
- **A universe-wide scan (all D1 bars since 2026-06-01, flagging day-over-day ratios >3x or
  <0.34x) found DFNS as the ONLY affected symbol** — so prior analyses remain valid; DFNS's
  rows were too few to move their averages.
- Worth fixing at the ingest layer (split-adjustment verification) rather than by patching the
  one symbol.
