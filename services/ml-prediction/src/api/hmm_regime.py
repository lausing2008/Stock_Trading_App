"""HMM regime classifier — T211.

Trains a 4-state GaussianHMM on (VIX_level, SPY_5d_return, IWM_vs_EMA200).
States sorted by mean VIX: rank-0 = bull, rank-1 = neutral, rank-2 = choppy, rank-3 = bear.
Model persisted to /tmp/hmm_regime.pkl. Auto-refreshes when older than REFRESH_DAYS.
Fail-open: if hmmlearn is not installed, returns {"error": "hmmlearn not installed"}.
"""
import logging
import os
import time
from datetime import date, timedelta

import numpy as np

log = logging.getLogger("ml.hmm_regime")

MODEL_PATH = "/tmp/hmm_regime.pkl"
REFRESH_DAYS = 7
STATE_NAMES = ["bull", "neutral", "choppy", "bear"]


def _model_age_days() -> float:
    if not os.path.exists(MODEL_PATH):
        return float("inf")
    return (time.time() - os.path.getmtime(MODEL_PATH)) / 86400


def _fetch_features(lookback_days: int = 700):
    import pandas as pd
    import yfinance as yf

    end = date.today()
    start = end - timedelta(days=lookback_days)
    raw = yf.download(
        ["^VIX", "SPY", "IWM"],
        start=str(start),
        end=str(end),
        auto_adjust=True,
        progress=False,
    )
    closes = raw["Close"] if "Close" in raw.columns else raw

    vix_s = closes["^VIX"].dropna() if "^VIX" in closes.columns else None
    spy_s = closes["SPY"].dropna()   if "SPY"  in closes.columns else None
    iwm_s = closes["IWM"].dropna()   if "IWM"  in closes.columns else None

    if vix_s is None or spy_s is None or iwm_s is None:
        raise ValueError("Failed to download VIX/SPY/IWM from yfinance")

    df = pd.DataFrame(
        {
            "vix":         vix_s,
            "spy_5d_ret":  spy_s.pct_change(5),
            "iwm_vs_e200": iwm_s / iwm_s.ewm(span=200, adjust=False).mean() - 1,
        }
    ).dropna()

    if len(df) < 120:
        raise ValueError(f"Insufficient data: {len(df)} rows after dropna")

    return df


def _fit_and_save() -> dict:
    from hmmlearn.hmm import GaussianHMM
    import joblib

    df = _fetch_features()
    X = df[["vix", "spy_5d_ret", "iwm_vs_e200"]].values

    model = GaussianHMM(
        n_components=4,
        covariance_type="full",
        n_iter=300,
        random_state=42,
    )
    model.fit(X)

    # Rank states ascending by mean VIX: rank 0 = lowest VIX = bull
    vix_means = model.means_[:, 0]
    sorted_by_vix = np.argsort(vix_means)  # sorted_by_vix[rank] = raw_state_idx

    joblib.dump(
        {"model": model, "sorted_by_vix": sorted_by_vix, "n_obs": len(df)},
        MODEL_PATH,
    )
    log.info("hmm_regime.fitted n_obs=%d vix_means=%s", len(df), vix_means.round(2).tolist())
    return {"n_obs": len(df), "state_vix_means": vix_means.round(2).tolist()}


def predict_current() -> dict:
    """Fit (if needed) and return current HMM state + probability distribution."""
    try:
        import hmmlearn  # noqa: F401 — import check only
    except ImportError:
        return {"error": "hmmlearn not installed — run: pip install hmmlearn>=0.3.0"}

    try:
        import joblib

        if _model_age_days() > REFRESH_DAYS:
            _fit_and_save()

        data = joblib.load(MODEL_PATH)
        model = data["model"]
        sorted_by_vix = data["sorted_by_vix"]
        n_obs = data.get("n_obs", 0)

        # Use 300 days for prediction context (HMM needs sequence history)
        df = _fetch_features(lookback_days=300)
        X = df[["vix", "spy_5d_ret", "iwm_vs_e200"]].values

        # Posterior state probabilities for last observation
        probs = model.predict_proba(X)[-1]  # shape (4,)
        raw_state = int(np.argmax(probs))

        # Map raw state index → semantic rank → name
        rank = int(np.where(sorted_by_vix == raw_state)[0][0])
        state_name = STATE_NAMES[rank]

        # Build named probability dict
        prob_named = {}
        for r, name in enumerate(STATE_NAMES):
            raw_s = int(sorted_by_vix[r])
            prob_named[name] = round(float(probs[raw_s]), 4)

        return {
            "hmm_state": state_name,
            "hmm_prob": prob_named,
            "model_age_days": round(_model_age_days(), 1),
            "n_obs": n_obs,
            "vix_now": round(float(df["vix"].iloc[-1]), 2),
            "spy_5d_return": round(float(df["spy_5d_ret"].iloc[-1]), 4),
            "iwm_vs_ema200": round(float(df["iwm_vs_e200"].iloc[-1]), 4),
        }
    except Exception as exc:
        log.warning("hmm_regime.predict_failed error=%s", exc)
        return {"error": str(exc)}


def refit() -> dict:
    """Force-refit the HMM model. Returns fit summary."""
    try:
        import hmmlearn  # noqa: F401
    except ImportError:
        return {"error": "hmmlearn not installed"}
    try:
        return _fit_and_save()
    except Exception as exc:
        return {"error": str(exc)}
