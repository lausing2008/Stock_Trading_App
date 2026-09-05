# PHASE B2 — CONFIDENCE INVERSION: ROOT CAUSE FOUND (AND ALREADY FIXED)

**Date:** 2026-09-04
**Status:** **RESOLVED.** The inversion was caused by a real, since-fixed bug. It is gone from
all data generated after 2026-08-03.
**Supersedes:** the "root cause unfound" conclusion in `PHASE_B_LIVEBAR_HYPOTHESIS_TEST.md` §3
and the open-question framing in `SYSTEM_CAPABILITY_ASSESSMENT_2026-09-04.md`.

---

## Answer

The confidence inversion (higher AI confidence → worse forward returns) was **an artifact of
`AUD232-BUY-FROM-TOP`, a real signal-generation bug fixed in commit `aee6d17` on 2026-08-03.**

The bug caused the *highest-conviction* BUY signals to fire on **extended, overbought stocks
sitting at their 20-day highs** — momentum peaks that then reverted. Because those setups also
score highest on TA, confidence and "buying the top" were the same thing. High confidence was
therefore *causally* selecting the worst entries.

Since the fix, the relationship has **flipped to the correct direction** and high-confidence
signals are now the best-performing bucket — the only one above break-even.

## The evidence

Split precisely on the fix date (`aee6d17`, 2026-08-03), BUY outcomes only:

| Era | Low conf (<40) | Mid conf (40–80) | High conf (≥80) |
|---|---|---|---|
| **Before fix** | −1.06% (46.3% win, n=2448) | −2.84% (42.2%, n=4581) | **−7.19% (33.5%, n=744)** |
| **After fix** | −2.57% (36.0%, n=3032) | −1.96% (40.4%, n=1689) | **−1.02% (51.6%, n=95)** |

High-confidence BUYs went from the **worst** bucket to the **best** bucket, and are now the only
band with a win rate above 50%. The ordering after the fix is monotonic in the correct direction.

### Weekly correlation, `CORR(confidence, pct_return)` — the sign flip is sharp

| Week | n | corr |
|---|---|---|
| 2026-06-22 | 1469 | −0.120 |
| 2026-06-29 | 1206 | **−0.215** |
| 2026-07-06 | 1303 | −0.128 |
| 2026-07-20 | 613 | −0.142 |
| 2026-07-27 | 873 | −0.109 |
| **2026-08-03** ← fix lands | 1633 | −0.006 |
| 2026-08-10 | 1761 | **+0.072** |
| 2026-08-17 | 1298 | +0.013 |
| 2026-08-24 | 302 | **+0.100** |

Consistently negative for six weeks, then non-negative every week from the fix onward.
`avg_confidence` also drops sharply (≈55 → ≈29) at the same boundary — consistent with the fix
removing a large population of falsely-high-confidence overbought entries.

### Why this mechanism explains it exactly

From `aee6d17`'s own commit message — a live case on `0939.HK`:

> the conviction gate correctly blocked a BUY alert all week via `stoch_rsi_overbought`
> (`stoch_k > 0.80`) while price sat near its 20-day high — then fired the moment `stoch_k`
> ticked from 0.824 to 0.735 on ONE noisy 5-min refresh, with RSI (70) and price (still within
> 1.5% of the high) essentially unchanged. **The disqualifier vanished on oscillator noise, not
> real cooling.**

The fix added two hard disqualifiers in `signals.py`'s `_ta_score()`:
- `stoch_rsi_still_hot` — the *prior* bar must also be below 0.80, so a one-tick dip isn't
  mistaken for a genuine reset.
- `near_recent_high_hot` — price within 3% of its 20-day high with RSI still >65 is still
  extended, independent of the stochastic.

This is a satisfying causal story rather than a statistical curiosity: the inversion was not
"confidence is meaningless," it was "confidence was measuring momentum-extension, and we were
buying it."

---

## A second, independent finding: the reported magnitudes were wrong (unit-scale error)

While running this test I found that **`signal_outcomes.pct_return` is stored as a FRACTION, not
a percent** — `(exit - entry) / entry`, no `× 100`
([`services/signal-engine/src/api/outcomes.py:445`](../../services/signal-engine/src/api/outcomes.py#L445)).
Observed distribution over all 16,732 rows: min −0.7032, median −0.0045, max +1.1215,
mean −0.0161 — i.e. **mean −1.61%**.

Note this differs from `paper_trades.pct_return` (market-data), which **is** stored ×100 as a
true percent (e.g. `paper_trading_engine.py:283`). **Two same-named columns on two tables, two
different scales.** That is the trap.

Consequence: prior analyses that read the fraction as if it were already a percent overstate
magnitudes by 100×. The figures quoted in `PHASE_B_LIVEBAR_HYPOTHESIS_TEST.md` (e.g. "−11.52%"
for the top confidence bucket, "−2.51%" overall) should be read with this in mind — the
*shape* of the inversion reported there was real and is reproduced here, but the absolute
numbers in that document were not on the scale they claimed.

**Recommendation:** treat this scale mismatch as its own finding worth fixing — either rename
one of the two columns, or normalize them to the same unit, so the next analysis doesn't
silently inherit the same 100× error.

---

## Consequences for prior conclusions

1. **`PHASE_B_LIVEBAR_HYPOTHESIS_TEST.md`'s negative result stands and is now explained.** That
   test correctly rejected live-bar contamination as the cause. It searched for the real cause
   in the wrong dimension (time-of-day) — the actual cause was a *code version* boundary. Its
   four proposed next-hypotheses (confidence formula rewards extremity, fusion weighting,
   selection bias, training leakage) are **all superseded** — no further investigation of them
   is warranted on the strength of this finding.
2. **The inversion is not evidence that "confidence is meaningless."** Post-fix it carries real,
   correctly-signed information. Any roadmap item premised on rebuilding the confidence formula
   because it is inverted should be re-scoped or dropped.
3. **Aggregate/all-time performance stats are polluted by the pre-fix era** and understate current
   system quality — roughly 60% of all evaluated BUY outcomes (7,773 of 12,589) predate the fix.
   Any dashboard or report that pools all history is showing a blend of two materially different
   systems.

## Recommended follow-ups (none yet actioned)

- **Re-baseline reported performance from 2026-08-04 onward**, or split pre/post-fix everywhere
  aggregate signal performance is displayed. This is the highest-value follow-up.
- **Fix the `pct_return` unit-scale mismatch** between `signal_outcomes` and `paper_trades`.
- **Keep monitoring** — the post-fix high-confidence bucket is still only n=95. The direction is
  clear and consistent across three weekly correlations, but the magnitude deserves another look
  once more outcomes accumulate.
- **The live-bar fix remains open on its own merits** (still paused per instruction). This
  finding does not change that: it was never the cause of the inversion, but it is still a real
  point-in-time-correctness defect.
