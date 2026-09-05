# PHASE E — LIVENESS, COMPLETENESS & FINAL SYNTHESIS

**Date:** 2026-09-05
**Prompt:** REVISED 2026-09-04 audit prompt, Phase E
**Companion deliverables:** `RECOMMENDATIONS.md`, `IMPLEMENTATION_PLAN.md`

---

# EXECUTIVE SUMMARY

**Is the system profitable?** No. Expectancy −0.387%/trade, profit factor 0.653, −1.07% vs
SPY's +2.69% over the same window.

**Is it statistically credible?** Partially. 16,732 tracked outcomes is a genuinely large
sample — but **97.3% of it is a single bull regime, with exactly one bear observation.** Any
conclusion about behaviour in a declining market is unsupported.

**Where is the edge?** Nowhere yet, in signal generation. The one measured positive is
structural, not predictive: **OPTIMAL-classified entries win 52.2%** vs 32.9% for CHASE entries,
so entry *selection* has real signal even though the system as a whole does not.

**Where does it lose money?** Three specific, now-quantified places:
1. **CHASE entries** — 38% of signals, −3.94% avg, 32.9% win.
2. **Concentration** — one symbol (2382.HK) opened 3 concurrent positions and produced 64% of
   net loss. Fixed today.
3. **Loss asymmetry at the tail** — 11 trades lost >$500 each, totalling −$15,875 against a
   −$8,029 net. The other 105 trades are collectively profitable.

---

# E.1 LIVENESS & COMPLETENESS — one real defect found

**Method: exercised, not read** — per the prompt's instruction after the
`/options-game-plan/batch` incident.

### Scheduler jobs — clean

81 job status records inspected. 36 fresh (<26h); 45 "stale" — **all correctly idle**, verified
individually rather than assumed:
- Weekly-cadence jobs at 126–161h (`promotion_gate`, `rl_agent_train`, `sector_rotation`,
  `watchlist_auto_rotation`) — traced to `_weekly_full_refresh()` and confirmed registered.
- HK jobs at 36–45h and US digests at 27–33h — a weekend. Correct.

**No dead jobs found.**

### Routes — exercised live, all functional

16 routes called against the running system. All returned 200 with real data, including the
previously-broken classes: `/stocks/{sym}/dark-pool-prints`, `/stocks/dark-pool-alerts-recent`,
`/stocks/options-flow-alerts-recent`, `/stocks/{sym}/gamma-exposure`, `/stocks/{sym}/options-chain`,
`/signals/outcomes/summary`.

Three non-200s triaged as correct behaviour, not defects: `401` on `/paper-portfolio/list` (a
service token is not a user), `404` on `/research/AAPL` ("no cached report — POST to generate").

### DEFECT: `shared/db/models.py` drift on decision-engine

Checksummed `shared/db/models.py` inside all 12 backend containers against local:

| | md5 |
|---|---|
| 11 containers + local | `bf38e87b…` |
| **decision-engine** | **`d8d18d37…`** |

decision-engine had **zero occurrences of `gex_corroborated`** — it missed the model deploy
earlier today. It would have crash-looped on its next restart if it touched those columns.

**Fixed and verified:** redeployed, restarted, now matches all 11 others.

This is the second instance today of the same class (news-intelligence was running six-week-old
code). **The generalisable gap: nothing detects deploy drift.** A `docker cp` is reverted by any
container recreation, and the only symptom is an error log nobody watches.

### Table liveness — all current except one expected zero

| Table | Rows | Newest |
|---|---|---|
| signal_outcomes | 16,732 | 2026-08-27 |
| options_flow_alert_outcomes | 1,539 | 2026-09-05 |
| squeeze_alert_outcomes | 320 | 2026-09-05 |
| dark_pool_alert_outcomes | 184 | 2026-09-05 |
| paper_trades | 124 | 2026-09-04 |
| prebreakout_alert_outcomes | 26 | 2026-09-03 |
| **dark_pool_prints** | **0** | — |

`dark_pool_prints` at zero is **expected**: persistence shipped today and markets have been
closed since. Verify Monday.

### DQ-check coverage gaps (not built)

No liveness check exists for: the Alpaca news WebSocket (six weeks dead, undetected), deploy
drift, or per-portfolio trading activity.

---

# E.2 STRATEGY RANKING & CULLING

**Ranked on expectancy, profit factor, and sample size only** — Sharpe/Sortino/drawdown are not
credible at 2.5 months in one regime (§F.1), and the prompt requires saying so rather than
computing them anyway.

| Strategy / portfolio | n | Avg return | Net P&L | Verdict |
|---|---|---|---|---|
| **HK GROWTH** | 15 | −0.04% | **+$2,315** | **KEEP** — only net-positive book |
| **US SWING** | 40 | **+0.59%** | −$950 | **KEEP** — only positive expectancy |
| GROWTH Paper | 42 | −0.34% | −$1,270 | **IMPROVE** — largest sample, mild negative |
| ETrade Sandbox SWING | 15 | −2.08% | −$1,512 | **PAPER ONLY** — sandbox, not real |
| **HK SWING** | 4 | **−5.66%** | **−$6,611** | **DISABLE** pending review |

**HK SWING should stop generating live recommendations.** −5.66% average over 4 trades, and it
holds 3 of the 5 worst dollar losses. n=4 is too small to condemn the *strategy*, which is why
this is DISABLE-pending-review rather than RETIRE — but it should not be allocated capital while
it looks like this.

**Alert strategies:**

| Alert | Resolved | Win rate | Verdict |
|---|---|---|---|
| gamma_unwind_puts | 119 | 31.8% | **PAPER ONLY** |
| gamma_unwind_calls | 64 | 24.5% | **PAPER ONLY** |
| short_squeeze | 11 | 9.1% | **PAPER ONLY** — n too small |
| squeeze_ignition | 0 | — | **UNMEASURABLE** — never fired |
| options_flow | 1,444 (1d) | ~40% | **UNMEASURABLE** — 2 days resolved |
| dark_pool | 49 | 40.8% | **UNMEASURABLE** — was firing on 85% of universe |

**No alert currently qualifies to drive live recommendations.**

---

# E.3 SIGNAL QUALITY SCORE

The prompt requires weights be **configurable and backtestable**, and warns against hard-coding
arbitrary ones.

**Assessment: the existing architecture already satisfies this** — `_TA_WEIGHTS` is
Optuna-tuned and persisted via `trade_params.json`, `_STYLE_PARAMS` is overlaid from tuned
values, and calibrated win rates are computed per band from real outcomes.

**Recommendation: do NOT build a new unified score.** The measured problem is not that the
weights are wrong — it is that **every input is a momentum measure** (`WHY_SIGNALS_FIRE_LATE.md`).
Re-weighting correlated momentum inputs cannot produce a non-momentum signal. A new score would
add complexity without addressing the cause.

---

# E.4 PROFIT MAXIMISATION

Each proposal with current → proposed, evidence, and confidence. **No proposal increases risk.**

### P1. Anti-chasing filter — SHIPPED TODAY
- **Current → Proposed:** all signals (−1.89%) → block `roc_10 ≥ 10` (−0.65%)
- **Out-of-sample:** −0.96% → −0.79% (+0.17 pts). In-sample suggested +1.6 — most was curve-fit.
- **Risk change:** none (fewer trades). **Drawdown:** lower. **Confidence: HIGH** (validated OOS).

### P2. Concentration cap — SHIPPED TODAY
- **Current → Proposed:** 3 concurrent positions in one symbol → 1
- **Evidence:** 2382.HK cost $5,143 = 64% of net loss
- **Risk change:** strictly lower. **Confidence: HIGH** (mechanical).

### P3. Scale-in position cap — SHIPPED TODAY
- **Current → Proposed:** scale-in could exceed 10% cap (observed 13.0%) → truncated to headroom
- **Risk change:** lower. **Confidence: HIGH** (mechanical).

### P4. Tighten `max_portfolio_drawdown_pct` 20% → 10–12% — RECOMMENDED
- **Why:** 20% permits substantial destruction before halting, with expectancy negative
- **Risk change:** strictly lower. **Confidence: MEDIUM** (judgement, not measurement).

### P5. Evaluate the prebreakout/compression pillar — RECOMMENDED, WAIT FOR DATA
- **Why:** it is the only genuinely **non-momentum, leading** signal in the system, and the
  architectural gap diagnosed in Phase B. Already built; only 10 resolved outcomes.
- **Expected benefit:** UNMEASURABLE until n grows. **Confidence: UNKNOWN — that is the point.**

### Rejected after modelling
- **Breakeven-ratchet after +5%:** modelled at **+$347 of $8,029** (4%). The big losses never ran
  up first. **Rejected.**
- **Loosening any alert threshold:** every looser variant measured *worse*. **Rejected.**
- **Gating on `insider_score`:** +0.164 correlation was 6 stocks splitting 3 up / 3 down.
  **Rejected.**

---

# E.5 EVIDENCE-BACKED RATINGS

Per the prompt, numeric scores are replaced with ratings, each tied to a measurement.

| Dimension | Rating | Evidence |
|---|---|---|
| **Data quality** | **STRONG** | 1.47M bars; universe-wide split scan found exactly 1 bad symbol (fixed); 44 DQ checks passing |
| **Data pipeline liveness** | **ADEQUATE** | No dead jobs; all routes 200. But 2 deploy-drift incidents found today, and nothing detects drift |
| **Signal quality** | **WEAK** | −0.76% alpha; 38% of signals are CHASE (−3.94%) |
| **Confidence calibration** | **ADEQUATE** | Correctly signed post-`aee6d17`: high-conf 51.6% win vs low-conf 36.0% |
| **Entry timing** | **WEAK** | Placebo test: 14 days earlier was ~6 pts better |
| **Exit logic** | **STRONG** | trailing_stop and target_reached both **100% profitable** (n=18) |
| **Execution realism** | **UNMEASURABLE** | No bid/ask/MAE captured (C.0a) |
| **Risk limits** | **STRONG** | 1% risk/trade, avg 0.41–0.92% actual; limits justified |
| **Risk enforcement** | **ADEQUATE** | Gate chain real and enforcing; 2 bypasses found and fixed today |
| **Portfolio construction** | **UNMEASURABLE** | n=8 open positions; concentration/correlation not computable |
| **Regime robustness** | **UNMEASURABLE** | 97.3% bull, **1 bear observation** |
| **Alert predictive value** | **UNMEASURABLE** | No alert has both adequate n and resolved outcomes |
| **Self-measurement** | **STRONG** | 16,732 tracked outcomes; the reason this audit was possible |
| **Test coverage** | **STRONG** | 67k test LOC / 386 files; 0.8:1 ratio; all suites green |

---

# TOP 10 PROBLEMS (by financial impact)

1. **CHASE entries** — 38% of signals, −3.94%. *Partially fixed (P1).*
2. **Single-symbol concentration** — 64% of net loss. *Fixed.*
3. **Regime blindness** — 1 bear observation. **Hard blocker to live automation.**
4. **Negative expectancy** — −0.387%, profit factor 0.653.
5. **Deploy drift undetected** — 2 incidents today; one service ran 6-week-old code.
6. **HK SWING** — −5.66%/trade, 3 of the 5 worst losses.
7. **No execution-realism data** — slippage/spread unmeasurable.
8. **Alerts unvalidated** — none has adequate resolved outcomes.
9. **Idle capital** — 3 of 5 portfolios ~100% cash.
10. **GROWTH −12% stop** — widest stop, worst outlier (SNOW −19%).

# TOP 10 OPPORTUNITIES (by expected improvement)

1. **Evaluate the prebreakout pillar** — the only non-momentum signal; already built.
2. **Let the anti-chasing filter run** — +0.17 pts validated, live now.
3. **Add deploy-drift detection** — would have caught 2 incidents today.
4. **Add Alpaca-stream liveness check** — 6 weeks dead, undetected.
5. **Re-baseline reporting** from 2026-08-04 — `by_era` shipped; extend to all dashboards.
6. **Populate insider/institutional data** — 6 of 193 symbols; makes an untestable question testable.
7. **Tighten drawdown limit** to 10–12%.
8. **Disable HK SWING** pending review.
9. **Build C.0a instrumentation** — unblocks all execution-realism analysis.
10. **Wait for GEX/dark-pool measurement** — both instrumented today.

---

# THE HARD BLOCKER

**This system must not be given live capital autonomously.** Three independent grounds:

1. **Negative expectancy** (−0.387%) — it loses money per trade on 116 closed trades.
2. **Profit factor 0.653** — loses $1 for every $0.65 made.
3. **97.3% single-regime dependence** with **one** bear observation — behaviour in a declining
   market is entirely unknown.

Any one of these blocks live automation. All three together are decisive. This is stated as a
blocker, not a caveat, per the prompt's explicit instruction.

**What it IS good for today:** research, monitoring, risk warnings, and generating candidates for
a human to filter. Those are real capabilities backed by real infrastructure.
