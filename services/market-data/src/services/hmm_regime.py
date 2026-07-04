"""HMM regime classifier — T211.

Trains a 4-state GaussianHMM on (VIX_level, SPY_5d_return, IWM_vs_EMA200).
T232-ML7: features are standardized (StandardScaler) before fitting so the return-based
features aren't drowned out by VIX's much larger numeric range. States are labeled by a
composite rank (SPY 5d return primary, VIX mean as tiebreaker) rather than pure VIX-mean
sort: rank-0 = bull, rank-1 = neutral, rank-2 = choppy, rank-3 = bear.
Model persisted to /tmp/hmm_regime.pkl (model + scaler + label mapping). Auto-refreshes when
older than REFRESH_DAYS; a fit that doesn't converge, or a refresh that throws (e.g. yfinance
outage), keeps using the existing pickle rather than deploying a bad model or losing the
signal entirely.
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


def _label_states(model, scaler) -> np.ndarray:
    """T232-ML7: map raw HMM state index -> semantic rank (0=bull .. 3=bear).

    Previously ranked purely by ascending mean VIX. Because VIX (~15-80) and the return-based
    features (~+/-0.05) were never standardized, VIX dominated the model's covariance
    structure, and pure-VIX ranking meant a low-VIX GRINDING DOWNTREND could be labeled "bull"
    (low VIX, but negative returns), while the two middle states could swap semantic identity
    between weekly refits with no meaningful separation between them — silently flapping the
    QW-8 bear-overlay position-size reduction that reads this label.

    Composite ranking: sort primarily by mean SPY 5-day return, highest (most bullish) first,
    using mean VIX (lowest first) only as a tie-breaker for near-equal return states. This
    ties the label to what "bull"/"bear" actually mean (price direction) rather than to
    volatility level alone — a state can have moderate VIX and still be correctly labeled
    bear if returns are negative there, and vice versa.
    """
    # means_ are in STANDARDIZED units (scaler applied before fit) — unscale to get real VIX/
    # return units for the composite score, since z-scored VIX and z-scored returns aren't on
    # a comparable footing for a manual weighted sort.
    means_real = scaler.inverse_transform(model.means_)
    ret_means = means_real[:, 1]   # spy_5d_ret column
    vix_means = means_real[:, 0]   # vix column
    # Rank 0 must be the MOST bullish state (highest return, lowest VIX as tiebreak) and
    # rank 3 the most bearish. np.lexsort only sorts ascending, so negate both keys to get
    # descending-return-first / descending-VIX-tiebreak ordering in a single ascending sort.
    composite = np.lexsort((-vix_means, -ret_means))  # last key is primary in lexsort
    return composite  # composite[rank] = raw_state_idx, rank 0 = most bullish


def _fit_and_save() -> dict:
    from hmmlearn.hmm import GaussianHMM
    from sklearn.preprocessing import StandardScaler
    import joblib

    df = _fetch_features()
    X_raw = df[["vix", "spy_5d_ret", "iwm_vs_e200"]].values

    # T232-ML7: standardize before fitting. Unscaled, VIX's ~15-80 range swamps the
    # covariance structure relative to the ~+/-0.05 return-based features, making the model
    # effectively a VIX-only classifier regardless of the other two features' actual predictive
    # value. The fitted scaler is persisted alongside the model so predict_current() transforms
    # new observations the same way.
    scaler = StandardScaler()
    X = scaler.fit_transform(X_raw)

    model = GaussianHMM(
        n_components=4,
        covariance_type="full",
        n_iter=300,
        random_state=42,
    )
    model.fit(X)

    # T232-ML7: model.fit() never raises on non-convergence — it silently returns whatever
    # state the EM algorithm reached after n_iter iterations. A non-converged model was
    # previously saved and used for a full week regardless. Now checked explicitly; a
    # non-converged fit raises so the caller (predict_current) can fall back to the existing
    # pickle instead of deploying a model that never stabilized.
    if not model.monitor_.converged:
        raise RuntimeError(
            f"HMM fit did not converge after {model.monitor_.iter} iterations "
            f"(tolerance={model.monitor_.tol}, final log-likelihood delta unresolved)"
        )

    composite = _label_states(model, scaler)

    joblib.dump(
        {"model": model, "scaler": scaler, "sorted_by_vix": composite, "n_obs": len(df)},
        MODEL_PATH,
    )
    vix_means = scaler.inverse_transform(model.means_)[:, 0]
    log.info("hmm_regime.fitted n_obs=%d vix_means=%s converged=%s",
              len(df), vix_means.round(2).tolist(), model.monitor_.converged)
    return {"n_obs": len(df), "state_vix_means": vix_means.round(2).tolist(),
            "converged": bool(model.monitor_.converged)}


def predict_current() -> dict:
    """Fit (if needed) and return current HMM state + probability distribution."""
    try:
        import hmmlearn  # noqa: F401 — import check only
    except ImportError:
        return {"error": "hmmlearn not installed — run: pip install hmmlearn>=0.3.0"}

    try:
        import joblib

        if _model_age_days() > REFRESH_DAYS:
            # T232-ML7: a refit failure (yfinance outage, non-convergence) previously
            # propagated straight out of predict_current(), returning {"error": ...} and
            # losing the regime signal entirely even though the still-valid week-old pickle
            # was sitting right there on disk, untouched. Now caught here specifically so a
            # transient upstream failure degrades to a stale-but-real prediction instead of
            # no prediction at all — matching the QW-8 caller's own fail-open design (no
            # signal = no size reduction, so silently losing the signal isn't actually safe).
            try:
                _fit_and_save()
            except Exception as _refit_exc:
                if os.path.exists(MODEL_PATH):
                    log.warning("hmm_regime.refit_failed_using_stale_model error=%s age_days=%s",
                                _refit_exc, round(_model_age_days(), 1))
                else:
                    raise

        data = joblib.load(MODEL_PATH)
        model = data["model"]
        sorted_by_vix = data["sorted_by_vix"]
        n_obs = data.get("n_obs", 0)
        scaler = data.get("scaler")  # absent in pickles saved before T232-ML7

        # Use 300 days for prediction context (HMM needs sequence history)
        df = _fetch_features(lookback_days=300)
        X_raw = df[["vix", "spy_5d_ret", "iwm_vs_e200"]].values
        # T232-ML7: transform with the SAME scaler fit alongside this model — mismatched
        # scaling between fit and predict would silently corrupt every posterior probability.
        X = scaler.transform(X_raw) if scaler is not None else X_raw

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
