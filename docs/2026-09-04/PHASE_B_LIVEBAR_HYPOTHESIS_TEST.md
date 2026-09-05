# PHASE B — TARGETED TEST: DOES LIVE-BAR CONTAMINATION EXPLAIN THE CONFIDENCE INVERSION?

**Date:** 2026-09-04
**Prerequisite:** `DATA_QUALITY_AUDIT.md` Part 4 — this test was explicitly required before treating
the live-bar contamination finding as the cause of the confidence inversion reported in
`SYSTEM_CAPABILITY_ASSESSMENT_2026-09-04.md`.

**Question tested:** *does the inversion (higher confidence → worse returns) weaken or disappear
once restricted to signals generated after their reference bar had genuinely settled?*

---

## RESULT: NO. The hypothesis is REJECTED as the primary cause.

The inversion is **essentially unchanged** in the clean subset. **A second, independent cause is
still unfound.** Do not proceed to fix the live-bar defect under the belief it will resolve this
finding — fix it on its own merits (§ below), but keep looking for the real driver of the
inversion.

---

## Method

`signal_outcomes` has no signal-generation timestamp of its own (`signal_date` is a date, not a
timestamp), but `signal_outcomes.signal_id` joins to `signals.ts` (a real, precise generation
timestamp). Classified every BUY outcome by whether its signal was generated during genuine US
market hours (9am–3:59pm ET, where the "today" bar this report's Part 1 describes is still
actively forming) vs. outside that window (pre-market/after-close/overnight, where the most
recent bar reflects a real prior settled close).

**Timezone correction applied and verified:** `signals.ts` is stored naive-but-UTC. A naive
`AT TIME ZONE 'America/New_York'` conversion silently does the WRONG direction (interprets the
value as already being ET and converts to UTC). Verified this directly against known real
timestamps before trusting any result — the correct form is
`ts AT TIME ZONE 'UTC' AT TIME ZONE 'America/New_York'`.

## Step 1 — is the intraday subset actually worse? (sanity check before the real test)

| Bucket | n | Win rate | Avg return |
|---|---|---|---|
| Intraday (live bar) | 810 | 38.9% | **−3.23%** |
| After-close (settled) | 11,779 | 41.0% | **−2.51%** |

Directionally consistent with the contamination hypothesis (intraday is worse), but the gap is
small and the intraday sample is thin. This alone neither confirms nor refutes the hypothesis —
it's the setup for the real test below.

## Step 2 — THE TEST: confidence-vs-return, settled subset ONLY (n=11,779)

| Confidence bucket | n | Win rate | Avg return |
|---|---|---|---|
| 4–20% | 1,156 | 34.4% | −2.52% |
| 20–40% | 3,927 | 43.1% | −1.64% |
| 40–60% | 3,829 | 42.5% | −2.13% |
| 60–80% | 2,072 | 40.0% | −3.37% |
| 80–100% | 680 | 36.5% | **−5.52%** |
| =100% | 115 | 29.6% | **−11.52%** |

**Compare to the full-population result already reported** (4–20%: −2.63%; =100%: −11.43%):
the pattern is **the same monotonic inversion, same magnitude, in the population that has
already been cleared of live-bar contamination.** Removing the suspected contaminant did not
move the result.

## Step 3 — intraday-only subset (n=810, exploratory, not conclusive)

| Confidence bucket | n | Win rate | Avg return |
|---|---|---|---|
| 4–20% | 58 | 13.8% | **−4.87%** |
| 20–40% | 339 | 38.3% | −2.26% |
| 40–60% | 247 | 41.7% | −3.62% |
| 60–80% | 122 | 47.5% | −2.50% |
| 80–100% | 41 | 36.6% | −8.28% |
| =100% | 3 | 33.3% | −8.08% |

Too thin to draw a real conclusion (n=3 in the top bucket). Notably the *lowest*-confidence
bucket is disproportionately bad here (−4.87% vs. −2.52% in the settled subset) — worth
revisiting once more intraday-generated outcomes accumulate, but not decision-grade today.

---

## Conclusion

1. **The live-bar contamination defect is real, confirmed, and worth fixing** — see
   `DATA_QUALITY_AUDIT.md` §1.8 for the recommended fix (port `train_model()`'s own existing
   guard to `predict_latest()`, the T196 gate, and signal-engine's feature stack). It measurably
   makes intraday-generated signals somewhat worse (Step 1).
2. **It does not explain the confidence inversion.** The inversion is a property of the
   *settled*, uncontaminated data too (Step 2) — same shape, same magnitude, most acute at the
   very top of the confidence range regardless of whether the reference bar had settled.
3. **The real cause of the inversion remains unfound.** Candidate directions for the next
   investigation, per the original audit's own §B.5 (per-layer edge attribution):
   - Is the *training* pipeline itself (separately confirmed correctly excluding today's bar)
     nonetheless training on mislabeled or leaking targets in some other way?
   - Is `confidence` itself computed via a formula that inherently rewards extremity
     (`confidence = abs(bull_probability - 0.5) × 200`, per CLAUDE.md) in a market where extreme
     model outputs correlate with overfitting rather than genuine conviction?
   - Does the ML/TA fusion weighting itself amplify noise at the tails — i.e., is `fused_prob`'s
     construction (not its inputs) the actual defect?
   - Is there a survivorship/selection effect in which of the enormous universe scanned each
     cycle, only the most "confident-looking" candidates get logged as `signal_outcomes` at all,
     biasing the top bucket toward a specific failure mode (e.g., overextended/chasing entries)?

**Recommendation:** proceed with the live-bar fix on its own merits (it's a real, confirmed
defect regardless of this result), but treat the confidence-inversion root cause as still open.
The next diagnostic should test the "confidence formula rewards extremity" and "fusion weighting"
hypotheses directly, since those sit upstream of both TA and ML and would explain why the
inversion appears in EVERY component layer (`ta_score`, `ml_prob`, `fused_prob`) independently,
which this test's negative result makes more, not less, puzzling.
