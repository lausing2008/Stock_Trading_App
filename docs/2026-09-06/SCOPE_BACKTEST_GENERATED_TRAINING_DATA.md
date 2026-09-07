# SCOPE: Backtest-Generated Signal / Paper-Trade Data for ML and Self-Tuning

**Date:** 2026-09-06/07
**Ask:** "Can we use backtesting to generate the AI Signal and Paper Trading data, test and verify
the accuracy, and then use in ML and self-tuning?"

**Short answer: partly — and the part that works is genuinely valuable, but it is NOT the part
that sounds most appealing.** You can meaningfully expand *paper-trade* data by replaying entry
gates over already-persisted historical signals. You cannot manufacture *new signal history*
before 2026-05-25, and attempting to would produce leaky training data that is worse than having
less data.

---

## 1. The decisive fact: signals already snapshot their own inputs

`signals.reasons` is a JSONB blob written at generation time, and it is **not** a thin summary —
it carries roughly **170 fields**, including exactly the inputs that would otherwise be
lookahead-contaminated. Verified live in production:

| Table | Span | Rows |
|---|---|---|
| `signals` | 2026-05-25 → 2026-09-07 | **45,278** |
| `signal_outcomes` | 2026-05-25 → 2026-08-27 | **16,732** |
| `rankings` | 2026-04-29 → 2026-09-07 | 12,746 |
| `paper_trades` | 2026-06-16 → 2026-09-04 | **124** |

**Every one of the 45,278 signal rows has a non-null `reasons` blob (0 nulls).** Input coverage,
measured across the 20,872 signals since 2026-08-01:

| Input | Coverage | Why it matters |
|---|---|---|
| `market_regime` | 20,872 / 100% | would otherwise be today's-value only |
| `kscore` | 20,864 / 100% | ranking-engine value, not stored per-day elsewhere |
| `news_sentiment` | 20,856 / 99.9% | **no news-sentiment time series exists anywhere else** |
| `rs_rank` | 20,604 / 98.7% | relative strength vs sector |
| `options_cp_ratio` | 14,180 / 68% | options flow at signal time |
| `short_pct_float` | 12,716 / 61% | short interest at signal time |
| `ml_probability` | 8,827 / 42% | the ML model's own output at the time |

Also present: `insider_score`, `congress_score`, `analyst_upgrades_7d`, `breadth_pct`,
`fear_greed_score`, `days_to_earnings`, `ml_test_auc`, all four pillar scores, every TA
indicator, and the full bearish-pillar set.

**This is the asset that makes the request partly viable.** Without it, replaying a signal for a
past date would mean re-reading today's news sentiment / today's K-Score / today's regime and
pretending they were historical — textbook lookahead. With it, those values are already frozen
at the moment the signal fired.

---

## 2. What IS safely possible

### 2a. Replay entry gates over persisted signals ✅ — **BUILT AND RUN (2026-09-07). Result below.**

`replay_full_signal_history()` + `GET /paper-portfolio/backtest/replay-full-history` are live.
First production run, US, full persisted history (floor 2026-05-25):

| Style | Signals seen | Entered | Win rate | Avg return | Weeks | **Max in one week** |
|---|---|---|---|---|---|---|
| SWING | 2,654 | **402** | 52.0% | +0.30% | 13 | 69 |
| GROWTH | 2,912 | **1,378** | **47.0%** | **−0.37%** | 11 | **178** |

**Sample expansion is real: 124 real trades → ~1,780 replayed decisions**, roughly 14×. But three
findings matter more than that headline:

1. **GROWTH's replayed edge is negative.** 47.0% win rate and −0.37% average forward return, on
   n=1,378 — a far larger sample than anything this platform has judged on before. This is not a
   promising base to tune on; it's evidence that today's GROWTH gates admit a losing population.
   (`signal_outcomes.pct_return` is a FRACTION, so −0.0037 = −0.37%.)
2. **Clustering is severe, exactly as feared.** GROWTH put **178 of 1,378 entries (13%) into a
   single week**, across only 11 distinct weeks. The raw count overstates independent
   information substantially — which is precisely the failure mode §4 was written about, and the
   reason `effective_sample_note` was built into the response rather than left to a reader.
3. **The dominant skip reason is the earnings blackout**, not the newer gates —
   `"Earnings in 0-4 days — binary event risk"` occupies the top 4-5 slots for both styles. That's
   a long-standing, deliberate gate behaving exactly as intended, which is a good sign for replay
   fidelity.

**What this does NOT license.** The 1,780 rows must not be fed to a tuner as though they were
1,780 independent observations. Per §4: tag as synthetic, report clustered effective-N, keep a
real-outcome validation slice, and never let replayed data alone trigger a promotion. Given
finding (1), the more valuable immediate use of BT-1 is **diagnostic** — "today's GROWTH gates
would have lost money over the last 3 months" is an actionable finding in its own right, and
worth more than using the same data to tune.

### 2a-context. Why the expansion is worth having at all ✅

**This is the highest-value, genuinely-safe item, and the infrastructure already exists.**
`gate_harness.py`'s `replay_should_enter()` was built for exactly this, with point-in-time-safe
reconstruction helpers (`_fetch_matched_signals`, `_historical_atr`,
`_build_game_plan_for_style`, `_historical_confidence_delta`, `_historical_kscore`).

The opportunity is stark: **45,278 signals exist but only 124 paper trades.** Paper trading
started 2026-06-16 and only ever acted on signals arriving *after* that, under whatever gate
config was live at the time. Replaying the current gates across all 45k historical signals turns
a 124-row sample into potentially thousands — and every input is read from the frozen `reasons`
blob rather than from today.

**Why this is legitimate and not circular:** the *outcome* (forward return) comes from real
subsequent price bars, which are immutable historical fact. Only the *decision* is being
recomputed, from inputs frozen at decision time. That is the definition of an honest backtest.

### 2b. Verify accuracy of the replay ✅ — **BUILT AND RUN (2026-09-07). Result below.**

`verify_replay_fidelity()` + `GET /paper-portfolio/backtest/replay-fidelity` are live. First run
against production, 120-day window, US:

| Style | Real trades | Visible to replay | Replay entered | **Recall** |
|---|---|---|---|---|
| SWING | 54 | 39 | 10 | **25.6%** |
| GROWTH | 58 | 40 | 24 | **60.0%** |

**Recall looks alarming and is NOT a replay defect.** The skip-reason tally (added precisely
because a bare recall number isn't actionable) shows the rejections are dominated by gates that
**did not exist, or were looser, when those trades were taken**:

- `"Already ran 19.6% in 10 days (limit 10%) — chasing an extended move"` — this is
  **`AUD-CHASE-ROC10`, shipped 2026-09-05**. Every June/July trade predates it.
- `"Gap-up 3.8% … exceeds limit 3%"` / `"exceeds limit 4%"` — `max_entry_gap_pct`, part of the
  mirrored-constant family corrected in this same audit cycle.
- `"Confidence 39.8% below floor 45.0%"` — confidence floors retuned since.

So the replay is faithfully applying **today's** gates to **yesterday's** decisions. That is
correct backtest behavior, and it is the cfg-drift effect §4 anticipated.

**What this means for BT-3 — and it cuts both ways:**

1. **The replay mechanism is validated.** It reads frozen inputs, reconstructs PIT values, and
   rejects for substantive, explainable reasons rather than noise or crashes.
2. **But raw recall is NOT a fidelity metric while gates are in flux.** A future run must
   compare against the cfg *in force at the time* to isolate genuine replay error from
   deliberate gate changes — otherwise every tightening looks like a regression.
3. **Most importantly, this is direct evidence the current gates are much stricter than the
   ones that produced the existing 124 trades.** Replaying today's gates over the full 45k
   signal history will therefore yield materially FEWER entries than a naive extrapolation from
   124 suggests — so the "124 → thousands" expectation in §5 should be treated as an upper
   bound, not a forecast.

Directly testable: replay the gates over the window where real paper trades exist (2026-06-16 →
2026-09-04) and check that the replay reproduces those 124 real trades. **If replay can't
reproduce the trades that actually happened, it must not be trusted on the ones that didn't.**
This is the "test and verify the accuracy" half of the ask, and it's a real, passable gate.

### 2c. Feed the *paper-trade* result into self-tuning ⚠️ conditionally

Several tuners are gated on paper-trade volume — `calibrate_entry_weights()` needs
`_MIN_CALIBRATION_TRADES = 100` and currently sees only 37 eligible closed trades. Replay could
clear that. **But see §4 before doing it.**

---

## 3. What is NOT safely possible

### 3a. Manufacturing signal history before 2026-05-25 ❌

The `reasons` snapshot only exists from 2026-05-25. Before that there is **no** point-in-time
record of news sentiment, K-Score, regime, options flow, short interest, or ML probability.
Regenerating a "historical" signal for e.g. 2024 would necessarily read today's values for all of
them. That's not a longer backtest — it's a fabricated one that would look impressively large and
be systematically wrong.

**Hard rule: 2026-05-25 is the floor. There is no honest signal history before it.**

### 3b. Training ML on replayed signal *labels* ❌

The ML model's own output (`ml_probability`) is **an input to the signal**. Train a model on
signals that were themselves produced using that model's predictions and you get a feedback loop
where the model learns to agree with its past self, not to predict returns. The existing pipeline
already labels on real forward returns; keep it that way.

### 3c. Backtesting anything options-related ❌

No historical options chains exist at all (see `SCOPE_OPTIONS_SIMULATOR.md`). Unrelated to this
request's core, but worth stating since "backtest everything" invites it.

---

## 4. The real risk — and it's the one that matters most

**Synthetic volume can push a tuner past its promotion gate without adding any new information.**
That is *actively harmful*, not merely useless: it would let unvalidated parameters get promoted
into live trading on the strength of a bigger-looking sample.

The tuners' gates exist precisely to prevent that:

| Gate | Value | Where |
|---|---|---|
| `_KSCORE_SWEEP_MIN_ROWS` | 200 **per slice** (400 total) | `ranking-engine/src/api/routes.py:881`, enforced `:1049` |
| `_MIN_PROMOTION_EV_LIFT_PCT` | 0.5 | `market-data/src/backtest/gate_harness.py:1051` |
| `_MIN_PROMOTION_LIFT_SD_RATIO` | 0.5 | `gate_harness.py:1052` |
| `_MIN_CALIBRATION_TRADES` | 100 | `paper_trading_engine.py` |

**The specific failure mode:** 45,278 signals replayed into (say) 4,000 synthetic trades sail past
`_MIN_CALIBRATION_TRADES = 100` — but if 9 of every 10 came from the same few correlated market
weeks, the *effective independent* sample is a fraction of the nominal one. This platform has
already been burned by exactly this: the short-squeeze alert's 9.1% win rate on n=11 where 9 fired
in a single 8-day window, giving an effective independent sample "closer to 2 than 11"
(`docs/audits/2026-09-03-six-part-platform-audit-5-short-squeeze.md`). And the 2026-09-05 cycle
retracted *three separate findings* that reversed once samples widened.

### Required guardrails if this is built

1. **Tag every replayed row as synthetic** — a real column, not a convention. Any tuner must be
   able to filter, weight, or exclude them.
2. **Keep the chronological train/validation split**, and require the validation slice to contain
   **real** outcomes, not only replayed ones.
3. **Report effective sample size, not just row count** — cluster by week; a promotion gate should
   see the clustered count.
4. **Do not lower any promotion threshold** because more rows are now available. The thresholds
   are calibrated against real-sample noise.
5. **Never let replayed data alone trigger a promotion.** Treat it as evidence-strengthening for a
   candidate that already passes on real data — not as a substitute for real data.

---

## 5. Recommendation

**Do 2a and 2b. Hold 2c behind the §4 guardrails.**

| Phase | Work | Effort |
|---|---|---|
| **BT-1** | Replay current entry gates across all 45,278 persisted signals using the existing `replay_should_enter()` path; persist results tagged `synthetic=true` | **M** |
| **BT-2** | Accuracy verification: replay the 2026-06-16 → 2026-09-04 window and confirm it reproduces the 124 real paper trades. **Gate BT-3 on this passing.** | **S** |
| **BT-3** | Wire replayed data into tuners *only* with the §4 guardrails — synthetic tagging, clustered effective-N reporting, real-outcome validation slice | **M** |
| **BT-4** | *(don't)* Pre-2026-05-25 signal reconstruction, ML training on replayed labels | ❌ |

**The honest framing:** this request's real payoff is turning **124 paper trades into thousands**,
which unblocks tuners that are currently starved. It is *not* a way to manufacture years of
history — the data to do that honestly does not exist, and the value of the `reasons` snapshot is
precisely that it tells you where the honest floor is.

---

## 6. Not verified in this pass

The scoping agents for this item were terminated by an API spend limit mid-run. I verified the
decisive facts directly (history depth, `reasons` coverage, per-input fill rates, tuner
constants). **Not independently re-verified here:** the current PIT-join status of every ML
feature (`T234-ML-FUND-BROADCAST-LEAKAGE` / `T228` `merge_asof`), and the live `n_outcome_rows`
distribution across model artifacts. Neither changes the recommendation above — §3b rules out ML
training on replayed labels regardless — but both should be checked before any ML-side work.

---

## 7. WHAT IS ACTUALLY BEING TESTED — and what isn't (added 2026-09-07)

A direct question worth answering precisely, because the answer is narrower than "we're
backtesting the alerts":

**BT-1 and BT-2 test NEITHER the AI Signal alert NOR any options alert.** They replay
`_should_enter()`, which is the **AI Signal → paper-trade ENTRY** gate. Three separate paths
exist and they are easy to conflate:

| Path | Gate function | Outcome table | Rows | Covered by BT-1/BT-2? |
|---|---|---|---|---|
| **AI Signal → paper-trade entry** | `_should_enter()` | `signal_outcomes` | 16,732 | ✅ **yes — this is what we built** |
| **AI Signal email alert** | `_is_conviction_buy()` (`scheduler.py:840`) | `signal_outcomes` (shared) | 16,732 | ❌ no — different gate entirely |
| **Options alerts** (squeeze / gamma / options-flow) | `check_*_alerts()` | `squeeze_alert_outcomes` (335), `options_flow_alert_outcomes` (1,552) | — | ❌ no — separate tables, separate dashboards |

### ⚠ And a sharper caveat on even the path we DO cover

`_should_enter()` is the **decision-engine-outage FALLBACK gate**, not the live authoritative
one. `_DEFAULT_CONFIG["decision_engine_mode"] = "primary"`
(`paper_trading_engine.py:656`) means decision-engine's own `check_hard_rejects()` /
`compute_score()` decides real entries; `_should_enter()` only runs when DE is unreachable.

**This qualifies the BT-1 GROWTH finding.** "47% win rate, −0.37% avg return" describes what the
*fallback* gate would have admitted — it is **not** a measurement of live entry quality. Still a
real and useful signal (the fallback is what runs during any DE outage, and it shares most
thresholds with DE), but it must not be quoted as "the platform's GROWTH entries lose money."

### What testing the other two paths would require

- **AI Signal alert:** replay `_is_conviction_buy()` over the same 45k persisted signals. Cheap —
  it's a pure function reading `signal_data`, and `sig.reasons` already carries every input it
  needs. **This is the natural next backtest and is genuinely low-effort.**
- **Options alerts:** `options_flow_alert_backtest()` and `squeeze_alert_backtest()` already
  exist (see `docs/features/squeeze-and-options-alerts.md`) and have their own dashboards at
  `/squeeze-alert-performance` and `/options-flow-alerts`. Nothing new needed — they're just
  separate from this work.

---

## 8. BT-4 — the AI Signal ALERT gate, backtested (2026-09-07)

Closes the §7 gap. `replay_alert_gate()` + `GET /paper-portfolio/backtest/alert-gate` replay
`_is_conviction_buy()` — the **email alert** gate, not the paper-trade entry gate.

**This replay is cleaner than BT-1's.** `_is_conviction_buy()` is a pure function whose own
docstring says it reads regime from the stored signal's `reasons` dict ("the regime at
generation time"), so there is **no regime-blindness gap** here — the function natively consumes
exactly the frozen snapshot a replay supplies. `kscore` likewise comes from
`reasons["kscore"]` (point-in-time) rather than a live rankings read.

### Result (US, full persisted history)

| Style | Signals | Alerted | Alerted win | Alerted return | Baseline win | Baseline return | **Win lift** | **Return lift** |
|---|---|---|---|---|---|---|---|---|
| SWING | 2,654 | 497 (18.7%) | 45.1% | **−0.55%** | 41.5% | −1.41% | **+3.6pp** | **+0.85pp** |
| GROWTH | 2,912 | 701 (24.1%) | 44.4% | **−0.99%** | 41.7% | −1.57% | **+2.7pp** | **+0.58pp** |

### Two findings, and they point in opposite directions

**1. The conviction gate genuinely works — it is selective and it adds value.** It admits only
19-24% of BUY signals, and that admitted population beats the all-signals baseline on both win
rate (+3.6pp / +2.7pp) and average forward return (+0.85pp / +0.58pp). This is the first
quantified evidence on this platform that `_is_conviction_buy()` earns its place. Tier
distribution shows it is genuinely discriminating rather than rubber-stamping: only 104 SWING /
195 GROWTH signals reach `full` conviction, with most rejected outright.

**2. But the alerted population is still net-negative.** −0.55% (SWING) and −0.99% (GROWTH)
average forward return. **The gate filters toward less-bad, not toward profitable.** Both the
alerted and baseline populations lose money over this window; conviction filtering reduces the
loss without reversing its sign.

That is a materially different claim from "the alerts are good" and should not be rounded up to
it. It is consistent with the 2026-09-05 cycle's own conclusion that entry TIMING is the weak
component — a gate that better ranks a population of poorly-timed entries still yields
poorly-timed entries.

### Where the gate rejects (both styles, same ordering)

1. `Uptrend structure not aligned (SMA50/SMA200/price)` — ~1,141 / 1,144
2. `Stoch RSI overbought — pullback risk elevated` — ~966 / 1,000
3. `OBV: volume not confirming direction` — ~751 / 673
4. `MACD: momentum fading` — ~272 / 331

Note (2): the gate is already rejecting ~1,000 signals per style for being *overextended* — the
same failure mode `AUD-CHASE-ROC10` addresses on the entry side. Two independent gates
converging on "we fire too late" reinforces the entry-timing finding rather than duplicating it.

### Caveats that bound all of the above

- **Clustering is present**: 80 of 497 SWING alerts and 105 of 701 GROWTH alerts fall in a
  single week, across 11-12 weeks total. The lift figures rest on far fewer independent
  observations than the row counts suggest.
- **~3 months of history, one market phase.** Per §4's standing warning, treat a lift measured
  here as provisional until it survives a wider sample.
- Baseline is *all resolved BUY signals*, which is the right comparison for "does the gate
  select better" but is not a tradeable alternative (nobody trades every BUY).
