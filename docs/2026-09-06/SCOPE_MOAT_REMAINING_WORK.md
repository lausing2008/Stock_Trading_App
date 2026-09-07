# SCOPE: What's Left for the Quantitative Moat Score

**Date:** 2026-09-06
**Status of the prerequisite:** ✅ **DONE and validated** (MOAT-1, commit `9db3e94`, deployed).

---

## 0. Where we actually are

MOAT-1 shipped the `financial_statements` table + backfill. Real production state after the
full-universe run:

| Metric | Result |
|---|---|
| Symbols backfilled | **156** (annual), 155 (quarterly) |
| Rows written | **1,424** (653 annual + 771 quarterly) |
| Annual periods per symbol | 5 periods: 31 symbols · 4 periods: 123 · 3 periods: 2 |
| **ROIC-computable annual rows** | **565 / 653 (86.5%)** |
| Failures | **17 — all ETFs** (QQQ, QQQM, TQQQ, GLD, VOO, SOXX, XLE, SCHD…) |

**The 17 "failures" are correct behavior, not a bug.** ETFs don't file income statements or
balance sheets, so there is nothing to fetch. Real operating-company coverage is effectively
**156/156**. Any moat score must simply skip non-filers rather than treat them as missing data.

ROIC is confirmed computable end-to-end straight from DB rows, with the persistence signal
already visible:

| Symbol | ROIC by year (newest→oldest) | Signal |
|---|---|---|
| AAPL | 82.3 / 70.0 / 68.0 / 62.8% | extreme **and rising** → wide moat |
| MSFT | 28.5 / 27.8 / 28.5 / 32.0% | remarkably **stable** → wide moat |
| 0700.HK | 17.0 / 17.2 / 12.6 / 21.4% | good but **volatile** → narrow |
| 9988.HK | 8.8 / 11.5 / 9.9 / 8.8% | low and flat → **none** |

MSFT's stability vs. Tencent's swings *at broadly similar levels* is exactly the durability
discrimination that was impossible before this data existed.

---

## 1. What's left — four pieces, in dependency order

### MOAT-2 — the score itself (S-M, the core deliverable)

A `compute_moat_score()` producing a 0-100 score plus a `None/Narrow/Wide` bucket, from four
components. All four are computable from `financial_statements` **today**:

| Component | Formula sketch | Why it belongs |
|---|---|---|
| **ROIC level** | mean ROIC across available years | Is the business earning above its cost of capital at all? |
| **ROIC persistence** | inverse of stdev, and/or count of years above a WACC-ish floor | **The actual moat measure** — durability, not level |
| **Gross-margin durability** | mean and stdev of `gross_profit / total_revenue` | Best available pricing-power proxy |
| **Reinvestment runway** | `capital_expenditure / operating_cashflow` trend | Can it redeploy capital at those returns? |

**Conventions it must follow** (`services/ranking-engine/src/scoring/kscore.py`):
- `_MOAT_WEIGHTS` module dict of hardcoded defaults, summing to 1.0 (`kscore.py:31-38` pattern)
- Redis live-override key + `_load_active_weights()`-style reader that **fails open** to defaults
  and requires a **complete** key set (`kscore.py:115-139`)
- **Always return a fresh dict**, never the module-level object (`kscore.py:125-130`)
- **Skip-and-renormalize** for missing components, with explicit `is None` checks — never
  truthiness, so a legitimate `0.0` margin still scores (`kscore.py:376-391`). Do **not** copy
  research-engine's `scoring.py`, which still has truthiness guards (`:476-483`) and fabricates a
  `35` for empty fundamentals (`:433-435`).
- Minimum-periods floor: require ≥3 annual periods, else return `None` rather than scoring
  durability off 1-2 points.

**Where it lives: `ranking-engine`.** It already has DB access to `Fundamental/Price/Ranking/
TuneHistory` (`ranking-engine/src/api/routes.py:25`), owns the Redis-override + sweep +
TuneHistory machinery, and is already HTTP-polled by signal-engine and research-engine.
research-engine imports only `SessionLocal, ResearchReportCache` (`research-engine/src/api/
routes.py:27`), so a score defined there is reachable by nobody except through the report blob.

### MOAT-3 — surface it (S)

Two display targets, both low-risk:
1. Replace/augment the LLM `moat_rating` in the research checklist
   (`research-engine/src/scoring.py:701,712`) and the `MoatBadge`
   (`frontend/src/pages/research/[symbol].tsx:625`). **Show both side by side** rather than
   replacing — they measure genuinely different things (mechanical durability vs. qualitative
   competitive narrative).
2. Optionally a column on the rankings/screener view.

**Trading-safe by construction:** `grep -rn moat` returns zero hits across ranking-engine,
signal-engine, decision-engine, market-data and shared. Nothing consumes it for entry decisions,
so introducing this cannot regress live behavior — and conversely it reaches trading **only** if
deliberately wired in later.

### MOAT-4 — keep it fresh (XS)

Add `backfill_financial_statements()` to the weekly Sunday chain. It's already idempotent
(`ON CONFLICT DO UPDATE`), so the same call picks up newly-filed periods with no separate
incremental path. Must carry an explicit `misfire_grace_time` (its runtime is ~275s measured, far
past APScheduler's 1s scheduler-level default — the exact
`AUD-MISFIREGRACE-GAMMAUNWIND` class fixed earlier today).

### MOAT-5 — validation, and an honest limitation (M, defer)

**The existing sweep harness cannot validate this score, and that's a real constraint to state
rather than paper over.** `_KSCORE_SWEEP_MIN_ROWS = 200` **per slice**
(`ranking-engine/src/api/routes.py:881`, enforced at `:1049` as `* 2`) against 20-bar forward
returns. A moat score is near-constant per symbol across any short window, so:
- it produces almost no cross-sectional variation *change* to attribute returns to
- `_kscore_cross_sectional_ev()` would reject it for lack of measurable EV lift

**What that means practically:** MOAT-2 is a *descriptive/analytical* score on arrival, not a
validated alpha factor. Do **not** wire it into entry gates or sizing on the strength of face
validity. Genuine validation needs either (a) years of accumulated score history, or (b) a
different methodology than the forward-return EV sweep — e.g. testing whether high-moat names
exhibit smaller drawdowns or faster drawdown recovery, which is a *risk* claim the existing
harness isn't built to measure.

---

## 2. Known limitations to carry forward

1. **4-5 years is thin for true persistence.** Morningstar's own quant model uses 10+ years.
   4 years spans roughly one cycle phase — it will distinguish MSFT-stable from Tencent-volatile
   (demonstrated above), but cannot see how either behaves through a real recession. FMP offers
   10-30y and was already deliberately deferred
   (`docs/audits/2026-08-24-data-subscriptions-and-improvement-batches.md`).
2. **86.5% ROIC-computable, not 100%.** Of the 88 non-computable annual rows: 67 missing `ebit`,
   34 missing `total_equity`, 31 missing `pretax_income`, 14 missing `total_debt` (overlapping).
   Financials/REITs are the likely cluster — for those, ROIC is arguably the wrong metric anyway
   (banks don't have "invested capital" in the same sense), so **sector-aware component skipping**
   is better than forcing a number.
3. **No segment or volume data**, so genuine pricing power (price vs. units) stays unmeasurable.
   Gross margin is a proxy, not the real thing.
4. **ETFs have no moat score, by definition.** Skip non-filers explicitly.

## 3. Two small unrelated fixes surfaced during scoping

- **`research-engine/src/scoring.py:712`** — an `"Unknown"` moat renders a red **FAIL** while
  sibling checklist items default to `warning`. Real inconsistency, cosmetic-only, trading-safe.
- The LLM `moat_rating` prompt (`research-engine/src/api/routes.py:411,437`) receives **no
  moat-specific input data** — it says "fill in all fields based on your knowledge of {symbol}",
  i.e. pure parametric recall with no validation of the returned enum. Worth noting when deciding
  how much weight the qualitative rating should keep once MOAT-2 exists.

## 4. Recommended order

**MOAT-2 → MOAT-4 → MOAT-3**, deferring MOAT-5.

Rationale: build the score, wire the refresh so the data never goes stale, then surface it — and
hold validation until there's either more history or a methodology that suits a slow-moving
fundamental factor. Total for 2+3+4 is roughly S-M; it's a contained piece of work now that the
data exists.
