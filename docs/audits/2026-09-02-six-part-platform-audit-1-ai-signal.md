## Deep Audit Series (2026-09-02): AI Signal — 1 of 6

**Scope**: AI Signal generation (`services/signal-engine/src/generators/signals.py`,
`api/routes.py`, `api/calibration.py`, `api/analytics.py`, `api/outcomes.py`), sequential
platform audit series (AI Signal → Decision-Making → Paper Trading → Model Training → Short
Squeeze Alerts → Options Trading & Alerts), following `docs/AUDIT_DOMAIN_SERIES_TEMPLATE.md`.
Real fixes applied where confirmed safe; larger/riskier fixes deferred with explicit reasoning.

### Ground truth (queried directly against production before dispatching any subagent)

`signal_outcomes` table, 16,088 rows, 2026-05-25 to 2026-08-27:

```
horizon | direction | total | win_rate_5d | avg_return_5d | win_rate_10d
SHORT   | BUY       |  3259 |       40.3% |        -1.20% |        38.7%
SHORT   | SELL      |  1052 |       39.0% |        +0.80% |        38.6%
SWING   | BUY       |  3052 |       41.5% |        -1.08% |        39.5%
SWING   | SELL      |  1074 |       40.6% |        +0.50% |        44.0%
LONG    | BUY       |  2231 |       41.0% |        -1.16% |        42.4%
LONG    | SELL      |  1074 |       37.9% |        +1.03% |        38.9%
GROWTH  | BUY       |  3472 |       40.4% |        -1.24% |        39.9%
GROWTH  | SELL      |   874 |       36.4% |        +1.77% |        36.2%
```

Every BUY row, every horizon: negative average 5-day return, ~40-41% win rate. `is_correct`
(the horizon-native window, 7-28 calendar days for BUY) is worse still: 40.8% win rate, -2.61%
avg return across 12,014 resolved rows. Confidence is 0-100 (not 0-1 — my own first query
assumed 0-1 and produced a meaningless bucket; corrected before handing to the subagent).
Correctly bucketed, confidence does NOT predict outcome — if anything it's inverted:

```
conf_bucket   n     win_rate_5d   avg_ret_5d
high(>=70)    191   40.8%         -2.04%   <- worst return of the three
low(<40)      1608  40.0%         -1.09%
mid(40-70)    1253  43.4%         -0.91%
```

### Headline findings (9 total; top 4 independently re-verified by me before recording)

1. **CONFIRMED, independently re-verified — confidence is meaningless.**
   `signals.py:2566`: `confidence = round(abs(fused - 0.5) * 200, 2)` — a pure, deterministic
   restatement of the input probability, never touching real historical accuracy. The
   calibration machinery that WOULD fix this (`_build_confidence_calibration`,
   `signals_shared.py:209-294`) exists, runs correctly, and is exposed via
   `reasons["calibrated_win_rate"]` — but only as a **display annotation**, never fed back into
   the stored `confidence` value itself. **FIX DEFERRED** (see below) — feeding calibration
   back into confidence needs a trustworthy `signal_outcomes` population to calibrate against,
   which finding #2 shows didn't exist until today's fix.

2. **CONFIRMED, independently re-verified, FIXED — the ground-truth table recorded the wrong
   moment.** `signals` is upserted ~77x/trading day (`ON CONFLICT (stock_id, horizon,
   date_trunc('day', ts)) DO UPDATE`). `evaluate_signal_outcomes()` filtered on the LIVE
   `Signal.signal` column (`outcomes.py:216`, pre-fix) — the day's FINAL state — for both
   selecting which signals to score and what confidence/reasons to score them with. Two
   distinct corruptions: (a) a signal genuinely BUY/SELL at 10am that faded to HOLD by close
   was invisible to evaluation entirely (a systematic selection effect on the whole dataset —
   only signals still BUY/SELL at 4pm were ever recorded); (b) a signal that stayed BUY all day
   was scored using its 4pm confidence/reasons, not the state that actually fired the trade
   thesis. **Fixed**: 5 new nullable columns on `Signal`
   (`first_buy_sell_at/signal/confidence/bullish_probability/reasons`), frozen via `COALESCE`
   in the upsert at the FIRST BUY/SELL transition of each calendar day, never overwritten for
   the rest of that day. `evaluate_signal_outcomes()` now selects and scores exclusively from
   these frozen columns. Verified end-to-end against a real disposable `postgres:16-alpine`
   container (fresh-DB run, idempotent re-run, forced `ALTER TABLE` path on an existing table,
   and a direct upsert simulation proving a BUY-then-fade-to-HOLD sequence correctly freezes the
   10am state while the live display row still correctly shows the final HOLD). 14 new
   regression tests, 1 adversarial sabotage cycle (2 tests caught it, restored + confirmed
   byte-identical). Existing 16,088 rows are left as-is under the old methodology — no
   retroactive fix is possible (their true first-fire state was never captured) — new rows
   accumulate cleanly under the fix going forward.

3. **CONFIRMED, independently re-verified, FIXED — stale regime vocabulary regressed an
   already-fixed bug.** Both catalyst-nudge sites (`routes.py:323`, `routes.py:1151`) still
   whitelisted the OLD 4-state regime vocabulary (`bull/high_vol/bear/unknown`) years after
   `AUD264-SIGNALENGINE-SECOND-REGIME-CLASSIFIER` migrated `market_regime` to the canonical
   5-state value (`bull/neutral/choppy/risk_off/bear`, confirmed live-emitted by `/stocks/
   regime`). A real `choppy`/`risk_off` regime silently fell through to `unknown` — the
   LOOSEST threshold tier — reopening the exact `T237-SIG2` failure mode this file's own
   comment describes ("exactly backwards during the regime that should be most conservative"),
   through a different door. Measured real gaps: SHORT risk_off vs unknown +0.06, LONG/GROWTH
   +0.08. **Fixed**: both whitelists updated to the full canonical 6-value set. 4 regression
   tests, 1 adversarial sabotage cycle (3 of 4 tests caught it cleanly, restored + confirmed
   byte-identical).

4. **CONFIRMED, FIXED — falsy-zero AUC bug, same class already fixed 3x in ml-prediction.**
   `signals.py:402` (pre-fix): `test_auc = float(m.get("mean_model_test_auc") or m.get("auc") or
   m.get("cv_auc_mean") or 0.55)` — a genuine, legitimate `auc=0.0` (a rank-inverted/untrained
   model) is falsy and silently substituted with a fabricated 0.55 "healthy" default, defeating
   the very guard (`if ml_test_auc < 0.50: raw_w = 0.0`) designed to zero out a worthless
   model's weight. Same bug class as `AUD-ML1B-NUDGEGATE` and its 2 siblings
   (`services/ml-prediction/src/training/trainer.py`). **Fixed**: replaced the `or`-chain with
   an explicit `is not None` presence-check loop, matching the established fix pattern exactly.
   6 regression tests (including one exercising the real downstream weight-zeroing guard via
   source-extraction of the exact snippet).

5-9. **CONFIRMED, documented as tracker items, not fixed this pass** (see tracker for full
   detail): compression cap can restore probability up to ~18 independent risk gates
   legitimately removed (needs backtesting before changing); an unreachable confidence-
   calibration band populated only by the now-fixed corruption (should self-resolve/need
   re-checking once F2's fresh data accumulates); stale win-rate figures embedded as design
   justification in code comments (e.g. "BUY 63.3%" vs. today's measured 40.8%); no
   benchmark-relative (vs. SPY) evaluation exists.

### Corrected framing (not a bug — recorded so a future audit doesn't re-flag it)

The subagent's own draft characterized the 5d/10d/20d field naming as "a real mislabeling" —
independently checked and this is **corrected**: `_lookup_outcome_price` genuinely uses
calendar days (`target = entry_date + timedelta(days=days)`), but the owning constant
(`_OUTCOME_HOLD_DAYS`, `signals_shared.py:344`) is explicitly commented "Hold window in
calendar days per horizon. Approximates actual trading days held," and every inline comment
(`# ~5 trading days` etc.) is labeled as an approximation. This is a consistent, self-disclosed
convention throughout the codebase, not an undisclosed inconsistency between two systems. The
real, still-valid part of that finding stands: `is_correct` (7-28 calendar days) IS worse than
the 5-day window, and that's a genuine "holding longer loses more" signal, not a labeling bug.

### Answers to the audit's own lettered questions (full detail in tracker AUD-SIGNAL-REF)

- **Is Unusual Whales wired into AI Signal?** No — zero wiring. Confirmed no import of
  `unusual_whales.py` anywhere in signal-engine. The two adjacent-looking inputs (short
  interest, options flow) are both free-tier (yfinance / market-data's own `/options-flow`).
- **Should it be?** Not as a fix for what was found here — F1-F4 are defects in how EXISTING
  inputs are combined/thresholded/recorded; adding a new paid input on top of a compression cap
  that overrides ~18 risk gates and a ground-truth table that (until today) mis-recorded which
  signal fired would be neither a fix nor measurable against a broken evaluation substrate. One
  narrow, real opportunity for later: `_fetch_short_interest` uses yfinance's exchange-settled
  `shortPercentOfFloat`, independently documented elsewhere in this app as lagging up to ~6
  weeks (`unusual_whales.py:249`, `AUD265-SHORT-INTEREST-AGE-NEVER-CHECKED`) — UW's faster
  short-interest/borrow-fee feed would make the existing squeeze-boost gate
  (`signals.py:2359-2365`) honest. Dark pool and GEX belong downstream (decision-engine,
  squeeze, options domains — separate audits in this series), not in signal generation.

### Checked and found CLEAN (do not re-investigate)

- `short_pct_float` unit consistency end-to-end (fraction throughout the pipeline; the one `x100`
  is display-only, downstream squeeze-score consumer correctly uses the percent form).
- ML/TA fusion weight ramp (AUC ramp, per-style cap, AUC-scaled floor, ML/TA conflict cut) —
  internally consistent and correctly signed.
- Pillar gates (bullish/bearish None-sentinel handling) — no falsy-zero bug.
- `_lookup_outcome_price` grace-window censoring (`AUD261-CENSORING-NEVER-FIRED`) — correct and
  complete.
- SELL-side sign convention (`_signed_return`) — correct; the "+0.80% SELL avg return" in the
  ground-truth table above is a price RISE, i.e. a SELL LOSS — SELL is also unprofitable, not
  "slightly positive" (a reading correction, not a code bug).
- Watchdog falsy-zero candidate (`calibration.py:2631`) — investigated as a sibling to F6 and
  REFUTED: the value is a Redis string (`"0.0"` is truthy), not a real falsy-zero risk.
- Per-row SAVEPOINT / incremental commit in the evaluator, duplicate-outcome guard, catalyst-
  nudge label re-derivation structure (T237-SIG2/SIG3) — all correctly implemented.

### Pre-existing, unrelated test breakage found (not caused by this audit's fixes)

`tests/test_signal_generator.py` fails to import (`_decide` no longer exists in `signals.py` —
was likely renamed at some point; the test file was never updated). `tests/
test_analyst_momentum.py` has 4 failing assertions, confirmed via `git stash` to fail identically
on the unmodified codebase. Neither touched by this audit's changes — flagged here so a future
session doesn't mistake either for a regression of today's work.

### What was NOT independently verified (explicit, not silently assumed)

The subagent's own answer to audit question F (sample-size/recency bias) is tagged PLAUSIBLE,
not CONFIRMED — it did not have production DB access to query the date/symbol distribution
behind the negative-BUY-return pattern. I did not independently re-run that specific query
either; the finding stands as "not dominated by one period/ticker" is plausible given the
consistency across 4 horizons/16,088 rows/both directions, but unconfirmed.

### Disposition

F3, F6 fixed (safe, small, mechanical). F2 fixed (larger — new columns, migration verified
against a real disposable Postgres, new evaluator query, full regression + adversarial-sabotage
coverage). F1 (confidence calibration) deliberately DEFERRED: wiring calibration into the
stored confidence value now would calibrate against a near-empty freshly-fixed dataset (~175
outcome rows/day historically, split across 80 `(horizon, direction, market, band)` buckets
each needing 30+ samples — realistically weeks before most buckets clear the floor). Revisit
once F2's fix has had time to accumulate a real, uncorrupted sample. F4/F7/F8/F9 documented as
tracker items, not fixed — F4 needs backtesting before touching ~18 risk gates; F7/F8 are
downstream consequences of F1-F3 and should be re-checked once those land; F9 needs its own
scoping (a benchmark-relative evaluation is a real, separate feature).

**What to check if this needs re-verifying:**
```bash
grep -n "confidence = round(abs(fused - 0.5)" services/signal-engine/src/generators/signals.py
grep -n "first_buy_sell_signal.in_" services/signal-engine/src/api/outcomes.py
grep -n '"bull", "neutral", "choppy", "risk_off", "bear", "unknown"' services/signal-engine/src/api/routes.py
docker exec stockai-postgres-1 psql -U stockai -d stockai -c "\d signals" | grep first_buy_sell
```
