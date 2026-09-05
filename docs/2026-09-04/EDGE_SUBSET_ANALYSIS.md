# EDGE SUBSET ANALYSIS — IS THERE ANY PROFITABLE SLICE?

**Date:** 2026-09-04
**Question:** does *any* subset of the system (style, market, regime, research-backed,
symbol group) beat buy-and-hold on the same stocks over the same window?
**Window:** 2026-08-04 onward only — post-`aee6d17`, so the confidence-inversion bug is
excluded (see `PHASE_B2_INVERSION_ROOT_CAUSE_FOUND.md`).

## ANSWER: No. No slice shows durable alpha.

Headline, all 158 symbols with ≥10 outcomes:

| Metric | Value |
|---|---|
| Signal return | **−2.72%** |
| Buy-and-hold, same stocks, same window | **−1.96%** |
| **Alpha** | **−0.76%** |
| Symbols beating buy-and-hold | **73 / 158 (46%)** — a coin flip |

---

## Method note: two traps that produce false positives

**1. `pct_return` is unsigned raw price movement.** A SELL signal "returning +1.58%" means
price ROSE after a SELL — a loss. Every comparison here uses
`CASE WHEN signal_direction='BUY' THEN pct_return ELSE -pct_return END`. The unsigned view
makes SELL signals look like the best performers in the system; they are not.

**2. Beating zero ≠ having edge.** A slice returning +0.5% in a window where its stocks rose
+3% destroyed value. Everything below compares against buy-and-hold on **the same symbols over
the same window**, never against zero.

---

## What was tested

### By horizon × direction — all negative (direction-signed)

| Horizon | Dir | n | Signed return |
|---|---|---|---|
| SHORT | SELL | 110 | 0.00% |
| SWING | SELL | 135 | −0.71% |
| GROWTH | SELL | 109 | −0.83% |
| LONG | BUY | 147 | −1.05% |
| SHORT | BUY | 1939 | −1.20% |
| LONG | SELL | 147 | −1.58% |
| GROWTH | BUY | 1324 | −3.01% |
| SWING | BUY | 1406 | −3.36% |

Not one combination is positive.

### By market × regime — only a noise-sized positive

Only HK/choppy is positive (+0.82%, n=41 — too small to act on). US/bull, the dominant
population at n=3,924, is −2.20%.

### Research-backed signals — the most promising lead, and it did not survive

Naive comparison suggested a large effect:

| Group | n | Signed return |
|---|---|---|
| research_rec = BUY | 242 | **+0.62%** |
| research_rec = WATCH | 387 | +0.45% |
| no research | 4,596 | **−2.61%** |

**But this is stock selection, not research skill.** Research runs on only 70 of 233 symbols.
Restricting to the 67 symbols that have outcomes BOTH with and without research:

| Group | n | Symbols | Signed return |
|---|---|---|---|
| has_research | 717 | 67 | +0.57% |
| **no_research (same 67 symbols)** | 1,789 | 67 | **+0.41%** |

The gap collapses from ~3.2 points to **0.16 points**. Research adds almost nothing; those
symbols perform well with or without it. The real driver is *which stocks*, not *which reports*.

### Symbol persistence — real, but it is not signal skill

Split the window in half. Symbols profitable in the first half vs. the second:

| First-half group | Symbols | 2nd-half signal | 2nd-half buy-and-hold | **Alpha** |
|---|---|---|---|---|
| Was profitable | 47 | +0.37% | +0.59% | **−0.22%** |
| Was losing | 81 | −2.06% | −8.03% | **+5.97%** |

Persistence is genuine (corr 0.659 in the losing group). **But the interpretation inverts the
obvious reading:**

- On stocks that went UP, the system returned +0.37% while holding returned +0.59% — trading
  them **underperformed** doing nothing.
- On stocks that went DOWN −8.03%, the system lost only −2.06% — **+5.97% of damage avoided.**

The system's only measurable skill is **not losing as much in falling stocks**. It does not
capture upside better than holding.

---

## Conclusions

1. **There is no profitable subset to concentrate on.** Every dimension tested — style, market,
   regime, research backing, symbol history — is negative or explained by stock selection.
2. **The confidence fix worked but was not sufficient.** High-confidence is now the best bucket
   (51.6% win rate) rather than the worst. It is still unprofitable, because winners average
   +4.66% and losers −8.07% — a ~1.7x adverse ratio.
3. **Entries have no edge; this is not an exit-tuning problem.** Fixed 5d/10d/20d horizons are
   all negative (−1.38% / −1.83% / −2.01%), so no exit rule recovers it.
4. **The system's demonstrable skill is defensive** — avoiding damage in declining names. That
   is a real, measurable capability, and it is a *risk-management* product, not an
   alpha-generation one.

## Recommendation

**Stop optimizing signal generation.** Further signal fixes optimize a component with no
measured edge. Specifically:

- **Do not** run the signal-engine live-bar refactor (already recommended against on regression
  risk — see this session's notes on AUD232).
- **Do** treat the platform as research/monitoring/risk tooling, where its capability is real.
- **Do** fix the +4.66% / −8.07% asymmetry regardless of anything else — that ratio bleeds
  capital even with a positive-edge system.
- **For the stated goals** (dip buying, passive income, trading "like an expert"), the QQQ LEAPS
  playbook is the better vehicle: it does not depend on this system having edge, and
  buy-and-hold beat the signals by 0.76% per trade over this window.

## Caveats

- One month of post-fix data (5,317 outcomes, 158 symbols). The direction is consistent across
  every cut, but a month is a month.
- Window was near-flat (SPY +0.24%, QQQ −0.38%) — an untested regime dependence remains.
- The defensive-skill finding (+5.97% alpha on declining stocks) deserves its own study; it is
  the one genuinely positive result here and was not the thing being looked for.
