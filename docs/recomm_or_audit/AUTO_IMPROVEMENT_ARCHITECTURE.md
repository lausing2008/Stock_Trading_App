# AUTO-IMPROVING TRADING SYSTEM — ARCHITECTURE & ROADMAP

**Written:** 2026-09-04
**Companion to:** `WEEKLY_SYSTEM_REVIEW_PROMPT.md` (weekly),
`AI Stock Trading Platform — Independent Trading Audit Prompt (REVISED 2026-09-04).md` (quarterly)

---

# 0. THE HEADLINE

**You already have most of an auto-improving system.** The machinery exists and is unusually
well-built for a platform this size:

| Component | Status |
|---|---|
| Outcome recording (`signal_outcomes`, n=16,732) | ✅ Built, working |
| Alert-outcome tracking (options-flow, squeeze, dark-pool) | ✅ Built, working |
| Weekly auto-tuning (TA weights, ML weight, K-Score, entry weights, min_rr) | ✅ Built, working |
| Train/validation split with held-out validation | ✅ Built |
| "Candidate must beat current baseline on validation" gate | ✅ Built |
| `tune_history` audit trail of every attempt | ✅ Built |
| Promotion gate (human-in-loop, never auto-writes config) | ✅ Built |
| Data-quality / job-liveness monitoring | ✅ Built (expanded 2026-09-04) |
| ML retraining + OOS suppression | ✅ Built |
| Walk-forward harness | ❌ **Missing** — the biggest gap |
| Execution instrumentation (bid/ask, MFE, MAE) | ❌ **Missing** |
| Regime diversity in the data | ❌ **97.3% bull, 1 bear row** |

**So the answer to "how do I build an auto-improving system" is mostly: you did. The remaining
work is (a) closing three specific gaps, and (b) resisting the urge to add more tuning loops
before the feedback signal those loops consume is trustworthy.**

---

# 1. THE CENTRAL RISK: A CONFIDENT LOOP OPTIMISING A BROKEN SIGNAL

Every auto-improvement loop is a **feedback amplifier**. It makes whatever you point it at
stronger — including a mistake.

Two live examples from this platform, both cases where the loop worked perfectly and the
*target* was wrong:

**Example 1 — `regime_min_rr_ratio`.** Auto-calibrated weekly from real closed trades, with a
proper train/validation split and a beats-baseline gate. It selected **3.38** from a curve whose
sample collapsed to **5–6 trades** at the winning threshold. That value was then applied
market-blind to HK, whose own stop/target parameters cap achievable R:R near **2.9**. Result: two
HK portfolios silently stopped trading for **2+ months**. The tuner did exactly what it was told.
The instruction had a blind spot.

**Example 2 — confidence calibration.** The platform tunes weights that feed a confidence score.
A live check found win rate **flat at ~29–31% across every confidence quintile** — i.e. the
score carried nearly no information about outcome. Tuning the inputs to a score that doesn't
predict anything optimises noise very precisely.

**The lesson that should govern every decision below:**

> Before adding a feedback loop, prove the signal it optimises is real.
> Before trusting a loop's output, check the sample it decided on.
> A loop with a validation gate is not automatically safe — it is safe only if the validation
> data can actually distinguish good from lucky.

---

# 2. THE FIVE LAYERS OF A SELF-IMPROVING SYSTEM

Ordered by dependency. **Each layer is only as trustworthy as the one below it.** Most failures
come from building an upper layer on a broken lower one.

```
   ┌─────────────────────────────────────────────────┐
   │ L5  STRATEGY EVOLUTION   (add/retire strategies) │  ← quarterly, human-gated
   ├─────────────────────────────────────────────────┤
   │ L4  PARAMETER TUNING     (thresholds, weights)   │  ← weekly, auto + gate
   ├─────────────────────────────────────────────────┤
   │ L3  VALIDATION           (walk-forward, OOS)     │  ← ❌ PARTIALLY MISSING
   ├─────────────────────────────────────────────────┤
   │ L2  ATTRIBUTION          (which input has edge?) │  ← ⚠️  UNVERIFIED
   ├─────────────────────────────────────────────────┤
   │ L1  MEASUREMENT          (outcomes, execution)   │  ← ⚠️  PARTIAL (no MFE/MAE/spread)
   └─────────────────────────────────────────────────┘
```

**You currently have a strong L4 sitting on an incomplete L1–L3.** That is the single most
important structural observation in this document. More L4 (more tuners, more frequent tuning)
will not help and may hurt. The work is downward, not upward.

---

# 3. WHAT TO BUILD, IN ORDER

## Priority 1 — Close the L1 measurement gaps

**1a. Execution instrumentation** *(full audit §F.5)*
Record per trade: bid, ask, spread at decision time, MFE, MAE, and real fill vs. intended fill.
Today there is only a flat `entry_slippage_pct: 0.001` **assumption**.

*Why first:* you cannot answer "is paper trading unrealistically optimistic?" — and therefore
cannot trust any expectancy number — without it. It also needs **lead time to accumulate data**,
so every week it isn't built is a week of un-instrumented trades. Build it before you need it.

**1b. Regime recording integrity**
With 97.3% of outcomes in `bull` and exactly **1** `bear` row, the regime field is the binding
constraint on most performance conclusions. Verify it's recorded correctly on every outcome now,
so the data is clean when regimes finally diversify. (The regime vocabulary has already needed
one migration — a second silent drift would waste the wait.)

## Priority 2 — Verify the L2 attribution before trusting L4

**2a. Confidence calibration at scale** *(full audit §B.3)*
Re-run confidence-vs-actual-win-rate across all **16,732** signal outcomes, per horizon. The
flat-across-quintiles finding was measured on the small paper-trade sample; confirm or refute it
properly.

**If it holds: pause weight-tuning loops that feed confidence until calibration is rebuilt.**
Continuing to tune inputs to a non-predictive score is the exact failure in §1.

**2b. Per-layer edge attribution** *(full audit §B.5)*
`signal_outcomes` carries `ta_score`, `ml_prob`, `ml_auc`, `fused_prob` — enough to separate TA
from ML from fusion. Any layer with no measurable alpha should have its weight cut, not tuned.

## Priority 3 — Build the L3 validation gap

**3. Walk-forward harness** *(full audit §F.3)*
Known and deferred ("2+ weeks of work"). It is the **highest-leverage single build** in this
document because it partially unblocks three other things: per-strategy portfolio statistics
(§F.1), Monte Carlo (§F.4), and the promotion gate's currently-unimplemented rule 4
(`SignalOutcome`/`PaperTrade` agreement, today always recorded as `not_yet_available`).

Until it exists, "out-of-sample validated" means a single train/validation split on a small
sample — better than nothing, considerably weaker than it sounds.

## Priority 4 — Only then, extend L4/L5

Once L1–L3 are solid, extending auto-tuning becomes reasonable. Until then, **adding tuning
loops increases the rate at which the system confidently moves in unverified directions.**

---

# 4. SAFETY RAILS FOR ANY AUTO-TUNING LOOP

Rules any existing or future loop must satisfy. Most are already implemented — the ones marked
⚠️ are the gaps that produced real incidents.

1. **Held-out validation, never in-sample selection.** ✅
2. **Must beat the current live value on validation, not merely be the best candidate.** ✅
3. **Minimum sample size at the *winning* value** — ⚠️ **this is the `min_rr` failure.** A
   threshold that wins on 5 trades must be rejected, however good its EV looks. Enforce a floor
   on the winning bucket's own n, not just total n.
4. **Segment-awareness.** ⚠️ A parameter tuned on pooled data must not be applied to a segment
   that structurally cannot satisfy it (the HK R:R case). Either tune per segment, or cap the
   pooled value at what each segment can actually achieve.
5. **Bounded movement per cycle.** Cap how far any parameter can move in one week. A tuner that
   can double a threshold in one step can silently disable a market in one step.
6. **Full audit trail of every attempt, promoted or not.** ✅ (`tune_history`)
7. **Human gate on anything that changes live trading behaviour.** ✅ — the promotion gate
   deliberately does not write `portfolio.config`. **Keep it that way.** The temptation to close
   this loop is exactly the temptation to remove the last check on §1's failure mode.
8. **Liveness monitoring on the loop itself.** ⚠️ A tuner that silently stops running looks
   identical to one that finds no improvement. (`_DQ_CHECKS` now covers job liveness broadly —
   confirm every tuning job specifically is included.)
9. **Never tune a safety mechanism toward looser.** Stops, drawdown limits, and circuit breakers
   are risk controls, not performance parameters.

---

# 5. RECOMMENDED CADENCE

| Cadence | Activity | Doc | Acts on |
|---|---|---|---|
| **Continuous** | Alerting, DQ checks, job liveness | (automated) | Breakage |
| **Weekly** | System review — liveness + drift + outcome logging | `WEEKLY_SYSTEM_REVIEW_PROMPT.md` | **Breakage only** |
| **Weekly (auto)** | Existing tuning chain + promotion gate | (automated) | Params, human-gated |
| **Monthly** | Review `tune_history` trend; check observations log for 4+ week patterns | — | Investigations |
| **Quarterly** | Full audit | `...Audit Prompt (REVISED)` | Strategy, architecture |
| **Event-driven** | Full audit re-run | see REVISED §H.3 / weekly §6 | Structural change |

**The critical rule:** *weekly fixes breakage, quarterly changes strategy.* Collapsing those two
is how a review process becomes a noise-amplifier.

---

# 6. HOW TO KNOW IT'S ACTUALLY WORKING

Meta-metrics — track these over quarters, not weeks:

1. **Time-to-detection for silent failures.** The HK dormancy went 2+ months undetected; the
   `options-game-plan/batch` 500 went undetected since it shipped. Both should now be caught in
   ≤1 week by the weekly review. **If a future silent failure again goes >2 weeks, the weekly
   review has a coverage gap — fix the review, not just the bug.**

2. **Promotion-gate acceptance rate.** If ~every candidate promotes, the gate is too loose. If
   ~none do, either the system has converged or the tuner is searching uselessly. Both extremes
   warrant a look.

3. **Calibration slope.** Once §2a is measured, track whether higher confidence increasingly
   predicts higher win rate over time. **This is the single best proxy for "is the system
   actually learning."** A flat slope quarter after quarter means the loops are optimising noise.

4. **Regime coverage.** Track outcomes-per-regime. Most performance conclusions stay
   `UNMEASURABLE` until a second regime reaches ~300 outcomes.

5. **Out-of-sample decay.** Once walk-forward exists: does live performance match validation
   performance? A persistent gap means overfitting somewhere in the chain.

---

# 7. WHAT NOT TO DO

- **Do not close the promotion-gate loop** (auto-writing `portfolio.config`) until calibration is
  verified and walk-forward exists. The human gate is the last defence against §1.
- **Do not add more AI agents** (full audit §F.8) while the existing confidence score may not
  correlate with outcomes. Sophistication on an unvalidated base amplifies error.
- **Do not increase tuning frequency.** Weekly already exceeds what ~10 trades/week supports.
- **Do not tune from a single week**, however compelling the week looks.
- **Do not let the weekly review grow.** Scope creep turns a 45-minute health check into a
  multi-hour re-audit, which stops getting done — and an unrun review catches nothing.

---

# 8. SUMMARY

You asked how to build an auto-improving system. You largely have one: outcome tracking,
validated weekly tuning, an audit trail, a human-gated promotion step, and now broad liveness
monitoring. That is a genuinely good foundation.

The gap is **not** more automation. It is:

1. **Measure execution honestly** (bid/ask, MFE, MAE) — build now, it needs lead time.
2. **Verify the signal is real** (confidence calibration at n=16,732) — before tuning it further.
3. **Build walk-forward validation** — the keystone that unblocks the most downstream work.
4. **Wait for regime diversity** — no amount of engineering substitutes for a market that has
   only ever been measured going up.

And the discipline that makes it all work: **weekly fixes what's broken, quarterly changes what's
strategic, and the human gate stays closed until the feedback signal is proven trustworthy.**
