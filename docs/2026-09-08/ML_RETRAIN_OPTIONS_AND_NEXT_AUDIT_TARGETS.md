# ML Retrain: What To Actually Do — and Where To Audit Next (2026-09-08)

Follow-up to the 2026-09-07 six-part audit, which fixed the retrain's **style starvation** but
explicitly left "the job cannot finish a full pass in one night" as an open decision.

---

## Part 1 — The retrain problem is NOT what it looks like

The obvious framing is "1,384 serial runs take ~2 hours, so parallelise it." **That is the wrong
problem.** Measured against production:

### Finding A — we train 581 models to get 22 good ones

| family | total | suppressed | **active** | % active |
|---|---|---|---|---|
| xgboost | 290 | 252 | 38 | 13% |
| random_forest | 262 | 240 | 22 | 8% |
| lightgbm | 29 | 27 | 2 | 7% |
| **total** | **581** | **519** | **62** | **11%** |

Of those 62 active models, **45 have cv_auc ≥ 0.55 and only 22 reach ≥ 0.60.**

So ~2 hours of nightly compute produces **22 models anyone should trust**. Making that job
finish *faster* mostly means producing suppressed models faster.

### Finding B — the binding constraint is the FEATURE PIPELINE, not the schedule

`n_train` median is **233** while **~667 daily bars are available per symbol** — a 3× gap. It is
not a data-availability problem; training already requests a 5-year lookback. Decomposed:

| stage | AAPL | NVDA | MSFT | JPM | TSLA |
|---|---|---|---|---|---|
| bars loaded | 752 | 752 | 752 | 752 | 752 |
| after feature warmup / NaN | **500** | 500 | 500 | 500 | 500 |
| after **dead-zone filter** | 329 | 319 | 319 | 328 | 382 |
| *dead-zone cost* | *171 (34%)* | *181 (36%)* | *181 (36%)* | *172 (34%)* | *118 (24%)* |

**Two separate losses, both fixable, neither related to scheduling:**

1. **~252 rows (34%) lost to feature warmup.** Six long-window features force it:
   `sma_200_gap`, `dist_52w_high`, `dist_52w_low`, `ret_60`, `vol_60`, `hsi_200d_gap`. A 200-day
   SMA cannot produce a value until bar 200, and `notna().all(axis=1)` drops the row entirely.
2. **~170 rows (34% of what survives) lost to the dead-zone filter**
   (`outside_deadzone = fwd_ret.abs() >= label_threshold`, builder.py). This deliberately keeps
   only "clear signal" rows. It is a *defensible* modelling choice — but it is also the single
   largest remaining consumer of training data, and it was never revisited against the
   small-sample problem it now dominates.

**Net effect:** `n_test` ≈ 31 rows. That is the true origin of the extreme AUC values (exactly
0.0 and 1.0 appear on 62 artifacts), the dead-recall pathology, and the 89% suppression rate.
Every one of those is a downstream symptom of ~325 usable rows.

---

## Recommended actions, in priority order

**1. ~~Shrink the training universe to what is actually used.~~ RETRACTED — I checked, and it
is not safe.**

> **This was my own top recommendation and the measurement killed it.** The reasoning was "only
> 61 of 173 symbols have ever been traded, so train those." But **169 of 173 symbols produced a
> BUY signal in the last 30 days** — signals span essentially the whole universe, and ML feeds
> *signals*, not just entries.
>
> Even the softer `traded ∪ watchlisted` version (133 of 173, a 23% cut) is a bad trade: the 40
> symbols it would drop produced **1,588 BUY signals in 30 days — 38% of all signals**. Trimming
> them buys compute by degrading signal quality on more than a third of live output.
>
> (A secondary inference of mine was also wrong: eyeballing the excluded list suggested it was
> HK-dominated. It is **29 US / 11 HK**. The objection holds on signal volume, not on market mix.)
>
> **Conclusion: there is no safe universe trim.** The ~1,384-run job is training the symbols it
> genuinely needs to. Which makes items 2 and 3 the *only* real levers, and makes parallelism a
> more legitimate option than I first judged — see the revised item 4.

**2. Backfill more history before touching anything else.** ~667 bars ≈ 2.6 years. Extending to
5 years of daily bars would roughly double post-warmup rows and is the only change that attacks
the small-sample problem at its source. The 5-year lookback is *already requested* — the data
simply is not there. This is an ingestion task, not an ML task.

**3. Re-examine the dead-zone filter — measure, do not assume.** It discards ~34% of surviving
rows. Worth an A/B: train with and without it on the same symbols and compare *cv_auc* (not test
AUC, which is noise at n=31). It may well be earning its keep; nobody has checked since n_test
fell this low.

**4. Parallelism — REVISED to a genuine option, since item 1 is retracted.** My original
argument was "trim the universe first, only parallelise if it still does not fit." With no safe
trim available, the job legitimately needs to train ~173 symbols x 4 styles x 2 models. Two
caveats keep it at priority 4 rather than 1: it still produces mostly-suppressed models (items
2-3 are what change that), and the current interleave+rotation fix means truncation now degrades
*evenly* rather than starving whole styles — so the job failing to finish is no longer causing
targeted damage. Worth doing, but after the data problems, not instead of them.

**Explicitly not recommended:** a fleet-wide retrain (established 2026-09-07 — 89% of freshly
retrained models immediately self-suppress, and stale models measure marginally *better* than
fresh), and any threshold retuning (measured worse every time it has been tried).

---

## Part 2 — Where the same audit should go next

The six-part series covered the *trading decision path*. Ranked by expected value, the
uncovered areas:

### Tier 1 — genuinely untouched, and the failure mode would be silent

1. **Data ingestion & price integrity.** Everything upstream of every audited domain, and never
   audited. Concrete leads already in hand: **21 symbols have <400 daily bars and one has 1 bar**;
   V had a missing 2026-09-02 bar flagged in a prior audit and never chased; yfinance is the de
   facto sole source (Polygon/Alpha Vantage confirmed dead). A bad bar silently corrupts signals,
   ML features, backtests and paper trades simultaneously — the highest blast radius on the
   platform, and exactly the "silent wrong answer" shape the series found everywhere else.

2. **Ranking engine / K-Score.** Feeds `min_kscore` gates, the LONG style's unique `kscore_boost`,
   and position sizing — but was only ever touched tangentially. Nothing has verified the K-Score
   is computed as documented or that its inputs are point-in-time.

3. **Email/alert delivery & the notification layer.** Domain 6 found alerts recommending expired
   contracts; nothing has audited whether the *right users* get the *right* alerts, whether
   suppression/cooldown works per-user, or whether tier gating actually holds at send time.

### Tier 2 — worth doing, lower blast radius

4. **Research engine + LLM cost path.** Real spend, and a prior audit found a genuine leak.
5. **Frontend data correctness.** Domain 3's watchdog bug and the Horizon Comparison fix both
   showed displayed numbers diverging from live state; there are many more surfaces.
6. **Backtest harness fidelity (BT-1/2/4).** Built recently and never independently audited —
   and it is what future tuning decisions will rest on.

### Deliberately NOT recommended

- **Re-auditing the six domains just completed.** The 2026-09-03 series re-audited Short Squeeze
  three days after an exhaustive pass and did find new ground — but that was a *different lens*
  (display/backtest fidelity vs correctness), not a repeat.
- **Options/UW integration.** Covered by Domain 6 plus the 2026-09-07 endpoint survey.

**Recommended next: Data Ingestion & Price Integrity.** It is upstream of everything already
fixed, it has concrete unexplained anomalies waiting, and a corrupt bar invalidates conclusions
in all six audited domains at once.
