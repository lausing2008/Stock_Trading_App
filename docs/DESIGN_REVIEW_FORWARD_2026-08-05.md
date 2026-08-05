# Forward-Looking Design Review — 2026-08-05

Scope: AI Signal, RVOL, Volume Profile, HMM, News Events, FVG, Fixed Range VP, VWAP/AVWAP,
Swing Pivots, Short Squeeze, Option Expiry/gamma, Accumulation-Distribution, Breakout Quality,
CAPE, Sector Rotation, Confluence Score, Conviction Gate, K-Score.

Mapped to the 5 stated goals: (1) better signals, (2) **don't buy the top — buy when it starts
to rally**, (3) better prediction, (4) confidence/trust, (5) better return.

Documentation-only. No source files were edited.

---

## 0. Two findings that change the priority order

### 0.1 CRITICAL — Every service is running 3-week-old code. Most reviewed features are NOT live.

This was not in the brief and it dominates everything else. Verified directly:

```
market-data / decision-engine / technical-analysis / ranking-engine
event-intelligence / research-engine        → "Up 3 weeks"
ml-prediction                               → "Up 3 weeks (unhealthy)"
news-intelligence                           → NEVER BUILT / NOT RUNNING (in compose, no container)
```

The live `signal-engine` container **predates the 2026-07-22 routes.py split**: `calibration.py`
and `outcomes.py` do not exist inside it at all, and its `routes.py` is 5,543 lines vs. 1,222 in
git. Feature-by-feature presence in the running container:

| Feature | In git | In live container |
|---|---|---|
| SA-33 early-recovery entry timing | yes | **no** |
| `bearish_pillars_active` (SELL telemetry) | yes | **no** |
| `min_pillars_for_sell` gate | yes | **no** |
| hot-news signal gate (T259) | yes | **no** |
| canonical ATR / TA S-R consolidation | yes | **no** |
| `tune_sell_pillars` + backfill scheduling | yes | **no** |
| T257 attention list / options-flow EOD | yes | **no** |

Confirmed against live data, not just file contents — of **584 signals written in the last 48h,
ZERO** carry `early_recovery_trend`, `bearish_pillars_active`, or `hot_news_flag`, while the
older `independent_pillars_active` appears in 576. Signals are being generated (newest
`2026-08-05 13:26`), so the service is up — it is just executing old code.

`signal_outcomes.bearish_pillars_active` **does not exist as a column** in the database
(`ERROR: column "bearish_pillars_active" does not exist`), and `tune_history` contains **zero**
rows for `min_pillars_for_sell`.

**This corrects a premise in the brief.** The SELL gate's 4 attempts did not "fail validation" —
they never ran. `tune_sell_pillars` *is* registered in `_weekly_full_refresh` (scheduler.py:4344,
commit `3b850a1`), but the container running the scheduler predates that commit, and the endpoint
it would POST to doesn't exist in the target container either. Only 5 parameters have ever been
tuned in this database (`sell_threshold` 8, `buy_threshold` 8, `ml_weight_global_cap` 2,
`min_entry_score` 1, `ml_weight_cap` 1) — 1 promotion out of 20 attempts.

The prior audits' finding that "entry gates were tuned on corrupted labels" is real but
*narrower than it sounds locally*: almost no tuning has actually happened.

**Consequence for this review:** any recommendation to build a new indicator ranks below
"make the last three weeks of committed work actually execute." R1 is not optional; several
other recommendations are blocked behind it.

### 0.2 This is the LOCAL dev database — my numbers are an independent second sample

Local DB: 1,724 resolved outcomes, **0 closed paper trades**, 3 portfolios. The prior audits'
9,001 outcomes / 82 trades / 5 portfolios is the EC2 production dataset. So the paper-trading
corruption findings (scale-out mislabeling, `stop_hit` conflation) **cannot be reproduced or
fixed against local data** — that work must be done against production.

Usefully, this means the entry-timing evidence below is a *genuinely independent* 1,724-row
sample corroborating the production picture, not a restatement of it.

---

## 1. New empirical evidence: the user's goal 2 intuition is measurable, and it is not "buy dips"

Bucketing every resolved BUY outcome by entry price vs. the prior 20-day high (local DB, n=651):

| Extension bucket | n | Win rate | Avg return |
|---|---|---|---|
| At/above 20d high (≥99%) | 98 | 29.6% | **−4.43%** |
| Within 5% of high | 218 | **52.3%** | **+0.08%** |
| 5–10% below high | 113 | 35.4% | −4.27% |
| >10% below high | 222 | 31.1% | **−5.76%** |

**The relationship is non-monotonic and it holds independently in all four horizons:**

| Horizon | At high | Within 5% | >5% below |
|---|---|---|---|
| SHORT | 36.1% / −3.04% | **47.8% / −0.52%** | 29.3% / −6.47% |
| SWING | 23.1% / −5.05% | **69.8% / +1.39%** | 40.0% / −4.38% |
| LONG | 0.0% / −71.4% (n=2) | **66.7% / +1.04%** | 57.6% / +1.22% |
| GROWTH | 27.7% / −2.47% | **47.1% / −0.13%** | 25.9% / −6.28% |

"Within 5% of the 20-day high" is the best bucket in **4/4 horizons**, and is the only BUY
bucket anywhere near positive EV. This is exactly the user's phrasing — *not* the top
(chasing at the high loses), *not* a deep dip (falling knives lose worse), but a name that has
already turned up and is approaching its recent high.

Corroborated independently by a *different* already-stored field, `sr_context`:

| `sr_context` | n | Win rate | Avg return |
|---|---|---|---|
| at_resistance | 65 | 43.1% | **−0.69%** (least bad) |
| neutral | 365 | 41.4% | −3.97% |
| at_support | 58 | 31.0% | −2.48% |
| **breakout** | 150 | 33.3% | **−3.21%** |

The classic "buy the breakout" setup is among the *worst* buckets. Two independent stored fields
agree. This is the strongest, cheapest edge available in this codebase.

**And a warning about the VOLUME pillar** — bucketing BUY outcomes by stored `volume_z`:

| `volume_z` | n | Win rate | Avg return |
|---|---|---|---|
| < 0 (quiet) | 436 | **41.7%** | **−1.44%** |
| 0 to 1 | 114 | 28.9% | −6.16% |
| ≥ 1 (expansion) | 101 | 36.6% | **−8.39%** |

Volume expansion — which `_ta_score()`'s VOLUME pillar and RVOL rewards — is the **worst** BUY
bucket by EV. Quiet entries do best. This is consistent with the extension finding (volume
spikes cluster at breakouts/tops) and means the VOLUME pillar may be actively contributing to
negative EV. It is a candidate for sign-flip testing, not a new feature.

---

## 2. Recommendations, ranked by expected value per unit effort

### R1 — Redeploy every service; rebuild images, don't `docker cp`. **Effort S. Dependency: none. Blocks R2, R3, R6, R8.**

**What:** Rebuild and recreate all containers so committed code actually runs. Start
`news-intelligence` (never built). Fix `ml-prediction`'s unhealthy state (its logs show
`OperationalError('unable to open database file')` on SPY/^VIX downloads — a broken yfinance
cache path, not a model bug). Run `init_db()` so the `bearish_pillars_active` migration applies.

**Why:** Three weeks of committed signal work — SA-33, the SELL pillar telemetry, the hot-news
gate, the ATR/S-R consolidations, the SELL tuner scheduling — is not executing. Every other
recommendation here is unverifiable until this is true. This also explains why `tune_history`
is nearly empty: the self-improvement loop has not been running.

**How to validate:** After rebuild, re-run the exact query that exposed this — confirm fresh
signals carry `early_recovery_trend`/`bearish_pillars_active`; confirm
`\d signal_outcomes` shows the new column; confirm the next `_weekly_full_refresh` writes
`tune_history` rows for `min_pillars_for_sell`. Per CLAUDE.md's own standing invariant, tail
each container's logs for a clean startup — `docker ps` showing "Up" is not proof.

**Also:** this is the *fourth* recurrence of the "`docker cp` is session-scoped" bug class
documented in CLAUDE.md. Recommend adding a CI/cron drift check that diffs each container's
`/app/src` against the git checkout and alerts — the class will otherwise recur a fifth time.

---

### R2 — (Goal A) Ship the SWING/SELL edge as a real, tradeable output. **Effort M. Dependency: R1.**

This is the biggest untapped asset and the answer to question A.

**What, in three ordered steps:**

1. **Get the measurement running** (S): after R1, run `backfill_bearish_pillars` then
   `tune_sell_pillars`. It has *never executed*. Note its floor is `min_samples=50` per slice
   → needs 100 SELL outcomes per horizon; local SWING/SELL has 254, so it will actually run.
2. **Do not assume the gate is the win.** Local SELL numbers by horizon (signed so positive =
   profitable): SHORT −0.6%, **SWING +1.30%**, LONG +2.7%, GROWTH +2.7% — LONG/GROWTH SELL look
   *better* than SWING here, which conflicts with production's SWING-only finding. Reconcile the
   two datasets before choosing a horizon; do not tune on local and ship to production.
3. **The real gap is that SELL has no execution path at all.** BUY signals flow into paper
   trading, entry gates, position sizing, alerts. SELL is display + email only. The
   highest-value play is not the pillar gate — it is giving the one profitable direction
   somewhere to go: either a short-side paper portfolio, or (more useful and lower-risk)
   treating SWING/SELL as a **hard veto on BUY entries and an exit trigger** on open positions.

**Why:** Every BUY horizon is negative in both datasets. Rather than trying to fix BUY, route
capital decisions through the one direction with a measured edge. Using SELL as a veto/exit
requires no short-selling capability and no new broker integration.

**How to validate:** `gate_harness.replay_should_enter` with SELL-veto added as a hard reject;
require `_passes_promotion_margin` (≥0.5pp EV lift AND ≥0.5× dispersion) on the held-out
validation slice. For the exit trigger, backtest against `signal_outcomes` directly — no paper
trades needed locally.

**Caveat to state plainly:** SELL's EV is positive but small (+1.3%), and `_SELL_THRESHOLD_FALLBACK
= 0.35` has never been validated against a regime tier (production has ~zero non-bull SELL
samples). Treat as a modest, real edge — not a fix for the whole system.

---

### R3 — (Goal 2, D) Build Option 3 (extension penalty) — the evidence is already in the DB. **Effort M. Dependency: R1. NOT blocked by the corruption fix.**

The answer to question D: **Option 3 before Option 4**, and it can be validated *before*
promoting, using only already-stored fields.

**What:** Add a distance-from-recent-high term to the fused probability, shaped as a **band, not
a monotonic penalty** — reward the 0–5%-below-high zone, penalize both at/above the high *and*
>10% below. A naive "penalize extension" (the literal Option 3 design) would reward the
>10%-below bucket, which is the **worst** bucket (−5.76%). The designed-but-unbuilt option is
directionally incomplete; the data says the target is a sweet spot.

**Why it's cheap:** `last_price` and `sr_52w_high` are already on every signal row, and 20-day
high is derivable from the `prices` table. No new data collection, no new indicator, no
regeneration — this is pure re-filtering of existing history, so it can be swept exactly like
`tune_strategy` does.

**Dependency note — this is the one high-value item that does NOT need the corrupted-data fix
first**, because it validates against `signal_outcomes.pct_return`/`is_correct` on
signal-generated outcomes, not paper-trade P&L. The scale-out corruption affects paper-trade
writeback; it does not touch the signal-outcome evaluation path used here.

**How to validate (before any promotion):**
1. Offline: chronological 70/30 split over existing BUY outcomes; confirm the band survives
   out-of-sample and per-horizon (it already holds 4/4 in-sample).
2. **Register it as a sweepable parameter first — see R9.** `walk_forward_extended_gate`
   hard-rejects any parameter outside its `{min_kscore, min_ta_score, min_volume_z}` allowlist,
   so there is currently **nowhere to plug this in**. Do R9(b) alongside, not after.
3. Then sweep following `tune_strategy`'s exact conventions (`EV = mean(pct_return)`, beat the
   *current live* baseline on the never-searched validation slice, unconditional rejection of
   non-positive lift, one `tune_history` row per attempt) and require
   `_passes_promotion_margin`'s ≥0.5pp-and-≥0.5×dispersion bar.
4. Only then wire into `_ta_score`/`_apply_style_signal`, defaulting to **off** until a validated
   value exists — the discipline `min_pillars_for_sell` already uses.

**One honest limitation:** the harness re-filters stored outcomes, so it can only test
*tightening* — i.e. it can prove "excluding at-the-high entries helps," but it cannot prove
"waiting for a pullback into the band would have produced entries we didn't take." The first is
the achievable win; the second needs the deferred equity-curve replay (Phase 2b). Do not claim
the second from this evidence.

**Option 4 (split trend-quality from entry-quality into two gates that both must clear)** is the
right *eventual* architecture and directly encodes the user's goal, but it is Effort L and
should follow R3's result. If the extension band shows real validated lift as a score component,
that is the evidence needed to justify promoting it to a separate mandatory gate.

---

### R4 — Fix the SignalOutcome scale-out mislabeling + `stop_hit` conflation. **Effort M. Dependency: none — but must be done on PRODUCTION.**

**The bug is a two-variable slip, now located exactly.** `paper_trading_engine.py:2530-2532`
computes the correct blended values and :2540-2541 applies them to `PaperTrade`:

```python
2530:  total_pnl_dollar = round((trade.realized_pnl or 0.0) + pnl_dollar, 2)
2532:  total_pnl_pct    = (total_pnl_dollar / _cost_basis) if _cost_basis else pnl_pct
2540:  trade.pnl        = total_pnl_dollar          # blended — CORRECT
2541:  trade.pct_return = round(total_pnl_pct*100,4) # blended — CORRECT
```
…but the SignalOutcome writeback 45 lines later uses the **pre-blend** variables:
```python
2578:  setattr(_so, f"return_{_bucket}",     round(pnl_pct, 4))    # ← unblended
2579:  setattr(_so, f"is_correct_{_bucket}", pnl_dollar > 0)       # ← unblended
```
So T232-PT6's fix was applied to `PaperTrade` and **never propagated to the writeback**. Fields
corrupted: `return_5d/10d/20d`, `is_correct_5d/10d/20d`.

**Three things make this worse than "a stats bug":**

1. **`stop_hit` conflation actively HALTS TRADING.** There is no separate trailing-stop exit
   reason — the trail mutates `current_stop` in place (:2688-2694), so by exit time the
   provenance is gone; only a ±0.5% breakeven band is split out (:2396-2404). Consequences:
   `exit_reason == "stop_hit"` drives a **120-hour re-entry ban** (:3720-3731) and a
   **heat brake that blocks ALL new entries at 3 stops in 48h** (:3946-3963). A trade that
   trailed out at +30% therefore blacklists its own symbol for 5 days and pushes the portfolio
   toward a full entry halt. The exit email even renders `stop_hit` as red "Stop Loss
   Triggered — capital protected" (`email_service.py:1773`). This plausibly contributes
   directly to goal 5 (returns): the system stops itself from trading after winning.
2. **Two writers own the same six columns with incompatible semantics.**
   `evaluate_signal_outcomes` (`outcomes.py:2185-2187`, :2261-2271) writes them from
   fixed-horizon close-to-close returns *with* a cost hurdle; the paper writeback writes
   realized last-tranche P&L with **no hurdle** (`pnl_dollar > 0`). Whichever runs last wins.
   This needs an explicit ownership decision, not just a blend fix.
3. **The verification loop is measured with the broken ruler.**
   `tune_history.realized_ev_pct_after` — the retro-feedback that answers "did our change
   help?" — is computed from these same columns. And `promotion_gate.py:74` **unconditionally
   appends** `not_yet_available:signal_outcome_papertrade_agreement` on every run: the system
   already has a permanently-failing placeholder for exactly the cross-check that would have
   caught this.

**What to build:** (a) use `total_pnl_pct`/`total_pnl_dollar` in the writeback; (b) add a
`trailing_stop` exit reason flagged where the trail mutates the stop, and repoint the cooldown
and heat brake at *protective* stops only; (c) decide which writer owns the window columns and
make the other stop writing them; (d) implement the `promotion_gate` rule-#4 cross-check.

**Dependency direction, stated explicitly:** R4 does **not** block R2 or R3 (both validate on
`signal_outcomes` primary `is_correct`/`pct_return`, which this writeback does not touch). It
**does** block anything tuning on paper-trade labels or the window columns — notably
`calibrate_entry_weights`, `min_entry_score` promotion, `realized_ev_pct_after`, and the
multi-window accuracy report. Do not re-tune entry gates until R4 lands.

**How to validate:** Recompute the 82 production closed trades both ways and diff; confirm the
14 profitable `stop_hit`s reclassify and that the heat brake would not have fired. Then re-run
affected tuners and expect prior verdicts to change. Back up before mutating, per the precedent
set when 3,808 `signal_outcomes` rows were rebuilt for SE-F2.

---

### R5 — (Goal B) Consolidate the 5 entry/stop/target implementations to 2; do NOT merge the conviction scores. **Effort M. Dependency: none.**

The answer to question B.

**It is actually FIVE implementations, not four — and two pairs are already redundant:**

| System | Stop basis | Multiplier | Target basis | Real decision? |
|---|---|---|---|---|
| (a1) `_default_game_plan` (decision-engine) | `max(ATR, fixed %)` | style 2.0 / 3.0 GROWTH | `min(2×R, style cap)` | **Yes** |
| (a2) `_build_game_plan_for_style` (paper engine) | same, tick-rounded | same 2.0 / 3.0 | style cap only, **no R:R** | **Yes** |
| (c) T252 R/R lines | ATR, support fallback | **flat 2.0** | analyst target | No |
| (d) Position Sizer | ATR, support fallback | **flat 2.0** | analyst target | No |
| (b) FVG Trade Plan | gap far edge + 0.1×gapSize | — | entry ± 1.5×risk | No |

Two concrete redundancies to collapse:

- **(c) and (d) are the same numbers.** (c)'s stop expression
  `atrData?.stop_loss_2atr ?? chartNearestSupport` is character-equivalent to (d)'s
  `atrStop ?? stopLoss`; both call the same `computeRiskReward()` on the same page state. (c) is
  a visual re-projection of (d), not an independent system — the project's own record says so
  (`improvements.tsx:15885`). **Merge: keep the chart lines as a rendering of Position Sizer,
  delete the separate concept.** Near-zero risk.
- **(a1) and (a2) have already drifted once and still differ on the target leg.** The stop legs
  are now numerically identical (a GROWTH 2.5-vs-3.0 drift was previously found and fixed, with
  a regression test). But a1 computes `min(2×R, cap)` while a2 uses the raw cap with no R:R
  concept — so **for the same input, a1 and a2 give different take-profits today.** These two
  are supposed to agree; converge them on one implementation.
- **⚠ Correction to my own earlier read:** (c)/(d)'s ATR multiplier is hardcoded **flat 2.0** in
  the `/atr` endpoint's `stop_loss_2atr` field — it is *not* style-aware, so a GROWTH position
  shows a 2×ATR stop in the sizer while the engine actually trades a 3×ATR stop. That is a live
  user-facing inconsistency, not just duplication.
- **Keep FVG Trade Plan separate** — genuinely different math, the only real second opinion.
  Note its displayed R:R is mathematically pinned to `minRR` (always "1.5:1"), so it conveys no
  information; either vary it or drop the label.

Net: 5 → 2 decision surfaces (one converged Game Plan; FVG for structure) + Position Sizer
scoped to sizing, with the chart lines as its renderer.

**The two conviction scores are NOT symmetric and must not be merged into each other:**
- **Conviction Gate is load-bearing on THREE real decisions** — `_is_conviction_buy` writes
  `conv_gate:{symbol}:{style}` (scheduler.py:216, 1-day TTL), read by
  `paper_trading_engine.py:4534` (blocks a paper entry) and
  `decision-engine/hard_rejects.py:455` (hard-rejects `/decide`), plus it gates whether the BUY
  alert email fires at all.
- **Confluence Score is load-bearing on ZERO.** No backend code imports `confluence.ts`; the
  score is never POSTed. Its only action is a client-side browser toast.
- **⚠ Correction to my own earlier read:** the `"confluence"` key in `_REGIME_THRESHOLDS`
  (70/75/82) is **dead code** — grep confirms only `["ml"]` and `["confidence"]` are ever read.
  So the docstring at scheduler.py:3060 claiming the gate requires *"confluence >= 75"* is
  **false**. Confluence is not part of any gate. Fix the docstring or wire it up deliberately.

**Recommendation:** do not merge the scores. Instead (a) delete the vestigial `confluence` key
and correct the docstring, (b) present Confluence in the UI as an *explanation* of the
Conviction Gate's inputs rather than a competing number. **Risk of merging: you lose per-layer
attribution for a blocked entry, and you couple the alert path to the entry path.**

**Two real duplications inside the Conviction Gate worth fixing while here:**
- The **tiering logic exists twice** — `_is_conviction_buy` (scheduler.py:774-782) and
  `_store_conviction` (scheduler.py:206-213) each re-derive `conviction_tier` from the same
  soft-keyword list. Classic drift risk.
- The **conv_gate check exists twice and both fire on the same candidate**:
  `paper_trading_engine.py:4528` checks it, then calls `_call_decision_engine` at :4562, which
  checks the identical key with the identical predicate. Harmless today but redundant.
- **Layer 4b's docstring is stale** — it says RSI 45-65; the code is 45-72 (50-85 for GROWTH).
  Given goal 2, these RSI bands are exactly the parameters that decide whether an
  "already-rallying" name is admitted, so a stale doc here is genuinely misleading.

---

### R6 — (Goal 4) Publish a single "why this trade" trust surface. **Effort S–M. Dependency: R1, R4.**

**What:** One panel per signal showing, in order: the gate that would block it, the extension
band (R3), the calibrated historical win rate for its confidence bucket *with n*, and the live
vs. default parameter values in play. All of these already exist and are already stored — this
is composition, not new computation.

**Why goal 4 is currently unachievable by construction:** the app shows a confidence number
derived from `abs(fused_prob − 0.5) × 200`, which measures distance from a coin flip, not
accuracy. Meanwhile the measured reality is 34–41% win rates and negative EV. Showing a "72%
confidence" next to a strategy that wins 37% of the time actively destroys trust. Displaying
*measured* win rate with sample size — which the confidence-calibration cache already computes
— is the honest version, and it is the same discipline the T257 alerts already adopted.

**How to validate:** this is a presentation change; validate by confirming displayed win rates
reconcile to a direct SQL recomputation over `signal_outcomes` (the exact cross-check already
established for the gate harness).

---

### R7 — (Goal C) Wire ONE client-side indicator server-side: the Volume-Profile/extension pair. Leave the rest display-only. **Effort M. Dependency: R1, R3.**

The answer to question C.

**Stay display-only** — no testable hypothesis that isn't already covered:
- **Swing Pivots** — a chart-anchoring convenience (snapping clicks). No signal hypothesis.
- **Anchored VWAP** — inherently user-chosen anchor; not automatable without inventing the anchor
  rule, at which point it's a different feature.
- **FVG** — keep as the independent second opinion per R5. Note the Python detector
  (`detect_fair_value_gaps`) and the TS trade-plan already coexist with parity risk; wiring FVG
  into the score would make that parity risk load-bearing for money. Not worth it yet.

**Worth wiring: Volume Profile / POC.** There *is* a real hypothesis, and it is the same one R3
just validated from two independent angles: **price position relative to a structural reference
predicts BUY outcome.** A server-side `volume_area.py` already exists (POC/VAH/VAL, persisted
daily by a real job) — so "distance from POC" and "inside vs. outside the value area" are
already computable server-side without touching the TS code at all.

**Why this specifically:** it tests the *same* underlying edge as R3 with a better-grounded
reference level (volume-weighted fair value rather than a raw 20-day high). If R3's band works,
POC-relative position is the natural refinement. If R3 fails, don't build this.

**Also worth wiring cheaply:** `detect_accumulation_distribution` and `assess_breakout_quality`
already exist server-side and feed only `/levels` (confirmed: no signal generator, scheduler,
gate, or ML pipeline reads either). Given the finding that `sr_context='breakout'` is a *losing*
bucket, `assess_breakout_quality`'s `real`/`failed`/`unconfirmed` classification is a directly
relevant discriminator already computed — add it to `Signal.reasons` as **telemetry first** (like
`bearish_pillars_active` was), then sweep once rows accumulate. Effort S.

**⚑ A duplication to fix while wiring this:** `detect_accumulation_distribution` recomputes OBV
inline (`trendlines.py:383-384`, 10-vs-30-bar averages) — its own docstring admits this is "the
same construction signal-engine's own `obv_trend_bullish` already uses." So `obv_trend_bullish`
is computed **twice, in two services, from the same daily bars** — once for a real gate
(Conviction Gate Layer 4d) and once for a display card, with no shared implementation and
hardcoded windows on the display side. Consolidate onto the canonical one before adding any
consumer.

**Parity caveats to know before trusting any of this:** the TS/Python volume-profile pair use
the *same* algorithm and constants (24 buckets, 0.70 value area, same tie-break) but **different
input windows** — Python uses a fixed 60-day lookback, TS uses whatever bars the chart holds — so
they produce different POC/VAH/VAL in practice. Python is also a deliberate partial port
(no HVN/LVN). Swing pivots are likewise a hand-port with no shared parameterization. None of this
matters while they are display-only; all of it becomes load-bearing the moment one feeds a score.

**How to validate:** identical protocol to R3 — telemetry into `reasons`, accumulate, sweep with
`tune_strategy` conventions, promote only on validated held-out lift.

---

### R8 — Retire the second regime classifier. **Effort S. Dependency: R1.**

**What:** Point signal-engine's and the alert gate's regime reads at the canonical market-data
classifier (`/stocks/regime`), and delete the `fear_greed`-derived one.

**Why:** Two classifiers exist and are **incompatible** — the `fear_greed` one cannot emit
`choppy`/`risk_off` at all. So regime-tiered thresholds (`buy_threshold` varies bull/high_vol/
bear; `_REGIME_THRESHOLDS` for alerts) are keyed off a classifier that cannot represent two of
the canonical states. Any regime-conditional tuning is being fit against a degraded label.
CLAUDE.md already documents that two same-market portfolios disagreeing on regime is *always* a
bug — this is the upstream cause.

**How to validate:** log both classifiers side by side for one week; confirm the canonical one
emits the full state set and that no consumer regresses. Then re-run regime-conditional tuners.

---

### R9 — Add an entry-timing metric AND a sweepable timing parameter. **Effort S–M. Dependency: R4 for the paper-trade half. Promote to do-alongside-R3.**

**This is more foundational than I first ranked it.** Verified: **the platform cannot currently
prove any entry-timing change works**, for three compounding reasons.

1. **No entry-timing metric exists.** Grep returns **zero** hits for maximum adverse excursion /
   MAE / `lowest_price` / run-up / `distance_from_high` anywhere in `services/` or `shared/`.
   `max_favorable_excursion` exists but only inside one admin per-trade post-mortem endpoint,
   with no aggregation and no tuner reading it. `entry_slippage_pct` in that post-mortem is a
   **hardcoded `0.0` placeholder** and measures nothing.
2. **No sweepable timing parameter exists.** `gate_harness.py` can sweep exactly **four**
   parameters — `min_entry_score`, `min_kscore`, `min_ta_score`, `min_volume_z` — and all four
   are *selection-quality* gates ("is this a good stock"), not *timing* gates ("is this a good
   moment"). So even with a metric, there is nothing to optimize against.
3. **The harness can only ever TIGHTEN.** It re-filters stored outcomes, so it structurally
   cannot test a looser or differently-*timed* rule, and its EV numbers read the same window
   columns R4 corrupts.

**What to build:** (a) record MAE (`min(Price.low)` post-entry) and distance-from-20d/50d-high at
entry; (b) **register the R3 extension band as a fifth sweepable parameter** in
`walk_forward_extended_gate` — it currently hard-rejects any param outside its three-name
allowlist, so R3 has nowhere to plug in without this.

**Why this must land with R3, not after it:** R3's validation plan assumes the harness can sweep
its band. It cannot, yet. Without (b), R3's "validate before promoting" step is not executable
and the band would ship on in-sample evidence only — exactly the failure mode this codebase has
documented repeatedly.

**How to validate:** MAE reconciles against raw `prices` for known trades; the new sweep
parameter reproduces the R3 bucket table when run over the same window.

---

### R10 — (Goal E) Stop investing in: Short Squeeze, Option Expiry/gamma, CAPE. **Effort S (documentation/deprecation).**

The answer to question E — and I'll be direct, because this system loses money and attention is
the scarce resource.

- **CAPE** — by its own documentation it can stay "extreme" for years and is explicitly framed as
  macro context, not a trade trigger. It occupies a tab on two pages and a daily sync job. It has
  never fed a decision and cannot, on any horizon this platform trades. **Keep the tab, stop
  investing.** Cost of keeping: near zero. Cost of extending: not repayable.
- **Option Expiry / gamma** — the options-chain data is the most rate-limit-fragile external
  call in the app, and options flow is persisted for a bounded symbol set only. There is no
  measured link from any options field to outcomes. **Do not extend into gamma exposure
  modelling** (which would need dealer positioning data this app doesn't have) until something
  cheaper is proven first.
- **Short Squeeze** — `short_pct_float`/`short_ratio`/`short_interest_flag` are already on every
  signal row. So this needs **no new work at all**: run the same bucket analysis R3 used. If it
  discriminates, it's free; if not, stop. **Measure before building.** This is the correct
  treatment for any "we have the field, should we use it?" question in this codebase.
- **Sector Rotation / HMM** — keep, but note both are currently display-only, and the HMM
  regime-state is *not* exposed outside market-data's process (per the position-scaling gap
  analysis). Neither is earning its keep yet; neither is expensive. No new investment until R3
  resolves.

**Also consider removing:** the `gate_backtest` endpoint in signal-engine — it uses same-day-close
entry (reintroducing the SE-F2 look-ahead bias fixed everywhere else), has no caller, and its
docstring's "no look-ahead" claim is false. It is a landmine for whoever wires it up next.

---

### R11 — (Goal F) Two new features the evidence actually justifies. **Effort M each. Dependency: R3 for the first.**

The answer to question F. Deliberately short — most "new indicator" ideas are worth less than
R1–R4.

1. **"Rally-start" detector as a first-class signal state.** Not a new indicator — a *composition*
   of things now proven or already stored: within 5% of the 20-day high (R3), `sr_context` not
   `breakout`, quiet volume (`volume_z < 0` — the best bucket at −1.44%), plus SA-33's
   early-recovery flag once it's actually live. This is the literal implementation of "don't buy
   the top, buy when it starts to rally," and every input already exists. Validate as one
   composite sweepable parameter, exactly like R3.

2. **A short-side (or veto-side) execution path for SWING/SELL** — see R2 step 3. This is the
   only genuinely new *capability* worth adding, because it's the only direction with a measured
   edge and it currently has nowhere to go.

**Explicitly NOT recommended as new features:** dark-pool/block-trade data (no source), true
tick footprint (needs paid Polygon upgrade), gamma exposure (needs dealer positioning), and any
further chart overlay. The platform's problem is not a shortage of indicators — it has ~18 and
loses money. It is a shortage of *validated* ones.

---

### R12 — Schedule the drift check + finish the self-improvement loop. **Effort S. Dependency: R1.**

**What:** (a) the container-vs-git drift alert from R1; (b) confirm every tuner in
`calibration.py` is both scheduled *and* reachable post-rebuild; (c) alert when
`tune_history` receives zero rows in a week.

**Why:** 20 tuning attempts, 1 promotion, in a system with ~6 scheduled tuners is the signature
of a loop that isn't running — and nothing currently notices. Goal 3 (better prediction) is
unreachable if the mechanism meant to improve predictions is silently idle.

---

## 3. Dependency summary

```
R1 (redeploy) ──┬── R2 (SELL edge)
                ├── R9(b) sweepable timing param ── R3 (extension band) ──┬── R7 (VP wiring)
                │                                                          └── R11.1 (rally-start)
                ├── R6 (trust surface) ← also needs R4
                ├── R8 (regime consolidation)
                └── R12 (drift + loop watchdog)

R4 (corruption fix, PRODUCTION) ──┬── R6
                                   ├── any entry-gate / min_entry_score re-tune
                                   ├── realized_ev_pct_after retro-feedback
                                   └── position-scaling gate work

R5 (consolidation) — independent
R9(a) MAE metric — paper-trade half needs R4
R10 (stop investing) — independent
```

**If only three things get done: R1, R3+R9(b), R4.** R1 makes the last three weeks real;
R3 (with R9(b), without which it cannot be validated at all) is the cheapest evidence-backed path
to the user's own stated goal; R4 stops the system from learning that its winners were losses —
and stops the heat brake from halting trading *because* it won.

**Ordering note:** R4 is arguably the true #1 on pure impact — it corrupts the ruler used by every
other measurement, and its `stop_hit` half is actively suppressing live entries. It ranks second
only because it must be executed against the production database, which is a different operational
context from the local checkout this review was run in.

---

## 4. Explicit caveats

- **Do not promote anything on local numbers.** n=1,724 local vs 9,001 production, and the two
  disagree on which SELL horizon is best. Every sweep must be re-run on production data.
- **SA-33 is still unproven** — and now for a second reason: it is not merely n=11, it is *not
  running at all*. Post-R1, the n=11 clock effectively restarts. Do not claim SA-33 worked or
  failed.
- **The extension band is in-sample.** It holds 4/4 horizons and is corroborated by an
  independent field, which is unusually strong for this codebase — but it has not been through a
  held-out split yet. R3 step 1 exists precisely to try to kill it.
- **`volume_z ≥ 1` being the worst bucket** is the single most surprising result here and
  contradicts the VOLUME pillar's design intent. Verify on production before acting; if it
  replicates, the VOLUME pillar and RVOL both need re-examination rather than extension.
- **Three of my own initial reads were wrong and were corrected during this review**, which is
  itself a caution about how much of this codebase's documentation can be trusted at face value:
  (1) `tune_sell_pillars` *is* scheduled — the brief said it wasn't; the real problem is the
  container predates the commit. (2) The T252 R/R lines use a **flat 2.0** ATR multiplier, not
  the style-aware one, so they disagree with the engine for GROWTH. (3) `_REGIME_THRESHOLDS`'
  `confluence` key is **dead code**, so the Conviction Gate docstring claiming a confluence
  requirement is false. In each case the code disagreed with the documentation — consistent with
  the "verify tracker status in both directions" discipline already in CLAUDE.md.
- **Docstring/code drift found in three more places** worth a cleanup pass: Conviction Layer 4b
  (says RSI 45-65, code is 45-72 / 50-85 GROWTH), `gate_backtest`'s false "no look-ahead" claim,
  and `email_service.py:1773` describing a trailing stop as a capital-protecting loss.
- **`tune_style_profiles`' validation bar is 2 samples per side** at its default
  `min_samples=10` (`min_samples // 4`), vs. `* 2` for every sibling — flagged in-code as
  `TUNE-VALIDATION-BAR-INCONSISTENT`. Any promotion it has made should be treated as unvalidated.
