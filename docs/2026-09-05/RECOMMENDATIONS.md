# RECOMMENDATIONS — PRIORITISED

**Date:** 2026-09-05 · **Companion to:** `TRADING_SYSTEM_AUDIT.md`, `IMPLEMENTATION_PLAN.md`

Each item: WHY / EXPECTED BENEFIT / RISK / COMPLEXITY / EXPECTED IMPACT.
Ratings are evidence-backed; **UNMEASURABLE** is used wherever the data does not support a claim.

---

## CRITICAL BUGS — all fixed and deployed today

### C1. Cross-portfolio symbol cap went stale mid-scan (`AUD-GLOBALSYMCAP-STALE`)
- **Why:** `_global_sym_open` loaded once from the DB, never incremented as positions opened, so
  2382.HK opened 3 concurrent positions in one day — two in the same portfolio.
- **Benefit:** removes the single largest loss driver. **Impact: HIGH** (64% of net P&L).
- **Risk:** none — strictly reduces exposure. **Complexity:** trivial (one line + tests).

### C2. news-intelligence ran six-week-old code
- **Why:** container recreation on 2026-09-04 reverted a `docker cp` fix from 2026-07-27.
  16,302 auth errors/24h while reporting *healthy*; the real-time news stream was dead.
- **Benefit:** restores the entire service's primary feed. **Impact: HIGH.**
- **Risk:** none. **Complexity:** trivial (redeploy).

### C3. `decision-engine` had stale `shared/db/models.py`
- **Why:** missed the `gex_corroborated` deploy; would crash-loop on next restart touching those
  columns. **Impact: HIGH** (latent outage). **Risk:** none. **Complexity:** trivial.

### C4. Research engine deadlocked on any bad ticker (`AUD-RESEARCH-INFLIGHT-LEAK`)
- **Why:** in-flight Event released only on the success path, with a `raise HTTPException(404)`
  in between. One bad ticker poisoned that symbol permanently.
- **Benefit:** removes a permanent per-symbol outage. **Impact: MEDIUM-HIGH.**
- **Risk:** none. **Complexity:** low (`try/finally`).

### C5. Hot-news age decay never worked (`AUD-HOTNEWS-TS-STRIPPED`)
- **Why:** `HotNewsResponse` omitted `ts`, so FastAPI stripped it; the consumer gated on it and
  always saw `None`. Every hot signal took the maximum 0.70 compression forever.
- **Benefit:** restores intended decay. **Impact: MEDIUM.** **Complexity:** trivial.

---

## HIGH-IMPACT IMPROVEMENTS

### H1. Evaluate the prebreakout/compression pillar — **the single highest-value item**
- **Why:** Phase B concluded the architecture needs a **non-momentum** pillar. It already exists
  (`check_prebreakout_alerts()` / `detect_price_compression()`) but has only 10 resolved outcomes.
- **Benefit: UNMEASURABLE until n grows — and that is precisely why it must be measured.** If
  compression leads, it is the structural fix for late entry.
- **Risk:** none (analysis only). **Complexity:** low (wait, then compare).
- **Impact: potentially HIGH, currently UNKNOWN.**

### H2. Add deploy-drift detection
- **Why:** two drift incidents found today; one service ran 6-week-old code undetected.
- **Benefit:** converts silent multi-week failures into same-day detection.
- **Risk:** none. **Complexity:** low. **Impact: HIGH** (prevents recurrence of C2/C3).

### H3. Add background-worker liveness checks
- **Why:** health checks verify HTTP, not that the Alpaca WebSocket ever authenticated.
- **Benefit:** closes the exact gap that hid C2 for six weeks.
- **Risk:** none. **Complexity:** low (existing `_record_job_status` pattern). **Impact: HIGH.**

### H4. Disable HK SWING pending review
- **Why:** −5.66%/trade, holds 3 of the 5 worst dollar losses.
- **Benefit:** stops the worst book. **Risk:** n=4 may be noise — hence *review*, not retire.
- **Complexity:** trivial (config). **Impact: MEDIUM-HIGH.**

---

## MEDIUM-IMPACT

### M1. Tighten `max_portfolio_drawdown_pct` 20% → 10–12%
- **Why:** 20% permits substantial destruction while expectancy is negative.
- **Risk:** may halt trading sooner — acceptable, arguably desirable. **Complexity:** trivial.

### M2. Extend `by_era` reporting to all dashboards
- **Why:** 62% of outcomes predate the inversion fix; pooled stats blend two systems.
- **Impact: MEDIUM** (perception accuracy, not P&L). **Complexity:** low.

### M3. Populate insider/institutional data across the universe
- **Why:** only 6 of 193 symbols have it, making the strongest correlate untestable.
- **Benefit:** converts an unanswerable question into an answerable one. **Complexity:** medium.

### M4. Build C.0a execution instrumentation (bid/ask, MFE, MAE)
- **Why:** all execution-realism analysis is currently **UNMEASURABLE**.
- **Complexity:** medium-high. **Impact: MEDIUM** (unblocks future audits).

---

## FEATURES TO REMOVE OR RECONSIDER

- **`squeeze_ignition`** — has fired **zero** times since T260, consuming a scheduler slot every
  60s. Now instrumented; **decide within a month: loosen deliberately, or retire.**
- **A new unified signal-quality score (E.3)** — explicitly **do not build.** Re-weighting
  correlated momentum inputs cannot create a non-momentum signal.

## STRATEGIES TO DISABLE / IMPROVE

- **DISABLE:** HK SWING (pending review).
- **PAPER ONLY:** every alert strategy — none has adequate resolved outcomes.
- **KEEP:** US SWING (only positive expectancy, +0.59%), HK GROWTH (only net-positive P&L).
- **IMPROVE:** GROWTH Paper — largest sample, mild negative, widest stop.

## DATA-SOURCE CHANGES

- **Polygon / Alpha Vantage:** keys now persist (fixed today), but yfinance remains the de facto
  sole source. Verify the fallbacks actually engage before relying on "diversification."
- **Borrow rates:** not ingested; the only genuine leading indicator of short-side stress.

## RISK-ENGINE IMPROVEMENTS

Limits are **well chosen and enforced** (audited as values per the prompt). Two bypasses found
and fixed today (C1, scale-in cap). **Recommend: no further changes except M1.**

## BACKTESTING IMPROVEMENTS

- **Walk-forward validation** is not implemented (§F.3) and is a **required gate** for live
  automation. Until it exists, live autonomy stays blocked regardless of expectancy.
- **Regime coverage is the binding constraint** — 97.3% bull with 1 bear observation. No amount
  of backtesting on this window fixes that; it needs either time or genuine historical replay.
