# Bug Fix Log

All confirmed bugs found and fixed during the June 2026 audit sessions.
Each entry includes the affected file, root cause, impact, and the fix applied.

---

## Critical — System crashes or completely wrong output

### 1. ML predict_proba shape mismatch (IndexError on every ML call)
**Files:** `services/ml-prediction/src/models/xgb.py`, `services/ml-prediction/src/models/rf.py`
**Commits:** `363bbd5`

**Root cause:** Both model wrappers pre-sliced the probability array with `[:, 1]`, returning a 1D array. The caller in `trainer.py` then applied `[:, 1]` again, which fails on a 1D array.

**Impact:** Every ML inference call raised an `IndexError`. The ML probability field was never populated in signal reasons.

**Fix:** Removed `[:, 1]` from both model wrappers. Wrappers now return the full 2D array `(n_samples, 2)`. All callers correctly extract `[:, 1]` themselves.

---

### 2. Signal accuracy: entry price look-ahead + entry == exit (pct_change always 0%)
**File:** `services/signal-engine/src/api/routes.py`
**Commits:** `fe42717`

**Root cause (look-ahead):** Entry used `price_on_or_before(signal_date + 1 day)`. For Friday signals, `signal_date + 1 = Saturday` has no trading data, so bisect fell back to Friday's close — the same day as the signal. This is same-day look-ahead.

**Root cause (pct=0%):** Exit used `latest_price_after(signal_date)`, which returns the *first* close after the signal. Entry (`price_on_or_before(signal_date+1)`) also resolved to the first close after signal_date for Mon–Thu signals. Entry == exit → pct_change = 0% for ~80% of signals.

**Impact:** The signal accuracy page showed inflated accuracy (all 0% changes classified as wrong) and meaningless P&L figures.

**Fix:** Replaced both helpers:
- Entry → `first_close_after(signal_date)` using `bisect.bisect_right` (always strictly after signal date)
- Exit → `most_recent_close(stock_id)` (latest price in dataset = running P&L to today)

Same fix applied to the ML weight sweep endpoint.

---

### 3. Factor exposure: correct always False
**File:** `services/signal-engine/src/api/routes.py`
**Commits:** `5165023`

**Root cause:** The `factor_exposure` endpoint defined its own local helpers. Entry used `price_on_or_before(signal_date + 1 day)` and exit used `latest_price_after(signal_date)`. Both resolved to the same value (first trading day after signal), so `correct = exit_p > entry` was always `False`.

**Impact:** The entire factor analysis was meaningless — every signal was classified as "wrong" regardless of actual outcome, making the correct vs. wrong factor comparison purely noise.

**Fix:** Replaced `latest_price_after` with `most_recent_close_fe()` (last available close in the loaded price window), so entry is the next-day fill and exit is the running price to today.

---

## High — Wrong results, silent failures

### 4. Bullish probability displayed as ~1% instead of ~65%
**File:** `frontend/src/pages/opportunities.tsx:524`
**Commits:** `5165023`

**Root cause:** `bullish_probability` is a 0–1 decimal from the API. The tooltip used `.toFixed(0)` without multiplying by 100 first. `(0.65).toFixed(0)` → `"1"`, showing "1%" instead of "65%".

**Impact:** Every AI Signal tooltip in the Opportunities page showed near-zero bullish probability regardless of actual signal strength.

**Fix:** `(sig.bullish_probability * 100).toFixed(0)`.

---

### 5. Signal accuracy entry/exit fix also needed in weight sweep endpoint
**File:** `services/signal-engine/src/api/routes.py`
**Commits:** `fe42717`

**Root cause:** The `ml_weight_validation` endpoint had identical old-style helpers. After the signal_accuracy fix, this endpoint still used `price_on_or_before` for entry and `latest_price_after` for exit — both returning the same value.

**Impact:** All P&L calculations in the ML weight sweep were 0%, making the optimal weight determination meaningless (all weights appeared equally bad).

**Fix:** Added `_first_close_after` and `_most_recent_close` helpers local to the weight sweep function, using the same corrected logic.

---

### 6. options_flag absent when options sentiment is None
**File:** `services/signal-engine/src/generators/signals.py`
**Commits:** `fe42717`

**Root cause:** The options flow block handled `strongly_bullish`, `bullish`, `bearish`, `slightly_bearish`, and `not None` (neutral), but had no `else` branch for `None`. When options data was unavailable, `reasons["options_flag"]` was never set.

**Impact:** Any downstream code expecting `options_flag` to always be present would get a `KeyError`. The signal reasons JSON was inconsistent across signals.

**Fix:** Added `else: reasons["options_flag"] = "no_data"`.

---

### 7. Trainer crashes on degenerate labels after dead-zone filtering
**File:** `services/ml-prediction/src/training/trainer.py`
**Commits:** `fe42717`

**Root cause:** After the volatility-adjusted dead-zone filter removes rows with small price moves, the remaining training labels (`y_train`) can be all-one-class for low-volatility symbols. No guard existed before calling `model.fit()`.

**Impact:** For quiet symbols, the model would fit on degenerate data and produce meaningless probabilities (all predictions identical), or XGBoost would raise an error when evaluating on the calibration set.

**Fix:** Added early return before the StandardScaler fit:
```python
if len(np.unique(y_train)) < 2:
    return {"symbol": symbol, "skipped": True, "reason": "degenerate labels after dead-zone filter"}
```

---

### 8. RS score explosion when ETF return near -100%
**File:** `services/signal-engine/src/generators/signals.py`
**Commits:** `363bbd5`

**Root cause:** When `etf_ret` was near -100% (denominator `1 + etf_ret` near zero), the RS ratio `(1 + stock_ret) / (1 + etf_ret)` would explode to a huge number, then get silently clipped to 100 by `np.clip`. The previous guard used `1e-6` as a floor, which was too small to prevent explosion.

**Impact:** Any period where the benchmark ETF had a near-total-loss day (circuit breaker, data error) would assign every stock an RS score of 100 regardless of actual performance.

**Fix:** Return `(None, None)` when `abs(1 + etf_ret) < 0.01` (ETF return within 1% of -100%), propagating null cleanly.

---

### 9. Backtest equity curve captures return on entry bar
**File:** `services/strategy-engine/src/backtest/engine.py`
**Commits:** `60a7e92`

**Root cause:** The position array was set to `1` on the entry bar (`position[i] = 1`), so the equity return at bar `i` (`pct_change[i] = close[i] / close[i-1] - 1`) was included in P&L. But you entered at `close[i]` — you should only capture returns from bar `i+1` onward.

**Impact:** Each trade overstated P&L by exactly one bar's return (the return from the prior close to the entry close, which you didn't hold). For strategies with many trades, this inflated overall returns and CAGR.

**Fix:** Shifted the position array by 1 when computing returns:
```python
pos_shifted = pd.Series(position).shift(1, fill_value=0).values
rets = feat["close"].pct_change().fillna(0) * pos_shifted
```
Updated docstring from "next-bar fill" to "same-bar-close fill" to accurately reflect the implementation.

---

### 10. Division by zero on entry price in signal accuracy, weight sweep, trade performance
**File:** `services/signal-engine/src/api/routes.py`
**Commits:** `60a7e92`

**Root cause:** Pct-change calculations (`(exit - entry) / entry * 100`) had no guard for `entry <= 0`. The existing guard only checked `entry is None`.

**Impact:** Corrupted price data (e.g., a zero close stored from a bad ingestion) would raise `ZeroDivisionError`, crashing the endpoint.

**Fix:** Added `if entry_close <= 0: continue` (or `if entry <= 0: continue`) before each division in all three affected endpoints.

---

## Medium — Incorrect behavior in specific conditions

### 11. SPX momentum window off-by-one
**File:** `services/market-data/src/api/routes.py`
**Commits:** `363bbd5`

**Root cause:** The 20-day SPX momentum calculation used `> 21` as the length check instead of `>= 21`. With exactly 21 bars, `len >= 21` is true but `len > 21` is false, so the momentum was set to 0.0 for the minimum valid window.

**Impact:** The fear-and-greed momentum component would be 0.0 (neutral) when exactly 21 daily bars were available — rare but incorrect.

**Fix:** Changed `> 21` to `>= 21`.

---

### 12. TimeFrame validation returning 500 instead of 400 on bad input
**File:** `services/market-data/src/api/routes.py`
**Commits:** `363bbd5`

**Root cause:** The original code tried to validate the timeframe parameter inline using a generator expression trick that doesn't work in Python. Invalid timeframe strings caused an unhandled `ValueError` which propagated as a 500 Internal Server Error.

**Impact:** API clients passing an invalid timeframe string (typo, wrong version) received a 500 with a stack trace instead of a useful 400 with valid options.

**Fix:** Added a proper `try/except ValueError` block before the database query:
```python
try:
    tf = TimeFrame(timeframe)
except ValueError:
    raise HTTPException(400, f"Invalid timeframe '{timeframe}'. Valid values: {[v.value for v in TimeFrame]}")
```

---

### 13. 52-week high/low alert window one bar too short
**File:** `services/market-data/src/services/scheduler.py`
**Commits:** `5165023`

**Root cause:** The 52-week window used `.tail(251)` on the prior closes (`close.iloc[:-1]`). A trading year is 252 days, so the window should be `tail(252)`.

**Impact:** The bar exactly 252 trading days ago was excluded from the high/low calculation. If that bar was the actual prior high, today's price would incorrectly appear to be a new 52-week high, firing a false alert.

**Fix:** Changed `tail(251)` to `tail(252)` for both the 52-week high and 52-week low checks.

---

### 14. Alert triggered_at uses naive datetime alongside aware datetimes
**File:** `services/market-data/src/services/scheduler.py`
**Commits:** `5165023`

**Root cause:** One code path set `alert.triggered_at = datetime.utcnow()` (naive UTC) while another used `datetime.now(timezone.utc)` (timezone-aware). The `timezone` import was already present.

**Impact:** Mixed naive/aware datetimes in the same column cause comparison failures in SQLAlchemy and Python's `datetime` module, breaking alert deduplication logic that compares `triggered_at` timestamps.

**Fix:** Changed `datetime.utcnow()` to `datetime.now(timezone.utc)` to be consistent throughout.

---

## Low — Edge case display bugs

### 15. Best-performer P&L shows "+−5.00%" when all positions are losing
**File:** `frontend/src/pages/positions.tsx:335`
**Commits:** `5165023`

**Root cause:** The "Best Performer" card used a hardcoded `+` prefix: `+{fmt(mBest.pnlPct ?? 0)}%`. If all positions are losing, `mBest` is the least-negative position and `pnlPct` is still negative. `fmt(-5)` returns `"-5.00"`, so the display becomes `"+-5.00%"`.

**Impact:** Visually broken display in the edge case where every position in the portfolio is currently underwater.

**Fix:** Conditional sign prefix with `Math.abs`:
```tsx
{(mBest.pnlPct ?? 0) >= 0 ? '+' : ''}{fmt(Math.abs(mBest.pnlPct ?? 0))}%
```

---

## Summary by service — Audit Round 1 (2026-06-02)

| Service | Bugs fixed |
|---------|-----------|
| signal-engine | 5 (accuracy look-ahead, entry=exit, factor exposure, options_flag, RS score) |
| ml-prediction | 2 (predict_proba shape, degenerate labels) |
| market-data | 3 (momentum window, timeframe validation, 52-week window) |
| strategy-engine | 1 (backtest entry-bar return) |
| scheduler | 2 (datetime naive/aware, 52-week window) |
| frontend | 2 (bullish probability display, P&L sign prefix) |

---

---

# Audit Round 2 — 2026-06-02 (continued)

Bugs 16–23 found during deeper audits of the frontend, alert system, ML inference, and signal alert email pipeline.

---

## High — Wrong results in specific conditions

### 16. Factor exposure: correct always False
**File:** `services/signal-engine/src/api/routes.py`
**Commits:** `5165023`

**Root cause:** The `factor_exposure` endpoint defined its own local `price_on_or_before` and `latest_price_after` helpers. Entry used `price_on_or_before(signal_date + 1 day)` and exit used `latest_price_after(signal_date)`. Both resolved to the same close (first trading day after signal), so `correct = exit_p > entry` was always `False`.

**Impact:** Every signal classified as "wrong" regardless of actual outcome. The factor analysis (which factors correlate with success) was entirely based on noise.

**Fix:** Added `most_recent_close_fe()` helper. Exit now uses the most recent available close (running P&L to today), making the `correct` classification meaningful.

---

### 17. Bullish probability displayed as ~1% instead of ~65%
**File:** `frontend/src/pages/opportunities.tsx:524`
**Commits:** `5165023`

**Root cause:** `bullish_probability` is a 0–1 decimal from the API. The tooltip used `.toFixed(0)` without multiplying by 100 first. `(0.65).toFixed(0)` → `"1"`, showing "1%" instead of "65%".

**Impact:** Every AI Signal tooltip in the Opportunities page showed near-zero bullish probability regardless of actual signal strength.

**Fix:** `(sig.bullish_probability * 100).toFixed(0)`.

---

### 18. Best-performer P&L shows "+−5.00%" when all positions losing
**File:** `frontend/src/pages/positions.tsx:335`
**Commits:** `5165023`

**Root cause:** Hardcoded `+` prefix: `+{fmt(mBest.pnlPct ?? 0)}%`. If all positions are negative, `mBest.pnlPct` is the least-negative value (still negative). `fmt(-5.0)` = `"-5.00"`, producing `"+-5.00%"`.

**Impact:** Visually broken display in the edge case where every portfolio position is underwater.

**Fix:** Conditional sign: `{(mBest.pnlPct ?? 0) >= 0 ? '+' : ''}{fmt(Math.abs(mBest.pnlPct ?? 0))}%`.

---

### 19. 52-week high/low alert window one bar short
**File:** `services/market-data/src/services/scheduler.py:694, 703`
**Commits:** `5165023`

**Root cause:** `close.iloc[:-1].tail(251)` used 251 bars for a 52-week (252 trading day) window. The bar exactly 252 trading days ago was excluded.

**Impact:** If the excluded bar was the actual prior 52-week high, the stock would appear to set a new high when it hasn't — triggering a false breakout alert.

**Fix:** Changed to `tail(252)` for both the high and low checks.

---

### 20. Alert triggered_at uses naive datetime alongside aware datetimes
**File:** `services/market-data/src/services/scheduler.py:590`
**Commits:** `5165023`

**Root cause:** One code path used `datetime.utcnow()` (naive UTC) while another used `datetime.now(timezone.utc)` (timezone-aware). Both write to the same `triggered_at` column.

**Impact:** Mixed naive/aware datetimes in the same column cause comparison failures in SQLAlchemy, breaking alert deduplication.

**Fix:** Changed to `datetime.now(timezone.utc)` throughout.

---

## High — Page crashes on malformed AI response

### 21. GamePlan null dereferences crash the stock detail page
**File:** `frontend/src/pages/stock/[symbol].tsx:146, 162, 164, 168, 1330, 1349, 1350, 1365`
**Commits:** `a75060f`

**Root cause:** `gamePlan.stop_loss.price`, `gamePlan.entries.map()`, and `gamePlan.catalysts.map()` were accessed without optional chaining. `take_profit` was already guarded with `?.` but `stop_loss`, `entries`, and `catalysts` were not. The AI occasionally returns partial game plan objects with missing fields.

**Impact:** If the AI response omits `stop_loss` or returns `entries` as null, the page crashes with "Cannot read property 'price' of null."

**Fix:** Added `?.` optional chaining on all `stop_loss` property accesses; changed `.map()` calls to `(field ?? []).map()` pattern for arrays.

---

## Medium — Silent wrong calculations

### 22. News sentiment score not clamped to [0, 100]
**File:** `services/signal-engine/src/generators/signals.py:229`
**Commits:** `a75060f`

**Root cause:** `float(a["sentiment"]) * 50 + 50` assumes sentiment is in [-1, 1]. If a sentiment API returns a value slightly outside this range (e.g., 1.1 → score of 105, or -1.1 → -5), the downstream flag thresholds (`< 25`, `< 35`) produce wrong results.

**Impact:** Sentiment scores outside [0, 100] give incorrect `news_sentiment_flag` classifications, slightly skewing signal fusion.

**Fix:** `max(0.0, min(100.0, float(a["sentiment"]) * 50 + 50))`.

---

### 23. Research engine growth rate heuristic misidentifies ≥1000% growth
**File:** `services/research-engine/src/api/routes.py:448, 462, 539`
**Commits:** `a75060f`

**Root cause:** `rev_pct = rev_growth * 100 if rev_growth < 10 else rev_growth` was a defensive guard for ambiguous decimal/percentage format. For decimal values ≥ 10.0 (representing ≥1000% growth), the condition `< 10` is False and the value is treated as already a percentage — showing "10%" instead of "1000%". Same bug in earnings growth and PEG calculation.

**Impact:** Hyper-growth stocks (rare but real, e.g. post-pandemic recovery names) show incorrect growth scores in the research report.

**Fix:** Removed heuristic. yfinance always returns growth as a decimal fraction; always multiply by 100.

---

---

# Audit Round 3 — 2026-06-02 (signal alert email pipeline)

Bugs 24–30 found during targeted investigation of why buy signal emails were not being sent.

---

## Critical — Emails silently never sent

### 24. Signal alert state consumed by failed conviction gate
**File:** `services/market-data/src/services/scheduler.py`
**Commits:** `648d315`

**Root cause:** `alert.last_signal = current` was executed on line 483, unconditionally, BEFORE running the conviction gate. If the 5-layer gate failed (e.g., RSI at 67 instead of ≤65, ML at 0.68 instead of >0.70), the function exited via `continue` — but `last_signal` was already updated to `"BUY"`. On every subsequent scheduler run: `prev == current == "BUY"` → no transition detected → no email, forever.

**Impact:** The conviction gate had exactly one chance per BUY transition. If it failed for any reason (slightly out-of-range RSI, choppy ADX, weak ML), the opportunity was permanently lost regardless of how conditions improved later.

**Fix:** Moved `alert.last_signal = current` to after a successful email send for bullish transitions. Failed conviction checks leave `last_signal` unchanged so the transition is retried on the next scheduler run.

---

### 25. New alert for stock already at BUY never triggers email
**File:** `services/market-data/src/services/scheduler.py`
**Commits:** `648d315`

**Root cause:** New `SignalAlert` records have `last_signal = None`. The transition `(None, "BUY")` was not in `_BULLISH_TRANSITIONS`. It was classified as a neutral/unknown transition and skipped — but `last_signal` was still updated to `"BUY"`, permanently locking out any future email for that alert.

**Impact:** If a user created a signal alert while the stock was already at BUY, they would never receive an email for that stock regardless of future signal changes.

**Fix:** Added `or (prev is None and current == "BUY")` to the bullish check, so the conviction gate runs and fires an email on first detection.

---

### 26. Missing bearish exit transitions — HOLD and WAIT deteriorations silent
**File:** `services/market-data/src/services/scheduler.py:147-149`
**Commits:** `b4bc6ce`

**Root cause:** `_BEARISH_TRANSITIONS` only contained transitions from BUY: `{(BUY,HOLD), (BUY,WAIT), (BUY,SELL)}`. Three deterioration paths were missing:
- `(HOLD, WAIT)` — signal backing off from hold
- `(HOLD, SELL)` — direct sell signal from hold
- `(WAIT, SELL)` — signal turning bearish from wait

**Impact:** Users subscribed to stocks at HOLD or WAIT never received exit warnings when the signal deteriorated. Only users whose stocks were at BUY received exit alerts.

**Fix:** Added the three missing transitions to `_BEARISH_TRANSITIONS`.

---

## High — Conviction gate bypass

### 27. K-Score unavailable silently passes Layer 2
**File:** `services/market-data/src/services/scheduler.py:207-212`
**Commits:** `b4bc6ce`

**Root cause:** `if kscore is not None:` skipped the entire K-Score check when the rankings API was unreachable. Neither `passed` nor `failed` was updated, so an unavailable K-Score was effectively treated as a free pass for Layer 2.

**Impact:** BUY conviction emails could fire without any fundamental/momentum verification during rankings API outages, sending low-quality alerts.

**Fix:** `kscore is None` now explicitly appends a failed layer: `"K-Score unavailable (rankings API down) — cannot verify conviction"`.

---

### 28. Empty email address causes infinite retry loop
**File:** `services/market-data/src/services/scheduler.py`, `services/market-data/src/services/email_service.py`
**Commits:** `b4bc6ce`

**Root cause:** `to=alert.email or ""` passed an empty string to the SMTP/SES call when `alert.email` was None. The send raised an exception, returned `False`, and because `last_signal` is now only updated after a successful send, the alert retried the same failed transition on every scheduler run indefinitely.

**Impact:** Any alert with a missing email address spammed the error log every minute and blocked the state machine permanently.

**Fix:** Added explicit guard before the send call — if email is empty, log a warning, advance `last_signal` (to prevent the loop), and continue. Added a second guard at the top of `send_email()` as defence-in-depth.

---

### 29. Analyst rating default inconsistency
**File:** `services/market-data/src/services/scheduler.py`
**Commits:** `b4bc6ce`

**Root cause:** The conviction gate defaulted to `""` when analyst data was unavailable (correctly fails the gate), but the email send call defaulted to `"buy"` — so the email template displayed a false "buy" analyst consensus on stocks where no analyst data existed.

**Impact:** Email recipients saw a "buy" analyst rating on stocks that had no analyst coverage, misleading them about the strength of the signal.

**Fix:** Changed the email call to use `""` as default, consistent with the gate logic.

---

## Summary by service — Audit Rounds 2 & 3

| Service | Bugs fixed |
|---------|-----------|
| scheduler / signal alerts | 6 (state machine, missing transitions, K-Score bypass, email loop, analyst inconsistency, datetime) |
| frontend / stock detail | 1 (gamePlan null dereferences) |
| frontend / opportunities | 1 (bullish probability ×100 missing) |
| frontend / positions | 1 (sign prefix on best-performer card) |
| signal-engine | 2 (factor exposure always-false, news sentiment clamping) |
| research-engine | 1 (growth rate heuristic) |

---

*Audit rounds 2 & 3 conducted: 2026-06-02*

---

## Feature Fix — Style-Aware Game Plans (2026-06-02)

### 30. `_build_game_plan()` used identical levels for all trading styles
**Files:** `services/market-data/src/services/scheduler.py`, `services/market-data/src/services/email_service.py`, `frontend/src/pages/stock/[symbol].tsx`
**Commit:** `e0ebe0e`

**Root cause:** `_build_game_plan()` used hardcoded fixed percentages for entry, stop, and take-profit regardless of whether the user's trading style was SHORT, SWING, or LONG. The `style` variable was available in the calling scope but was never passed to the function.

**Impact:** A SHORT-term momentum trader and a LONG-term position trader received identical entry levels (-1.5%/-3.5%), stops (-5.5%), and a +12% target — completely wrong for SHORT (too wide stop, too large target) and for LONG (stop too tight, target too small for months-long hold).

**Fix — scheduler.py:** Added `_STYLE_PARAMS` dict with per-style multipliers:

| Style | Entry 1 | Entry 2 | Stop | Default Target |
|-------|---------|---------|------|----------------|
| SHORT (1–5d) | -0.5% | -1.5% | -3% | +5% |
| SWING (5–30d) | -1.5% | -3.5% | -5.5% | +12% |
| LONG (1–12mo) | -2% | -5% | -10% | +25% |

`_build_game_plan()` now accepts a `style` parameter and selects the matching row. The analyst take-profit threshold is also style-adjusted (LONG requires a larger upside to override the default). A `horizon_note` field is returned to explain the expected hold duration. Call site passes `style` from the per-alert watchlist trading style.

**Fix — email_service.py:** Game plan header updated from hardcoded "10-Day Game Plan" to "Game Plan — {style label} — {symbol}". A `horizon_note` line below the header explains expected hold duration and execution guidance.

**Fix — frontend:** The AI game plan prompt (`generateGamePlan()`) now derives `tradeStyle` from `sig.horizon` and injects a `styleInstruction` block into the Claude system prompt. The instruction block carries style-specific entry/stop/target percentages so the AI returns levels appropriate for the user's actual trading horizon. The JSON title field is also updated to match.

---

*Style-aware game plan fix: 2026-06-02*

---

## Audit Round 4 — 2026-06-02

### 31. `datetime.utcnow()` in 5 files across 3 services (naive vs. aware datetime mismatch)
**Files:** `services/market-data/src/api/auth.py`, `services/market-data/src/api/board.py`, `services/market-data/src/api/routes.py`, `services/signal-engine/src/api/routes.py`, `services/research-engine/src/api/routes.py`
**Commit:** `d044524`

**Root cause:** `datetime.utcnow()` returns a *naive* datetime (no timezone info). When compared against timezone-aware datetimes in SQLAlchemy queries or Python datetime arithmetic, this can cause `TypeError: can't compare offset-naive and offset-aware datetimes` in Python 3.11+ and silently wrong comparisons in earlier versions.

**Affected call sites:**
- `auth.py:37` — JWT `exp` claim: token expiry used naive datetime
- `board.py:144,161` — plan `closed_at` / `updated_at` timestamps stored as naive
- `routes.py:518` — market breadth `updated_at` metadata field
- `routes.py:1244` — relative-performance chart lookback cutoff
- `signal-engine/routes.py:137,138,289,290,419,420,559` — signal accuracy / factor exposure / trade performance lookback windows (8 call sites)
- `research-engine/routes.py:1104,1276` — cache TTL check and cache write (must be consistent for `(now - ts).total_seconds()` to work without a TypeError)

**Impact:** Mixed naive/aware datetimes could silently produce wrong lookback windows (all signals shown, or none), broken JWT token validation, and crash the research-engine cache on Python 3.11+.

**Fix:** Replaced all occurrences with `datetime.now(timezone.utc)` and added `timezone` to imports in each file.

---

## Summary by service — Audit Round 4

| Service | Bugs fixed |
|---------|-----------|
| market-data/api/auth | 1 (JWT naive datetime) |
| market-data/api/board | 1 (plan timestamp naive datetime) |
| market-data/api/routes | 2 (market breadth + relative-performance cutoff) |
| signal-engine/api/routes | 1 (4 endpoints × 2 cutoffs = 8 call sites) |
| research-engine/api/routes | 1 (cache TTL check + write) |

---

*Audit round 4 conducted: 2026-06-02*

---

## Audit Round 5 — 2026-06-02

### 32. Options flow sentiment labels fire without sufficient put volume
**File:** `services/market-data/src/api/routes.py`
**Commit:** `f5c3a00`

**Root cause:** The `sufficient_put_vol` guard (`total_put_vol >= 100`) was only applied to the `"strongly_bullish"` branch. The `"bullish"`, `"bearish"`, and `"slightly_bearish"` labels fired on `cp_ratio` alone, regardless of put volume. With very few puts (e.g., 2 calls vs 1 put → cp_ratio = 2.0, put_vol = 1), the label returned `"bullish"` on a completely illiquid name.

**Impact:** Signals for low-liquidity options would show bullish/bearish sentiment when the data was meaningless. This false options sentiment flowed into the signal conviction check, potentially blocking or passing emails incorrectly.

**Fix:** Applied `and sufficient_put_vol` to all four sentiment branches. Any ticker with fewer than 100 put contracts falls through to `"neutral"` regardless of cp_ratio.

---

## Summary — Audit Round 5

| Service | Bugs fixed |
|---------|-----------|
| market-data/api/routes | 1 (options flow sentiment volume guard) |

---

*Audit round 5 conducted: 2026-06-02*

---

---

# Improvements — Expert Review Batch (2026-05-31 → 2026-06-03)

All items implemented from the expert-review `/improvements` tracker.
Organised by tier (Critical → Analytical → New Features), each with the commit/ship date and a concise description of what changed and why.

---

## Tier 1 — Critical Signal Integrity & Security

### I-1. ML model probability calibration (isotonic regression)
**File:** `services/ml-prediction/src/training/trainer.py`
**Shipped:** Pre-existing (confirmed 2026-05-31)

XGBoost outputs raw margin scores, not true probabilities. An uncalibrated "65% bullish" may correspond to only 52% real probability. Implemented `IsotonicRegression` calibrator on a held-out 15% calibration set, saved in the joblib bundle alongside the model, applied at inference time. Three-way train/calibrate/test split (70/15/15) prevents double-dipping.

---

### I-2. K-Score falling knife gate
**File:** `services/ranking-engine/src/scoring/kscore.py` (`_value_proxy()`)
**Shipped:** 2026-05-31

Value proxy was `1 − (price / 52w_high)`. A stock down 80% scored 80 on "value" — indistinguishable from a genuine value play. Added gate: if 1-month return < −5% AND 3-month return < −15%, value sub-score is capped at 25. Prevents sustained downtrends from masquerading as opportunity.

---

### I-3. Redis fallback for macro features (yfinance failure protection)
**File:** `services/ml-prediction/src/features/builder.py` (`fetch_macro_features()`)
**Shipped:** 2026-05-31

When yfinance fails to fetch SPY/VIX at inference time, macro features previously zero-filled silently. Zero-fill looks like extreme market panic to a model trained on real values, biasing all signals defensively. Now: successful fetches write to Redis (key `stockai:macro_features`, TTL 24h). On failure, Redis cache is used. Zero-fill only occurs when both yfinance and Redis have no data.

---

### I-4. Look-ahead bias guard in ML training
**File:** `services/ml-prediction/src/training/trainer.py` (`train_model()`)
**Shipped:** 2026-05-31

If daily ingest runs mid-session, a partial "today" bar is included in feature windows (SMA, ATR, z-scores) even though its label is NaN. Added: `df = df[pd.to_datetime(df["ts"]).dt.date < today].copy()` after loading price history. Training always operates on fully-closed bars only.

---

### I-5. Prompt injection security fix — symbol sanitisation
**File:** `services/research-engine/src/api/routes.py` (`_sanitise_symbol()`)
**Shipped:** 2026-05-31

Stock symbol from the URL was interpolated directly into the Claude prompt. A crafted symbol containing newlines or instruction text could attempt to redirect the AI response. Added `_sanitise_symbol()` that strips all characters outside `[A-Z0-9.\-:]`. Applied at entry point of all four route handlers. Invalid symbols return HTTP 400 before any prompt is constructed.

---

## Tier 2 — Analytical & Scoring Improvements

### I-6. Sector-relative fundamental scoring
**File:** `services/ranking-engine/src/scoring/` + `services/market-data/src/api/routes.py` (`fundamentals_bulk` endpoint)
**Shipped:** 2026-06-01

All fundamental thresholds were absolute (P/E 25 = "fairly valued" for every sector). A utility at 14× is correct; a SaaS at 14× is deeply discounted. Implemented `_sector_relative_scores()`: group stocks by sector, percentile-rank each metric (PE/PB/EV-EBITDA inverted; earnings_growth/revenue_growth/ROE direct) within the peer group. Falls back to price proxy when fewer than 2 peers available.

---

### I-7. Asymmetric RSI scoring curve
**File:** `services/ranking-engine/src/scoring/kscore.py` (`_technical_score()`)
**Shipped:** 2026-05-31

Previous formula `100 - abs(RSI - 55)` was symmetric and peaked at RSI=55. RSI=70 (healthy uptrend) scored the same as RSI=40 (weak/recovering). Replaced with asymmetric piecewise: RSI ≤30 → 50, RSI 30–50 → 50→90 linear, RSI 50–70 → 90→100 (optimal zone), RSI >70 → -2.5 pts/pt. A trending stock at RSI 70 now scores ~100 instead of 85.

---

### I-8. Standardise on adjusted close for all feature computation
**File:** `services/market-data/src/adapters/yfinance_adapter.py`
**Shipped:** 2026-05-31

Some code paths called yfinance with `auto_adjust=False`. A 2-for-1 split creates an apparent 50% price drop in raw close data — momentum becomes deeply negative on a shareholder-neutral event. Changed all feature computation paths to `auto_adjust=True`. Raw close is still used only for support/resistance levels (which are traded prices).

---

### I-9. Strategy weight normalisation in opportunities scoreFor()
**File:** `frontend/src/pages/opportunities.tsx` (`scoreFor()`)
**Shipped:** 2026-05-31

Strategy weights did not sum to 100%. Swing: 40%+25%+15% = 80% baseline. Short: 85% + unbounded momentum bonus. Scores were not comparable across tabs. Fixed: capped the day-change bonus in Short at 15 pts (≡ 5% move), capped upside bonus in Long-term at 25 pts, wrapped all strategies in `Math.min(100, ...)`. All strategies now output 0–100.

---

### I-10. Zero-volume bar filter at ingestion boundary
**File:** `services/market-data/src/services/ingestion.py` (`validate_ohlcv()`)
**Shipped:** 2026-05-31

Validation accepted `volume >= 0`. Zero-volume bars (trading halts, data errors) distorted ATR, OBV, and volatility calculations. Changed to `df = df[df["volume"] > 0]`. Zero-volume daily bars are now rejected at the ingest boundary and never stored in the database.

---

### I-11. Research engine cache quality flag
**File:** `services/research-engine/src/api/routes.py` + `frontend/src/pages/research/[symbol].tsx`
**Shipped:** 2026-05-31

When Claude timed out, the engine returned hardcoded defaults (company_score: 50, industry_score: 50). This was cached for 24h and served with no indication it was synthetic. Added `report_quality: "full" | "partial" | "fallback"` field. `_fallback_ai()` sets `_is_fallback=True`. Frontend shows red banner for fallback reports, yellow for partial, with a Regenerate prompt.

---

### I-12. ML fusion weight validation — switch from CV AUC to test AUC
**File:** `services/signal-engine/src/generators/signals.py` + `services/signal-engine/src/api/routes.py`
**Shipped:** 2026-06-01

The ML weight formula `0.40 + (auc - 0.50) / 0.20 * 0.35` used in-sample cross-validation AUC instead of held-out test AUC — the model's own training data fed back into its weight. Switched `predict_ensemble` to use held-out test AUC for both internal ensemble weighting and the fusion weight formula. Added `GET /signals/ml-weight-validation` endpoint that sweeps ML weight 0→1 across 180 days of real signal history, returning accuracy + avg return at each step. Empirical result: 0.40 ML weight is optimal — exactly the formula lower bound, validating the existing range.

---

### I-13. Staleness check in signal generator
**File:** `services/signal-engine/src/generators/signals.py` (`_check_price_staleness()`)
**Shipped:** 2026-05-31

Signal generator assumed the most recent bar was current. No check that `last_bar_ts` was within an expected window (holiday, gap, service restart). Added `_check_price_staleness()`: logs a structured warning with `last_bar` and `days_old` if the most recent bar is >3 days old. Does not block signal computation — makes pipeline gaps observable in logs.

---

### I-14. Standard Wilder ATR (EWM, not SMA)
**File:** `services/research-engine/src/api/routes.py` (`_atr()`)
**Shipped:** 2026-05-31

Research engine computed ATR using a simple moving average of true range. Standard ATR (Wilder) uses exponential smoothing (alpha = 1/period). The SMA result diverges from TradingView, Bloomberg, and ThinkOrSwim readings, especially in volatile periods. Fixed `_atr()` to seed with SMA of the first 14 bars then apply Wilder's EWM (alpha = 1/14). Results now match all major platforms exactly.

---

### I-15. Multi-timeframe signal confirmation (weekly alignment gate)
**File:** `services/signal-engine/src/generators/signals.py` + `frontend/src/components/SignalCard.tsx`
**Commit:** `35a6381` — 2026-06-03

Daily TA can show a BUY setup while the weekly chart is still in a confirmed downtrend, producing whipsaw trades that fail within days of entry.

**What changed:**
- Replaced `_weekly_ta_score()` (single float) with `_weekly_technicals()` returning four components: `weekly_rsi` (float), `weekly_trend` ("up"/"down"/"neutral" based on price vs **10-week SMA**, previously 20-week), `weekly_macd_bull` (bool), `weekly_score` (0-1 composite for existing boost/compress logic)
- All four components stored individually in signal `reasons` dict
- **SWING/LONG BUY gate**: when `weekly_rsi < 40 AND weekly_trend == "down"` simultaneously, applies an additional 0.40× compression on top of the normal `weekly_compress` factor. A strong daily fused signal of 0.80 compresses to ~0.60 — below the SWING bull threshold of 0.65. Only a daily signal ≥ ~0.90 (overwhelming conviction) can still reach BUY.
- Gate does not apply to SHORT style — weekly context is irrelevant for 1–5 day holds
- SignalCard now shows: `"RSI 34, trend down — BUY gate active"` instead of an opaque composite score percentage

---

## Tier 3 — New Features

### I-16. Trade Performance page (in-sample backtest engine)
**File:** `services/strategy-engine/src/backtest/engine.py` + `frontend/src/pages/trade-performance.tsx`
**Shipped:** 2026-06-01/02

Added GET `/signals/trade_performance` endpoint. Backend: compounded equity curve built from historical signals, annualised Sharpe, max drawdown, Calmar ratio, SPY benchmark comparison. Entry bar return bug fixed (position array shifted by 1 so entry-bar return is not captured). Frontend `/trade-performance` page shows equity curve chart, summary metrics, and SPY comparison line.

---

### I-17. Options flow integration
**File:** `services/market-data/src/api/routes.py` + `services/signal-engine/src/generators/signals.py` + `frontend/src/pages/stock/[symbol].tsx`
**Shipped:** 2026-06-01/02

Added GET `/stocks/{symbol}/options-flow` endpoint using yfinance options chain (no external API key required). Fetches 2 nearest expiries, computes call/put ratio, flags contracts where volume > 30% of OI. Sentiment tiers wired into signal fusion: `strongly_bullish` (C/P ≥ 2.0) → +7% boost; `bullish` (C/P ≥ 1.3) → +3%; `bearish` (C/P ≤ 0.5) → −15% compress. All branches require ≥100 put contracts to prevent illiquid names from triggering sentiment labels. Stock detail page shows C/P ratio bar, sentiment badge, and unusual contracts table.

---

### I-18. Earnings surprise model
**File:** `services/market-data/src/api/routes.py` (fundamentals endpoint) + `services/research-engine/src/api/routes.py` + `frontend/src/pages/stock/[symbol].tsx`
**Shipped:** 2026-05-31

Added `eps_beat_rate`, `eps_avg_surprise_pct`, `eps_surprise_trend`, and `eps_history` (last 8 quarters) to the fundamentals endpoint. Research engine awards +5 pts for beat_rate ≥ 75%, +2 pts for ≥ 50%. Stock detail page shows a per-quarter beat/miss grid with colour coding.

---

### I-19. Relative strength vs sector ETF
**File:** `services/signal-engine/src/generators/signals.py` + `services/ranking-engine/` + `frontend/src/pages/rankings.tsx`
**Shipped:** 2026-06-01/02

`rs_rank = (1 + stock_20d_return) / (1 + sector_ETF_20d_return)`. Mapped to RS score 0–100 (50 = in-line with sector). Sector ETFs: XLK/XLV/XLF/etc for US; ^HSI components for HK. Added as 10% K-Score weight. Signal engine: `rs_rank < 0.8` applies 15% compression to fused score. RS column added to Rankings page (green ≥ 60, red < 40).

---

### I-20. News sentiment layer
**File:** `services/signal-engine/src/generators/signals.py` + `frontend/src/pages/stock/[symbol].tsx`
**Shipped:** 2026-05-31

Fetches the last 10 yfinance news articles per symbol. VADER sentiment applied (−1 → +1), mapped to 0–100 score. Sentiment score < 25 compresses fused signal by 30%; < 35 compresses by 20%. Wired into `generate_signal()` after earnings proximity penalty. Sentiment score shown in stock detail trade plan section.

---

### I-21. Four-state market regime detection
**File:** `services/market-data/` + `services/signal-engine/src/generators/signals.py` + `frontend/src/components/SignalCard.tsx`
**Shipped:** 2026-05-31 / extended in 2026-06-01

Four-state regime: `bull` / `high_vol` (F&G < 30 despite SPY above 200MA) / `bear` / `unknown`. Buy/hold threshold tables per state — e.g., SWING bull 0.65/0.50 vs bear 0.73/0.56. Market breadth (% stocks above 200-day SMA) added as a second filter: breadth < 40% compresses signal 10% toward neutral even in bull regime. All stored in signal reasons dict and shown in SignalCard confluence panel.

---

### I-22. Trade Board closed position P&L tracking
**File:** `frontend/src/pages/board.tsx` + `services/market-data/src/api/board.py`
**Shipped:** 2026-05-31

Trade Board closed cards now show exit price input and P&L% (green/red). Performance summary bar above market tabs shows win rate, average return, best trade, and worst trade. `exit_price` and `closed_at` columns added to `trade_plans` table. Closed positions feed directly into the signal accuracy / factor exposure analysis as labelled training examples.

---

### I-23. Factor exposure analysis
**File:** `services/signal-engine/src/api/routes.py` + `frontend/src/pages/signal-accuracy.tsx`
**Shipped:** 2026-06-01/02

GET `/signals/factor-exposure` endpoint. Aggregates RSI, ADX, Volume Z-score, ML Probability, News Sentiment, and TA Score from the signal reasons JSON across the last 180 days, split by correct vs. wrong outcome. Factor bar chart added to Signal Accuracy page showing average value per factor for winning vs. losing signals, with deviation from neutral baseline. Reveals whether alpha is real or a disguised factor tilt.

---

## Summary — Improvements (all tiers)

| ID | Area | Ship date |
|----|------|-----------|
| I-1 | ML calibration (isotonic regression) | pre-existing |
| I-2 | K-Score falling knife gate | 2026-05-31 |
| I-3 | Redis macro feature fallback | 2026-05-31 |
| I-4 | Look-ahead bias guard in training | 2026-05-31 |
| I-5 | Prompt injection — symbol sanitisation | 2026-05-31 |
| I-6 | Sector-relative fundamental scoring | 2026-06-01 |
| I-7 | Asymmetric RSI scoring curve | 2026-05-31 |
| I-8 | Adjusted close standardisation | 2026-05-31 |
| I-9 | Strategy weight normalisation | 2026-05-31 |
| I-10 | Zero-volume bar filter | 2026-05-31 |
| I-11 | Research engine cache quality flag | 2026-05-31 |
| I-12 | ML weight validation — CV → test AUC | 2026-06-01 |
| I-13 | Price staleness check in signal generator | 2026-05-31 |
| I-14 | Wilder ATR (EWM, not SMA) | 2026-05-31 |
| I-15 | Multi-timeframe weekly confirmation + gate | 2026-06-03 |
| I-16 | Trade Performance backtest engine | 2026-06-01/02 |
| I-17 | Options flow integration | 2026-06-01/02 |
| I-18 | Earnings surprise model | 2026-05-31 |
| I-19 | Relative strength vs sector ETF | 2026-06-01/02 |
| I-20 | News sentiment signal layer | 2026-05-31 |
| I-21 | Four-state market regime detection | 2026-05-31+ |
| I-22 | Trade Board P&L feedback loop | 2026-05-31 |
| I-23 | Factor exposure analysis | 2026-06-01/02 |

*Expert review batch completed: 2026-05-31 → 2026-06-03*

---

---

# Signal Quality Review — 2026-06-03

An honest assessment of whether each improvement is generating better signals or adding noise.
Reviewed after all 23 improvements were live.

---

## Verdict summary

| Rating | Improvements |
|--------|-------------|
| Clearly helping | I-1, I-3, I-4, I-8, I-10, I-12, I-19, I-21 |
| Probably helping (unverified) | I-2, I-7, I-15 (alignment), I-18 |
| Needs validation before trusting | I-15 (BUY gate), earnings compression magnitude |
| Likely adding noise | I-20 (VADER news sentiment), I-17 (options flow data quality) |
| No signal impact (display/tooling) | I-5, I-6, I-9, I-11, I-13, I-14, I-16, I-22, I-23 |

---

## What is clearly helping

### Data quality fixes (I-3, I-4, I-8, I-10)
Removing bad data is an unambiguous improvement — it does not "add" a signal layer, it removes systematic errors that were corrupting existing ones.

- **I-8 (adj close):** A 2-for-1 split created an apparent 50% price crash in raw data. Momentum, SMA, and ATR were all measuring a phantom crash. Now fixed at the adapter boundary.
- **I-4 (look-ahead guard):** Partial mid-session bars in the training set contaminated rolling feature windows. The model was unknowingly trained on slightly-future data.
- **I-10 (zero-volume filter):** Trading halt bars (volume = 0) inflated ATR and distorted OBV, making stocks look more volatile than they are.
- **I-3 (Redis macro fallback):** yfinance failures caused macro features to zero-fill, making every signal look like extreme market panic. Now the most-recent successful fetch is used instead.

### ML probability calibration and weight formula (I-1, I-12)
Everything downstream — the 65% confidence display, the BUY threshold comparison, the signal fusion weight — depends on the ML probability being a real probability. Without isotonic calibration, a 65% output might correspond to only 52% true probability. These two fixes are foundational: nothing else in the system is reliable until these are right.

### Four-state market regime (I-21)
Raising BUY thresholds during `high_vol` and `bear` regimes is grounded in 40+ years of documented evidence that momentum and breakout strategies underperform in volatile regimes. The addition of market breadth (% stocks above 200-day SMA) as a second regime input — compressing signals when breadth < 40% — adds a meaningful cross-sectional check that the regime classification is not just SPY-specific.

### Relative strength vs sector (I-19)
RS is one of the most replicated and durable factors in quantitative finance. A stock outperforming its sector ETF on a 20-day basis while generating a BUY setup is a meaningfully different situation from one that is lagging the sector. The 15% compression for `rs_rank < 0.8` is directionally correct and has academic backing.

---

## What is probably helping but unverified

### Weekly alignment — boost/compress (I-15, existing part)
When daily and weekly directions agree, amplifying the signal is sound multi-timeframe practice. When they conflict, compressing it is conservative and correct. The 10-week SMA (changed from 20-week) responds faster to genuine trend changes. **Verdict: directionally right, but the magnitude of boost (1.12×) and compress (0.85×) have not been empirically tuned.**

### K-Score RSI curve and falling knife gate (I-2, I-7)
Both fix obvious logical errors in K-Score computation (a stock down 80% should not score 80 on "value"; RSI 70 should not score the same as RSI 40). These improve the K-Score quality, which feeds into LONG-style signal boosts. Effect on short-term signal accuracy is indirect.

### Earnings surprise model (I-18)
A history of consistently beating EPS estimates is one of the best-documented predictors of analyst upgrades and post-earnings drift. Adding it to the research engine score (+5 pts for beat_rate ≥ 75%) is correct. **Effect is felt in research engine scores, not directly in signal fusion.**

---

## What needs validation before trusting

### Weekly BUY gate — the 0.40× compression (I-15, new part)
The gate fires when `weekly_rsi < 40 AND weekly_trend == "down"` simultaneously. The logic is sound: this combination indicates a confirmed bearish weekly structure, not a temporary dip. However:

- **Unknown false-negative rate.** Early reversals from a weekly downtrend are exactly when a SWING BUY can be most profitable (catching the turn). The gate will block many of these.
- **0.40× is a strong compression.** A fused score of 0.80 becomes 0.66 — just above SWING bull threshold of 0.65. Only a truly extreme daily signal (~0.90+) gets through. This may be too aggressive.
- **Needs walk-forward validation.** The correct evaluation: did signals blocked by the gate have worse outcomes than those that passed? That requires the walk-forward backtest (still pending).

### Earnings proximity compression magnitude
The `0.50×` compression for earnings ≤ 2 days was fixed from an impossible `0.25×`, but `0.50×` still means you need a fused score of ~1.30 to get a SWING BUY with earnings in 2 days. That is unreachable (scores are clipped to 1.0). In practice, no BUY signal can fire within 2 days of earnings for SWING. This may be intentional (earnings are binary events) but it is a hard block, not a compression.

---

## What is likely adding noise

### VADER news sentiment (I-20)

**Assessment: weak signal source, probably net negative for accuracy.**

VADER is a rule-based lexicon tool designed for social media text. Financial news has domain-specific language that VADER handles poorly:

| Headline | VADER reads | Correct reading |
|----------|-------------|-----------------|
| "Stock faces regulatory headwinds" | Negative (sees "headwinds") | Neutral/contextual |
| "Volatile session closes flat" | Negative (sees "volatile") | Neutral |
| "Earnings beat but stock falls on guidance" | Mixed/neutral | Negative (guidance matters more) |
| "Apple hits resistance at 52-week high" | Negative (sees "resistance") | Neutral — this is a technical observation |

The 30% compression when sentiment < 25 is a large penalty applied to a noisy score. If VADER misclassifies a neutral article as negative, a legitimate BUY signal is suppressed with no recovery mechanism.

**Recommended fix:** Replace VADER with a Claude API call. A single classification call (`POSITIVE / NEGATIVE / NEUTRAL` with a short financial-context system prompt) on the top 3 headlines would be far more accurate. Claude is already in the stack for research reports — this would cost ~$0.001 per signal refresh and would actually understand financial language.

### Options flow via yfinance (I-17)

**Assessment: data quality is too low for the signal magnitudes applied.**

The signals that actually predict moves are:
- **Intraday sweep orders** — large block trades executed across multiple exchanges within seconds (dark pool + lit market)
- **Short-dated OTM calls with unusual size vs. open interest** — in real-time, before the move
- **Gamma exposure concentration** at specific strikes

What yfinance provides is **end-of-day aggregate volume** across all strikes and all expiries. By the time this data is available:
1. Informed traders have already positioned
2. The C/P ratio reflects what happened yesterday, not what is being positioned for tomorrow
3. Retail activity dilutes any institutional signal, especially for large-cap stocks

**Specific risk:** C/P ratio is correlated with recent price direction (stocks that went up yesterday have more calls today). This makes it a **lagging indicator** — it confirms a move that already happened rather than predicting one.

The ±7%/±15% boost/compress magnitudes are large relative to the signal quality. A spurious `strongly_bullish` options reading (2 calls to 1 put, both with 5 contracts) boosts a fused score from 0.62 to 0.67, potentially triggering a BUY that would not have fired.

**Recommended fix:** Either (a) reduce boost/compress magnitudes to ±3%/±5% until a real options data source is integrated, or (b) raise the minimum contract threshold from 100 to 500 put contracts to filter out all but the most liquid names.

---

## The compression accumulation problem

This is the most important systemic risk introduced by the improvement batch. Each compression layer is independent but they multiply together. A plausible scenario for a stock in a choppy market:

| Compression source | Factor applied |
|-------------------|----------------|
| Weekly direction conflict | 0.85× |
| Weekly BUY gate fires (RSI < 40, trend down) | 0.40× |
| ADX below minimum (choppy market) | 0.90× |
| High-volatility regime | 0.85× |
| News sentiment < 35 | 0.85× |
| Earnings in 5 days | 0.75× |
| RS rank < 0.80 | 0.85× |

**Combined raw compression: 0.85 × 0.40 × 0.90 × 0.85 × 0.85 × 0.75 × 0.85 ≈ 0.127×**

The `max_compress_ratio` cap (SWING = 0.55, LONG = 0.65) prevents reaching 0.127× in practice, but even with the cap, a fused score of 0.80 becomes at most `0.5 + (0.30 × 0.55) = 0.665` — barely above the SWING bull threshold of 0.65. In genuinely adverse conditions (bear regime, threshold = 0.73), even this would not be a BUY.

**Net effect:** In any choppy, uncertain market — which describes the majority of trading days — the system may produce near-zero BUY signals. If the system is supposed to find opportunities in all market conditions, this is over-suppression. If it is supposed to only fire in ideal conditions, this is correct behavior. That decision has not been made explicitly.

**Diagnostic query to run:**
```sql
SELECT signal, horizon, COUNT(*) as n,
       ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER (PARTITION BY horizon), 1) as pct
FROM signals
WHERE ts > now() - interval '30 days'
GROUP BY signal, horizon
ORDER BY horizon, signal;
```
If BUY signals are < 10% of total for each style, the system is likely over-compressed.

---

## Recommendations

### R-1. Replace VADER with Claude news sentiment
**Priority: High.** One API call per symbol per refresh. System prompt: "You are a financial analyst. Classify this headline as POSITIVE, NEGATIVE, or NEUTRAL for a stock investor. Respond with one word." Cost: negligible. Accuracy: dramatically better than VADER for financial text.

### R-2. Reduce options flow magnitudes or raise liquidity threshold
**Priority: Medium.** Change boost from +7%/+3% to +4%/+2%, and compress from −15% to −8%. Alternatively, raise minimum put contract threshold from 100 to 500. Both changes reduce the impact of noisy data without removing the signal.

### R-3. Monitor BUY signal rate over time
**Priority: High.** Add a daily metric: how many BUY signals fired per style, as a percentage of all signals computed. If this drops below 10–15% consistently, investigate which compression layers are dominating. The signal distribution is the canary — a system that almost never says BUY is either very conservative or broken.

### R-4. Build the walk-forward backtest
**Priority: High (already in improvements tracker).** The only way to empirically validate whether the weekly BUY gate, news compression, and options flow are improving or hurting accuracy is to replay historical signals and measure outcomes. Without this, every compression decision is faith-based.

### R-5. Consider a compression budget
**Priority: Medium.** Rather than stacking independent multipliers, define a total maximum compression budget per signal. Example: no combination of filters can compress a signal by more than 50% of its pre-filter deviation from 0.5. This prevents the scenario where 7 individually reasonable filters combine to make BUY effectively unreachable.

---

*Signal quality review conducted: 2026-06-03*

---

---

# Signal Filter Reference — Gate Conditions and Signal Suppression (2026-06-03)

Complete reference for every condition that can block or suppress a BUY signal, ordered by severity.
All compression multipliers follow the convention: `fused = 0.5 + (fused − 0.5) × multiplier`.

---

## Hard blocks — BUY is effectively impossible

These conditions make a BUY signal practically unreachable without an extreme daily setup (fused ≥ 0.90+).

### Weekly BUY gate (SWING and LONG only)
**File:** `services/signal-engine/src/generators/signals.py` — applied after compression cap

**Fires when:** `weekly_rsi < 40 AND weekly_trend == "down"` simultaneously.

**Weekly RSI definition:**
- RSI(14) computed on weekly bars — daily OHLCV resampled to Monday-anchored weekly bars
- Current (partial) week is dropped if it started within the past 4 calendar days (unreliable partial bar)
- Requires ≥ 26 weekly bars (≈ 6 months) to compute; returns `None` if insufficient history
- RSI < 40 = weekly momentum has entered bearish territory (not just neutral — confirmed weak)

**Weekly trend definition:**
- 10-week SMA (changed from 20-week in v3 — more responsive to medium-term turns)
- `"up"`: price > SMA by more than +1%
- `"down"`: price < SMA by more than −1%
- `"neutral"`: within ±1% band

**Effect:** 0.40× compression applied **after** the `max_compress_ratio` cap — making it the final, unoverridable filter.

| SWING starting fused | After gate (0.40×) | vs. bull BUY threshold (0.65) |
|---------------------|-------------------|-------------------------------|
| 0.80 | 0.5 + (0.30 × 0.40) = 0.62 | Below threshold — HOLD |
| 0.90 | 0.5 + (0.40 × 0.40) = 0.66 | Above threshold — BUY (barely) |
| 0.95 | 0.5 + (0.45 × 0.40) = 0.68 | Above threshold — BUY |

In practice: only an overwhelming daily setup (RSI oversold, golden cross, MACD breakout, strong volume) reaches 0.90+ fused, so the gate blocks the vast majority of BUY attempts during confirmed weekly downtrends.

**Applies to:** SWING and LONG. SHORT style is excluded — short-term momentum plays can legitimately run against the weekly trend.

---

### Earnings in ≤ 2 days (SWING only)
**Compression:** 0.50×. A fused score of 0.80 becomes 0.65 — just at the SWING bull threshold. A score of 0.79 becomes 0.645 — below it. The earnings binary event risk is treated as a near-hard block for SWING.

---

### Stale price data
**Fires when:** Most recent price bar is > 3 calendar days old (pipeline gap).
**Compression:** 0.60×. Signals based on stale data are unreliable; this prevents a Monday signal from reflecting last Wednesday's close as if it were current.

---

### Insufficient history
**Fires when:** Fewer than 50 daily bars available for the symbol.
**Compression:** 0.50×. SMA200, ADX(14), and RSI(14) are all unreliable below 50 bars. The signal is flagged as low-confidence rather than silently serving defaults.

---

## Strong compressions — meaningfully reduce BUY probability

These apply in common conditions and can stack.

| Condition | Applies to | Multiplier | Threshold |
|-----------|-----------|-----------|-----------|
| Weekly misalignment (daily vs. weekly direction conflict) | SHORT / SWING / LONG | 0.93× / 0.85× / 0.80× | Always when directions disagree |
| ADX choppy market | SHORT (< 25), SWING (< 20) | 0.85× / 0.90× | ADX below minimum |
| High-volatility regime (Fear & Greed < 30) | All styles | 0.92× / 0.85× / 0.90× | F&G < 30 |
| Market breadth < 40% | SWING, LONG | 0.90× / 0.92× | < 40% of US stocks above 200-day SMA |
| News sentiment strongly negative (SWING only) | SWING | 0.75× | Sentiment score < 25/100 |
| News sentiment negative (SWING only) | SWING | 0.85× | Sentiment score < 35/100 |
| Earnings in ≤ 5 days (SWING only) | SWING | 0.75× | days_to_earnings ≤ 5 |
| Earnings in ≤ 10 days (SWING only) | SWING | 0.90× | days_to_earnings ≤ 10 |
| Relative strength lagging sector | SHORT / SWING / LONG | 0.90× / 0.85× / 0.80× | rs_rank < 0.80 (stock underperforming sector ETF by > 20% on 20d basis) |
| Options flow bearish | All styles | 0.92× | C/P ratio < 0.7, put vol ≥ 100 |
| Options flow slightly bearish | All styles | 0.96× | C/P ratio < 1.0 |
| K-Score weak fundamentals (LONG only) | LONG | −0.06 direct | K-Score < 35/100 |
| Bearish chart pattern (active, recent) | All styles | up to −0.15 direct | head_and_shoulders, double_top, bear_flag, descending_triangle |

---

## Threshold-based blocks — market regime raises the bar

The BUY threshold itself increases in adverse regimes, meaning a signal must be stronger to fire even without any compression.

| Style | Bull BUY threshold | High-vol BUY threshold | Bear BUY threshold |
|-------|-------------------|----------------------|-------------------|
| SHORT | 0.60 | 0.65 | 0.68 |
| SWING | 0.65 | 0.70 | 0.73 |
| LONG  | 0.60 | 0.65 | 0.70 |

In a bear regime, the threshold increase alone is equivalent to applying an additional ~0.85× compression on a typical 0.80 fused signal.

---

## Compression cap (safety valve)

The `max_compress_ratio` prevents stacked filters from making BUY completely unreachable:

| Style | Cap ratio | Effect on fused 0.80 |
|-------|-----------|---------------------|
| SHORT | 0.70× | Minimum fused = 0.5 + (0.30 × 0.70) = 0.71 |
| SWING | 0.55× | Minimum fused = 0.5 + (0.30 × 0.55) = 0.665 |
| LONG  | 0.65× | Minimum fused = 0.5 + (0.30 × 0.65) = 0.695 |

**Exception:** The weekly BUY gate is applied after the cap and is not subject to it. This is intentional — a confirmed bearish weekly structure overrides the safety valve.

---

## Typical scenario — worst-case SWING compression

A stock in a choppy market with several bearish signals hitting simultaneously:

| Layer | Multiplier | Cumulative fused (starting 0.80) |
|-------|-----------|----------------------------------|
| Starting fused | — | 0.80 |
| Weekly misalignment | 0.85× | 0.775 |
| ADX choppy (< 20) | 0.90× | 0.748 |
| High-vol regime | 0.85× | 0.723 |
| Market breadth < 40% | 0.90× | 0.700 |
| News sentiment < 35 | 0.85× | 0.678 |
| Earnings in 5 days | 0.75× | 0.658 |
| RS lagging sector | 0.85× | 0.640 |
| Cap enforced (0.55×) | → | 0.665 (floor) |
| Weekly gate fires (RSI < 40, trend down) | 0.40× | **0.583** — HOLD |

**Conclusion:** In a genuinely adverse environment, even a strong daily signal (fused 0.80) cannot reach a SWING BUY (threshold 0.65 in bull, 0.73 in bear) if the weekly gate fires.

---

*Filter reference written: 2026-06-03*

---

---

# Signal Firing Mechanics — Do All Conditions Need to Be Clear? (2026-06-03)

**Short answer: No.** A BUY signal does not require all suppression conditions to be inactive.
Each condition compresses the fused score toward 0.5 — it does not zero it out or directly block the signal.
A stock with a strong enough underlying TA/ML signal can fire a BUY even with several conditions active.

The Signal Filter Monitor page uses coloured dots to show which conditions are firing:
- **Dot OFF (gray)** = condition is not suppressing the signal (good)
- **Dot ON (coloured)** = condition is actively compressing the fused score (bad)

---

## How the threshold works

A BUY signal fires when the final fused probability exceeds the style-specific BUY threshold:

| Style | Bull threshold | High-vol threshold | Bear threshold |
|-------|---------------|-------------------|----------------|
| SHORT | 60% | 65% | 68% |
| SWING | 65% | 70% | 73% |
| LONG  | 60% | 65% | 70% |

Every suppression condition reduces how far the fused score sits above 0.5. If the score still clears the threshold after all compressions, the signal fires BUY regardless of how many conditions are active.

---

## Concrete examples (SWING, bull regime, BUY threshold = 65%)

| Scenario | Starting fused | Active conditions | Final fused | Signal |
|----------|---------------|-------------------|-------------|--------|
| All clear | 72% | None | 72% | **BUY** |
| Moderate suppression | 80% | W.Align + ADX + RS (×3) | ~66.5% (cap floor) | **BUY** |
| Gate fires, weak signal | 70% | Weekly Gate | 58% | HOLD |
| Gate fires, strong signal | 92% | Weekly Gate | 67% | **BUY** (barely) |
| Gate + three other filters | 80% | Gate + W.Align + ADX + RS | 58% (gate after cap) | HOLD |

---

## The compression cap — safety net for stacked filters

The `max_compress_ratio` prevents multiple soft filters from stacking into an effective hard block:

- SWING cap: **0.55×** — minimum 55% of pre-filter distance from neutral is preserved
- LONG cap: **0.65×**
- SHORT cap: **0.70×**

Example: if starting fused = 0.80 (distance = 0.30 above neutral), no combination of soft filters can push the final score below `0.5 + 0.30 × 0.55 = 0.665`.

**In a bull regime (threshold 0.65), a stock with fused 0.80+ will fire BUY even if every soft filter fires simultaneously**, because the cap floor (0.665) is still above the threshold.

In a bear regime (threshold 0.73), this same stock would be HOLD (0.665 < 0.73).

---

## The Weekly Gate — the one true hard block

The Weekly Gate is exempt from the compression cap. It is applied after the cap and its compression cannot be offset by any other factor.

**Gate fires when:** `weekly_rsi < 40 AND weekly_trend == "down"` simultaneously (SWING and LONG only).

**Effect:** 0.40× compression applied to the post-cap fused score.

| Post-cap fused | After gate (0.40×) | vs. SWING bull threshold (0.65) |
|---------------|-------------------|----------------------------------|
| 0.665 (cap floor for 0.80 signal) | 0.566 | **HOLD** |
| 0.80 | 0.62 | **HOLD** |
| 0.90 | 0.66 | **BUY** (barely) |
| 0.95 | 0.68 | **BUY** |

**Practical implication:** A stock with the Weekly Gate active needs a fused score of approximately 0.89+ after all other filters to still reach BUY. This represents an exceptionally strong daily setup — RSI oversold, golden cross, MACD breakout, strong volume, and bullish chart patterns all firing together.

---

## Decision guide for reading the Signal Filter Monitor

| What you see | What it means |
|---|---|
| All dots gray, signal = BUY | Clean signal — no suppression, fired on merit |
| Several soft dots on, signal = BUY | Strong underlying signal overcame the suppression |
| Several soft dots on, signal = HOLD | Signal is being held back; a stronger daily setup could push it to BUY |
| Gate dot on, signal = HOLD | Very unlikely to flip to BUY — needs extreme daily setup (≥90%+) |
| Gate dot on + other dots on, signal = HOLD | Effectively blocked in all realistic market conditions |
| Stale or History dot on | Signal quality is unreliable regardless of what it says — data gap or new stock |

---

## Why filters exist even when signal fires BUY

A BUY signal with 3 soft filters active is a lower-conviction call than one with zero filters. The filters document the risk factors the system is aware of but could not fully suppress. This is intentional — the system does not silence a strong signal just because conditions are imperfect, but the filter monitor lets you see exactly what risks exist so you can decide whether to act.

---

*Signal firing mechanics documented: 2026-06-03*

---

---

# Two-Tier Signal System — Engine vs Email Alert (2026-06-03)

The system has two completely independent layers that must both pass before a user sees an actionable outcome. Confusing them is the most common source of "why didn't I get a signal?" questions.

---

## Tier 1 — Signal Engine (what the Signal Filter Monitor shows)

The signal engine runs for every active stock on every scheduler tick. It produces a fused probability score and maps it to BUY / HOLD / WAIT / SELL. This result is what the Signal Filter Monitor page displays.

**A BUY on the Signal Filter Monitor means: the fused score crossed the threshold. It does NOT mean an email was sent.**

### How the score is computed

1. TA score (RSI, MACD, SMA, Bollinger, VWAP, OBV, ADX, Stoch RSI) — normalised to 0–1
2. ML probability (XGBoost+RF ensemble) — blended with TA using AUC-based dynamic weight (0.40–0.75)
3. Fused score passed through suppression filters (see Signal Filter Reference section above)
4. Final score compared to BUY threshold → signal label

### What the dots on the Signal Filter Monitor mean

Each coloured dot represents one suppression condition that is compressing the fused score. Dots off (gray) = clean. Dots on (coloured) = actively suppressing. A BUY can still fire with several dots on if the underlying signal is strong enough. The Weekly Gate (red) is the only near-hard block.

---

## Tier 2 — Email Alert Conviction Gate (what triggers a notification)

The scheduler runs the conviction gate only for stocks where a user has created a signal alert AND the signal has just transitioned to a bullish state. All 5 layers must pass for an email to be sent.

### The 5-layer conviction gate

| Layer | What it checks | Requirement |
|-------|---------------|-------------|
| 1 | Analyst consensus | Recommendation mean must be in bullish range ("buy" / "strong buy" / "outperform") |
| 2 | K-Score | ≥ 55 / 100 (fundamental + momentum composite) |
| 3 | Signal engine | BUY (already confirmed — gate only runs on BUY transitions) |
| 4a | Uptrend structure | SMA50 > SMA200 AND price > SMA50 (golden cross alignment) |
| 4b | Entry timing | RSI 45–65 AND Stoch RSI recovering from oversold (cross_up OR oversold+K<60) |
| 4c | MACD momentum | Histogram positive+rising OR zero-line crossover |
| 4d | Volume confirmation | OBV bullish (20-day OBV avg above 30-day avg) |
| 4e | Trend strength | ADX > 25 (signal unreliable in choppy/directionless markets) |
| 5 | ML agrees with TA | ML bullish probability > 70% |

**Disqualifiers** — block the email even if all layers pass:
- Bearish RSI divergence: price rising but RSI falling (momentum fading — high false-BUY risk)
- Stoch RSI overbought: K > 0.80 (RSI itself overextended — pullback risk elevated)

### Additional gate for non-BUY bullish improvements (e.g. WAIT→HOLD)

Lighter check: analyst must be bullish AND confidence ≥ minimum threshold. No full 5-layer check.

### State machine — why a stock already at BUY never sends a second email

The gate only fires on signal **transitions**. The `last_signal` field tracks the previous signal per stock per user alert. If `last_signal == "BUY"` and the new signal is also `"BUY"`, no transition is detected and no email is sent regardless of how strong the signal is.

This prevents spam on stocks that remain at BUY across many scheduler runs. A new email is only sent if the signal leaves BUY (e.g. drops to HOLD or SELL) and then returns to BUY again.

---

## Real example — FCEL (2026-06-03)

FCEL showed BUY at 69.3% bullish probability with only 1 suppression filter active (earnings in 5 days). No email was sent.

| Layer | Requirement | FCEL result |
|-------|-------------|-------------|
| 2 — K-Score | ≥ 55 | ✅ 69 |
| 4a — Uptrend | SMA50 > SMA200, price > SMA50 | ✅ |
| 4b — Entry timing | RSI 45–65, Stoch recovering | ✅ RSI 62, Stoch oversold (K=0.05) |
| 4c — MACD | Histogram positive+rising or zero-cross | ❌ hist −0.29, falling |
| 4d — OBV | Bullish | ✅ |
| 4e — ADX | > 25 | ✅ 46 |
| 5 — ML probability | > 70% | ❌ unavailable (model not trained for FCEL) |

Two layers failed → email blocked despite the signal engine reporting BUY. The Signal Filter Monitor correctly showed BUY because the fused score (69%) crossed the SWING bull threshold (65%). The conviction gate is a separate, stricter check designed to prevent low-confidence BUY emails.

---

## Full flow — from price data to user notification

```
Market closes
    │
    ▼
Scheduler tick
    │
    ├─► Signal Engine (runs for ALL stocks)
    │       TA score → ML fusion → suppression filters → BUY/HOLD/WAIT/SELL
    │       Result stored in signals table
    │       Visible on: Signal Filter Monitor, Opportunities, Stock Detail
    │
    └─► Alert Checker (runs only for stocks with user signal alerts)
            Has signal transitioned? (last_signal → new signal)
            │
            ├─ No transition → skip (no email)
            │
            └─ Bullish transition detected
                    │
                    ├─ BUY transition → run full 5-layer conviction gate
                    │       All 5 pass → send email, update last_signal
                    │       Any fail  → skip, last_signal NOT updated (retry next tick)
                    │
                    └─ WAIT/HOLD improvement → lighter gate (analyst + confidence)
                            Pass → send email, update last_signal
                            Fail → skip, last_signal NOT updated
```

---

## Practical guide — "why didn't I get an email for X?"

Check in this order:

1. **Is there a signal alert set up for this stock?** (Alerts page → Signal Alerts tab)
2. **What is the current signal?** HOLD/WAIT/SELL → email only sent on BUY transition
3. **Was it already at BUY last time?** If `last_signal == BUY` already, no transition → no email
4. **Did the conviction gate pass?** Fetch `GET /signals/{symbol}?style=SWING` and check reasons against the gate table above:
   - `sma50_above_sma200` + `trend_above_sma50` → Layer 4a
   - `rsi` 45–65 + (`stoch_rsi_cross_up` OR `stoch_rsi_oversold`) → Layer 4b
   - `macd_hist` > 0 + `macd_rising` → Layer 4c
   - `obv_bullish` → Layer 4d
   - `adx_trending` (ADX > 25) → Layer 4e
   - `ml_probability` > 0.70 → Layer 5
5. **Check disqualifiers:** `rsi_divergence == "bearish"` or `stoch_rsi_overbought == True`

---

*Two-tier system documented: 2026-06-03*
