# Short-Sell / Squeeze Alert System — What's Built, and What's Recommended Next

**As of 2026-08-15.** This documents everything shipped for short-sell-related signals and
alerts across all sessions, plus open recommendations. Verified against current code, not
just commit history, before writing this.

---

## 1. The three live alert types

There are **three genuinely distinct alerts** in this family, each answering a different
question, each with its own DB table for outcome tracking. They are not variations of one
alert — they fire at different moments in a squeeze's lifecycle.

### 1a. Short Squeeze Alert (`check_short_squeeze_alerts`) — the move is already happening

**File:** `services/market-data/src/services/scheduler.py:2605`
**Question it answers:** "A heavily-shorted stock just moved sharply — is this the start of a
short squeeze?"
**Trigger:** short interest ≥ 15% of float (`_SQUEEZE_MIN_SHORT_FLOAT`) **AND** an intraday
move ≥ 3% already in progress (`_SQUEEZE_MIN_INTRADAY_MOVE_PCT`). Runs every minute during
market hours.
**Email includes:** a full entry/stop/target game plan (T260-SHORTSQUEEZE-ALERT-GAMEPLAN),
days-to-cover escalation language, and (as of AUD265) the **age of the short-interest reading
in days** — added specifically because exchange short interest settles only ~2×/month and can
be several weeks stale; the email now says so instead of implying a live number.
**Outcome table:** `SqueezeAlertOutcome` (`alert_type="short_squeeze"`).

### 1b. Gamma Unwind Alert (`check_gamma_unwind_alerts`) — options-positioning based

**File:** `services/market-data/src/services/scheduler.py:3197`
**Question it answers:** "Is options open interest lopsided enough, close enough to expiry,
that dealer hedging could accelerate a move?"
**Trigger:** expiry within 5 calendar days (`_GAMMA_UNWIND_MAX_DAYS_TO_EXPIRY`) **AND** ≥55% OI
concentration on one side (`_GAMMA_UNWIND_MIN_OI_CONCENTRATION`, puts-side threshold — calls
have a different, asymmetric threshold per `f228bbe`, since equity options carry a structural
call skew and 55% isn't equally selective on both sides).
**Two sub-flavors, scored separately, never pooled:** `gamma_unwind_calls` (bullish-leaning)
and `gamma_unwind_puts` (bearish-leaning — this app's closest concept to "option sell"/betting
against a squeeze). Pooling them would silently cancel real signal in either direction, the
same class of bug already fixed once elsewhere in this app (`BUG233-RETROEV-SIGNMIX`).
**Outcome table:** `SqueezeAlertOutcome` (`alert_type` = `gamma_unwind_calls` or
`gamma_unwind_puts`).

### 1c. Pre-Breakout Watch (`check_prebreakout_alerts`) — BEFORE any move starts

**File:** `services/market-data/src/services/scheduler.py:2915`
**This is the direct answer to your original ask**: *"predict the short sell not able to
recover and send me the alert BEFORE it starts to breakout."*
**Question it answers:** "Is a heavily-shorted stock quietly coiling right now, with no move
yet, such that a squeeze COULD build?"
**Trigger:** short interest ≥ 15% of float **AND** a "coiling" read — Bollinger Band width
**AND** ATR (both price-normalized) sitting in the bottom 20th percentile of their own trailing
126-day (~6 month) range. Volume dry-up is reported but not required. Runs every 4 hours,
market-hours gated.
**Outcome table:** `PreBreakoutAlertOutcome` — deliberately a separate table from
`SqueezeAlertOutcome`, since this alert fires at a genuinely different moment (before a move,
not during one).

**Why this one has extra fields the other two don't** — see §2 and §3 below.

---

## 2. Supporting infrastructure shared across all three

| Piece | What it does |
|---|---|
| `SqueezeWatch` table + `check_squeeze_watch_reverts` | User-added manual watchlist entries (from the short-squeeze page's "Add to watch" button), with a one-shot "this setup has faded" revert email once real evidence shows the pressure is gone. Covers both `short_squeeze` and `bearish_puts` watch types. |
| `short_interest_date` on `Fundamentals`/snapshots (AUD265) | Captures and now **enforces** the real settlement date of short-interest data — previously never checked at all, meaning a stale reading could silently drive an alert with no way to know it was old. |
| `/short-selling` page | A plain sortable screener across the whole universe by short-%-of-float, short ratio, market cap — a lookup tool, not an alert. |
| `/short-squeeze` page | The live dashboard for both squeeze-in-progress alerts, with a market-wide screener, an in-page "How to read this page" guide, and manual watch-add buttons. |
| `/squeeze-alert-performance` (Admin) | Measures real win rates + average forward returns (5d/10d/20d) for every alert type that has actually fired, using `SqueezeAlertOutcome`/`PreBreakoutAlertOutcome` rows — see §4. |

---

## 3. The confidence-scoring problem, and what was actually built for it

You asked specifically for a model prediction with confidence, combined with the rule-based
gate. Here is the honest story of what was investigated and what shipped.

### The constraint that shaped everything

A real historical backtest (`build_prebreakout_dataset()`,
`services/market-data/src/backtest/prebreakout_dataset.py`) was run against 3 years of real
daily price history + real weekly short-interest snapshots. It found only **~68 historical
candidate days / 17 positive labels** across the entire tracked universe. That is far too few
to train and validate a real classifier — this app's own promotion-margin discipline
(`gate_harness.py`'s `MIN_SAMPLES_PER_SPLIT = 15` **per class per split**, plus an EV-lift/
standard-deviation margin check that was itself added after a real, measured **~50%
false-promotion rate at n=15-50** in a simulation) would reject a model built on this data by a
wide margin. Because `FundamentalsSnapshot` (the short-interest history) only started being
populated 2026-07-05, reaching a defensible sample size (~150-200+ rows) would take **well over
a year** of continued weekly accumulation — not weeks.

**Decision made (with your explicit sign-off): do not train a squeeze-specific model yet.**
Fabricating a confidence number from 17 examples would be actively misleading — it would look
like a real percentage while being statistically indistinguishable from noise.

### What was built instead — two honestly-scoped signals, not one invented number

1. **`ml_price_direction_confidence` / `ml_price_direction_model_version`** — reuses this app's
   **existing, already-trained, already-promoted** per-symbol price-direction model (the same
   one behind `POST /ml/predict`, used live elsewhere in this app for unrelated trading
   decisions). This is a genuinely independent second read — a general price-direction model,
   not fit on the thin squeeze dataset at all. Deliberately **not** named `model_confidence` —
   it answers "what does the app's general model think about this stock's direction," never
   "will this specific squeeze setup break out." Conflating the two would repeat exactly the
   kind of false-precision mistake this app's own margin-check discipline exists to prevent.
   Fails open (`None`, `None`) when no trained model exists for a symbol (a routine 404) or
   when the model itself is internally flagged as unreliable (`oos_suppressed`) — never
   substitutes a misleading neutral value.

2. **`calibrated_win_rate` / `calibrated_win_rate_count`** — a **measured** historical win rate
   computed from this alert's own resolved outcomes, bucketed by short-interest band (15-20%,
   20-30%, 30%+), reported only once **≥30 resolved outcomes** exist in that band. This mirrors
   a pattern already proven elsewhere in this app (`signal-engine`'s confidence calibration and
   the Top-3 Conviction Alert) — a real fraction, with its real sample count shown, `None`
   below the floor rather than a fabricated rate.

3. **`model_confidence` / `model_version`** (the columns reserved for an actual
   squeeze-breakout-trained classifier) remain `None` today, correctly, and will for well over
   a year at current data-accumulation rates.

**The alert email now shows all of this side by side** — the rule gate's own compression
evidence, the general ML model's read (clearly labeled as not squeeze-specific), and the
measured historical win rate (or an explicit "not enough resolved history yet" line).

### Why this is the right design, not a shortcut

- It answers your actual intent ("give me confidence on this") honestly, using real signals
  that already exist, instead of either (a) refusing to add anything, or (b) training a model
  that would produce numbers indistinguishable from noise.
- It follows this app's own established playbook exactly — the calibrated-win-rate pattern is
  copied from a mechanism already proven to work for a different alert (Top-3 Conviction).
- The path to a real squeeze-specific model isn't abandoned — it's correctly deferred, with the
  exact data-volume gate documented, so a future session can revisit it once the data actually
  supports it (see §5).

---

## 4. Retroactive backtest (already built, separate from live forward-tracking)

**Endpoint:** `GET /admin/squeeze-alert-backtest`
Forward-tracking (`SqueezeAlertOutcome`/`PreBreakoutAlertOutcome`) needs months to accumulate
enough real fires to report a reliable number. The backtest instead replays the **real, live**
short-squeeze thresholds against 3 years of already-stored weekly short-interest snapshots +
daily price bars, scoring candidate days the same way the live evaluator would — giving a real
number today instead of waiting.

**Explicitly does NOT cover gamma_unwind** — there is no historical options open-interest data
anywhere (yfinance has no historical-OI API, and this app stores none). Rather than fabricate
historical OI data, the endpoint says plainly that gamma_unwind isn't backtestable and why.
Live-verified against real production data: 93 qualifying historical snapshots found.

---

## 5. Open recommendations

These are real, identified gaps — none silently dropped, all worth tracking as future work.

1. **Revisit the squeeze-specific classifier once data volume clears the bar.** Concretely:
   once `PreBreakoutAlertOutcome` (plus the underlying `FundamentalsSnapshot` weekly history)
   accumulates roughly 150-200+ resolved candidate rows with a healthy positive/negative split
   (est. well over a year away at today's pace), re-run this exact investigation and check
   whether `gate_harness.py`'s promotion-margin math can actually be cleared. Don't build one
   before then.

2. **Options data is a permanent, not just current, limitation for training.** Even once price/
   short-interest history is deep enough, options open-interest history will likely remain thin
   (only ~2 weeks of real history exists anywhere in this app today). If a real model is ever
   trained, options positioning should probably stay an inference-time context modifier, not a
   training feature, unless a paid historical-OI data source is added.

3. **Consider extending the calibrated-win-rate pattern to the two other alert types.** Right
   now `calibrated_win_rate`/`_count` only exists on `PreBreakoutAlertOutcome`. The same
   sample-count-gated, band-bucketed approach could be added to `SqueezeAlertOutcome` (e.g.
   bucketed by intraday-move magnitude for `short_squeeze`, or by OI-concentration for
   `gamma_unwind_*`) once each has enough resolved rows — giving the same honest "measured win
   rate, real n=" treatment to every alert in this family, not just the newest one.

4. **Short-interest data age is now captured but not yet surfaced everywhere it's used.**
   `short_interest_date` was added and is shown in the short-squeeze alert email (AUD265), but
   worth double-checking every OTHER place `short_percent_of_float` drives a decision (the
   pre-breakout rule gate, the backtest, the `/short-selling` screener) also surfaces or at
   least respects staleness consistently.

5. **The pre-breakout alert's calibration bucket boundaries (15-20%, 20-30%, 30%+) were chosen
   as a reasonable first cut, not empirically tuned.** Once enough resolved data exists, it may
   be worth re-checking whether these three bands are actually where the real win-rate
   differences fall, or whether a different split would separate the data better.

6. **No alert in this family currently cross-checks against the app's own regime/risk gates**
   (e.g. suppressing a squeeze alert during a broad market risk-off regime, the way some other
   alert types in this app already do). Worth evaluating whether that class of gate applies
   here too, or whether squeeze setups are meant to be regime-agnostic by design.

---

## 6. Quick reference — files and tables

| Concern | File / Table |
|---|---|
| Short squeeze alert logic | `services/market-data/src/services/scheduler.py:2605` |
| Gamma unwind alert logic | `services/market-data/src/services/scheduler.py:3197` |
| Pre-breakout alert logic | `services/market-data/src/services/scheduler.py:2915` |
| Watch-revert logic | `services/market-data/src/services/scheduler.py:3473` |
| Compression ("coiling") detector | `services/technical-analysis/src/indicators/trendlines.py`, `services/market-data/src/services/price_compression.py` (dual-ported) |
| Labeled historical dataset builder | `services/market-data/src/backtest/prebreakout_dataset.py` |
| ML price-direction reuse + calibration | `services/market-data/src/services/scheduler.py` (`_fetch_ml_price_direction`, `_build_prebreakout_calibration`) |
| Email bodies | `services/market-data/src/services/email_service.py` |
| Outcome tables | `SqueezeAlertOutcome`, `PreBreakoutAlertOutcome`, `SqueezeWatch` — all in `shared/db/models.py` |
| Admin performance page | `GET /admin/squeeze-alert-performance`, `frontend/src/pages/squeeze-alert-performance.tsx` |
| Admin backtest endpoint | `GET /admin/squeeze-alert-backtest` |
| Live dashboard | `frontend/src/pages/short-squeeze.tsx` |
| Plain screener | `frontend/src/pages/short-selling.tsx` |
| Full build history + test/verification detail | `frontend/src/pages/improvements.tsx` — search for `T264-SHORTSQUEEZE`, `T264-SQUEEZEALERT`, `AUD265` |
