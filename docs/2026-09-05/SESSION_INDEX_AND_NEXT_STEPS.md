# SESSION INDEX — 2026-09-04/05 AUDIT & FIX CYCLE

**Read this first.** Eleven documents were produced across two days. Read individually they give
a distorted, defect-heavy impression, because each was scoped to find defects. This index gives
the whole picture and the order to read them in.

**Start with [`WHERE_THE_APP_ACTUALLY_STANDS.md`](./WHERE_THE_APP_ACTUALLY_STANDS.md)** — the
balanced assessment. Everything else is detail underneath it.

---

## The one-paragraph summary

The platform is substantial and operationally sound (1,551 commits, 83k production LOC, 67k test
LOC, 12 healthy services, 44 passing data-quality checks). **One component has a real,
measured weakness: signal entry timing.** Everything else found was either a measurement gap
(now instrumented) or a threshold that was measured and deliberately left alone. Sixteen defects
were fixed and deployed. Two encouraging leads were investigated and honestly retracted. The
single largest realised loss driver — one symbol at 64% of net P&L — was root-caused and fixed.

---

## What was fixed and deployed

| Tag | What it was | Impact |
|---|---|---|
| `AUD-GLOBALSYMCAP-STALE` | Cross-portfolio symbol cap never updated mid-scan, so one idea opened 3 concurrent positions | **64% of net paper-trading loss** |
| `AUD-UWRATELIMIT-FLOWALERTS` | Uncached 1-min UW call | 22,031 rate-limit events/48h → cached 45s |
| `AUD-PROVIDERKEY-INMEMORY` | Polygon/AlphaVantage keys lived in memory, wiped on restart | Keys now persist in Redis |
| `AUD-LIVEBAR` | ML inference + T196 gate used a live, unsettled bar | Train/inference symmetry restored |
| `AUD-CHASE-ROC10` | No guard against buying already-extended stocks | **+0.17 pts, out-of-sample validated** |
| `AUD-HKWEEKEND` | HK ran full cycles every weekend | 3 scheduling gates fixed |
| `AUD-CONVRATIO-WEEKEND` | DQ check failed every weekend by construction | False alarm eliminated |
| `AUD-DARKPOOL` | Flat $1M threshold fired on ~85% of universe daily | Relative threshold + prints now persisted |
| `AUD-BASELINE-ERASPLIT` | Dashboards pooled two different systems | `by_era` breakdown live |
| `AUD-GEXCORROBORATE-UNMEASURED` | GEX corroboration displayed but never stored | Now measurable |
| `AUD-IGNITION-NEVERFIRES` | Alert fired 0 times ever, invisibly | 4 rejection-reason gauges |
| `AUD-DQCHECK-WRONGCADENCE` | 1-min threshold on a 5×/day job | False alarm eliminated |
| — | DFNS 125× unadjusted split | 790 bars re-ingested |
| — | 2 broken signal-engine test files | Suite fully green (448) |

**Test suites after all changes:** market-data 2,819 · signal-engine 448 · ml-prediction 164.

---

## The documents, in reading order

### 1. Start here
- **[`WHERE_THE_APP_ACTUALLY_STANDS.md`](./WHERE_THE_APP_ACTUALLY_STANDS.md)** — balanced
  assessment. Scale, what works, what doesn't, and the finding that paper trading is a *risk
  management* problem (average win +$408 vs average loss −$293 — a genuinely good ratio).

### 2. The confidence-inversion arc (resolved)
- **[`../2026-09-04/DATA_QUALITY_AUDIT.md`](../2026-09-04/DATA_QUALITY_AUDIT.md)** — Phase A;
  found the live-bar defect.
- **[`../2026-09-04/PHASE_B_LIVEBAR_HYPOTHESIS_TEST.md`](../2026-09-04/PHASE_B_LIVEBAR_HYPOTHESIS_TEST.md)**
  — tested it against the inversion and **rejected** it.
- **[`../2026-09-04/PHASE_B2_INVERSION_ROOT_CAUSE_FOUND.md`](../2026-09-04/PHASE_B2_INVERSION_ROOT_CAUSE_FOUND.md)**
  — **the answer**: the inversion was `AUD232-BUY-FROM-TOP`, already fixed 2026-08-03.
  High-confidence BUYs went from worst bucket (−7.19%) to best (−1.02%).

### 3. The edge investigation
- **[`../2026-09-04/EDGE_SUBSET_ANALYSIS.md`](../2026-09-04/EDGE_SUBSET_ANALYSIS.md)** — no
  profitable subset; includes the retraction of the "defensive skill" finding (it was 0.355 beta).
- **[`ENTRY_TIMING_PLACEBO_TEST.md`](./ENTRY_TIMING_PLACEBO_TEST.md)** — **the key diagnostic.**
  Same stocks, same holding period, entry shifted earlier. Signal is the only entry point with
  negative excess return; 14 days earlier was ~6 points better.
- **[`WHY_SIGNALS_FIRE_LATE.md`](./WHY_SIGNALS_FIRE_LATE.md)** — root cause: every conviction
  pillar is a momentum measure, so requiring confirmation guarantees late entry.
- **[`ENTRY_TIMING_WHAT_CAN_BE_DONE.md`](./ENTRY_TIMING_WHAT_CAN_BE_DONE.md)** — the insider
  lead and its retraction; **and the discovery that the missing pillar is already built.**

### 4. Alert-specific audits
- **[`OPTIONS_DARKPOOL_ALERT_AUDIT.md`](./OPTIONS_DARKPOOL_ALERT_AUDIT.md)** — what they tell
  you and how to use them.
- **[`GAMMA_SQUEEZE_CAPABILITY_REVIEW.md`](./GAMMA_SQUEEZE_CAPABILITY_REVIEW.md)** — short vs
  gamma squeeze; why the alert can't yet see dealer positioning.
- **[`SHORT_SQUEEZE_ALERT_TUNING_REVIEW.md`](./SHORT_SQUEEZE_ALERT_TUNING_REVIEW.md)** — tunable
  but **should not be tuned**; every looser variant measured worse.
- **[`SQUEEZE_IGNITION_REVIEW.md`](./SQUEEZE_IGNITION_REVIEW.md)** — has never fired; includes a
  result that reversed on a wider sample.

---

## Two retractions worth remembering

Both were encouraging results that dissolved under a wider sample. They are recorded because the
pattern will recur:

1. **"The system has defensive skill"** (avoiding damage in falling stocks) — was **0.355 beta**.
   Capturing 36% of a −18% move looks like skill; capturing 36% of a +18% move looks like
   incompetence. Same mechanic.
2. **"insider_score predicts returns"** (+0.164 correlation, monotonic, profitable top bucket) —
   was **6 stocks**, splitting 3 positive / 3 negative. Two names had a good month.

**The methodological lesson:** on this data, always widen the sample and split out-of-sample
before believing a result. Three separate findings today reversed under that test.

---

## Next steps, in priority order

### Wait for data (nothing to build)
1. **Prebreakout/compression alert** — `check_prebreakout_alerts()` is the non-momentum,
   genuinely leading pillar the architecture needs, and **it already exists**. Only 10 resolved
   outcomes so far. In 3–4 weeks, compare its forward returns against the classic squeeze alert
   and against no-signal on the same names. **This is the highest-value open question.**
2. **Anti-chasing filter** — shipped today, blocks ~17% of BUYs. Verify the +0.17 pts holds live.
3. **GEX corroboration** — now recorded. In 3–4 weeks, test whether corroborated gamma alerts
   outperform. If not, don't build the `gamma_flip` gate.
4. **Dark pool baselines** — prints now persist. The relative threshold engages per-symbol once
   20 prints accumulate; watch the fire rate fall from ~85% toward 5–10%.

### Worth doing
5. **GROWTH stop width** — −12% is genuinely wide (SNOW hit −19%). Now that concentration is
   fixed, let a few weeks run before tuning, or the two changes can't be told apart.
6. **Widen insider/institutional data coverage** — only 6 of 193 symbols have it. That's a
   data-coverage problem, and fixing it turns an unanswerable question into an answerable one.

### Do not do
- **Don't tune alert thresholds.** Every looser variant measured worse.
- **Don't rebuild the confidence formula.** It's correctly signed post-fix.
- **Don't run the signal-engine live-bar refactor.** High regression risk against `AUD232` — the
  fix that resolved the inversion — for marginal gain. Eight sites already implement the
  settled/live split by hand.

---

## How to read the audit documents

Every audit was scoped to find defects, so every audit returned defects. That's what they were
asked to do, and the findings are real and independently verified. But reading only those gives
a false picture of the whole: **the app is in materially better shape than that reading
suggests, and better today than yesterday**, because those defects are now found, fixed, or
instrumented rather than silently costing money.
