# AI Signal — How It Works & How to Read It

Source: [`services/signal-engine/src/generators/signals.py`](../services/signal-engine/src/generators/signals.py)

---

## What the signal is

The AI Signal is a **BUY / HOLD / WAIT / SELL** label that fuses two layers of analysis:

1. **Technical Analysis (TA)** — nine price-based indicators computed from daily OHLCV history
2. **Machine Learning (ML)** — an XGBoost model trained on the same stock's price patterns

Both layers produce a single number called the **fused bullish probability** (0–1).
That probability is then converted into a signal label based on the current market regime.

---

## The two-layer pipeline

```
Daily price history (last 400 bars)
         │
         ├─► TA score  ──────────────────────────────────────┐
         │   (9 indicators → probability 0–1)               │
         │                                                   ▼
         └─► ML probability  ──────► Fused probability = 60% ML + 40% TA
             (XGBoost, if trained)
                                              │
                                              ▼
                                   Market regime filter
                                   (raises BUY threshold in bear market)
                                              │
                                              ▼
                               BUY / HOLD / WAIT / SELL label
```

If the ML model is unavailable (not yet trained, or the ML service is down), the signal falls back to 100% TA. You can tell this happened when the `reasons.ml_probability` field is `null` in the stock detail page.

---

## Signal labels and what they mean

| Label | Fused probability (normal / bear market) | Meaning |
|-------|----------------------------------------|---------|
| **BUY** | > 0.65 / > 0.73 | Technicals and ML both lean bullish. Entry is supported. |
| **HOLD** | 0.50–0.65 / 0.56–0.73 | Mildly bullish lean. No strong entry signal yet. |
| **WAIT** | 0.35–0.50 / 0.35–0.56 | Slightly bearish lean. Keep watching. |
| **SELL** | < 0.35 / < 0.35 | Bearish. Technical picture has deteriorated. |

The BUY and HOLD thresholds are **raised in a bear market** (when S&P 500 is below its 200-day MA) because individual stock signals are less reliable when systemic risk dominates. You need stronger conviction before entering in a broad downtrend.

---

## Confidence and Bullish Probability

Two numbers accompany every signal in the UI:

**Bullish Probability %** — the raw fused probability scaled to a percentage.
- 70%+ = genuinely bullish
- 50% = neutral (coin-flip)
- 30%- = genuinely bearish

**Confidence** — how far the probability is from 50%, scaled to 0–100:
```
confidence = |fused_probability − 0.5| × 200
```
A BUY signal at 80% bullish probability has confidence 60. A HOLD at 55% has confidence 10.
High confidence + BUY is very different from low confidence + BUY — the latter can flip at the next refresh.

---

## The Technical Analysis layer

The TA score is built from nine independent indicators. Each contributes a fixed weight to a probability (0–1). Here is what each one checks and why.

### 1. SMA trend alignment (up to +0.35)

| Condition | Score | What it means |
|-----------|-------|---------------|
| Price above SMA(50) | +0.15 | Short-term trend is up |
| SMA(50) above SMA(200) | +0.10 | Medium-term trend is up — "golden cross regime" |
| Golden cross just fired | +0.10 | SMA(50) crossed *above* SMA(200) this bar — strong bullish regime change |
| Death cross just fired | −0.10 | SMA(50) crossed *below* SMA(200) — regime turned bearish |

The SMA trend is the backbone. A stock can score well on RSI and MACD but still get a WAIT/SELL if it sits below both moving averages. SMA alignment tells you *which direction the market has been trending* — not whether it's about to move.

### 2. RSI (14-period) (up to +0.15)

| RSI range | Score | Zone |
|-----------|-------|------|
| 45–65 | +0.15 | Ideal entry zone — bullish but not overbought |
| 35–45 | +0.08 | Oversold recovery — dip buyers may be stepping in |
| 65–72 | +0.06 | Extended but not extreme — still acceptable |
| > 72 | 0 | Overbought — pullback risk elevated |
| < 35 | 0 | Extreme oversold — too early, can keep falling |

RSI by itself is not a buy/sell trigger. The app uses it to measure *momentum quality* — whether the current price level is entering from a healthy base (RSI 40–60) or from an overextended extreme (RSI > 70 or < 30).

### 3. Stochastic RSI (up to +0.10, down to −0.08)

Stochastic RSI normalises RSI itself into a 0–1 range, then smooths it. It detects *when RSI is at an extreme relative to itself* — faster and more sensitive than raw RSI.

| Condition | Score | Signal |
|-----------|-------|--------|
| Stoch RSI K < 0.20 (RSI oversold zone) | +0.10 | RSI is at a low extreme — potential dip entry |
| Stoch RSI K just crossed up through 0.20 | +0.05 | Fresh oversold recovery — early confirmation |
| Stoch RSI K > 0.80 (RSI overbought zone) | −0.08 | RSI is stretched — upside may be limited |

When both RSI and Stochastic RSI are oversold simultaneously (+0.15 + 0.10 = +0.25), the signal has a strong oversold-recovery basis.

### 4. RSI divergence (up to ±0.10)

Looks back 10 bars to detect when price and momentum are moving in opposite directions.

| Divergence | Score | What it means |
|------------|-------|---------------|
| Bearish: price higher, RSI lower | −0.10 | Momentum is fading even as price rises — pullback risk |
| Bullish: price lower, RSI higher | +0.08 | Momentum is recovering even as price falls — reversal potential |
| None | 0 | No divergence detected |

Bearish RSI divergence is one of the most reliable warnings before a price top. If a stock shows BUY but has bearish RSI divergence, treat the signal with more caution.

### 5. MACD (up to +0.20)

| Condition | Score | Signal |
|-----------|-------|--------|
| MACD histogram > 0 AND rising | +0.15 | Momentum is accelerating upward — strong bullish |
| MACD histogram > 0 (but not rising) | +0.08 | Upward momentum, but slowing |
| MACD line just crossed zero from below | +0.05 | Trend-direction confirmation — MACD turned positive |

MACD histogram captures *momentum acceleration*, not just direction. `macd_hist > 0 AND macd_rising = true` is the strongest MACD reading. If `macd_hist > 0` but `macd_rising = false`, momentum exists but may be peaking.

### 6. Bollinger Bands %B (up to +0.10)

%B = `(price − lower band) / (upper band − lower band)`

| %B | Score | Zone |
|----|-------|------|
| 0.20 to 0.80 | +0.10 | Price is in the middle zone — not at an extreme |
| < 0.20 | 0 | Near lower band — could be oversold, but also falling through |
| > 0.80 | 0 | Near upper band — could be overbought |

This indicator rewards stocks trading in the middle of their Bollinger range — suggesting controlled momentum rather than parabolic extension or breakdown.

### 7. ADX — trend strength (up to +0.10)

ADX measures *how strongly the price is trending*, regardless of direction. DI+ and DI− determine which direction.

| Condition | Score | Signal |
|-----------|-------|--------|
| ADX > 25 AND DI+ > DI− | +0.10 | Strong upward trend — signals are more reliable |
| ADX > 25 AND DI+ ≤ DI− | 0 | Strong trend, but downward |
| ADX ≤ 25 | 0 | Weak/ranging — all other signals are less reliable |

When `adx_trending = false` in the reasons field, the market is choppy. Reduce conviction in any BUY signal — the TA indicators are less meaningful in a sideways market.

### 8. OBV (On-Balance Volume) (up to +0.10)

OBV tracks whether volume is flowing into or out of the stock.

| Condition | Score | Signal |
|-----------|-------|--------|
| OBV 10-day avg > OBV 30-day avg | +0.10 | Recent volume is net positive — buyers outnumber sellers |

`obv_bullish = true` means that on days the stock went up, the volume was higher than on days it went down. This is the most direct confirmation that price direction has volume support.

### 9. Volume Z-score (up to +0.05)

| Condition | Score | Signal |
|-----------|-------|--------|
| Volume > 0.5 std above 20-day average | +0.05 | Above-average participation — signal has more weight |

Small bonus for elevated volume. A BUY signal on 3× average volume is more significant than one on light holiday trading.

---

## The Machine Learning layer

The ML layer uses an XGBoost classifier trained on the stock's own price history. It predicts the **bullish probability** — the likelihood that the price is higher at a future point given the current pattern.

Key facts:

- Trained per-symbol, not a universal model. MU's model is trained on MU data only.
- Features are price-based: returns at multiple timeframes, RSI, MACD, ATR, volume ratios, and others derived from the same daily bars the TA layer uses.
- The model is retrained once per day at post-close (16:30). Intra-day refreshes use the model from the previous close.
- If the model has never been trained (new stock), the signal is 100% TA until you click **Train All** on the dashboard.
- ML failure (service down, model missing) is non-fatal — the system logs a warning and falls back to TA-only.

The ML probability is blended with the TA score at **60% ML weight**. This means the ML layer has a stronger vote than any single TA indicator. When ML and TA agree, the signal is high-confidence. When they disagree, the fused result is pulled toward whichever is stronger.

---

## Why a stock shows a specific signal

### Why BUY

All of these (or most) are true:

- Price is above SMA(50) and SMA(50) is above SMA(200) — uptrend intact
- RSI is in the 45–65 zone — not overbought
- MACD histogram is positive and rising — momentum accelerating
- OBV is bullish — volume is confirming the price direction
- ADX > 25 with DI+ > DI− — the trend has real strength
- ML model gives probability > 65% bullish

**Common BUY patterns:**
- Stock is in a clean uptrend with RSI holding 50–60 (healthy momentum, not extended)
- Stock pulled back to SMA(50), RSI dipped to 40–45, Stoch RSI turned oversold, then recovered — classic buy-the-dip setup
- Golden cross just fired — SMA(50) crossed SMA(200) for the first time after a prolonged downtrend

### Why HOLD

The stock is mildly bullish but lacks full conviction. Typical reasons:

- Price above SMA(50) but RSI is elevated (65–72) — extended short-term
- MACD is positive but not rising — momentum exists but isn't accelerating
- ML and TA are not in full agreement — fused probability is 55–65%
- Market is choppy (ADX < 25) — signals are uncertain

HOLD means "don't add to a new position, but don't exit an existing one either."

### Why WAIT

Slightly bearish lean. Common causes:

- Price below SMA(50) — short-term trend is down
- RSI is in the 35–50 range — recovering but not yet confirmed bullish
- MACD histogram is negative — momentum is on the bearish side
- Stoch RSI is neither oversold nor overbought — no clear reversal signal yet

WAIT means the setup is deteriorating but not yet conclusively bearish. Watch for a RSI drop below 35 (Stoch RSI oversold trigger) or a MACD zero-line crossover as the next signal.

### Why SELL

Multiple bearish indicators are aligned. Typical combination:

- Price below both SMA(50) and SMA(200) — both short and medium-term trends are down
- RSI > 70 (overbought) or RSI < 35 (no support, freefall mode)
- MACD histogram negative and falling — momentum accelerating down
- OBV not bullish — volume confirming the price decline
- Death cross: SMA(50) just dropped below SMA(200)
- ML probability < 35%

**Note on overbought SELL:** A stock can show SELL while the price is still rising, if RSI is extremely overbought (> 72) and Stoch RSI is in the overbought zone. This is not a crash warning — it means the recent run is overextended and a short-term pullback is likely. Hold existing positions; do not add.

---

## The market regime adjustment

The signal engine checks whether the **S&P 500 is above or below its 200-day moving average** on every signal computation.

| Regime | BUY threshold | HOLD threshold |
|--------|--------------|----------------|
| Bull (S&P above 200MA) | Fused prob > 0.65 | Fused prob > 0.50 |
| Bear (S&P below 200MA) | Fused prob > **0.73** | Fused prob > **0.56** |

In a bear market, the same stock that would get a BUY in a bull market might only get a HOLD. This is intentional — individual stock signals are less actionable when the broad market is in a sustained downtrend. You can see the current regime on the **Fear & Greed** section of any stock detail page.

If a stock shows BUY in a bear market, it required a particularly strong technical and ML setup to clear the higher threshold. These signals carry more weight, not less.

---

## Reading the reasons field

The stock detail page (`/stock/[symbol]`) shows the full indicator breakdown in the sidebar. Here is the complete field reference:

| Field | What to look for |
|-------|-----------------|
| `market_regime` | `bull` or `bear`. Bear raises the BUY threshold. |
| `trend_above_sma50` | `true` = short-term trend is up. False → uptrend not intact. |
| `sma50_above_sma200` | `true` = medium-term trend is up. Both true = clean uptrend. |
| `golden_cross_event` | `true` = SMA(50) just crossed above SMA(200) — regime change. |
| `death_cross_event` | `true` = SMA(50) just crossed below SMA(200) — regime change. |
| `rsi` | 45–65 = ideal entry. > 70 = extended. < 40 = oversold dip zone. |
| `stoch_rsi_k` | < 0.20 = oversold extreme. > 0.80 = overbought extreme. |
| `stoch_rsi_cross_up` | `true` = just recovered from oversold — early buy signal. |
| `rsi_divergence` | `"bearish"` = price up but RSI declining (momentum fading). `"bullish"` = price down but RSI recovering. |
| `macd_hist` | Positive + rising = strongest BUY confirmation. Negative + falling = SELL confirmation. |
| `macd_rising` | `true` = histogram grew since last bar — momentum increasing. |
| `macd_zero_cross_up` | `true` = MACD line just turned positive — trend direction confirmed. |
| `bb_pct_b` | 0.2–0.8 = healthy middle zone. > 0.85 = near upper band (extended). < 0.15 = near lower band. |
| `adx` | < 20 = choppy, ignore other signals. 20–40 = moderate trend. > 40 = strong trend. |
| `adx_trending` | `true` if ADX > 25. If false, all TA signals have lower reliability. |
| `adx_bullish` | `true` if ADX trending AND DI+ > DI−. Upward directional trend confirmed. |
| `obv_bullish` | `true` = volume confirms price direction. The best volume-based confirmation. |
| `volume_z` | Standard deviations above 20-day average. > 1.5 = unusually high volume. |
| `ta_score` | Raw TA probability before ML blending (0–1). Compare to ML to see which is driving the signal. |
| `ml_probability` | XGBoost bullish probability (0–1). `null` = model not trained yet. |

---

## High-confidence vs low-confidence signals

The confidence number tells you how decisive the signal is. Two stocks can both show BUY but behave very differently:

| Confidence | Bullish prob | What it means |
|------------|-------------|---------------|
| 60–80 | 80–90% | Strong signal — TA, ML, and market regime all aligned |
| 30–50 | 65–75% | Moderate signal — cleared the threshold but not by much |
| 5–20 | 55–60% | Weak signal — just barely crossed into BUY territory; can flip on the next refresh |

When confidence is below 20, treat the signal as "leaning BUY" rather than a confirmed entry. It is common for these to oscillate between BUY and HOLD day-to-day as minor price moves shift the RSI or MACD.

---

## Signal age and freshness

Dashboard cards show a colored timestamp below each signal badge:

| Color | Age | What to do |
|-------|-----|-----------|
| Green | < 1 hour | Fresh — computed this session |
| Yellow | 1–8 hours | Current trading day, still reliable |
| Orange | 8–24 hours | From earlier today or overnight |
| Red | > 24 hours | Stale — may not reflect today's price action |

Signals are persisted to the database after each scheduled refresh (5× per trading day). On weekends and non-trading days, the signal age will naturally be red — this is expected and does not mean the signal has changed. Click **⚡ Refresh Signals** in the dashboard toolbar to recompute fresh signals for all your tracked stocks immediately.

---

## What this signal cannot do

- **Earnings gaps**: The signal is trained on normal price behavior. An earnings surprise can move a stock 15–30% in one session — no technical signal predicts this. Check the earnings calendar on the stock detail page and reduce position size in the week before results.
- **Macro shocks**: Rate decisions, geopolitical events, and sector rotations can override any technical setup. The market regime filter (bull/bear) partially accounts for broad trends, but cannot predict sudden shocks.
- **Thin liquidity**: For small-cap or illiquid HK stocks with few daily bars, the TA indicators are less reliable. Volume Z-score will be low, ADX may be weak, and OBV may show erratic values.
- **New stocks**: If a stock was just added to the system and has less than 200 daily bars, indicators like SMA(200) and Momentum sub-scores will not yet be fully computed. The signal will have lower confidence until more history is ingested.
- **ML cold start**: A newly added stock has no trained ML model. The signal is 100% TA (visible when `ml_probability = null`). Click **Train All** on the dashboard to train the model on existing history.

---

## Quick reference — strongest setups

### Highest-conviction BUY setup

All five conditions are present:

1. SMA(50) above SMA(200), price above SMA(50) — clean uptrend
2. RSI 45–60, Stoch RSI just recovered from oversold — dip bought, not overextended
3. MACD histogram positive and rising, or MACD zero-line crossover — momentum confirmed
4. OBV bullish — volume confirms direction
5. ML probability > 70% — model agrees with TA

Add analyst consensus BUY, earnings > 30 days away, and insider net buying for maximum conviction (see [SCORING.md — Combining AI Signal and Analyst Ratings](SCORING.md#combining-ai-signal-and-analyst-ratings)).

### Highest-risk false BUY

Treat a BUY signal with caution when:

- Bearish RSI divergence is present (`rsi_divergence = "bearish"`) — price is rising but momentum is fading
- Stoch RSI K > 0.80 — RSI itself is in the overbought zone
- ADX < 20 — market is choppy, signals are noise
- `ml_probability` is null — no ML layer; TA-only signal is weaker
- Market regime is `bear` and confidence is < 30 — barely cleared the higher threshold
- Signal age is red (> 24 hours) and a volatile session has passed since

---

For how to combine this signal with K-Score, analyst ratings, insider activity, and earnings timing, see [SCORING.md](SCORING.md).
