# Technical Analysis Service — Domain Knowledge & Coding Standards

Computes TA indicators, detects chart patterns, and identifies support/resistance levels.
Consumed by signal-engine, ranking-engine, research-engine, and the frontend directly.

---

## What This Service Does

| Responsibility | Key file(s) |
|---|---|
| Core TA indicators | `indicators/core.py` (~126 lines) |
| Chart pattern recognition | `patterns/recognizer.py` (~226 lines) |
| Trendlines + support/resistance | `indicators/trendlines.py` (~143 lines) |
| TA API endpoints | `api/routes.py` (~184 lines) |

---

## Core Indicators (`indicators/core.py`)

Computed indicators:
- **RSI_14**: Relative Strength Index (overbought >70, oversold <30)
- **MACD** (12/26/9): MACD line, signal line, histogram; crossover = momentum shift
- **Bollinger Bands** (20-day, 2σ): upper/lower bands; `bb_pct` = price position within bands (0=lower, 1=upper)
- **EMA 20 / EMA 50**: trend direction; crossover signals trend change
- **ATR_14**: Average True Range — volatility measure used for position sizing
- **OBV**: On-Balance Volume — confirms price moves with volume direction

All indicators use `pandas_ta` or equivalent vectorized computation — no loops over price bars.

---

## Pattern Recognition (`patterns/recognizer.py`)

Recognized patterns:
- Breakout (price above resistance with volume)
- Breakdown (price below support)
- Double top / double bottom
- Head and shoulders / inverse
- Bull/bear flag
- Hammer / shooting star

Pattern detection uses sliding windows over OHLCV data. Each detection returns:
- `pattern_type`: string identifier
- `confidence`: 0–1 (how clean the pattern is)
- `target_price`: estimated target if the pattern plays out
- `invalidation_price`: level that would negate the pattern

---

## Support & Resistance (`indicators/trendlines.py`)

S&R levels are computed from:
- Historical pivot highs/lows (local extrema over N-bar windows)
- Volume-weighted price clusters
- Round number proximity

`support_proximity` feature = distance from current price to nearest S&R level as % of price.
Used by ML model as a feature — stocks near strong support have higher risk/reward for BUY.

---

## Endpoint Reference

| Endpoint | Auth | Purpose |
|---|---|---|
| `GET /ta/{symbol}` | Yes | Full TA analysis: all indicators + patterns + S&R |
| `GET /ta/{symbol}/indicators` | Yes | Indicators only (RSI, MACD, BB, EMA, ATR, OBV) |
| `GET /ta/{symbol}/patterns` | Yes | Detected patterns with confidence and targets |
| `GET /ta/{symbol}/sr` | Yes | Support/resistance levels |

---

## Data Dependency

This service reads price data from the `prices` table via the shared DB connection.
It does NOT call market-data over HTTP — it reads directly from PostgreSQL.
If prices are stale (ingest hasn't run), TA will be computed from stale data silently.

---

## Consumer Mapping

| Consumer | What it uses |
|---|---|
| signal-engine | RSI, MACD, BB, EMA crossovers, volume_z (all via direct feature computation) |
| ml-prediction | All 22 features (builds its own via `features/builder.py`, may call /ta directly) |
| ranking-engine | RSI, MACD, BB position for K-score |
| research-engine | Full TA context for research report prompt |
| frontend (stock detail) | Full TA display on chart page |
