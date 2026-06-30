# Decision Engine — Domain Knowledge & Coding Standards

Makes the final BUY/SKIP decision for paper trading by running hard rejects then numerically
scoring candidates across 9 dimensions. The gatekeeper before capital is deployed.

---

## What This Service Does

| Responsibility | Key file(s) |
|---|---|
| Hard reject gates (cheap, run first) | `api/core/hard_rejects.py` (~151 lines) |
| Market regime detection | `api/core/regime.py` (~217 lines) |
| 9-dimension numerical scoring | `api/core/scorer.py` (~219 lines) |
| Position sizing | `api/core/sizer.py` (~141 lines) |
| Data aggregation (signals + context) | `api/core/aggregator.py` (~177 lines) |
| Decision API endpoint | `api/routes.py` (~333 lines) |
| Shared data models | `api/core/models.py` (~102 lines) |

---

## Decision Pipeline

```
POST /decide (symbol, style, signal_data)
    ↓
hard_rejects.py  — check each gate in order; first BLOCK returns immediately
    ↓             score = -99 means hard rejected (display as "Hard rejected" not "-99/12")
scorer.py        — compute 9-dimension score (0–12 total)
    ↓
sizer.py         — compute position_size_pct based on score + regime + portfolio config
    ↓
Return: {decision: ENTER|SKIP, score, size_pct, reasons}
```

### Hard reject gates (as of T202)
Order matters — cheaper/more-common gates run first:
1. Open position limit per sector
2. Open exposure cap (`max_open_exposure_pct`)
3. Signal staleness (`max_signal_age_hours`)
4. Price drift from signal date (`max_price_drift_pct`)
5. Volume z-score gate (`min_volume_z`)
6. Confidence decline gate (`max_confidence_decline`)
7. Equity floor circuit breaker
8. Regime gate
9. Stop cooldown (same stock recently stopped out)

### Score = -99 convention
Hard rejected before scoring → `score = -99`. This is a sentinel value, not a real score.
The frontend (`decide.tsx` `ScoreBar`) displays this as "Hard rejected — no score computed"
rather than "-99 / 12".

---

## Regime Detection (`regime.py`)

Computes current market regime from VIX, SPY momentum, and breadth indicators.
Regimes: `bull`, `neutral`, `choppy`, `bear`, `risk_off`.

Used in two ways:
1. As a hard reject: if `regime_risk_off_gate=True` and regime is `risk_off` → BLOCK
2. As a position size multiplier: choppy/bear reduces `position_size_pct`

Regime is also cached in Redis for consumption by other services.

---

## Scoring Dimensions (`scorer.py`)

9 dimensions, each contributing 0–1.33 points (total: 12):
1. Signal confidence
2. Volume confirmation
3. Price momentum
4. ML probability alignment
5. Regime favorability
6. Sector relative strength
7. Research alignment (AVOID = penalty)
8. Confidence trajectory (rising = bonus)
9. Technical setup quality (RSI, MACD, BB position)

Score interpretation:
- ≥ 8: Strong BUY
- 6–8: Moderate BUY
- 4–6: Weak / borderline
- < 4: SKIP (even without hard reject)

---

## Position Sizing (`sizer.py`)

Base: `portfolio.config.position_size_pct` (default 0.10 = 10% of equity).
Adjusted by:
- Regime multiplier (1.0 bull → 0.5 bear)
- Score multiplier (higher score → slightly larger position)
- Sector concentration cap
- Open exposure remaining

---

## Key API Contract

The paper trading engine calls `POST /decide` with `signal_data` dict. This dict must include:
```python
{
    "symbol": str,
    "style": str,           # SWING, GROWTH, SHORT, LONG
    "signal": str,          # BUY / HOLD / SELL
    "confidence": float,
    "confidence_delta": float,
    "signal_age_hours": float,
    "live_price": float,
    "sig_ref_price": float, # close at signal date (for price drift)
    "volume_z": float,
    "open_exposure": float,
    "equity_ratio": float,  # equity / initial_capital
    "regime": str,
    "portfolio_config": dict,
}
```

---

## Endpoint Reference

| Endpoint | Auth | Purpose |
|---|---|---|
| `POST /decide` | Yes (JWT) | Main decision endpoint called by paper trading engine |
| `GET /decide/history/{portfolio_id}` | Yes | Past decision log |
| `GET /regime` | No | Current market regime from Redis |
