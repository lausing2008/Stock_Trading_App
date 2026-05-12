# Technical Indicators Reference

All indicators used in the **Signal Engine** (`services/signal-engine`) and **K-Score** (`services/ranking-engine`).

> For how indicators feed into the composite K-Score, see [SCORING.md](SCORING.md).

---

## Signal Engine — `reasons` dict

When you view a stock's AI signal, the `reasons` object contains the raw value of every indicator computed for that bar. Use these to understand *why* the signal fired.

---

## Trend Indicators

### SMA — Simple Moving Average

| Key | Values | What it means |
|-----|--------|---------------|
| `trend_above_sma50` | `true/false` | Close > SMA(50). Stock is above its 50-day average — short/medium-term bullish trend intact. |
| `sma50_above_sma200` | `true/false` | SMA(50) > SMA(200). Long-term bullish alignment — the medium-term average is above the long-term average. Sometimes called "being above the golden cross." |

**How to read:**
- Both `true` → price is rising and trend structure is bullish
- `trend_above_sma50 = false` but `sma50_above_sma200 = true` → short-term pullback within a long-term uptrend (often a buying opportunity)
- Both `false` → stock is in a downtrend

**Limitation:** SMAs are lagging — they confirm a trend that has already started, not one that is about to start. Avoid using them as a sole entry trigger.

---

### Golden Cross / Death Cross Events

| Key | Values | What it means |
|-----|--------|---------------|
| `golden_cross_event` | `true/false` | SMA(50) crossed **above** SMA(200) **today**. Fires only once — on the bar where the crossover happens. |
| `death_cross_event` | `true/false` | SMA(50) crossed **below** SMA(200) **today**. Fires only once — on the bar where the crossover happens. |

**Important distinction:**
- `sma50_above_sma200 = true` means the stock is currently in a bullish alignment (a state, not an event)
- `golden_cross_event = true` means the crossover happened **on this specific bar** — a one-time signal

**Why the event matters:**
A golden cross event is historically significant because institutional algorithms and fund mandates often trigger buying at this point, creating a self-fulfilling short-term price boost. However, false positives are common in ranging markets — always confirm with ADX (> 25) before acting on a cross event.

**How to trade:**
- Golden cross event + ADX > 25 + RSI 45–65 = strong buy setup
- Golden cross event + ADX < 20 = potentially whipsaw — wait for confirmation
- Death cross event = immediate review of position; consider trimming or hedging

---

### ADX — Average Directional Index (14)

| Key | Values | What it means |
|-----|--------|---------------|
| `adx` | `0–100` | Raw ADX value. Measures **trend strength**, not direction. |
| `adx_trending` | `true/false` | `adx > 25` — a meaningful directional move is in progress. |
| `adx_bullish` | `true/false` | `adx_trending = true` AND +DI > −DI — the trend is upward. |

**ADX thresholds:**

| ADX value | Interpretation |
|-----------|----------------|
| < 15 | No trend — market is choppy/ranging. MA crossovers and MACD signals are unreliable. |
| 15–25 | Weak trend developing. Monitor for breakout. |
| 25–40 | Trending market. Trend-following signals (MA crosses, MACD) are more trustworthy. |
| > 40 | Strong trend. Momentum plays are favoured; mean-reversion setups often fail. |
| > 60 | Extreme trend. Potential exhaustion; watch for reversal signals. |

**How to use ADX:**
ADX does not tell you direction — use `adx_bullish` for that. ADX tells you whether a trend is strong enough to trust other directional signals.

- Ignore a golden cross when ADX < 20
- A BUY signal with ADX > 30 is significantly more reliable than one with ADX < 15
- High ADX + deteriorating RSI = trend may be near exhaustion

---

## Momentum Indicators

### RSI — Relative Strength Index (14)

| Key | Values | What it means |
|-----|--------|---------------|
| `rsi` | `0–100` | 14-period RSI of daily closes. |

**Standard thresholds:**

| RSI | Zone | Interpretation |
|-----|------|----------------|
| > 70 | Overbought | Price has risen fast — elevated reversal risk. In a strong uptrend, can stay overbought for extended periods. |
| 55–70 | Bullish | Healthy upward momentum — not yet stretched. |
| 45–55 | Neutral | No strong directional bias. |
| 30–45 | Bearish | Downward momentum. |
| < 30 | Oversold | Price has fallen fast — potential bounce candidate, but confirm trend direction first. |

**K-Score RSI scoring:** peaks at RSI = 55 (bullish but not overbought). RSI at 30 or 80 both score low — the engine penalises extremes in both directions.

**Signal engine scoring:** awards 0.15 points when RSI is between 40 and 70 (bullish range, not extended).

**Tip:** RSI divergence is powerful but not yet automated — if price makes a new high but RSI makes a lower high, the uptrend may be weakening.

---

### MACD — Moving Average Convergence Divergence (12/26/9)

| Key | Values | What it means |
|-----|--------|---------------|
| `macd_hist` | float | MACD histogram = MACD line − Signal line. Positive = bullish momentum, negative = bearish. |
| `macd_rising` | `true/false` | Histogram is larger than the previous bar — momentum is **accelerating** bullishly (or decelerating bearishly). |

**How to read:**
- `macd_hist > 0` = MACD line above signal line — bullish
- `macd_hist > 0` AND `macd_rising = true` = histogram expanding upward — the strongest setup
- `macd_hist > 0` AND `macd_rising = false` = histogram shrinking — momentum fading, trend may be topping
- `macd_hist < 0` AND `macd_rising = true` = histogram rising from negative — potential bullish reversal forming

**Signal engine scoring:** awards full 0.15 pts for positive histogram that is also expanding; 0.08 pts for positive histogram alone. This rewards momentum acceleration over momentum presence.

**Limitation:** MACD is a lagging indicator derived from EMAs. It works best in trending markets (confirm with ADX > 25). In choppy markets it produces frequent false crossovers.

---

### OBV — On-Balance Volume

| Key | Values | What it means |
|-----|--------|---------------|
| `obv_bullish` | `true/false` | The 10-day moving average of OBV is above its 30-day moving average — net volume is flowing into the stock. |

**How OBV is computed:**
- Up day: add today's volume to cumulative OBV
- Down day: subtract today's volume from cumulative OBV
- Flat day: no change

OBV captures whether big money is accumulating (buying quietly) or distributing (selling into strength).

**How to read:**
- `obv_bullish = true` + rising price = volume confirms the move — strong signal
- `obv_bullish = false` + rising price = **divergence** — price rising but volume not confirming. Classic distribution warning; consider reducing position
- `obv_bullish = true` + falling price = accumulation during dip — potential reversal setup

**Tip:** OBV divergence (price and OBV moving in opposite directions) is one of the most reliable leading indicators of a trend change.

---

## Volatility / Range Indicators

### Bollinger Bands %B (20, 2σ)

| Key | Values | What it means |
|-----|--------|---------------|
| `bb_pct_b` | `0.0–1.0` (can exceed range) | Position of price within the Bollinger Bands. 0 = lower band, 0.5 = middle band (SMA20), 1.0 = upper band. |

**Thresholds:**

| %B | Zone | Interpretation |
|----|------|----------------|
| > 1.0 | Above upper band | Extremely overbought or breakout. In a strong trend, can persist; in a ranging market, fade it. |
| 0.8–1.0 | Near upper band | Overbought — elevated short-term pullback risk |
| 0.4–0.8 | Normal bullish range | Healthy — price extended but not stretched |
| 0.2–0.4 | Near midline | Neutral to slightly weak |
| 0.0–0.2 | Near lower band | Oversold — potential bounce if trend is still intact |
| < 0.0 | Below lower band | Extremely oversold or breakdown |

**Signal engine scoring:** awards 0.10 pts when %B is between 0.2 and 0.8 — penalises entries at overbought extremes.

**Bollinger Band squeeze (not yet scored):** when the bands narrow significantly (low band width), volatility has contracted and a directional breakout is likely imminent. The direction of the breakout is not predicted by the squeeze itself — use MACD or OBV for directional bias.

**Key insight:** Unlike RSI, Bollinger Bands are **relative to recent volatility**. A %B of 0.9 means the same thing for a high-vol stock (wide bands) as for a low-vol stock (narrow bands) — it's always measuring position within the current volatility envelope.

---

### Volume Z-Score

| Key | Values | What it means |
|-----|--------|---------------|
| `volume_z` | float (typically −3 to +3) | Number of standard deviations today's volume is above/below its 20-day average. |

**How to read:**

| Volume Z | Interpretation |
|----------|----------------|
| > 2.0 | Very high volume — institutional activity or news event. Confirm direction. |
| 0.5–2.0 | Above-average volume — elevated conviction in the day's move |
| −0.5 to 0.5 | Normal volume — routine trading |
| < −0.5 | Below-average volume — low conviction; be cautious about breakouts |

**Signal engine scoring:** awards 0.05 pts for `volume_z > 0.5`.

**Tip:** A breakout on low volume (volume_z < 0) is a red flag — it often fails and reverses.

---

## Signal Engine Scoring Summary

Each indicator contributes points to a `ta_score` (0–1) that is fused with the ML model probability.

| Indicator | Max contribution | Condition |
|-----------|-----------------|-----------|
| `trend_above_sma50` | +0.15 | Close > SMA(50) |
| `sma50_above_sma200` | +0.10 | SMA(50) > SMA(200) |
| `golden_cross_event` | +0.10 | Crossover fires today |
| `death_cross_event` | −0.10 | Crossover fires today (penalty) |
| `rsi` in 40–70 | +0.15 | Bullish, not overbought |
| `macd_hist > 0` + rising | +0.15 | Momentum accelerating |
| `macd_hist > 0` only | +0.08 | Momentum positive, not accelerating |
| `bb_pct_b` in 0.2–0.8 | +0.10 | Not at overbought/oversold extreme |
| `adx_bullish` | +0.10 | ADX > 25 and +DI > −DI |
| `obv_bullish` | +0.10 | Volume confirming price |
| `volume_z > 0.5` | +0.05 | Above-average volume |
| **Max possible** | **1.00** | All conditions met simultaneously |

The `ta_score` is then fused with the ML model: `fused = 0.6 × ml_prob + 0.4 × ta_score` (TA-only if ML unavailable). The final signal is determined by `fused`:

| Fused probability | Signal |
|-------------------|--------|
| > 0.65 | **BUY** |
| 0.51–0.65 | **HOLD** |
| 0.35–0.50 | **WAIT** |
| < 0.35 | **SELL** |

---

## Indicator Quick-Reference: When to Buy / Sell / Wait

| Indicator | Bullish setup | Bearish setup | Wait / Unclear |
|-----------|--------------|---------------|----------------|
| SMA alignment | Price > SMA50, SMA50 > SMA200 | Price < SMA50, SMA50 < SMA200 | Price < SMA50 but SMA50 > SMA200 (pullback) |
| Golden/Death cross | Golden cross + ADX > 25 | Death cross | Golden cross + ADX < 20 (whipsaw risk) |
| RSI | 45–65 | < 30 with downtrend or > 75 with weakening MACD | < 30 with no trend confirmation |
| MACD | Histogram positive + rising | Histogram negative + falling | Histogram crossing zero |
| Bollinger %B | 0.3–0.7 (not at extremes) | > 0.9 with declining OBV | < 0.2 (wait for bounce confirmation) |
| ADX | > 25 (trend is real) | > 25 with −DI > +DI | < 20 (no trend — avoid trend-following signals) |
| OBV | OBV rising with price | OBV falling with rising price (divergence) | OBV flat |
| Volume Z | > 0.5 on up day | > 1.5 on down day | < 0 (low conviction) |
