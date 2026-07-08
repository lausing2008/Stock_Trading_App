# Technical Indicators — Educational Reference

This document explains every indicator used in the **AI Signal engine** and **K-Score** in plain language.
Each indicator has: what it is, the theory behind it, how to read the values, where it fits in the signal
decision, and what its limitations are.

> **How to navigate:** start with [Signal Decision Flow](#signal-decision-flow) to see the big picture,
> then dive into whichever indicator you want to understand deeply.
>
> For K-Score sub-score formulas, see [SCORING.md](SCORING.md).
> For signal notification criteria, see [FEATURES.md](FEATURES.md#signal-change-email-notifications).

---

## Signal Decision Flow

Here is how the AI Signal reaches a BUY/SELL/HOLD decision:

```
Daily price bars (OHLCV)
         │
         ▼
┌─────────────────────────────────────────────────┐
│              TA Score  (0 → 1)                  │
│                                                 │
│  Trend    → SMA50, SMA200, Golden/Death Cross   │
│  Momentum → RSI, Stoch RSI, MACD, MACD zero    │
│  Divergence → RSI divergence (price vs RSI)     │
│  Volatility → Bollinger %B                      │
│  Volume   → OBV, Volume Z-score                 │
│  Trend str→ ADX (+DI / −DI)                     │
└──────────────────────┬──────────────────────────┘
                       │ weight: 40%
                       ▼
              ┌─────────────────┐
              │  Fused Signal   │◄── ML probability (60%)
              └────────┬────────┘    (XGBoost model, if trained)
                       │
                       ▼
        ┌──────────────────────────────┐
        │   Market Regime Adjustment   │
        │  Bull market: BUY > 0.65     │
        │  Bear market: BUY > 0.73     │
        └──────────────┬───────────────┘
                       │
                       ▼
              BUY / HOLD / WAIT / SELL
```

**Key principle:** No single indicator decides the signal. Points accumulate from all indicators, the ML
model adds its view, and the market regime determines how high the bar is set. A BUY signal means
*most* of the indicators are aligned — not that every one agrees.

---

## Part 1 — Trend Indicators

Trend indicators answer: **which direction is price moving over time?**
They are lagging (they confirm what already happened) but reliable.

---

### SMA — Simple Moving Average

**What it is:**
The SMA is the arithmetic mean of the closing price over the last N days.
SMA(50) = average of last 50 closes. SMA(200) = average of last 200 closes.

Think of it as the "centre of gravity" of recent prices. Price tends to oscillate around it —
sometimes above (healthy uptrend), sometimes below (downtrend or correction).

**Why it matters:**
- SMA(50) represents about 2.5 months of daily data. Many institutional fund managers and
  quantitative systems use it as a tactical trend filter: buy above it, reduce exposure below it.
- SMA(200) represents about 10 months of data. It is the most widely followed long-term trend
  indicator on Wall Street. Many hedge fund mandates prohibit holding stocks below their 200-day MA.
- Because so many players react to the same MAs, they become **self-reinforcing support/resistance levels**.

**In the app:**

| Reason key | Value | What it tells you |
|------------|-------|-------------------|
| `trend_above_sma50` | true/false | Close > SMA(50) — stock is in a short/medium-term uptrend |
| `sma50_above_sma200` | true/false | SMA(50) > SMA(200) — the medium-term trend is above the long-term trend |

**Reading the combination:**

| `trend_above_sma50` | `sma50_above_sma200` | Interpretation |
|---------------------|----------------------|----------------|
| ✓ true | ✓ true | Both aligned bullish — the strongest trend setup |
| ✓ true | ✗ false | Price recovered above SMA50 but still in a bear regime. Bounce, not trend reversal. |
| ✗ false | ✓ true | Pullback within a larger uptrend — often a buying opportunity if RSI is recovering |
| ✗ false | ✗ false | Full downtrend — avoid new entries |

**Scoring:** `trend_above_sma50` adds +0.15 to TA score. `sma50_above_sma200` adds +0.10.

**Limitation:** SMAs are slow. A SMA(200) takes 200 days of data to update and reacts to price changes
very gradually. A stock can fall 20% before the SMA(200) even starts sloping down.
Never use SMAs alone — always combine with a momentum indicator (RSI, MACD) to confirm the move is real.

---

### Golden Cross & Death Cross

**What they are:**
These are one-time events — they fire on the specific bar where SMA(50) crosses SMA(200).

- **Golden Cross** — SMA(50) crosses *above* SMA(200). Marks the transition from bear to bull regime.
- **Death Cross** — SMA(50) crosses *below* SMA(200). Marks the transition from bull to bear regime.

**Why they matter:**
The cross events receive media attention and trigger systematic rebalancing by index funds, risk
parity strategies, and algorithmic funds with trend-following rules. This creates a **short-term
self-fulfilling effect**: the cross causes buying (golden) or selling (death), which can persist for
days to weeks.

**In the app:**

| Reason key | Value | Fires |
|------------|-------|-------|
| `golden_cross_event` | true/false | Only on the day SMA50 > SMA200 for the first time |
| `death_cross_event` | true/false | Only on the day SMA50 < SMA200 for the first time |

Note: `sma50_above_sma200 = true` means the stock is *currently* in a golden cross regime.
`golden_cross_event = true` means it *just* crossed today. These are different things.

**Scoring:**
- Golden cross event: +0.10 bonus to TA score (one-time bonus on event day)
- Death cross event: −0.10 penalty to TA score (one-time penalty on event day)
- After the event, the regime state (`sma50_above_sma200`) continues to contribute but the event bonus does not repeat

**Real-world caution:**
Golden crosses fail frequently in **choppy markets** (ADX < 20). The SMA(50) and SMA(200) can
cross back and forth ("whipsaw") without producing a real trend. Always check:
- Is ADX > 25? (confirms a real trend exists)
- Is RSI in the 45–65 range? (confirms momentum is building, not extended)

A golden cross with ADX < 20 = wait. A golden cross with ADX > 30 + RSI 50–60 = strong setup.

---

### ADX — Average Directional Index

**What it is:**
ADX measures **trend strength** — how strongly price is moving in a direction, regardless of which
direction that is. It does NOT tell you if the trend is up or down. That is what +DI and −DI are for.

ADX is derived from two directional indicators:
- **+DI (Plus Directional Indicator):** measures upward pressure
- **−DI (Minus Directional Indicator):** measures downward pressure

All three use Wilder smoothing (an exponential moving average with alpha = 1/period, which is
slower than standard EMA). The computation takes 14 bars to fully warm up.

**Why it matters:**
Many indicators give unreliable signals in choppy, directionless markets. ADX is the filter that
tells you whether to trust your trend-following signals. A golden cross in a choppy market is noise.
A golden cross with ADX = 35 is meaningful.

**In the app:**

| Reason key | Value | Meaning |
|------------|-------|---------|
| `adx` | 0–100 | Raw ADX value |
| `adx_trending` | true/false | ADX > 25 — a real trend is in progress |
| `adx_bullish` | true/false | ADX > 25 AND +DI > −DI — the trend is upward |

**Thresholds:**

| ADX | Market condition | How to use other signals |
|-----|-----------------|--------------------------|
| < 15 | Flat / choppy | Ignore all trend-following signals (MA crosses, MACD). Only mean-reversion setups valid. |
| 15–25 | Weak trend developing | Signals are possible but unconfirmed. Wait for ADX to exceed 25. |
| 25–40 | Solid trending market | Trend-following signals (MA crosses, MACD histogram) are reliable. |
| > 40 | Strong trend | Momentum is strong. Trend continuation is likely. Reversal setups often fail. |
| > 60 | Extreme trend | Potential exhaustion. Watch for reversal signals (RSI divergence, OBV falling). |

**Scoring:** `adx_bullish` adds +0.10 to TA score. `adx_trending = false` means you get 0 from this component — but no penalty; the other indicators decide.

**Real example:** KGS with ADX = 65 means it is in a very strong trend. Combined with RSI = 76 and bearish RSI divergence, the engine correctly identifies this as an extended move — the *direction* is up but the *timing* for a new entry is poor.

---

## Part 2 — Momentum Oscillators

Momentum oscillators answer: **is the rate of price change accelerating or decelerating?**
They are best for timing entries and exits within a trend identified by the trend indicators.

---

### RSI — Relative Strength Index (14)

**What it is:**
RSI measures the speed and magnitude of recent price changes. It compares average gains to average
losses over the last 14 days, producing a score from 0 to 100.

Formula:
```
RSI = 100 − (100 / (1 + RS))
RS  = Average Gain (14 days) / Average Loss (14 days)
```

The smoothing method used in this app is **Wilder's EWM** (alpha = 1/14), which matches the
original Welles Wilder specification. This is slightly different from a simple 14-day average.

**Why it matters:**
Price does not move in a straight line. After a stock rises strongly, momentum slows and price tends
to pull back or consolidate. RSI captures this — it gets "overbought" (too much bullish momentum)
or "oversold" (too much bearish momentum) and reverts toward 50.

**In the app:**

| `rsi` value | Zone | Signal engine scoring |
|-------------|------|-----------------------|
| < 35 | Oversold | No credit (extreme zone — wait for recovery confirmation) |
| 35–45 | Recovering | +0.08 (oversold recovery in progress) |
| 45–65 | Ideal entry zone | +0.15 (maximum score — momentum is bullish, not extended) |
| 65–72 | Extended | +0.06 (still positive but diminishing returns) |
| > 72 | Overbought | 0 credit (elevated pullback risk — poor entry timing) |

**Why the tighter range (v2 improvement):**
The original engine gave the same +0.15 for RSI = 45 and RSI = 69. But RSI = 69 is close to
overbought and a much riskier entry than RSI = 52. The new tiered scoring reflects this.

**Classic RSI rules of thumb:**
- RSI > 70 on first visit in an uptrend = warning, not automatic sell
- RSI > 80 + declining = strong reversal signal
- RSI < 30 = potential bounce, but confirm the downtrend isn't accelerating
- RSI 50 = the line between bull and bear momentum. Stocks in uptrends tend to hold RSI above 50.

**Limitation:** RSI can stay overbought for extended periods in strong trending stocks.
A single RSI > 70 reading is not a sell signal on its own. That is why Stochastic RSI was added —
it tells you *whether RSI itself is at an extreme* within its recent range.

---

### Stochastic RSI (%K, %D)

**What it is:**
Stochastic RSI applies the Stochastic Oscillator formula to RSI values instead of price.
This makes it more sensitive and faster to signal turning points than raw RSI.

Formula:
```
Raw %K = (RSI − Lowest RSI over 14 days) / (Highest RSI − Lowest RSI over 14 days)
%K     = 3-bar SMA of Raw %K     (smoothed Stochastic RSI)
%D     = 3-bar SMA of %K          (signal line)
```

The result is always between 0 and 1 (0–100%). Think of it as: where is today's RSI relative
to its own recent high and low?

**Why it was added (v2):**
Plain RSI at 55 tells you momentum is positive. But it doesn't tell you whether RSI=55 is near
the top of its recent range or the bottom. Stochastic RSI fills this gap.

Example: a stock's RSI has ranged between 48 and 72 over the last 14 days. RSI is currently 52.
- Plain RSI = 52, looks neutral
- StochRSI %K = (52 − 48) / (72 − 48) = 0.17 → below the 0.20 oversold threshold
- Conclusion: RSI is near its recent floor → potential buy entry, even though plain RSI looked neutral

**In the app:**

| Reason key | Value | Meaning |
|------------|-------|---------|
| `stoch_rsi_k` | 0–1 | The %K value (smoothed, between 0 and 1) |
| `stoch_rsi_d` | 0–1 | The %D signal line (slower, 3-bar SMA of %K) |
| `stoch_rsi_oversold` | true/false | %K < 0.20 — RSI is at the bottom of its recent range |
| `stoch_rsi_overbought` | true/false | %K > 0.80 — RSI is at the top of its recent range |
| `stoch_rsi_cross_up` | true/false | %K just crossed above 0.20 — fresh exit from oversold zone |

**Thresholds (display as %):**

| %K | Zone | Interpretation |
|----|------|----------------|
| 0–20% | Oversold | RSI is at a low extreme relative to recent weeks. Strong buy timing signal. |
| 20–50% | Rising / Neutral | RSI recovering from oversold, or neutral. |
| 50–80% | Elevated / Bullish | RSI running high — not a problem, just not a great entry |
| 80–100% | Overbought | RSI is at a high extreme — elevated risk of momentum reversal |

**Scoring:**
- `stoch_rsi_oversold = true`: +0.10 to TA score
- `stoch_rsi_overbought = true`: −0.08 penalty
- `stoch_rsi_cross_up = true`: additional +0.05 (the fresh crossing is stronger than just being oversold)

**Highest-conviction setup:** StochRSI crosses up from oversold (< 20%) while price is above SMA50
and MACD histogram is turning positive. This is a "dip within an uptrend" entry — one of the
most reliable setups in technical analysis.

**Limitation:** Stochastic RSI is fast and sensitive — it can be oversold and overbought repeatedly
in a sideways market. Always confirm with ADX (> 20) to ensure there is a trend worth trading.

---

### RSI Divergence

> **Status: DISABLED** — The divergence detector is currently inactive in the signal engine. The `rsi_divergence` field always returns `"none"` and no score adjustment is applied. The simplified 10-bar lookback implementation was prone to false positives; a future version will use proper pivot-high/low detection before re-enabling it.

**What it is:**
RSI divergence occurs when price and RSI move in opposite directions. This signals that momentum is
diverging from price — a warning that the current trend may be running out of steam.

There are two types:

**Bearish divergence** — the most important one for protecting profits:
- Price is making a higher high (new recent peak)
- RSI is making a lower high (weaker peak than before)
- Meaning: price went up, but it took less and less momentum to do it. Sellers are gaining.

**Bullish divergence** — useful for finding bottoms:
- Price is making a lower low (new recent trough)
- RSI is making a higher low (stronger than before)
- Meaning: price fell, but each leg down was weaker. Buyers are quietly absorbing selling pressure.

**How the app computes it (10-bar lookback):**
The engine compares `close[-1]` vs `close[-11]` (10 bars back) and `rsi[-1]` vs `rsi[-11]`:
- If price is higher but RSI is lower → bearish divergence
- If price is lower but RSI is higher → bullish divergence

This is a simplified version. The rigorous method compares actual pivot highs/lows, but the
simplified version still captures the vast majority of meaningful divergences.

**In the app:**

| Reason key | Value | Meaning |
|------------|-------|---------|
| `rsi_divergence` | "bearish" / "bullish" / "none" | Type of divergence detected over the last 10 bars |

**Scoring:**
- Bearish divergence: −0.10 penalty
- Bullish divergence: +0.08 bonus
- None: no adjustment

**Real example:** KGS with RSI = 76 and bearish divergence. Price was at a recent high but RSI was
declining from its own recent peak. The engine correctly applied a −0.10 penalty, helping
push the signal toward SELL despite strong trend indicators.

**Why bearish divergence is so useful:**
It often appears 1–3 weeks *before* the price actually rolls over. By the time the SMA50 turns
down or MACD goes negative, you could have already exited. Divergence gives you early warning.

**Limitation:** Divergence can persist for weeks in strongly trending stocks before anything happens.
Do not use it as a sole sell signal. Treat it as a "raise your alert level" signal and wait for
another confirming signal (MACD turning negative, RSI breaking below 50) before acting.

---

### MACD — Moving Average Convergence Divergence (12/26/9)

**What it is:**
MACD measures the relationship between two Exponential Moving Averages (EMAs) of the closing price.

```
MACD line    = EMA(12) − EMA(26)
Signal line  = EMA(9) of MACD line
Histogram    = MACD line − Signal line
```

- When the shorter EMA (12) is above the longer EMA (26), MACD is positive → bullish momentum
- When MACD is above its signal line, the histogram is positive → momentum is accelerating

**Why EMAs instead of SMAs:**
EMAs give more weight to recent price data, making them faster to react than SMAs.
The 12/26/9 parameters are the most widely used (Gerald Appel's original specification from the 1970s)
and are now baked into enough trading systems that they create self-reinforcing effects.

**In the app:**

| Reason key | Value | Meaning |
|------------|-------|---------|
| `macd_hist` | float (positive or negative) | MACD line minus signal line. Positive = bullish. |
| `macd_rising` | true/false | Histogram is larger than the previous bar — momentum accelerating |
| `macd_zero_cross_up` | true/false | MACD line just crossed above zero (v2 addition) |

**How to read all combinations:**

| `macd_hist` | `macd_rising` | `macd_zero_cross_up` | Interpretation |
|-------------|---------------|----------------------|----------------|
| Positive | true | — | Best: momentum accelerating in bullish direction |
| Positive | false | — | Momentum exists but slowing — may be topping |
| Negative | true | — | Bearish but recovering — potential reversal forming |
| Negative | false | — | Momentum accelerating downward — avoid |
| Positive | — | true | MACD just confirmed the trend is up (crossed zero line) |

**Why the zero-line crossover matters (v2 addition):**
A MACD histogram can be positive (+0.05) even when MACD is still below zero — meaning the histogram
crossed its signal line but the actual trend hasn't confirmed. When the MACD *line itself* crosses
zero, it means the short-term EMA has crossed above the long-term EMA — a more reliable trend
direction signal. This is a stronger confirmation and earns an extra +0.05 bonus.

**Scoring:**
- `macd_hist > 0` AND `macd_rising`: +0.15
- `macd_hist > 0` only: +0.08
- `macd_zero_cross_up`: +0.05 bonus (additive)

**Limitation:** Like all EMA-based indicators, MACD lags price. In fast-moving markets it can still
be negative when price has already bottomed and started recovering. Combine with RSI/Stoch RSI for
better entry timing.

---

## Part 3 — Volume Indicators

Volume indicators answer: **is smart money confirming this price move?**
Price alone can be manipulated by a small number of large orders. Volume shows whether the broader
market is participating.

---

### OBV — On-Balance Volume

**What it is:**
OBV is a running total of volume, adding volume on up days and subtracting it on down days.

```
Up day:   OBV = OBV_yesterday + today's volume
Down day: OBV = OBV_yesterday − today's volume
Flat day: OBV = OBV_yesterday
```

The absolute number doesn't matter — only the trend of OBV matters.
Rising OBV = net volume flowing into the stock (accumulation).
Falling OBV = net volume flowing out (distribution).

**In the app:**
The engine compares the 10-day moving average of OBV to the 30-day moving average:
- `obv_bullish = true` if the short-term OBV average is above the long-term average → accumulation in progress

| Reason key | Value | Meaning |
|------------|-------|---------|
| `obv_bullish` | true/false | Net volume trend is positive over the last month |

**Scoring:** `obv_bullish = true` adds +0.10 to TA score.

**The most important OBV signal — OBV divergence:**
When price is rising but OBV is falling, it means price is going up on declining volume. This is
called distribution — large players are selling into the strength while retail buyers push the price up.
This divergence almost always precedes a significant price decline.

Conversely, when price is falling but OBV is flat or rising, it means buyers are quietly absorbing
the selling pressure. This often precedes a reversal.

> OBV divergence from price is one of the most reliable leading indicators in technical analysis.
> The OBV reason in the app only tells you the short-term vs long-term average, not explicit divergence,
> but you can read divergence manually from the chart — OBV is plotted in the volume panel.

**Limitation:** OBV gives equal weight to all volume, regardless of how large the move was.
A 10-million-share day with a flat close (+0.01%) adds 10M to OBV just like a 10-million-share day
with a +5% move. The Volume Z-score helps compensate for this.

---

### Volume Z-Score

**What it is:**
The Volume Z-score measures how unusual today's volume is relative to the recent 20-day average,
expressed in standard deviations.

```
Volume Z = (Today's volume − 20-day average volume) / 20-day standard deviation
```

A Z-score of 0 means completely average volume. +2 means today was 2 standard deviations above
average — unusual enough to suggest institutional activity, news-driven trading, or a significant
supply/demand imbalance.

**In the app:**

| Reason key | Value | Meaning |
|------------|-------|---------|
| `volume_z` | float | Standard deviations from average. Typically −2 to +4 on event days. |

**How to read it:**

| Volume Z | Interpretation |
|----------|----------------|
| > 2.0 | Extreme volume — institutional activity, news event, or earnings reaction. Look for the cause. |
| 0.5–2.0 | Above-average conviction. Directional move has real participation. |
| −0.5 to 0.5 | Normal volume — routine trading, no special signal. |
| < −0.5 | Low conviction. Breakouts on thin volume often fail. |

**Scoring:** +0.05 if `volume_z > 0.5`.

**Key rules:**
- A breakout to a 52-week high on volume_z = −0.3 (below average) is a red flag — the market is
  not enthusiastic. Wait for a retest with better volume.
- A selloff on volume_z = 2.5 is capitulation — sellers are exhausted. This often marks a short-term floor.
- A rally on volume_z = 1.8 is institutional buying — the move has backing.

---

## Part 4 — Volatility Indicators

Volatility indicators answer: **how wide is price swinging, and is the stock near an extreme?**

---

### Bollinger Bands %B (20-day, 2 standard deviations)

**What it is:**
Bollinger Bands draw a channel around price using the 20-day SMA and its standard deviation.
- Upper band = SMA(20) + 2 × StdDev(20)
- Lower band = SMA(20) − 2 × StdDev(20)

**%B** (percent B) tells you *where* the current price sits within this channel:
```
%B = (Close − Lower Band) / (Upper Band − Lower Band)
```

- %B = 1.0 → price is exactly at the upper band
- %B = 0.5 → price is at the midline (SMA20)
- %B = 0.0 → price is exactly at the lower band

By construction, about 95% of prices fall within the bands (within 2 standard deviations of the mean).

**In the app:**

| Reason key | Value | Meaning |
|------------|-------|---------|
| `bb_pct_b` | 0.0–1.0 (can go outside) | Position within the Bollinger channel |

**Thresholds:**

| %B | Interpretation |
|----|----------------|
| > 1.0 | Above the upper band — extreme overbought or momentum breakout |
| 0.80–1.0 | Near upper band — elevated pullback risk |
| 0.40–0.80 | Healthy bullish zone |
| 0.20–0.40 | Near midline — neutral |
| 0.00–0.20 | Near lower band — potential bounce zone |
| < 0.0 | Below the lower band — extreme oversold or breakdown |

**Scoring:** +0.10 when %B is between 0.2 and 0.8 — rewards stocks that are not at extremes,
penalises entries at the overbought upper band.

**Important: bands are relative to volatility.**
A high-volatility stock has wider bands than a low-volatility stock. %B = 0.9 means the same
"near the top of recent volatility" for both. This makes %B more comparable across different stocks
than a raw "price vs SMA" measure.

**Bollinger Band squeeze (bonus insight — not currently scored):**
When bands narrow significantly, it means volatility has contracted. This almost always precedes
a sharp directional move. The squeeze itself doesn't predict direction — use MACD or OBV for that.
Look for a squeeze followed by price breaking above the upper band with high volume → bullish breakout.

---

## Part 5 — Market Context

Market context indicators answer: **what is the overall market environment?**
Individual stock signals are significantly more reliable when the broad market is in a supportive regime.

---

### Market Regime — S&P 500 vs 200-day MA

**What it is:**
The "market regime" is simply whether the S&P 500 index is trading above or below its 200-day
simple moving average.

- **Bull market:** S&P 500 > SMA(200) — the broad market is in a long-term uptrend
- **Bear market:** S&P 500 < SMA(200) — the broad market is in a long-term downtrend

This is one of the oldest and most empirically validated filters in quantitative finance.
Research consistently shows that individual stock long signals have:
- Higher success rates in bull markets (S&P above 200MA)
- Significantly lower success rates in bear markets (S&P below 200MA)

The reason: in a bear market, systemic risk (macro fear, sector rotation, forced institutional
selling) dominates individual stock fundamentals. A technically perfect BUY setup in a bear market
can still fail because everything falls together.

**In the app:**

| Reason key | Value | Meaning |
|------------|-------|---------|
| `market_regime` | "bull" / "bear" / "unknown" | Current S&P 500 regime |

The `sp500_vs_ma200_pct` in the Fear & Greed widget shows how far above or below the MA the index is.

**How it adjusts the signal:**

| Regime | BUY threshold | HOLD threshold | Effect |
|--------|--------------|----------------|--------|
| Bull | > 0.65 | > 0.50 | Normal — a moderately strong signal qualifies |
| Bear | > 0.73 | > 0.56 | Raised bar — only the strongest signals qualify as BUY |

In a bear market, a stock with fused probability 0.68 would normally be BUY — but with the
raised threshold it becomes HOLD. You must have a score of 0.73+ to get BUY, which requires
*multiple* indicators aligned, not just a majority.

**What the "BEAR MKT" badge means in the app:**
When the Signal Card shows an orange "BEAR MKT" badge, it means: the current signal was evaluated
with the higher bar. A BUY in a bear market regime is a genuinely strong signal — it had to clear 0.73.

**Limitation:** This is a binary filter using a single index. It doesn't capture sector-specific
regimes (technology may be in a bear while healthcare is in a bull). It also lags — the S&P 500
can fall 15% before its 200MA starts declining. Treat it as a coarse risk filter, not a precise signal.

---

## Part 6 — ML Model

**What it is:**
The XGBoost model is a machine learning classifier trained on historical OHLCV data for each
individual stock. It learns which patterns in recent price behavior have historically predicted
upward vs downward moves over the next 5–10 trading days.

**Features fed into the model:**
RSI, MACD histogram, Bollinger %B, volume ratio, SMA50 slope, SMA200 slope, 5-day and 20-day
price returns, and ADX — approximately 20 features per bar.

**In the app:**

| Reason key | Value | Meaning |
|------------|-------|---------|
| `ml_probability` | 0–1 | Model's estimated probability of an upward move |

**Fusion with TA score:**
```
Fused = 0.60 × ml_probability + 0.40 × ta_score
```

If the ML model is not trained for a stock, `ml_probability = null` and the signal falls back to
100% TA score.

**Why 60/40 in favour of ML:**
ML models can capture non-linear interactions between indicators that the rule-based TA scoring
misses. However, they can also overfit to historical patterns that no longer hold. The 40% TA
weight ensures the fundamental indicator logic always has a floor of influence.

**When ML adds the most value:**
- Stocks with 1+ years of price history for training
- Stocks with predictable, recurring patterns (large caps, liquid mid-caps)
- After a fresh retrain (post-close retrains run nightly)

**When ML is least reliable:**
- Small-cap stocks with thin history or low liquidity
- After a major regime change (Federal Reserve surprise, sector rotation, geopolitical shock)
- If the model was trained more than 2 weeks ago without a retrain

> To retrain: click **Train This** on the stock detail page, or click **Train All Stocks** to
> retrain every stock in your watchlist at once (takes 2–5 minutes).

---

## Signal Scoring Summary (v2)

All TA indicators combine into a `ta_score` between 0 and 1:

| Indicator | Condition | TA Score impact |
|-----------|-----------|----------------|
| Trend above SMA50 | Close > SMA(50) | +0.15 |
| SMA50 above SMA200 | SMA(50) > SMA(200) | +0.10 |
| Golden cross event | SMA50 just crossed above SMA200 | +0.10 bonus |
| Death cross event | SMA50 just crossed below SMA200 | −0.10 penalty |
| RSI — ideal zone | RSI 45–65 | +0.15 |
| RSI — recovering | RSI 35–45 | +0.08 |
| RSI — extended | RSI 65–72 | +0.06 |
| RSI — extreme | RSI < 35 or > 72 | +0.00 |
| Stoch RSI oversold | %K < 0.20 | +0.10 |
| Stoch RSI cross-up | %K just crossed above 0.20 | +0.05 bonus |
| Stoch RSI overbought | %K > 0.80 | −0.08 penalty |
| RSI divergence — bearish | Price up, RSI down (10 bars) | −0.10 penalty |
| RSI divergence — bullish | Price down, RSI up (10 bars) | +0.08 |
| MACD histogram positive + rising | Momentum accelerating | +0.15 |
| MACD histogram positive only | Positive momentum, not accelerating | +0.08 |
| MACD zero-line cross up | MACD line just turned positive | +0.05 bonus |
| Bollinger %B in 0.2–0.8 | Not at overbought/oversold extreme | +0.10 |
| ADX bullish | ADX > 25 and +DI > −DI | +0.10 |
| OBV bullish | Short-term OBV > long-term OBV | +0.10 |
| Volume Z-score > 0.5 | Above-average volume | +0.05 |
| **Maximum possible** | All conditions met | **~1.05** (clipped to 1.0) |

**Decision thresholds:**

| Fused probability | Bull market signal | Bear market signal |
|-------------------|-------------------|-------------------|
| > 0.73 | BUY | BUY |
| 0.65–0.73 | BUY | HOLD |
| 0.56–0.65 | HOLD | HOLD |
| 0.50–0.56 | HOLD | WAIT |
| 0.35–0.50 | WAIT | WAIT |
| < 0.35 | SELL | SELL |

---

## Indicator Quick-Reference Card

Use this table when reading a signal to quickly identify the strongest and weakest points:

| Indicator | Bullish setup | Bearish / Caution |
|-----------|-------------|-------------------|
| **SMA50** | Close above | Close below |
| **SMA50 vs SMA200** | SMA50 > SMA200 (golden cross regime) | SMA50 < SMA200 (death cross regime) |
| **Golden cross** | Just fired + ADX > 25 | Whipsaw risk if ADX < 20 |
| **Death cross** | N/A | Just fired — review position immediately |
| **ADX** | > 25 with +DI > −DI | > 25 with −DI > +DI (downtrend) |
| **RSI** | 45–65 (ideal entry) | > 72 (overbought) or < 35 (no confirmation) |
| **Stoch RSI** | < 20% (oversold) or just crossed up | > 80% (overbought) |
| **RSI divergence** | Bullish divergence (price down, RSI up) | Bearish divergence (price up, RSI down) |
| **MACD histogram** | Positive and rising | Negative and falling |
| **MACD zero cross** | MACD line just went positive | MACD line just went negative |
| **Bollinger %B** | 0.30–0.70 (healthy range) | > 0.85 (overbought) or < 0.15 (breakdown) |
| **OBV** | Rising with price | Falling while price rises (distribution) |
| **Volume Z** | > 0.5 on up day | > 1.5 on a down day (capitulation or collapse) |
| **Market regime** | Bull (S&P > 200MA) | Bear (S&P < 200MA) — higher bar required |
| **ML probability** | > 0.65 | < 0.40 |

---

## Common Signal Patterns — What They Look Like in Practice

### "The Perfect Setup"
All indicators agree. Rare but high conviction.

```
SMA50 ✓ above  |  SMA50 > SMA200 ✓  |  RSI = 52 (ideal) ✓
Stoch RSI just crossed up ✓  |  MACD histogram positive + rising ✓
OBV bullish ✓  |  Volume Z = +1.2 ✓  |  ADX = 32 (trending) ✓
RSI divergence: none  |  Market regime: bull ✓
ML probability: 0.72 ✓
→ BUY with high confidence (fused ~0.76)
```

### "Extended and Topping" (the KGS pattern as of May 2026)
Trend is intact but momentum is fading.

```
SMA50 ✓ above  |  SMA50 > SMA200 ✓  |  RSI = 76 (overbought, no credit)
Stoch RSI = 0.59 (neutral, no bonus)  |  RSI divergence: BEARISH (−0.10 penalty)
MACD histogram: positive  |  ADX = 65 (very strong trend, +DI > −DI) ✓
OBV bullish ✓  |  ML probability: 0.18 (XGBoost is bearish)
→ SELL — ML is overwhelmingly bearish, RSI divergence confirms momentum fading
```

### "Dip in an Uptrend" (best risk/reward entry)
Stock pulled back within a healthy trend.

```
SMA50 ✓ above  |  SMA50 > SMA200 ✓  |  RSI = 42 (recovering, +0.08)
Stoch RSI = 0.15 (oversold, +0.10)  |  Stoch RSI cross-up ✓ (+0.05)
MACD histogram: slightly negative, but macd_rising = true (recovering)
OBV: flat  |  Volume Z = −0.3 (quiet pullback, low seller volume)
Market regime: bull ✓  |  ML probability: 0.58
→ HOLD with bullish lean — watch for MACD to turn positive, then BUY
```

### "Bear Market Survivor"
Strong stock in a weak market — requires higher conviction.

```
All TA indicators bullish (ta_score = 0.75)
ML probability: 0.71
Fused: 0.6 × 0.71 + 0.4 × 0.75 = 0.726
Market regime: BEAR → BUY threshold raised to 0.73
→ HOLD (fused = 0.726, just below 0.73 threshold)
Same setup in a bull market → BUY (0.726 > 0.65 threshold)
```

---

## Frequently Asked Questions

**Q: Why is the signal SELL when the stock is up today?**
The signal is based on the last 14–200 days of price data, not just today's move.
A one-day gain doesn't reset the broader trend signals. Common reasons for SELL despite today's green:
- RSI was already overbought before today's move
- RSI divergence: price higher but RSI declining over the last 10 days
- Stoch RSI overbought (RSI itself is at its recent high)
- ML model trained on historical patterns disagrees with the short-term move

**Q: Why does the signal change every time I refresh?**
It shouldn't change significantly unless the market closed with a new bar. Each refresh recomputes
all indicators on the latest prices. If the signal flips on the same day, check whether a new
price bar was ingested (daily bars close at 4:00 PM ET for US stocks).

**Q: What is the best single indicator to watch?**
None — the whole point of fusing multiple indicators is that each one fails in different conditions.
If you had to choose one: **RSI + ADX together**. RSI tells you momentum, ADX tells you whether
to trust it. RSI = 52 in a trending market (ADX > 25) is a much stronger signal than RSI = 52 in
a flat market (ADX < 15).

**Q: Should I act on every BUY signal?**
No. Use the BUY signal as a filter, not a trigger. When you get a BUY:
1. Check analyst consensus — is it BUY or STRONG BUY?
2. Check earnings date — is it more than 2 weeks away?
3. Check insider activity — are insiders net buyers?
4. Check market regime — are we in a bull market?
If three of four are positive, it is worth investigating further. All four aligned = highest conviction.

**Q: What does "confidence" mean vs "bullish probability"?**
- **Bullish probability** = the raw fused score (0–100%) — how bullish the signal engine thinks the stock is.
  50% = neutral. 80% = strong bullish. 20% = strong bearish.
- **Confidence** = how far from 50% the probability is, scaled to 0–100.
  Formula: `|fused − 0.5| × 200`. A score of 70% bullish and 30% bullish both give confidence = 40
  (equally far from 50). High confidence + BUY signal = the engine is very sure it is bullish.
  Low confidence BUY = slightly above the threshold but not a strong call.
