# ML Prediction Service — Domain Knowledge & Coding Standards

Trains, tunes, and serves ML models for signal confidence enhancement. XGBoost is the primary model;
LightGBM, Random Forest, and LSTM are available. Optuna handles hyperparameter tuning.

---

## What This Service Does

| Responsibility | Key file(s) |
|---|---|
| Model training pipeline | `training/trainer.py` (~1,355 lines) |
| Feature engineering (22+ features — verify count against current code, this doc is likely behind) | `features/builder.py` (~841 lines — nearly double an earlier ~548-line estimate; this file grows fast) |
| Optuna hyperparameter tuning | `training/tuner.py` (~199 lines) |
| Model serving (predict) | `api/routes.py` (~479 lines) |
| HMM regime overlay (consumed by paper_trading_engine, NOT by decision-engine — see T232-DL-REGIME5X) | `api/hmm_regime.py` |
| Model implementations | `models/` (xgb.py, lgb.py, lstm.py, rf.py, gbm.py) |

---

## Feature Engineering (`features/builder.py`)

22 features used by the XGBoost model. Categories:

**Price momentum:** `return_1d`, `return_5d`, `return_20d`, `return_60d`

**Technical indicators:** `rsi_14`, `macd_signal`, `bb_pct` (Bollinger Band position 0–1),
`atr_14_pct` (ATR normalized by price)

**Volume:** `volume_z` (z-score vs 20-day avg), `obv_change_5d` (OBV momentum)

**Trend:** `ema_20_gap` (price vs 20-day EMA pct), `ema_50_gap`, `ema_cross` (20/50 crossover signal)

**Pattern:** `support_proximity` (distance to nearest S&R level), `trend_strength`

**Market context:** `spy_corr_20d` (rolling SPY correlation), `vix_level`, `regime_code` (0–3)

**ML meta:** `ml_prob` (prior model probability), `signal_confidence` (TA signal confidence)

### Feature planned additions (improvement tracker)
- T204: `eps_surprise_pct` (earnings surprise momentum / PEAD)
- T204: `pc_ratio` (options put/call ratio)
- T214: `rs_vs_sector` (stock return vs sector ETF return)

---

## Training Pipeline (`training/trainer.py`)

### Walk-forward cross-validation
The trainer uses time-series cross-validation (not random split) to prevent look-ahead bias:
- Training window: rolling N months of historical data
- Validation: next M months immediately following the training window
- Multiple folds; final model trained on all data

### Model persistence
Trained models are saved as `.joblib` files in the `model_dir` directory (from settings).
Path: `{model_dir}/xboost/{symbol}_{style}_{horizon}.joblib`

If `model_dir` is empty on startup, all endpoints return a placeholder or error until
`POST /ml/train_all` is run.

---

## Optuna Tuning (`training/tuner.py`)

`POST /ml/tune_all?n_trials=60` runs Optuna hyperparameter search for all models.
**This is a long-running job** — do not restart the container while it's running.
Typical runtime: 20–40 minutes for 60 trials.

### Triggering tune_all (from market-data container)
```bash
docker exec stockai-market-data-1 python3 -c "
import sys, uuid, time; sys.path.insert(0, '/app'); sys.path.insert(0, '/app/src')
from common.config import get_settings; from jose import jwt as _jwt; import httpx
s = get_settings()
tok = _jwt.encode({'sub':'scheduler','jti':str(uuid.uuid4()),'exp':int(time.time())+86400*365}, s.jwt_secret, algorithm='HS256')
r = httpx.post('http://ml-prediction:8003/ml/tune_all?n_trials=60', headers={'Authorization': f'Bearer {tok}'}, timeout=20)
print(r.status_code, r.text[:300])"
```

---

## Critical: jose Dependency

Same issue as signal-engine: `python-jose` must be installed or all auth-protected endpoints
(including `POST /ml/tune_all`) silently return 401.

```bash
docker exec stockai-ml-prediction-1 python3 -c 'from jose import jwt; print("OK")'
# Fix: docker exec stockai-ml-prediction-1 pip install 'python-jose[cryptography]==3.3.0'
```

**After any container rebuild:** jose is wipe-and-reinstall. Always check after rebuilding.

---

## Model Startup Warning

If startup logs show:
```
ml.startup.no_models — No XGBoost models found
```
Run `POST /ml/train_all` first. Models are not shipped with the image — they are trained on EC2.

---

## Endpoint Reference

| Endpoint | Auth | Purpose |
|---|---|---|
| `POST /ml/train_all` | Yes | Train models for all symbols × styles × horizons |
| `POST /ml/tune_all?n_trials=N` | Yes | Optuna hyperparameter search (long-running) |
| `POST /ml/predict` | Yes | Single-symbol prediction |
| `GET /ml/accuracy` | Yes | Per-model accuracy metrics |
| `GET /ml/features` | Yes | Feature importance for a trained model |
