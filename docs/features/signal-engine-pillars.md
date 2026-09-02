## Design Reference: Why a BUY Signal Can Show Low Confidence

**A user asked this directly (2026-07-16)** after seeing a stock (6682.HK) show `AI Signal:
BUY` with only `13% Confidence` — worth documenting since it looks contradictory but is
working as designed, and the same question will come up again for other stocks.

**Confidence and the BUY/SELL/HOLD decision are two entirely independent calculations:**

- **Confidence** = `abs(fused_probability - 0.5) * 200`
  (`services/signal-engine/src/generators/signals.py:2118`, also duplicated at
  `services/signal-engine/src/api/routes.py:556` and `:5666`). This is purely "how far from a
  50/50 coin-flip is the model's probability" — a `fused_probability` of 56% bullish is barely
  above a toss-up, so confidence is mechanically forced to `abs(0.56-0.5)*200 = 12%` no matter
  what else is true about the stock. **Confidence measures conviction in the probability
  estimate itself, not trade quality.**
- **BUY/SELL/HOLD** is decided separately by `_decide_style()`
  (`services/signal-engine/src/generators/signals.py:1556`) — whether that same
  `fused_probability` clears a **threshold** (`buy_threshold`, `_STYLE_PROFILES`, varies by
  style + market regime, e.g. SWING/bull ≈ 0.60-0.63) that can itself be self-tuned over time
  by the watchdog/calibration jobs (see "Tier 85-86" in the tier-history section above —
  `_get_dynamic_buy_threshold()` reads a Redis-cached, empirically-tuned value before falling
  back to the hardcoded default).

**The practical read**: a BUY signal with low confidence means the probability barely cleared
the bar to be called BUY at all — a marginal, low-conviction call, not a strong one. **This is
exactly what the other panels on the stock detail page are for** — they're deliberately more
reliable signals of "should I actually enter" than the top-line BUY/SELL label alone:
- **Confluence Score** (weighted blend of AI signal + K-Score + technical + momentum,
  `frontend/src/lib/confluence.ts`) — a low/"Weak" score with "signals conflict" is a stronger
  real-world signal to heed than the BUY label.
- **Conviction Gate** (`_is_conviction_buy()` in `paper_trading_engine.py`, 7-layer check:
  K-Score, Uptrend, RSI, MACD, OBV, ADX, ML — see the existing Conviction Gate documentation
  elsewhere in this file) — "✗ Gate not met" with multiple failed layers means the paper
  trading engine itself would NOT have entered this position even though the top-line label
  says BUY. The gate exists specifically to catch cases like this one.

**Design invariant**: never treat the top-line AI Signal label (BUY/SELL/HOLD) as sufficient
justification to enter a real position on its own — always cross-check the Confluence Score
and Conviction Gate panels on the same page, which are deliberately independent, stricter
checks that can (and are meant to) disagree with the headline label.

---


## Design Reference: The ↑/↓ Percentage Arrows on the Daily Chart

**A user asked "what's the percentage on the graph like 50%" (2026-07-17)** after seeing small
green ↑ and red ↓ arrows above/below certain candles on the daily chart, each labeled with a
number like `46%`, `47%`, or `50%` — worth documenting since it's easy to confuse with the
sidebar's live `Confidence`/`Bullish` percentages, but it's a completely different, historical
signal.

**What they are**: `frontend/src/components/PriceChart.tsx:353-373` — these arrows mark **AI
Signal transition points** in the SWING horizon's stored signal history, daily timeframe only
(`!isIntraday`, line 353). The code takes every stored `signalMarkers` point, keeps only the
last entry per calendar date (signals fire every 5 min while stable — line 355-359), then
filters down to just the **transitions**: the first day a new signal direction appears,
compared to the previous day's stored signal (line 364,
`sorted.filter((m, i) => i === 0 || m.signal !== sorted[i-1].signal)`). Every day the signal
just *held* its existing direction is skipped — only the day it *flipped* gets a marker.

**What the percentage means**: `text: `${Math.round(m.confidence ?? 0)}%`` (line 370) — the
label is that stored signal's own **confidence at the moment it flipped**, using the exact
same `confidence = abs(fused_probability - 0.5) * 200` formula documented above. It is NOT
today's live confidence (shown separately in the sidebar) — it's a frozen historical value
from whichever day that specific transition happened.

**Visual encoding**: green `arrowUp` below the bar for a flip to BUY, red `arrowDown` above the
bar for a flip to SELL (line 367-369) — color and shape indicate direction, the number
indicates how confident that particular flip was.

**Practical read**: a marker with a low percentage (e.g. a red ↓ at "50%") means the signal
flipped to SELL on that day, but only barely cleared the bar to be called SELL at all —
matching the same "confidence measures conviction in the probability estimate, not trade
quality" caveat as the design reference above. A cluster of low-confidence flip markers close
together often reflects a choppy period where the signal was oscillating near its decision
threshold, not a series of strong directional calls.

**What to check if this looks wrong**: the transition-filtering logic is the only place this
renders — if arrows are missing entirely, confirm `!isIntraday` (daily timeframe only) and
`showSignals` is enabled; if a percentage looks inconsistent with the sidebar's current
reading, that's expected — they're deliberately different values (historical flip-moment vs.
live-today).

---


## Feature Reference: T232-SIG10 — Bearish Pillar Mirror (`bearish_pillars_active`, Built 2026-07-21)

**Context**: this tracker item's own history (2026-07-04) already investigated and rejected
regime-tiered SELL thresholds and a `min_pillars_for_sell` gate as **unsupported by data** —
96%+ of SELL outcome rows are bull-regime only, with near-zero bear/choppy/risk_off samples.
Re-verified live against production Postgres before starting any work (per this file's own
"verify against live state, not a stale investigation date" discipline): the gap is unchanged
— 2,474 bull-regime SELL outcomes vs. 33 unknown vs. **zero** bear/high_vol samples. A genuinely
new finding from this re-check: **BUY's own regime tiers are equally uncalibrated** — 3,176
bull vs. 1 bear vs. 3 unknown outcome rows — meaning BUY's bear/high_vol/unknown thresholds in
`_STYLE_PROFILES` were always hand-set deltas off the bull baseline, never actually fit against
real non-bull outcome data either. This reframes the item: it's not that SELL is missing
infrastructure BUY already validated — neither direction has real non-bull data to calibrate
against yet.

**What was built instead**: the genuinely tractable, non-data-blocked prerequisite —
`bearish_pillars_active` in `services/signal-engine/src/generators/signals.py`'s `_ta_score()`,
mirroring the existing bullish TREND/MOMENTUM/VOLUME/STRUCTURE pillar architecture (SA-19/SA-30)
with each pillar's own bearish-specific conditions, not a naive `1 - bullish_score` (which would
just restate the bullish pillar and score a merely-neutral stock as equally bearish):
- **TREND**: death cross / supertrend cross-down analog — golden cross or supertrend cross-up
  is a hard override to 0.0 (mirrors the bullish pillar's death-cross/cross-down override to 0.0).
- **MOMENTUM**: RSI in a bearish sweet spot (35-55) vs. mild overbought (55-65) vs. mild oversold
  (28-35, still a valid warning) vs. zeroed at extreme oversold (<=28, bounce territory, not
  confirmation to sell) — same "meaningful zone, not just inverse of bullish" structure as the
  bullish pillar's RSI scoring. MACD histogram negative-and-expanding-down, a new
  `macd_zero_cross_down` (mirrors the existing `macd_zero_cross_up`). Stochastic RSI overbought
  (bearish reversal) or a new `stoch_rsi_cross_down` (mirrors `stoch_rsi_cross_up`), zeroed when
  oversold (bullish reversal territory).
- **VOLUME**: OBV trend bearish + volume expansion together = full conviction (distribution, not
  accumulation); either alone = partial. Exact AND-logic mirror of the bullish volume pillar.
- **STRUCTURE**: below VWAP + BB%B pinned near the **lower** band specifically. New `bearish_trend`
  boolean (`di_minus > di_plus` when trending) added as the ADX-direction mirror of the existing
  `bullish_trend`.

**Deliberately NOT wired into any live gate, compression, or threshold** — this is pure
observability written into `Signal.reasons` (already a flexible JSON column, no schema change
needed) so real bearish-evidence data starts accumulating from today. A future calibration pass
needs both (a) enough non-bull-regime SELL outcomes AND (b) enough `bearish_pillars_active`
history to validate a real `min_pillars_for_sell` gate against — building the gate now, before
either exists, would repeat the exact "overfit argmax on thin data" mistake already documented
at T232-OC3. `SignalOutcome` does not yet have a `bearish_pillars_active` column (unlike
`market_regime`, which IS copied from `Signal.reasons` at outcome-evaluation time) — deliberately
deferred until there's a real calibration pass ready to consume it; adding the column now would
be schema infrastructure ahead of validated need.

**A real bug caught and fixed during development, before it could ship**: the first version of
the bearish structure sub-score used `bb_bear_score = 0.8 if not (0.2 < bb_pct_b < 0.8) else 0.0`
— treating BB%B outside the neutral band as bearish evidence on **either** extreme. But a %B near
1.0 (upper-band extreme) is what a steady, healthy uptrend produces — a bullish extreme, not
bearish evidence. A synthetic strong-uptrend fixture (clean, low-noise, `trend=0.5`) scored
`bearish_pillar_structure=0.65` and `bearish_pillars_active=4` (all 4 pillars) purely from this
bug, despite every other pillar correctly reading bullish. Caught by manually tracing the
sub-scores for a case that looked wrong, not by the test suite (which was written after the fix).
Fixed by restricting `bb_bear_score` to the **lower**-band extreme only (`bb_pct_b <= 0.2`),
mirroring the bullish pillar's own asymmetric structure (its `bb_score` only rewards the neutral
band, `0.2 < bb_pct_b < 0.8`, not either extreme).

**Tests**: `services/signal-engine/tests/test_bearish_pillars.py`, 12 cases — a new file rather
than extending `test_signal_generator.py`, which has a pre-existing, unrelated `ImportError`
(`_decide` no longer exists in `signals.py`, likely renamed to `_decide_style` at some point)
that blocks that file's collection entirely; confirmed via `git stash` that this failure
pre-dates this session's changes. Covers: all new `reasons` keys present, `bearish_pillars_active`
correctly derived as a count of sub-scores `>= 0.5`, all 4 sub-scores in `[0, 1]`, a strong clean
uptrend scores <=1 bearish pillars active (the exact property the bb_bear_score bug violated),
a strong downtrend has more bearish than bullish pillars active and vice versa for uptrends, the
golden-cross/supertrend-cross-up hard override to 0.0, and short-data robustness. Adversarially
verified: reverted the `bb_bear_score` fix and confirmed
`test_bearish_pillar_structure_not_triggered_by_upper_band_extreme` failed correctly
(`0.65 < 0.5` assertion) before restoring it.

**Verification**: 12/12 new tests pass; full signal-engine suite green modulo the 2 pre-existing,
unrelated failure groups already documented elsewhere in this file
(`test_signal_generator.py`'s `_decide` import error, 4 `test_analyst_momentum.py` failures) —
confirmed via `git stash` that both pre-date this change. Frontend typecheck clean (only
`improvements.tsx`'s tracker-entry text was touched on the frontend side).

**What to check if this looks wrong**:
```bash
docker exec stockai-signal-engine-1 python3 -c "
import sys; sys.path.insert(0, '/app'); sys.path.insert(0, '/app/src')
from generators.signals import _ta_score
import pandas as pd, numpy as np
# feed a real symbol's recent price DataFrame here to inspect bearish_pillars_active live
"
# Or check a real Signal row's reasons JSON directly:
docker exec stockai-postgres-1 psql -U stockai -d stockai -c \
  "SELECT symbol, reasons->'bearish_pillars_active', reasons->'independent_pillars_active' FROM signals ORDER BY ts DESC LIMIT 10;"
```
If `bearish_pillars_active` looks implausible for a specific stock (e.g. 4/4 on an obviously
strong uptrend), re-run the exact regression check this bug was caught with: confirm `bb_pct_b`
isn't near the upper-band extreme (>0.8) while `bearish_pillar_structure` still reads >= 0.5 —
that specific combination is the bug class this fix closed.

---


## Feature Reference: T232-SIG10-SELLGATE — Symmetric, Data-Validated SELL Pillar Gate (Built 2026-07-31)

**Closes the gap this tracker item's own 2026-07-21 build deliberately left open**:
`bearish_pillars_active` (a 0-4 count of independent bearish TA sub-scores, mirroring the
LIVE `independent_pillars_active` bullish gate) was computed into `Signal.reasons` starting
2026-07-21, explicitly as pure telemetry — "not yet wired into any live gate/compression,"
per that session's own comment, because there wasn't yet enough non-bull-regime SELL outcome
data to fit a real gate against. This session re-verified that finding is STILL true (a live
production query confirmed 3,063 bull-regime SELL outcomes vs. 57 unknown vs. **zero**
bear/high_vol/choppy/risk_off samples, unchanged from the 2026-07-20 count) — regime-tiered
SELL thresholds remain unjustifiable by data. But the user explicitly asked to "research it
more and see what's the best solution... I want the best buy and sell signals," which led to
a real, previously-undiscovered finding that reframed the whole approach.

### The real finding: `bearish_pillars_active` was NOT accumulating usable history at all

A dedicated research pass discovered `Signal.reasons` is silently overwritten on every
refresh — `signals` is upsert-per-`(stock_id, horizon, day)` (a real, existing unique
constraint), so a signal's `reasons` JSON reflects only its MOST RECENT computation, never a
point-in-time snapshot. Live-verified directly against production: of **3,120 resolved SELL
`SignalOutcome` rows**, only **70** still carried `bearish_pillars_active` (SHORT 34/835,
SWING 20/832, LONG 0/786, GROWTH 16/667) — all dated within days of the field first shipping.
Waiting longer would NOT have fixed this: older rows keep losing the field on their very next
refresh, and LONG (rarely refreshed) would need months to reach a usable sample regardless.
**A live gate built on "wait for outcomes to accumulate naturally" was never going to reach
statistical power.**

### The fix: deterministic backfill from stored price history, not waiting

`_ta_score(df)` (the function that computes `bearish_pillars_active`) is a **pure function of
one OHLCV DataFrame** — verified directly: every bearish-pillar input (`death_cross_event`,
`di_minus`/`di_plus`, `macd_line`, `k_smooth`, `obv_trend_bullish`, `bb_pct_b`, `vol_z`,
`price_above_vwap` — itself a rolling VWMA, not intraday VWAP, so also OHLCV-only) derives
from `close`/`high`/`low`/`volume` alone, nothing live/Redis/network-dependent. This makes it
fully reproducible for ANY historical date with enough prior daily bars on file (this app's
`Price` table for `TimeFrame.D1` goes back to 2023-04-21 — 113,767 rows, far more than needed).

**New backfill function**: `_backfill_bearish_pillars_for_stock()` +
`POST /signals/backfill_bearish_pillars` (`services/signal-engine/src/api/outcomes.py`) —
for each stock with resolved SELL outcomes still missing the field, one bulk `Price` fetch
(not one query per row), then for each of that stock's own `signal_date`s, slices the
DataFrame to `Price.ts <= that date` (point-in-time correctness — **never** leaks a later bar
into an earlier date's computation, the exact class of look-ahead bias `SE-F2` already cost
this repo a 3,808-row rebuild over) and calls the real `_ta_score()` to recompute the value
fresh. Requires `_BACKFILL_MIN_BARS = 220` bars of prior history (SMA200 plus warmup buffer)
before attempting a date — a date with insufficient prior history is skipped, not scored as 0.

**New column**: `SignalOutcome.bearish_pillars_active` (`shared/db/models.py`) — an existing,
already-populated table, so a manual `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` was added to
`shared/db/session.py`'s migration function (per this file's own standing `create_all()`-gap
invariant). `NULL` means "not yet backfilled" — never silently treated as "0 pillars."

### A real train/validation sweep before any live gate change — no unvalidated symmetry assumed

**New endpoint**: `POST /signals/tune_sell_pillars` (`services/signal-engine/src/api/
calibration.py`) — mirrors `outcomes_calibrate_apply`'s exact discipline (chronological 70/30
train/validation split, per-slice `min_samples` floor checked both ways, candidate must beat
the CURRENT LIVE baseline — `min_pillars_for_sell=0`, i.e. no gate at all, the real un-tuned
production state — on the validation slice's own never-searched EV, unconditional rejection of
non-positive lift, one `TuneHistory` row per horizon per attempt regardless of outcome).
Sweeps `min_pillars_for_sell` over 1-4 per horizon and only writes to Redis
(`stockai:style_tune:{H}:min_pillars_for_sell`, the SAME generic key
`_get_style_tuned_param()` already knows how to read — zero new read-side code needed for the
key itself) if a candidate value shows a genuine, validated improvement.

**A real EV-sign gotcha, caught and correctly handled**: `SignalOutcome.pct_return` is stored
UNSIGNED by direction (`(exit - entry) / entry`, a raw price change) — a SELL "wins" when
`pct_return` is NEGATIVE (confirmed directly: `is_correct = ret < -hurdle` for SELL). The
sweep's EV metric is therefore `-mean(pct_return)`, matching the EXACT convention the sibling
SELL-threshold sweep inside `outcomes_calibrate_apply` already uses for the identical reason
(`rets = [-o.pct_return for o in sub ...]`) — copied verbatim from that established precedent,
not re-derived from scratch.

**Deliberately does NOT reuse `min_pillars_for_buy`'s own values (2 default, 3 for SWING/
LONG)** — the bearish pillar sub-scores are NOT calibrated to the same base rate as the
bullish ones (e.g. `pb_volume` gives 0.6 for `not obv_trend_bullish` ALONE — a much weaker bar
than the bullish side's own AND-logic requiring BOTH OBV and volume expansion together).
Assuming symmetry would repeat the exact "overfit argmax on an unvalidated assumption"
mistake already documented at `T232-OC3`. The sweep tries all of 1-4 and lets the validation
slice decide — nothing is assumed.

### The live gate itself — symmetric structure, regime-agnostic, off by default

`services/signal-engine/src/generators/signals.py`'s `_apply_style_signal()` gained a new
"symmetric SELL-side pillar gate" block, placed directly after the existing BUY pillar gate
(SA-19/SA-30) it mirrors: applies ONLY to `fused < 0.5` (the exact opposite restriction of
`T232-SIG3`'s own comment on the bullish gate, which applies ONLY to `fused > 0.5` — a deeply
bullish stock naturally has 0-1 bearish pillars by definition, so this must never touch BUY
candidates, just as the bullish gate must never touch SELL candidates). Reads
`min_pillars_for_sell` via the ALREADY-GENERIC `_get_style_tuned_param(style_key,
"min_pillars_for_sell", 0)` — **defaults to 0 (no gate at all) until `tune_sell_pillars` has
found and validated a real, positive-EV-lift value for that specific style**. This means the
gate is a complete, verified no-op in production the moment it deploys — it can only ever
start compressing SELL signals once a real backtest has proven doing so improves outcomes,
never as an assumption. Missing/`None` `bearish_pillars_active` (a BUY-only signal, or a
computation failure) fails open to 0 bearish pillars, matching the neutral un-gated state —
never silently treated as the worst case.

### Tests

`services/signal-engine/tests/test_sell_pillar_gate.py` (19 cases) — the live gate itself is
tested directly (`signals.py` imports cleanly via `conftest.py`'s stubbing, matching
`test_hot_news_gate.py`'s established convention): the un-tuned default is a genuine no-op
regardless of bearish-pillar count, the gate compresses below the validated minimum and is
inert at/above it, the `fused < 0.5`-only restriction (mirroring `T232-SIG3` in reverse), a
missing key fails open to 0 (never the worst case), and the compression ratio matches the
BUY gate's own documented ×0.70 for the below-minimum case. The backfill/sweep endpoints live
in `outcomes.py`/`calibration.py`, which need `common.jwt_auth` and can't be imported directly
in this test environment — covered via source-text regression checks matching this repo's
established pattern for that exact constraint: the point-in-time-correctness filter, the
minimum-bar-count guard, per-stock batching (not per-row), the negated-EV sign convention, the
chronological (not random) train/validation split, the unconditional non-positive-lift
rejection, the generic Redis key the read side already knows how to consume, and
`_record_tune_history()` being called on every branch including skips.

**Adversarial verification** — 5 sabotage cycles, all caught and reverted: removing the
`fused < 0.5` restriction on the live gate (caught by the dedicated BUY-side-inertness test);
defaulting `min_pillars_for_sell` to a nonzero value instead of 0 (caught by 2 tests — the
no-gate-by-default test and the missing-key-fails-open test); removing the point-in-time
`Price.ts <= sd` filter in the backfill helper (caught directly); removing the `-o.pct_return`
negation in the sweep's EV metric (caught directly, reproducing the exact sign-inversion
gotcha this fix was built to avoid); disabling the non-positive-EV-lift rejection entirely
(caught directly). Full 142-in-scope-test signal-engine suite green (up from 123, excluding
the 2 pre-existing, unrelated failure groups already documented elsewhere in this file —
`test_signal_generator.py`'s `_decide` import-collection error and 4
`test_analyst_momentum.py` failures, both confirmed via `git stash` to predate this change).
`pyflakes` clean on all 3 touched files (confirmed via `git stash` that both pre-existing
warnings — `signals.py`'s unused `macd_line`, `outcomes.py`'s unused `httpx` import — predate
this change).

**What to check if this looks wrong**:
```bash
# Confirm the new column exists and check backfill progress:
docker exec stockai-postgres-1 psql -U stockai -d stockai -c \
  "SELECT horizon, COUNT(*) AS resolved_sell, COUNT(*) FILTER (WHERE bearish_pillars_active IS NOT NULL) AS backfilled
   FROM signal_outcomes WHERE signal_direction='SELL' AND is_correct IS NOT NULL GROUP BY horizon;"

# Trigger the backfill (safe, idempotent — only touches rows still missing the field):
docker exec stockai-signal-engine-1 curl -s -X POST 'http://localhost:8005/signals/backfill_bearish_pillars?limit=5000' \
  -H "Authorization: Bearer <token>"

# Run the sweep (needs the backfill to have run first):
docker exec stockai-signal-engine-1 curl -s -X POST 'http://localhost:8005/signals/tune_sell_pillars' \
  -H "Authorization: Bearer <token>"

# Check whether any horizon got a real, validated gate applied:
docker exec stockai-redis-1 redis-cli keys 'stockai:style_tune:*:min_pillars_for_sell'

# Check tune_history rows this mechanism wrote (promoted or not):
docker exec stockai-postgres-1 psql -U stockai -d stockai -c \
  "SELECT style, old_value, new_value, promoted, gate_failures FROM tune_history WHERE parameter_name='min_pillars_for_sell' ORDER BY ts DESC LIMIT 10;"
```
If `tune_sell_pillars` skips every horizon with `insufficient_total_samples`, that means the
backfill hasn't been run yet (or hasn't found enough historical price data for enough
symbols) — check the backfill's own `remaining_unbackfilled_sell_outcomes` count first before
assuming the sweep itself is broken.

---

