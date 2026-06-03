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
