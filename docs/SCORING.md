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

Measures trend alignment, RSI positioning, and trend strength (ADX).

**Inputs:** daily `open`, `high`, `low`, `close` — minimum ~50 rows recommended.

**Signals:**
- `above_sma50` = 1 if close > SMA(50), else 0
- `above_sma200` = 1 if close > SMA(200), else 0
- `sma50_above_sma200` = 1 if SMA(50) > SMA(200), else 0 — bullish regime
- `rsi_score` = `100 − |RSI(14) − 55|` — peaks at RSI 55 (bullish but not overbought)
- `adx_boost` = `clip((ADX(14) − 15) / 25, 0, 1) × 10` — 0–10 bonus for trending markets

**Formula:**
```
base       = (above_sma50 + above_sma200 + sma50_above_sma200) / 3 × 60 + rsi_score × 0.4
Technical  = clip( base + adx_boost, 0, 100 )
```

- MA signals contribute up to 60 pts; RSI up to 40 pts; ADX adds up to 10 bonus pts.
- All three MA signals + RSI at 55 + strong trend (ADX > 40) → near 110 before clipping to 100.
- ADX below 15 (choppy/ranging market) contributes 0 bonus — trend signals are less reliable in flat markets.

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
| Technical | Objective, reacts to price quickly; ADX filters out choppy-market noise | Lags — all signals are backward-looking |
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

---

## Combining AI Signal and Analyst Ratings

These two indicators answer different questions and operate on different time horizons. Using them together is more powerful than either alone.

### What each one measures

| | AI Signal | Analyst Ratings |
|---|---|---|
| **Source** | Price history (TA + ML) | Wall Street analysts (fundamentals) |
| **Horizon** | Days to weeks | 6–12 months |
| **Inputs** | RSI, MACD, Bollinger, ADX, OBV, SMA crossovers, XGBoost model | Earnings estimates, revenue growth, sector outlook, DCF |
| **Updates** | Every request (live) | Quarterly (cached 24 h) |
| **Strength** | Timing — catches overbought/oversold conditions | Direction — identifies fundamentally strong businesses |
| **Weakness** | No fundamental grounding; can be wrong in trending markets | No timing; stock can fall 20% before analysts downgrade |

### Decision framework

**Core principle: analysts filter the universe, AI Signal times the entry.**

| Analyst | AI Signal | Interpretation | Action |
|---------|-----------|----------------|--------|
| STRONG BUY / BUY | BUY | Best setup — fundamentals + technicals aligned | Enter position |
| STRONG BUY / BUY | HOLD | Bullish lean, not yet confirmed technically | Start a small position; add on BUY flip |
| STRONG BUY / BUY | WAIT | Good stock, extended short-term | Wait for pullback; watch for BUY flip |
| STRONG BUY / BUY | SELL (RSI > 70) | Overbought but fundamentally strong | Hold existing; do not add; wait for AI to reset |
| HOLD | BUY | Technical bounce but no fundamental catalyst | Small speculative position only; tight stop |
| HOLD | SELL | Neither side likes it | Avoid or exit |
| SELL / UNDERPERFORM | Any | Fundamental thesis broken | Exit regardless of AI signal |

### The highest-conviction entry

**Analyst = BUY + AI Signal = WAIT or just-flipped BUY + MACD turning positive + RSI 40–55**

This combination means: the business is fundamentally sound, the stock pulled back enough to cool the technicals, and momentum is starting to recover. This is typically the best risk/reward entry — you're buying a good company after a dip, with early technical confirmation.

### Reading the AI Signal `reasons` field

The signal API (`GET /signals/{symbol}`) exposes the underlying indicator values. Key ones:

| Field | What it tells you |
|---|---|
| `rsi` | < 40 = oversold (likely a buy dip); > 70 = overbought (risk of pullback) |
| `macd_rising` | `true` = momentum just shifted positive — early entry signal |
| `macd_hist` | Positive + rising = strong BUY confirmation; negative + falling = strong SELL |
| `adx_trending` | `true` (ADX > 25) = signals are more reliable; `false` = market is choppy, reduce conviction |
| `adx_bullish` | `true` = directional move is upward — trend is your friend |
| `obv_bullish` | `true` = volume is confirming price direction — high conviction |
| `bb_pct_b` | > 0.85 = near upper Bollinger Band (overbought zone); < 0.15 = near lower band (oversold) |

### Common real-world scenarios

**Scenario 1 — KGS-type divergence (AI: SELL, Analyst: Strong BUY)**
Stock has run up fast. RSI ~80, near upper Bollinger Band. Analysts still love the fundamentals but the technical picture is extended.
- If you own it: hold, don't add.
- If you don't own it: wait. A 5–15% pullback that brings RSI back to 50–60 with MACD flattening is the entry point. Set an alert at a key support level.

**Scenario 2 — Both say BUY**
Highest conviction. Size the position normally and hold as long as analysts remain positive and AI Signal stays BUY or HOLD.

**Scenario 3 — Analyst downgrades to HOLD mid-position**
This is a warning sign even if AI Signal is still BUY. Trim the position. Analysts downgrade slowly — by the time it reaches SELL the damage is usually done.

**Scenario 4 — AI flips SELL after a big gain**
Lock in partial profits (e.g. sell half). Let the rest run unless analysts also turn cautious.

### Adding Earnings Calendar and Insider Activity to the framework

Two additional data points are available on the stock detail page that should inform how you act on signal transitions:

**Earnings Calendar**

The earnings badge shows how many days until the next earnings release. This matters because:

- An AI Signal transition (e.g. SELL → BUY) happening **within 7 days of earnings** carries much lower confidence. The market is pricing in uncertainty — the stock can swing 10–20% in either direction regardless of what the technicals say.
- An AI Signal transition happening **more than 30 days before earnings** is more reliable as a technical setup — you have time for the thesis to develop before results can disrupt it.
- If you're holding through earnings, consider reducing position size. If the signal fires and earnings are imminent, wait for the post-earnings price to settle before acting.

| Days to earnings | Recommended action on a signal alert |
|-----------------|--------------------------------------|
| > 30 days | Act normally on the combined signal |
| 14–30 days | Smaller initial position; leave room to add post-earnings |
| 7–14 days | Watch only; wait for earnings before committing |
| < 7 days | Do not act on the signal — earnings risk dominates |

**Insider Activity**

Insider buying is one of the most reliable confirmation signals because insiders have asymmetric information about their own business. Use it as a filter, not a trigger:

| Insider pattern | How to weight it |
|----------------|-----------------|
| Net buying, multiple insiders, recent | Strong confirmation — raises conviction on a BUY signal |
| Net buying, single executive | Moderate confirmation — could be a scheduled purchase |
| Net selling, large %, multiple insiders | Caution flag — do not add; consider waiting even if AI says BUY |
| Net selling, small %, single insider | Neutral — likely routine liquidity, ignore |
| No insider data | Available for most US large-caps only; absence of data ≠ negative signal |

**Recent Analyst Actions**

The stock detail page shows a table of individual firm events from the last 90 days — upgrades, downgrades, initiations, and target changes. Use this to understand the *direction* of Wall Street sentiment, not just the current aggregate:

| Pattern | Interpretation |
|---------|---------------|
| 3+ upgrades in the last 30 days | Accelerating bullish consensus — strong confirmation of a BUY signal |
| Mix of upgrades and downgrades | Divided opinion — rely more on the AI signal and your own analysis |
| Recent downgrade after a run | Caution even if consensus is still BUY — momentum may be fading |
| Initiation by a major firm (Goldman, MS, JPMorgan) | Fresh coverage can move the stock; note the grade they opened with |
| "Raised Target" actions | Analysts chasing price up — bullish but late-cycle signal |
| "Lowered Target" with no grade change | Analysts reducing expectations while holding rating — watch for further deterioration |

**Note on per-firm price targets:** Individual dollar targets per firm (e.g. "Goldman Sachs: $180") are proprietary data not available through Yahoo Finance's free API. Only aggregate targets (mean, low, median, high across all analysts) are shown. The Recent Analyst Actions table shows grade changes only — not dollar targets per firm. A paid integration (e.g. Financial Modeling Prep) would be required to add per-firm targets.

**Best combination**: Analyst = BUY + recent upgrades trend + AI Signal just flipped BUY + insider net buying (large) + earnings > 30 days away. This five-way alignment is rare but represents the highest-conviction setup in the app.

**Worst combination**: AI Signal = BUY but analyst = HOLD with recent downgrades, insider net selling > 2% of float, and earnings within 7 days. In this case the signal is likely noise — the technicals may be reacting to pre-earnings positioning rather than a sustainable trend.

---

### Signal Change Email Notifications

When you subscribe to signal alerts on a stock (🔔 button in the sidebar), you receive two kinds of emails:

**Entry signals** — fires when the AI Signal improves (any transition toward BUY) **and** the analyst consensus is bullish. Both conditions must be true simultaneously.

**Exit warnings** — fires when a BUY signal deteriorates (BUY → HOLD, BUY → WAIT, or BUY → SELL), regardless of analyst consensus. Exit warnings use a red ⚠ SELL Alert subject and include a red call-to-action banner in the email body. They are not filtered by analyst rating because a technical reversal is actionable whether or not Wall Street has caught up yet.

Both email types include the full reasons table (all indicator values), the next earnings date, and an insider activity summary. See [FEATURES.md — Signal Change Email Notifications](FEATURES.md#signal-change-email-notifications) for the complete trigger tables, check schedule, and email format details.

#### How exit warnings fit the decision framework

The framework encourages you to act on signal transitions when multiple factors align. Exit warnings are designed around this logic:

| Transition | Recommended interpretation |
|-----------|---------------------------|
| BUY → HOLD | Momentum is softening. Avoid adding. Watch for the next refresh — if it recovers to BUY, no action needed. |
| BUY → WAIT | Signal is deteriorating. Consider taking partial profits or tightening your stop-loss. |
| BUY → SELL | AI has reversed. Review the reasons table in the email. If RSI is overbought, MACD is falling, and OBV is declining, the technical thesis has broken — consider exiting. |

Cross-reference with the analyst rating in the email: if the analyst is still BUY but AI is SELL, this is a divergence worth investigating (see Scenario 1 in the decision scenarios above).

### What this app cannot do

- These signals do not account for macro events, earnings surprises, or geopolitical risk.
- Analyst ratings from yfinance have a 24-hour cache and may lag intraday downgrades.
- The AI Signal is trained on historical price patterns — it can be wrong in regime changes (e.g. a sector rotation or a Federal Reserve surprise).
- Insider data from yfinance covers SEC filings only; it may miss off-market transactions or have a delay of several days after the Form 4 is filed.
- Nothing here is personalized investment advice.
