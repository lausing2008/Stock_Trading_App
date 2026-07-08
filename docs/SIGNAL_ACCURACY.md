# Signal Accuracy — Tracking, Testing & Tuning

This document covers:
1. How signal outcomes are tracked automatically in production
2. The `signal_outcomes` database table and its API endpoints
3. How to interpret accuracy metrics
4. Recommended framework for offline backtesting (vectorbt)
5. How to tune signal parameters empirically using Optuna

---

## Why two measurements exist

The system measures signal quality in two distinct ways that answer different questions:

| Measurement | Endpoint | Answers | Use for |
|---|---|---|---|
| **Fixed-window outcomes** | `GET /signals/outcomes/summary` | "Was the directional call correct at the target horizon?" | ML calibration, Optuna tuning, confidence-band validation |
| **Signal-transition P&L** | `GET /signals/trade_performance` | "What return did you get if you followed BUY→WAIT/SELL transitions?" | Live trading performance, equity curve tracking |

The fixed-window approach isolates *directional prediction accuracy* from *holding period management*. A BUY signal that is correct for 10 days but then reverses looks perfect in fixed-window terms (SWING 14d) even if the actual trade was a loss because the exit was missed. Both views matter, and neither replaces the other.

---

## The `signal_outcomes` table

Written automatically every trading day after market close by the scheduler (`POST /signals/outcomes/evaluate`). One row per BUY or SELL signal after its hold window closes.

```sql
CREATE TABLE signal_outcomes (
    id               BIGSERIAL PRIMARY KEY,
    signal_id        BIGINT NOT NULL UNIQUE REFERENCES signals(id) ON DELETE CASCADE,
    stock_id         INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
    symbol           VARCHAR(32) NOT NULL,
    horizon          signalhorizon NOT NULL,      -- SHORT | SWING | LONG | GROWTH
    signal_direction VARCHAR(8) NOT NULL,         -- BUY | SELL
    signal_date      DATE NOT NULL,
    confidence       FLOAT NOT NULL,              -- 0–100
    fused_prob       FLOAT,                       -- 0–1 (bullish_probability)
    ta_score         FLOAT,                       -- 0–1 (reasons.ta_score)
    ml_prob          FLOAT,                       -- 0–1 (reasons.ml_probability)
    ml_auc           FLOAT,                       -- 0–1 (reasons.ml_test_auc)
    market_regime    VARCHAR(16),                 -- bull | high_vol | bear | unknown
    entry_date       DATE,
    entry_price      FLOAT,
    exit_date        DATE,
    exit_price       FLOAT,
    hold_days        INTEGER,
    pct_return       FLOAT,                       -- (exit − entry) / entry
    is_correct       BOOLEAN,                     -- BUY: price up; SELL: price down
    ts_evaluated     TIMESTAMP NOT NULL DEFAULT now()
);
```

### Hold windows

| Style | Calendar days | Approx. trading days |
|-------|--------------|----------------------|
| SHORT | 7 | ~5 |
| SWING | 14 | ~10 |
| LONG | 28 | ~20 |
| GROWTH | 14 | ~10 |

- **Entry price**: first D1 close on or after the signal timestamp
- **Exit price**: first D1 close on or after `entry_date + hold_window_days`
- The `signal_outcomes` table is **idempotent** — re-running the evaluate endpoint never duplicates rows (UNIQUE on `signal_id`)

### Data maturity timeline

| When | What appears |
|------|--------------|
| Day 7 after a BUY | First SHORT outcomes |
| Day 14 after a BUY | First SWING and GROWTH outcomes |
| Day 28 after a BUY | First LONG outcomes |
| After ~4 weeks | Enough SHORT data to compute confidence-band win rates |
| After ~8 weeks | Enough SWING / GROWTH data for meaningful calibration |
| After ~3 months | Enough data to run Optuna on signal parameters |

---

## API endpoints

### `POST /signals/outcomes/evaluate`

Evaluates all BUY/SELL signals whose hold window has expired and persists outcomes.

- Called automatically by the scheduler every post-close
- Safe to call manually at any time (idempotent)
- Processes signals in chronological order; skips any that are still open or already evaluated

**Response:**
```json
{
  "evaluated": 42,
  "skipped_open": 1203,
  "skipped_no_price": 0
}
```

| Field | Meaning |
|-------|---------|
| `evaluated` | New outcome rows written this run |
| `skipped_open` | Signals whose hold window has not closed yet |
| `skipped_no_price` | Signals with no matching price data (rare) |

---

### `GET /signals/outcomes/summary`

Returns win-rate and return statistics from the `signal_outcomes` table.

**Query parameters:**

| Parameter | Default | Description |
|-----------|---------|-------------|
| `horizon` | (all) | Filter to `SHORT`, `SWING`, or `LONG` |
| `days` | 90 | Look-back window in calendar days |

**Example:**
```
GET /signals/outcomes/summary?horizon=SWING&days=60
```

**Response structure:**
```json
{
  "total": 312,
  "days_lookback": 60,
  "overall": {
    "win_rate": 0.634,
    "avg_return_pct": 3.21,
    "median_return_pct": 2.48
  },
  "by_confidence_band": [
    { "band": "0-40",  "count": 12,  "win_rate": 0.417, "avg_return_pct": -0.82 },
    { "band": "40-55", "count": 58,  "win_rate": 0.534, "avg_return_pct": 1.14 },
    { "band": "55-70", "count": 121, "win_rate": 0.628, "avg_return_pct": 2.95 },
    { "band": "70-85", "count": 89,  "win_rate": 0.719, "avg_return_pct": 4.67 },
    { "band": "85+",   "count": 32,  "win_rate": 0.781, "avg_return_pct": 6.12 }
  ],
  "by_horizon": {
    "SWING": { "count": 312, "win_rate": 0.634, "avg_return_pct": 3.21 }
  },
  "by_market_regime": {
    "bull":     { "count": 198, "win_rate": 0.672, "avg_return_pct": 3.85 },
    "high_vol": { "count": 71,  "win_rate": 0.563, "avg_return_pct": 1.92 },
    "bear":     { "count": 43,  "win_rate": 0.512, "avg_return_pct": 0.74 }
  }
}
```

### How to read the confidence-band table

The most important check is whether **higher confidence → higher win rate**. A well-calibrated signal system should show a monotonically increasing win rate as confidence rises:

```
band  0-40   →  win_rate ~0.40  (signals barely above random)
band 40-55   →  win_rate ~0.50  (marginal edge)
band 55-70   →  win_rate ~0.60  (decent edge)
band 70-85   →  win_rate ~0.70  (strong edge)
band   85+   →  win_rate ~0.78  (very high conviction)
```

If the table is **flat or non-monotonic** (e.g. band 70-85 has lower win_rate than band 55-70), that indicates the confidence score is not well-calibrated — the model is not correctly distinguishing strong from weak signals. This is a signal to retrain ML models or recalibrate the TA weight formula.

---

## Offline backtesting with vectorbt

**vectorbt** is the recommended library for sweeping signal parameters against historical OHLCV data stored in your PostgreSQL database.

### Why vectorbt

- Pure numpy/pandas — 100–1000× faster than event-driven simulators like Zipline or Backtrader
- Portfolio simulation with realistic position sizing, fees, and slippage
- Native parameter sweep support — test 1000 combinations in seconds
- Built-in Sharpe ratio, max drawdown, win rate, and equity curve

### What vectorbt can and cannot test

**Can test:**
- TA score thresholds and weights
- ADX filter settings
- Weekly alignment strength
- buy_threshold values
- ML probability independently

**Cannot test** (require live API data not available historically):
- News sentiment filter (not stored historically)
- Options flow filter (not stored historically)
- Market breadth filter (breadth_pct not stored as a time series)

For these, the live `signal_outcomes` table is authoritative.

### Example: sweep buy_threshold for SWING

```python
import vectorbt as vbt
import pandas as pd
from sqlalchemy import select, text
from db import SessionLocal, Price, Stock

# 1. Load SWING candidates from DB (stocks above SMA50)
with SessionLocal() as session:
    prices = pd.read_sql(
        "SELECT s.symbol, p.ts, p.close FROM prices p "
        "JOIN stocks s ON s.id = p.stock_id "
        "WHERE p.timeframe = '1d' AND p.ts >= '2023-01-01' "
        "ORDER BY s.symbol, p.ts",
        session.bind
    )

price_matrix = prices.pivot(index="ts", columns="symbol", values="close")

# 2. Recompute TA signals at different thresholds
# (simplified — replace with your actual _apply_style_signal logic)
ta_scores = compute_ta_scores(price_matrix)  # your function
buy_signals = {}
for threshold in [0.58, 0.60, 0.62, 0.64, 0.66, 0.68]:
    buy_signals[threshold] = ta_scores > threshold

# 3. Run vectorbt portfolio simulation for each threshold
results = {}
for threshold, entries in buy_signals.items():
    pf = vbt.Portfolio.from_signals(
        close=price_matrix,
        entries=entries,
        exits=ta_scores < 0.50,   # exit when TA drops below neutral
        freq="1D",
        fees=0.001,               # 0.1% commission
        slippage=0.0005,
    )
    results[threshold] = {
        "sharpe": pf.sharpe_ratio(),
        "win_rate": pf.win_rate(),
        "max_dd": pf.max_drawdown(),
        "total_return": pf.total_return(),
    }

# 4. Pick the threshold that maximises Sharpe ratio
best = max(results, key=lambda t: results[t]["sharpe"])
print(f"Best threshold: {best} → Sharpe {results[best]['sharpe']:.2f}")
```

### install

```bash
pip install vectorbt quantstats
```

---

## Parameter tuning with Optuna

Once you have at least **500 closed signal_outcomes** (target: ~8 weeks of SWING data), run Optuna on the signal parameters using the DB outcomes as ground truth. This replaces hand-tuning with data-driven optimisation.

### Parameters to tune

| Parameter | Range | Current | Code location |
|-----------|-------|---------|---------------|
| `buy_threshold_bull` (SWING) | 0.55 – 0.70 | 0.62 | `signals.py:759` |
| `adx_min` (SWING) | 10 – 25 | 15 | `signals.py:761` |
| `weekly_compress` (SWING) | 0.75 – 0.97 | 0.85 | `signals.py:764` |
| `earnings_compression[2]` | 0.35 – 0.70 | 0.50 | `signals.py:767` |
| `breadth_threshold` | 0.25 – 0.50 | 0.40 | `signals.py` |
| `ml_auc_floor` | 0.50 – 0.56 | 0.52 | `signals.py:845` |

### Objective function

Use **precision-weighted F-score** (β = 0.5, emphasises precision) or **Sharpe ratio** depending on your goal:

```python
import optuna
from sqlalchemy import select, text
from db import SessionLocal, SignalOutcome

def evaluate_params(params: dict, horizon: str = "SWING", days: int = 90) -> dict:
    """
    Re-evaluate stored signals using new parameter values.

    This replays the threshold/filter logic against the already-computed
    fused_prob, ta_score, ml_prob stored in signal_outcomes — no re-running
    the full signal pipeline.
    """
    cutoff = date.today() - timedelta(days=days)
    with SessionLocal() as session:
        rows = session.execute(
            select(SignalOutcome).where(
                SignalOutcome.horizon == horizon,
                SignalOutcome.signal_date >= cutoff,
                SignalOutcome.is_correct.is_not(None),
            )
        ).scalars().all()

    # Re-apply threshold with candidate params
    buy_t = params["buy_threshold_bull"]
    results = []
    for r in rows:
        if r.fused_prob is None:
            continue
        # ADX filter
        adx = r.reasons.get("adx") if hasattr(r, "reasons") else None
        # Simplified replay — apply just the threshold change
        would_fire = r.fused_prob > buy_t
        if would_fire:
            results.append(r.is_correct)

    if not results:
        return {"precision": 0.0, "recall": 0.0, "sharpe": 0.0}

    precision = sum(results) / len(results)
    recall = len(results) / len(rows) if rows else 0.0
    # Precision-weighted F-score (β = 0.5)
    beta = 0.5
    fbeta = (1 + beta**2) * precision * recall / (beta**2 * precision + recall + 1e-9)
    return {"precision": precision, "recall": recall, "fbeta": fbeta}


def objective(trial: optuna.Trial) -> float:
    params = {
        "buy_threshold_bull": trial.suggest_float("buy_threshold_bull", 0.55, 0.70),
        "adx_min": trial.suggest_int("adx_min", 10, 25),
        "weekly_compress": trial.suggest_float("weekly_compress", 0.75, 0.97),
        "earnings_comp_2": trial.suggest_float("earnings_comp_2", 0.35, 0.70),
    }
    metrics = evaluate_params(params)
    return -metrics["fbeta"]  # Optuna minimises — negate to maximise F-score

study = optuna.create_study(direction="minimize")
study.optimize(objective, n_trials=200)

print("Best params:", study.best_params)
print("Best F-score:", -study.best_value)
```

### Tuning priority order

Tune parameters in this order. Each layer has more data than the next, so you get reliable signal earlier:

1. **`buy_threshold_bull`** — single biggest lever. Controls signal frequency vs. precision trade-off. Start here.
2. **`weekly_compress`** — affects how aggressively weekly misalignment punishes signals. Hard to tune manually; empirical data helps.
3. **`earnings_compression[2]`** — binary event filter. Data shows whether the 50% compression is the right level.
4. **`adx_min`** — smaller effect, tune last.
5. **ML weight floor** — calibrate using the AUC vs. win_rate column in `signal_outcomes`. Plot `ml_auc` vs `is_correct` to find the true break-even AUC.

### When to retune

- After at least 500 SWING outcomes are in the DB (~8 weeks)
- After any major market regime shift (run immediately if bear→bull or high_vol→bull transition)
- Quarterly as a maintenance task

---

## ML model accuracy tracking

The `signal_outcomes` table also lets you validate whether the ML models are contributing positively.

### AUC vs. actual win rate

Plot `ml_auc` (from `signal_outcomes`) against `is_correct` to check if higher-AUC models actually produce better outcomes:

```sql
SELECT
    ROUND(ml_auc::numeric, 1) AS auc_bucket,
    COUNT(*)                  AS n,
    AVG(is_correct::int)      AS win_rate,
    AVG(pct_return) * 100     AS avg_return_pct
FROM signal_outcomes
WHERE horizon = 'SWING'
  AND ml_auc IS NOT NULL
  AND is_correct IS NOT NULL
GROUP BY 1
ORDER BY 1;
```

If win_rate is flat across AUC values (e.g. AUC=0.55 and AUC=0.70 both showing ~58% win rate), the AUC-based ML weight formula is not providing the expected lift. This may indicate:
- The training horizon was misaligned with actual hold period (fixed in SA-8 — SWING now uses 10d label)
- Insufficient training data per symbol
- Feature set not capturing the relevant signals

### TA score contribution

```sql
SELECT
    CASE
        WHEN ta_score < 0.45 THEN '< 0.45'
        WHEN ta_score < 0.55 THEN '0.45-0.55'
        WHEN ta_score < 0.65 THEN '0.55-0.65'
        ELSE '>= 0.65'
    END AS ta_bucket,
    COUNT(*) AS n,
    AVG(is_correct::int) AS win_rate
FROM signal_outcomes
WHERE horizon = 'SWING' AND is_correct IS NOT NULL
GROUP BY 1 ORDER BY 1;
```

High ta_score → high win_rate confirms the TA component is predictive. Flat or inverted relationships indicate the TA weights need recalibration (run `POST /signals/calibrate_ta_weights`).

---

## Complete tuning workflow

```
Week 0         Deploy signal_outcomes tracking (done)
               ↓
Weeks 1–7      Outcomes accumulate automatically post-close
               ↓
Week 8         Run: GET /signals/outcomes/summary?horizon=SWING
               Check: is win_rate monotonically increasing by confidence band?
               Check: is bear regime win_rate < bull regime win_rate? (should be)
               ↓
Week 8+        Run Optuna (200 trials, ~5 min):
               Tune: buy_threshold, weekly_compress, earnings_comp
               ↓
               Update signals.py with best params
               Commit + hot-patch EC2
               ↓
Quarterly      Repeat Optuna + compare to ML model AUC trends
               ↓
Parallel       Run vectorbt on historical OHLCV data to validate TA-only changes
               (no external API dependencies — fast to iterate)
```

---

## Related documentation

- [AI_SIGNAL.md](AI_SIGNAL.md) — Full signal calculation pipeline, all filter parameters
- [SCORING.md](SCORING.md) — How to combine signal with K-Score, analyst ratings, and earnings
- [REVIEW_AND_IMPROVEMENTS.md](REVIEW_AND_IMPROVEMENTS.md) — SA-1 through SA-8 improvement log
