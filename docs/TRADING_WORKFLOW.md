# Stock Trading Workflow Guide

How to use StockAI to find, research, and trade the most profitable stocks — from initial screening through to exit.

---

## Overview

The full workflow follows this sequence:

```
Screener → Rankings → Stock Detail → Game Plan → Watchlist / Board → Exit → Review
```

Each step narrows and validates your trade idea before any capital is committed.

---

## Step 1 — Screener: Filter the Universe

**Page:** Screener

Start by reducing hundreds of stocks down to a short list of high-probability candidates.

**Recommended filters:**

| Filter | Value | Why |
|--------|-------|-----|
| K-Score | ≥ 60 | Only technically and fundamentally sound stocks |
| AI Signal | BUY | ML + TA fusion confirms upward bias |
| Fair-value discount | > 0% | Stock is below intrinsic value — real upside exists |
| Sector | Your choice | Focus where you have an edge or where the macro is favourable |
| Short float | > 15% (optional) | Short-squeeze candidates have additional upside catalyst |

After filtering you should have 5–20 candidates. If more, raise the K-Score minimum.

---

## Step 2 — Rankings: Rank Your Candidates

**Page:** Rankings

Sort by **K-Score descending**. The K-Score is a 0–100 composite of five factors:

| Factor | Weight | What it measures |
|--------|--------|-----------------|
| Technical | 35% | Trend, moving averages, momentum indicators |
| Momentum | 25% | Price performance vs sector peers |
| Value | 20% | Distance below K-Score fair value (intrinsic) |
| Growth | 10% | Earnings and revenue trajectory |
| Volatility | 10% | Risk-adjusted quality (lower vol = higher score) |

**Target:** Focus on stocks scoring 65 or above. A score of 80+ is exceptional.

The rankings page also shows the live price, AI signal, and fair-value upside % at a glance — use it to quickly compare candidates side by side.

---

## Step 3 — Stock Detail: Deep-Dive Each Candidate

**Page:** `/stock/[SYMBOL]`

Click through to each candidate and work through the page top to bottom.

### Header Row

| Card | What to look for |
|------|-----------------|
| **Live Price** | Is it moving today? Volume confirming the move? |
| **Fair Value** | Green % = upside to intrinsic value. Red % = already stretched above fair value |
| **AI Signal** | BUY / WAIT / SELL with confidence %. Confidence ≥ 60% = strong conviction |
| **Earnings** | Orange/red badge = earnings within 21/7 days. Binary risk — treat with caution |

### Right Sidebar (most important section)

**Fair Value (K-Score)**
The K-Score's intrinsic value estimate. A large green % means the stock is meaningfully undervalued — this is your margin of safety. A red % means the AI is signalling momentum but the stock is already expensive.

**Confluence Score (0–100)**
This is your single best "should I enter?" indicator. It combines all signals:

| Score | Grade | Meaning |
|-------|-------|---------|
| 80–100 | Strong Buy | All signals aligned — high conviction |
| 65–79 | Moderate Buy | Most signals favourable |
| 50–64 | Neutral | Mixed signals — wait for clarity |
| < 50 | Avoid | Signals conflicted or bearish |

The score also sets the **max recommended position size** — the system will never suggest more than 15% of your portfolio on a single trade.

**Trade Setup**
- **Entry zone** — nearest support level below current price (use as a limit order)
- **Nearest resistance** — first meaningful overhead resistance
- **Analyst target** — consensus Wall Street target
- **K-Score fair value** — intrinsic value with % upside/downside inline

**Position Sizer**
Enter your account size → the system calculates exact share count and dollar risk based on the entry zone and nearest stop.

### Price Chart
- Look for price above SMA50 and SMA200 (trend confirmation)
- Check RSI(14): entry is best between 40–60 (not overbought)
- Support/resistance levels and Fibonacci retracements are marked on the chart

### K-Score Breakdown Table
Inspect the individual sub-scores. Examples:
- Technical 80 + Value 20 = momentum chase, not value — be more cautious
- Technical 65 + Value 75 = genuine undervaluation — higher conviction

### Options Flow
- **Strongly bullish / Unusual call activity** = institutional accumulation, confirms the signal
- **Bearish options flow on a BUY signal** = red flag — reduce size or skip entirely

---

## Step 4 — Generate a Game Plan

**Sidebar → "Generate 10-Day Game Plan" button** (visible for BUY and WAIT signals only)

The AI generates a concrete structured trade plan using your stock's actual support/resistance levels, Fibonacci zones, and analyst targets.

The plan includes:
- **Entry 1** (50% position) — limit buy at nearest strong support
- **Entry 2** (50% position) — limit buy at deeper support for averaging down
- **Breakout entry** (50% size) — above nearest resistance if limits don't fill
- **Stop loss** — just below the lowest entry; a close below this invalidates the setup
- **Take-profit** — analyst target or next resistance, whichever is closer
- **Catalysts** — 3 concrete reasons the stock could move up
- **Key risk** — the single biggest threat to the trade

> **Price constraints are enforced:** entry prices will always be below current price, stop loss below entries, take-profit above current price.

---

## Step 5 — Set a Price Alert

On the stock detail page, go to the **Price Alerts** section and set an alert at your entry price. You'll receive an email when the stock pulls back to your limit level so you can execute at the right time.

---

## Step 6 — Track on the Board

**Page:** Board

Once you enter a position, save the game plan to your Board. The Board tracks:
- Stage: **Watch → Active → Closed**
- Entry price, stop loss, take-profit
- Live P&L % vs target hit %
- Full game plan with catalyst notes

Move positions through stages as they progress. Record your exit price when you close — this feeds the Trade Performance backtest.

---

## Step 7 — Monitor with Watchlist

**Page:** Watchlist

Add stocks to a watchlist (you can have multiple lists) to monitor without re-entering the detail page. The watchlist shows:
- Live price + change %
- AI Signal + confidence
- K-Score
- Fair-value gap

**Daily routine:** scan the watchlist each morning. If a BUY signal has flipped to WAIT or SELL, review the trade. If a WAIT signal has strengthened to BUY, re-read the game plan.

---

## Step 8 — Reading Signal Changes

The AI Signal refreshes up to 5× per trading day. When a signal changes, the label alone is not enough — you need to understand **why** it changed before deciding what to do. Open the stock detail page and expand the **Signal Reasons** panel to inspect the underlying indicator values.

---

### BUY → BUY (confidence falling)

The signal label held but conviction is eroding. This is an early warning.

**Check in Signal Reasons:**

| Field | Warning sign |
|-------|-------------|
| `confidence` | Dropped below 40% — signal is near the threshold boundary |
| `ml_ta_conflict` | Flipped to `true` — ML and TA now disagree |
| `weekly_alignment` | Flipped to `false` — weekly trend no longer confirming daily |
| `rsi` | Moved above 70 — overbought territory |
| `macd_rising` | Flipped to `false` — momentum starting to stall |

**Action:** Do not add to the position. Raise your stop-loss to the most recent swing low or 1.5 × ATR below the current price. If you are in meaningful profit (> 1 R), consider trimming 25–30% of the position to de-risk while keeping the core.

---

### BUY → HOLD

The fused probability dropped below the BUY threshold but the model is not yet bearish. Conditions have weakened but not broken.

**Check in Signal Reasons:**

| Field | What it means if changed |
|-------|--------------------------|
| `weekly_alignment` | `false` → daily move is now against the weekly trend — the upside is likely limited |
| `adx_compression` | `true` → ADX dropped below the threshold; stock has entered a range, trend stalled |
| `market_regime` | Changed to `high_vol` or `bear` → regime shift raised the BUY threshold; signal is the same but the bar got higher |
| `breadth_compression` | `true` → market-wide weakness is compressing signals; this is macro, not stock-specific |
| `news_sentiment_flag` | `"negative"` → fresh negative headlines are applying a compression |
| `earnings_warning` | `"watch"` or `"note"` → upcoming earnings are suppressing conviction |
| `rsi` | Above 68 → overbought; probability of continued upside decreasing |

**Action:**
- Do not exit immediately — HOLD means the model is neutral, not bearish.
- Move your stop-loss to breakeven or to the nearest support below entry.
- Do not add to the position.
- Set a signal alert: if it recovers to BUY, you can re-add; if it falls to WAIT, begin reducing.
- If the change was driven purely by `market_regime` or `breadth_compression` (macro, not stock), be more patient — the stock setup may be intact and the signal will recover when the macro clears.

---

### BUY → WAIT

The fused probability has crossed into slightly bearish territory. The model is no longer neutral — it is saying conditions have **actively turned against** the trade thesis.

**Check in Signal Reasons:**

| Field | What it means if changed |
|-------|--------------------------|
| `rsi_divergence` | `"bearish"` → price made a new high but RSI did not — classic topping pattern |
| `macd_hist` | Turned negative → short-term momentum has flipped bearish |
| `trend_above_sma50` | `false` → price fell below SMA(50); primary support broken |
| `ml_probability` | Dropped below 0.45 → ML model is now actively bearish |
| `weekly_alignment` | `false` + weekly score below 0.5 → weekly trend is now a headwind |
| `adx_bullish` | `false` + `adx_trending` `true` → strong trend exists but DI− > DI+; trend has reversed direction |
| `options_flag` | Changed to `"elevated_put_volume"` or `"bearish"` → institutional options flow turned negative |

**Action:**
- This is an **exit signal** for active positions.
- Close at least half the position immediately. Keep the remainder only if you believe the change is a temporary filter (e.g. earnings compression that will expire, or a regime blip).
- Move any remaining stop-loss to the breakeven or a tight technical level (nearest support).
- Do not average down — a WAIT signal means the model sees more downside than upside.
- Re-evaluate after the next signal refresh. If it drops to SELL, exit the remainder.

---

### BUY → SELL

The fused probability has crossed the SELL threshold. This is the strongest bearish signal the system generates.

**Action:**
- Exit the full position. No exceptions.
- Review what drove the change in Signal Reasons — the combination of `ml_probability`, `rsi_divergence`, and `trend_above_sma50` will tell you whether this is a fundamental shift or a temporary spike.
- Do not re-enter until the signal recovers to BUY with confidence ≥ 50%.

---

### HOLD → BUY (re-entry signal)

A stock that was on your watchlist as HOLD has strengthened to BUY. This is a valid entry trigger, often cleaner than the original BUY because the setup has had time to build.

**Confirm before acting:**

| Check | What to look for |
|-------|-----------------|
| `weekly_alignment` | `true` — weekly timeframe also bullish |
| `ml_ta_conflict` | `false` — ML and TA agree |
| `confidence` | ≥ 50% — well above the threshold, not a borderline call |
| `adx_trending` | `true` — trend has the strength to sustain the move |
| `market_regime` | `bull` or `unknown` — regime is not actively working against you |
| `earnings_warning` | null or `"watch"` (≥ 10 days away) — no binary event imminent |

If all of the above are green, this is a high-conviction re-entry. Use the same position size process as a fresh entry.

---

### HOLD / WAIT → SELL

The signal has deteriorated past the HOLD zone directly into bearish territory. The speed of the move matters: a signal that went HOLD → WAIT → SELL across multiple refreshes gave you time to reduce. A single-session jump from HOLD to SELL is more urgent.

**Action:**
- If you have an open position, exit immediately.
- Check `market_regime` — if the regime just switched to `bear`, the SELL may apply broadly to everything in the portfolio, not just this stock.
- Check `breadth_pct` — if it's below 30%, the SELL is regime-driven (macro), not stock-specific. The position may recover when breadth recovers. Still exit — the model says risk is elevated.

---

### Summary table

| Transition | Urgency | Primary action |
|------------|---------|----------------|
| BUY → BUY (conf. falling) | Monitor | Raise stop, no adds |
| BUY → HOLD | Yellow — plan your exit | Move stop to breakeven, no adds |
| BUY → WAIT | Orange — begin reducing | Exit at least 50%, tight stop on remainder |
| BUY → SELL | Red — act now | Exit full position |
| HOLD → BUY | Opportunity | Confirm reasons panel, re-enter if clean |
| HOLD → WAIT | Yellow | Exit or hedge; no new entries |
| HOLD → SELL | Red | Exit full position |
| WAIT → SELL | Red | Exit; check if macro-driven (breadth/regime) |
| SELL → HOLD/BUY | Do not chase | Wait for two consecutive BUY refreshes before considering re-entry |

---

### How to diagnose the reason for a signal change

Open **Stock Detail → Signal Reasons** panel and look at these fields in order:

1. **`market_regime`** — if this changed, the shift is macro. The stock setup may be intact but the system raised its bar. Be patient.
2. **`breadth_compression`** — if `true`, market-wide weakness is compressing everything. Same logic as regime change.
3. **`weekly_alignment`** — if `false`, the signal is fighting the higher timeframe. Real but temporary — weekly trends reverse slowly.
4. **`ml_ta_conflict`** — if `true`, ML and TA disagree. High uncertainty. Treat the signal as lower confidence than the number suggests.
5. **`rsi_divergence`** — if `"bearish"`, price may be near a top even if everything else looks OK. A leading indicator.
6. **`trend_above_sma50`** — if `false`, the price structure has broken. More serious than a TA filter — this is a key level.
7. **`ml_probability`** — if below 0.45 when the signal was previously BUY, the ML model has flipped its view. Give this significant weight.
8. **`earnings_warning`** — if `"caution"` (≤ 2 days), the compression is temporary and mechanical. Hold if your thesis is intact, the signal will reset after earnings.
9. **`stale_price_warning`** or **`insufficient_history_warning`** — if `true`, the signal itself is unreliable. Don't act on it — wait for fresh data.

---

## Step 9 — Exit Signals

Exit triggers (in order of priority):

1. **AI signal flips to SELL** — primary exit trigger; trust the model
2. **Stop loss hit** — exit immediately; no averaging down, no exceptions
3. **Price reaches take-profit** — book partial or full profits; let the position run only if signal is still BUY and confluence remains high
4. **Earnings within 7 days** — reduce position size to limit binary risk; re-enter after the print if thesis holds
5. **Confluence score drops below 50** — signals have deteriorated; exit or tighten stop

---

## Step 10 — Review Performance

**Page:** Trade Performance

After closing trades, review the backtest metrics:

| Metric | What it tells you |
|--------|------------------|
| **Win Rate** | % of trades that were profitable |
| **Profit Factor** | Gross wins / gross losses — above 1.5 is healthy |
| **Sharpe Ratio** | Risk-adjusted return — above 1.0 is good |
| **Max Drawdown** | Worst peak-to-trough loss — keep below 20% |
| **Calmar Ratio** | Annualised return / max drawdown — above 0.5 is solid |
| **Equity Curve** | Should trend up-right with manageable drawdowns |

**Page:** Signal Accuracy

Shows per-stock hit rates and average return after each signal. Identify which stocks the model is most reliable on and bias your position sizing toward those.

---

## Red Flags — When to Skip

| Signal | Action |
|--------|--------|
| Confluence Score < 50 | Skip — no edge |
| Fair value % is red (overvalued) and large | Reduce size or skip — AI chasing momentum |
| Earnings within 7 days | Wait for the earnings print |
| Stale price warning on signal | Data gap — treat signal with extra skepticism |
| Options flow strongly bearish vs BUY signal | Conflicting data — reduce size by 50% |
| K-Score Value sub-score < 30 | Expensive stock; momentum trade only, set tight stop |
| ADX < 20 (no trend) | Choppy market — signal is compressed; wait for trend |

---

## Quick Decision Checklist

Before entering any trade, confirm:

- [ ] Screener: K-Score ≥ 60, AI Signal = BUY
- [ ] Confluence Score ≥ 65
- [ ] Fair value shows green upside (stock is not overvalued)
- [ ] RSI between 40–65 (not overbought at entry)
- [ ] No earnings within 7 days (or I've sized down accordingly)
- [ ] Entry price is at or below nearest support
- [ ] Stop loss is defined before I enter
- [ ] Position size is within the system's recommended max
- [ ] Options flow is neutral or bullish (not conflicting)

If all boxes are ticked, the trade has a well-defined edge. If more than two are unticked, wait.

---

## Workflow Diagram

```
SCREENER
(K-Score ≥ 60, Signal = BUY, fair-value discount > 0)
        ↓
RANKINGS
(Sort by K-Score, compare candidates)
        ↓
STOCK DETAIL
(Confluence ≥ 65? Fair value upside? Chart above SMA50/200?)
        ↓
GAME PLAN
(AI generates entry / stop / take-profit from real S/R levels)
        ↓
PRICE ALERT
(Email when stock hits your entry limit)
        ↓
BOARD
(Track active position: stage, P&L, catalyst notes)
        ↓
WATCHLIST
(Daily scan — signal changed? Stop close?)
        ↓
EXIT
(SELL signal / stop hit / take-profit reached / earnings risk)
        ↓
TRADE PERFORMANCE
(Review win rate, Sharpe, equity curve — refine your edge)
```
