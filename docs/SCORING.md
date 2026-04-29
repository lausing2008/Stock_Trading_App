# K-Score & Fair Value — Reference

Source: [`services/ranking-engine/src/scoring/kscore.py`](../services/ranking-engine/src/scoring/kscore.py)

---

## K-Score (0–100)

A composite ranking score that measures a stock's overall health across five dimensions. Scores are computed nightly (or on-demand via `POST /rankings/refresh`) from daily OHLCV price history.

### Composite formula

```
K-Score = Technical × 0.25
         + Momentum  × 0.25
         + Volatility × 0.20
         + Value      × 0.15
         + Growth     × 0.15
```

All sub-scores are clipped to [0, 100]. The composite is also clipped to [0, 100].

### Colour thresholds (UI)

| Range | Label | Colour |
|-------|-------|--------|
| ≥ 70  | Strong | Green |
| 50–69 | Neutral | Yellow |
| < 50  | Weak | Red |

---

## Sub-scores

### Technical (weight 25%)

Measures trend alignment and RSI positioning.

**Inputs:** daily `close`, minimum ~50 rows recommended.

**Signals:**
- `above_50` = 1 if close > SMA(50), else 0
- `above_200` = 1 if close > SMA(200), else 0
- `golden_cross` = 1 if SMA(50) > SMA(200), else 0
- `rsi_score` = `100 − |RSI(14) − 55|` — peaks at RSI 55 (bullish but not overbought)

**Formula:**
```
Technical = clip( (above_50 + above_200 + golden_cross) / 3 × 60 + rsi_score × 0.4, 0, 100 )
```

- MA signals contribute up to 60 pts; RSI contributes up to 40 pts.
- All three MA signals firing = 60 pts; RSI at exactly 55 = 40 pts → max 100.

---

### Momentum (weight 25%)

Measures recent price performance across three lookback windows.

**Inputs:** daily `close`, minimum 126 rows; returns 50 (neutral) if insufficient data.

**Returns:**
- `r1m` = return over last 21 trading days
- `r3m` = return over last 63 trading days
- `r6m` = return over last 126 trading days

**Formula:**
```
blended = 0.50 × r3m + 0.30 × r6m + 0.20 × r1m
Momentum = clip( 50 + blended × 150, 0, 100 )
```

- Neutral (50) at 0% blended return.
- Reaches 100 at ~+33% blended return; reaches 0 at ~−33%.

---

### Volatility (weight 20%)

Lower realized volatility scores higher — rewards consistency.

**Inputs:** daily `close`; returns 50 (neutral) if rolling std is NaN.

**Formula:**
```
vol = 60-day rolling std of daily % returns
Volatility = clip( 100 − vol × 1500, 0, 100 )
```

| Daily vol | Score |
|-----------|-------|
| ~0.7%     | ~90   |
| ~1.5%     | ~78   |
| ~3.0%     | ~55   |
| ~5.0%     | ~25   |
| ≥ ~6.7%   | 0     |

---

### Value (weight 15%) — price proxy

Measures how far the stock has fallen from its 52-week high. This is a **price-action proxy**, not a fundamental valuation (P/E, P/B, etc. are not yet integrated).

**Inputs:** daily `close`, minimum 252 rows; returns 50 (neutral) if insufficient data.

**Formula:**
```
high_52w = max(close over last 252 trading days)
discount = 1 − (close / high_52w)
Value = clip( discount × 200, 0, 100 )
```

| Price vs 52w high | Score |
|-------------------|-------|
| At 52w high (0% off) | 0 |
| 25% below high | 50 |
| 50% below high | 100 |

> **Limitation:** a deeply discounted stock is not necessarily cheap — it may be in a fundamental decline. This score is best read alongside Technical and Momentum.

---

### Growth (weight 15%) — price proxy

Measures 12-month price appreciation as a stand-in for business growth. This is a **price-action proxy**, not earnings or revenue growth.

**Inputs:** daily `close`, minimum 252 rows; returns 50 (neutral) if insufficient data.

**Formula:**
```
cagr = close[-1] / close[-252] − 1
Growth = clip( 50 + cagr × 120, 0, 100 )
```

| 12m price return | Score |
|------------------|-------|
| −42%+ decline | 0 |
| 0% (flat) | 50 |
| +42% gain | 100 |

> **Limitation:** this overlaps significantly with Momentum since both derive from price returns. A stock that rose 40% a year ago but has stalled recently will still score high here.

---

## Fair Value

**Definition:** the 200-day simple moving average (SMA200) of the closing price.

```
fair_price = mean(close over last 200 trading days)
```

This is a **trend-based mean-reversion anchor**, not an intrinsic value estimate. It answers "where has price been on average over the past year" rather than "what is the stock worth."

### How to read it

| Price vs Fair Value | Interpretation |
|---------------------|----------------|
| Price > Fair Value by >15–20% | Extended — elevated pullback risk |
| Price near Fair Value (±5–10%) | Healthy — trend intact, not stretched |
| Price < Fair Value | Below long-run average — watch for trend reversal before buying |

### What it is not

- Not a DCF or discounted earnings model
- Not based on P/E, P/B, EV/EBITDA, or analyst targets
- Not adjusted for earnings growth or interest rates

---

## Accuracy & limitations

| Sub-score | Strengths | Weaknesses |
|-----------|-----------|------------|
| Technical | Objective, reacts to price quickly | Lags — all signals are backward-looking |
| Momentum | Empirically validated factor in academic literature | Prone to sharp reversals at extremes |
| Volatility | Reliable risk proxy | Low vol doesn't mean low risk (gap risk, illiquidity) |
| Value | Simple, data-always-available | Conflates "cheap" with "fallen" |
| Growth | Simple, data-always-available | Heavily overlaps with Momentum |
| Fair Value | Good mean-reversion anchor | No fundamental grounding |

**Planned improvements:** replace the price-proxy Value and Growth scores with real fundamental data (P/E, forward EPS growth, revenue CAGR) once a fundamental data source is integrated.

---

## Using K-Score in practice

### As a screener
Sort the Rankings page by K-Score to surface the strongest stocks in your watchlist. Use sub-scores to understand *why* a stock ranks high or low.

### Common patterns

| Sub-score pattern | What it suggests |
|-------------------|-----------------|
| High Technical + High Momentum, low Value | Strong uptrend; momentum play, not a value entry |
| Low Technical + High Value | Beaten-down stock; potential mean reversion — confirm the downtrend has ended first |
| High Volatility score (i.e. low realized vol) + decent Technical | Quiet accumulation phase; often precedes a directional move |
| High Growth + Low Momentum | Strong past-year performer that has recently stalled |
| K-Score diverging from AI signal | Worth investigating — the ML signal may be picking up something the score hasn't priced in yet |

### With Fair Value
- **Upside % = (Fair Value − Price) / Price** — shown on stock detail cards.
- Positive upside + strong Technical = price recovering toward its mean with trend support.
- Negative upside (price above Fair Value) + weakening Technical = stretched, consider trimming.

### In the AI Outlook
The Generate Outlook feature sends each stock's K-Score sub-scores, AI signal confidence, price change, and recent news to the model. The sub-score breakdown is more informative than the headline K-Score number alone — the AI uses the individual components to identify the primary near-term catalyst or risk.
