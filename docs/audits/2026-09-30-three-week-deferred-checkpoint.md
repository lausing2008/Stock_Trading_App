# 3-week deferred checkpoint — measurement only (2026-09-30)

Window 2026-09-25 → 2026-10-03, scheduled by the 2026-09-05 audit cycle. **No code was written.**
Every query was read-only. The point of this checkpoint is to read accumulated outcomes and decide
what is now judgeable, not to re-engineer anything — see the
`project_pending_data_verification` memory for why that distinction exists.

**Two items are now decided. Two moved but are not yet judgeable. Three are still count-blocked.**

---

## ⚠ CORRECTED 2026-09-30 — three of the headline findings below do not survive review

An independent production verification ([broad](2026-09-29-production-checkpoint-verification.md),
[September-only](2026-09-29-september-only-production-verification.md)) and an
[improvement plan](2026-09-29-deferred-checkpoint-review-and-improvement-plan.md) re-ran these
measurements against the *actual production consumers*. **The decisions below all stand. The
reasoning for three of them was wrong, and one was backwards.**

| § | What I concluded | What the verification found |
|---|---|---|
| 1 | Confidence "barely discriminates — flat 36.5–43.9%" | **A pooling artifact.** My query had no market/horizon/direction split, used `is_correct_5d` rather than the consumer's primary outcome, 10-point buckets rather than `_CONF_BANDS`, and no 180-day window. Sliced the way production slices: 76 slices, 59 with n≥30, and wide dispersion — LONG/US 0–40 **53.0%** vs LONG/US 85+ **35.9%**; GROWTH/HK 85+ **13.8%**; SWING/HK 55–70 **16.5%**. Range is **7.4pp**, not "about 5". |
| 2 | Bearish flow "anti-predictive, 29.1% vs 55.2% — opposite in quality" | **Not robust.** ~1,000 rows are not ~1,000 bets: outcomes dedupe per option chain, so many contracts reuse one stock return. Weighting each symbol/fire-date equally **reverses the 5d gap** — bullish 42.3%, bearish **47.1%**. 116 symbol/date groups carry *both* labels. Matched SPY excess is nearly identical (+1.85 vs +1.84 pp). And the production consumer gates on **10d**, not 5d. |
| 3 | GEX: "corroborated +0.88% vs uncorroborated −1.53%" | **Backwards.** I pooled raw returns across bullish `gamma_unwind_calls` and bearish `gamma_unwind_puts`; production scores puts as a *bearish* thesis, so raw return means the opposite for half the rows. Thesis-signed: corroborated **−1.87%** vs uncorroborated **+2.28%**. |
| 4 | Dark pool: "~17% or ~45%, denominator unresolved" | Both understate it. Per **session**, alerting names / names with observed prints that session runs **81–88%** (Sep 23–29). My 78-symbol denominator was lifetime coverage, not daily. |

Two further factual corrections to §1 below: the 10d mean **rounds to 0.0%**, so "negative at
every horizon beyond 3d" is not established; and RGTI is +0.43%, so **POET is not the only positive
name**. September-only, prebreakout is 36 events across 6 names with AI at **50%**.

Also: September alone has **5** short-squeeze events, not 16 — the 16 spans earlier months and must
not be quoted as September support. And the September confidence extract is **2,524** resolved
rows, not 19,256; the upper bands nearly vanish (90–99: n=6).

**One thing the corrections sharpen rather than overturn:** every pooled band sits *between* the
policy's ±1 thresholds (≤35% / ≥55%), so on pooled data the adjustment would be a no-op. "Do not
promote" holds — but "it adds noise" was never demonstrated, and the real slices are far from flat.

**A new finding from following this up:** the anti-chase funnel in §8 is closer than I said.
`paper_entry_scan_logs.skip_tally` already records 10 skip reasons over 32,316 September candidate
checks (`not_on_watchlist` 39.2%, `conviction_gate` 16.5%, …). Anti-chase is simply **not one of
them** — it returns early inside `_should_enter()` without writing a tally key. So the realised
block rate is one `skip_tally` key away, not a new observability subsystem. It also confirms the
30.1% figure is not comparable to the predicted 17%: anti-chase sits *after* the watchlist and
conviction gates, so its exposed population is a fraction of all BUY signals.

---

---

## 1. Entry timing / prebreakout — MEASURABLE NOW, AND NOT ENCOURAGING

The highest-value item. The prior reading was a 0% win rate on an effective sample of ~2–3,
heavily clustered — explicitly *not* evidence of failure. **That objection is now retired: the
count has moved.**

| Horizon | Resolved | Distinct symbols | Win rate | Avg return |
|---|---|---|---|---|
| 1d | 47 | 7 | 36.2% | −0.1% |
| 3d | 45 | 7 | 35.6% | +0.4% |
| 5d | 43 | 7 | 30.2% | −0.6% |
| 10d | 36 | 6 | 52.8% | 0.0% |
| 20d | 28 | 6 | 39.3% | **−2.2%** |

52 fired since 2026-08-21, 40 of them in the last 30 days. The 0% figure is superseded.

**But the clustering warning still binds, and it binds harder than the event count suggests.**
Only **8 distinct symbols**, and **AI alone is 22 of 52 fires (42%)**. At 5d there are 43 resolved
events across **7 names** — the independent sample is ~7, not 43.

Per symbol at 5d:

```
AI     n=18  win 33.3%  avg  -0.11%
POET   n= 9  win 55.6%  avg  +3.95%     <- the only positive name
UPST   n= 5  win  0.0%  avg  -6.60%
SOUN   n= 4  win  0.0%  avg  -3.39%
BKSY   n= 3  win 33.3%  avg  -1.14%
QBTS   n= 2  win  0.0%  avg  -4.85%
RGTI   n= 2  win 50.0%  avg  +0.43%
```

Removing the dominant name makes it **worse**, not better: ex-AI at 5d is 28.0% win / −0.93%, and
at 20d **30.0% win / −3.74%** across 5 names.

**Verdict: the blocker has changed identity.** It is no longer "too few events" — it is "too few
independent names", and the direction of the evidence is negative at every horizon beyond 3 days.
Prebreakout is not yet demonstrating the entry-timing improvement it was built for.

**Do not** re-derive the live-bar refactor or the ingestion speed-up. Both were consciously
deferred and the live-bar fix was formally vetoed as high regression risk.

**Re-check when symbol diversity grows**, not on a date. A useful floor: ≥20 distinct symbols with
no single name above ~15% of fires.

---

## 2. Confidence-calibration feedback — DECIDED: DO NOT PROMOTE

**The count blocker is gone.** Previously several bands were n<50, some n<10. Now **19,256 resolved
outcomes**, every band ≥120:

| Band | n | Win rate |
|---|---|---|
| 0–9 | 174 | 39.1% |
| 10–19 | 1,240 | 36.5% |
| 20–29 | 2,223 | 40.2% |
| 30–39 | 4,594 | 40.9% |
| 40–49 | 4,389 | 40.4% |
| 50–59 | 2,879 | 38.9% |
| 60–69 | 1,737 | 40.0% |
| 70–79 | 1,113 | 41.4% |
| 80–89 | 541 | 36.8% |
| 90–99 | 246 | 43.9% |
| 100–109 | 120 | 38.3% |

**Confidence barely discriminates.** The whole range spans 36.5%–43.9% — about 5 points — and it is
not monotonic: the top band (100–109) drops to 38.3%, below the 30–39 band.

The sign is *correct* (90–99 at 43.9% beats 0–9 at 39.1%), consistent with the inversion having
been fixed by `AUD232-BUY-FROM-TOP`. **Do not rebuild the confidence formula** — that is explicitly
a do-not-re-derive item. The finding is narrower and different: correctly signed, and nearly flat.

**Decision: do not promote `calibration_feedback_enabled` for any portfolio.** A ±1 adjustment
driven by a band structure this flat adds noise, not information. This item moves from "waiting on
data" to **closed on measurement**.

---

## 3. GEX corroboration — STILL DO NOT BUILD THE GATE (control group too small)

The decision rule was set in advance: *if corroborated alerts don't outperform, do NOT build the
`gamma_flip` gate.*

| `gex_corroborated` | n | Resolved 5d | Win rate | Avg return |
|---|---|---|---|---|
| True | 136 | 109 | 42.2% | **+0.88%** |
| False | 14 | 13 | 30.8% | −1.53% |
| NULL (pre-instrumentation) | 331 | 330 | 43.9% | −0.16% |

Corroborated *does* look better than not-corroborated. **But the control group is n=13 resolved.**
That is not enough to establish outperformance, and the pre-set rule cannot be satisfied by a
comparison this lopsided.

**The more useful observation: corroboration barely discriminates because it is nearly always
true** — 136 True against 14 False, ~91%. A flag that fires on 91% of alerts cannot gate much even
if its direction is right.

**Verdict: do not build the gate. Re-check when the False arm reaches ~30 resolved.** If the ratio
stays at 91/9 that will take a long time, which is itself the finding.

---

## 4. Dark-pool relative threshold — MAJOR IMPROVEMENT, SHORT OF TARGET

| | |
|---|---|
| Prints persisted | **375,411** (the table previously had 0 rows despite live alerts) |
| Symbols with prints | 78 |
| Symbols with ≥20 prints (relative threshold engaged) | **71 of 78 (91%)** |
| Alerts, recent daily | ~32–45/day, steady |
| Tracked universe | 201 |

Persistence is fixed and the relative threshold is engaged for 91% of covered symbols.

**Fire rate depends on which denominator is correct, and that matters:**

- Against the **201-symbol universe**: ~35/201 = **~17%/day**
- Against the **78 symbols that actually have dark-pool coverage**: ~35/78 = **~45%/day**

The original defect was "fires on 85–89% of the universe daily". Against the same denominator, the
fix took it to ~17% — a large, real improvement — but the stated target was **5–10%**, so it is
still roughly 2x high. Against the coverage-only denominator it has barely moved.

**The denominator was never pinned down in the original finding**, and it changes the verdict from
"close to target" to "not close". Resolving which one the 5–10% target referred to is the next
step, and it is a question about the original spec, not about more data.

---

## 5. Options-flow direction — UNBLOCKED, AND THE BEARISH HALF IS ANTI-PREDICTIVE

The gate was ≥30 resolved outcomes per direction. **Both directions are now ~1,000.**

| Direction | Total | Resolved 5d | Win rate | Avg 5d return |
|---|---|---|---|---|
| bullish | 1,047 | 957 | **55.2%** | +3.05% |
| bearish | 1,070 | 984 | **29.1%** | **+4.00%** |

The earlier audit found the two directions "statistically identical ~80% price-rose rates" on two
days of data and attributed it to market drift. With ~1,000 resolved each, **they are not
identical — they are opposite in quality.**

- **Bullish: 55.2% correct.** Weakly useful.
- **Bearish: 29.1% correct, and price rises +4.0% on average afterwards.** A bearish alert is
  wrong roughly 7 times in 10, and the move that follows is *up*.

This is the same shape as the short-squeeze alert finding (anti-predictive, documented in
`docs/features/squeeze-and-options-alerts.md`). Note both directions show a positive average
return, so part of this is market drift over the period — but drift does not explain a 26-point
gap in win rate between the two arms.

**This item is no longer data-blocked. It is now a design decision:** the bearish arm is worse than
a coin flip and should be considered for suppression or inversion, which needs its own analysis
rather than a checkpoint line.

---

## 6. Squeeze ignition — NO LONGER ZERO, STILL UNDECIDABLE

It had fired **zero times ever**. It has now fired **6 times** (2026-09-08 → 2026-09-23):

```
RGTI 09-08  +1.08%     CRWV 09-18  -0.42%
DFNS 09-10 -19.92%     QUBT 09-22  -6.03%
NBIS 09-17  +1.37%     QUBT 09-23  -7.64%
```

2 up, 4 down, average ≈ −5.3%, and QUBT accounts for two of the six. The four rejection gauges are
doing their job — the alert is observable now. But **n=6 decides nothing**, and the loosen-or-retire
call should not be made on it. The one thing the data does say is that loosening is not obviously
warranted: the fires it does produce are not good.

---

## 7. Squeeze-family short-interest corroboration — COUNT HAS MOVED

Gate was "meaningfully more than ~107 total squeeze-family alerts". Now **481**:

```
gamma_unwind_puts   303
gamma_unwind_calls  156
short_squeeze        16
squeeze_ignition      6
```

The count gate is satisfied, but note **the family is 96% gamma-unwind**. The short-squeeze alert
itself — the one the UW short-interest corroboration was meant for — has fired only **16 times**.
So for the actual target of that work, the original blocker still stands.

---

## 8. Anti-chase `roc_10` filter — MEASURED, BUT NOT AGAINST THE RIGHT POPULATION

Of 12,992 signals in the last 21 days carrying `roc_10`, **1,512 (11.6%)** sit at ≥10. By signal:

| Signal | n | roc_10 ≥ 10 | Share |
|---|---|---|---|
| BUY | 1,955 | 588 | **30.1%** |
| HOLD | 5,047 | 882 | 17.5% |
| WAIT | 4,956 | 42 | 0.8% |
| SELL | 1,034 | 0 | 0.0% |

**30.1% of BUY signals carry `roc_10` ≥ 10, against the ~17% BUY-block rate the design predicted.**

I could not confirm this is the realised block rate. The filter blocks BUY **entries** in
`_should_enter()`, not signal generation — so this measures the population the filter is exposed
to, not what it actually rejected after the other entry gates ran. **The ~17% claim is neither
confirmed nor refuted here**; confirming it needs entry-level instrumentation that does not
currently exist, which is a code change and therefore out of scope for this checkpoint.

The OOS gain (+0.33 pts) is likewise not confirmable from stored outcomes alone.

---

## Items untouched this cycle

- **Regime robustness** — still exactly 1 bear sample. No code angle; the market has to do
  something it has not done in this system's lifetime.
- **ML `n_outcome_rows` drop-off** — needs a dedicated instrumentation session, not elapsed time.
- **Portfolio concentration** — still gated on open-position count.
- **`poll_broker_order_fills()`** — occurrence-based; needs a broker-linked position open across
  more than one scheduler cycle.
- **8-cell ablation grid** — still gated on the simpler 2-group result and on margin features that
  do not exist.
- **Watchlist style re-run** — no hard n; defer.

## Summary

| Item | Before | Now |
|---|---|---|
| Confidence calibration | n<50, some n<10 | **CLOSED — do not promote; bands are flat** |
| Options-flow direction | 2 days resolved | **UNBLOCKED — bearish arm anti-predictive (29.1%)** |
| Prebreakout | ~2–3 effective | Measurable (43 @5d) but **7 names**, negative beyond 3d |
| Dark pool | 0 prints, 85–89% fire | 375k prints, ~17% or ~45% — **denominator unresolved** |
| GEX corroboration | unmeasured | Control arm n=13 — **still do not build the gate** |
| Squeeze ignition | 0 fires ever | 6 fires, 2/4 win, avg −5.3% — still undecidable |
| Squeeze family total | ~107 | 481, but short_squeeze itself only 16 |
| Anti-chase | OOS +0.33 | 30.1% of BUYs exposed; **realised block rate unmeasurable** |

Nothing here establishes profitability. Two findings — flat confidence bands and an anti-predictive
bearish flow arm — argue against components currently in the decision path, and both deserve their
own follow-up rather than a line in a checkpoint.
