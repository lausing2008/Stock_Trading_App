"""AL-1: Lightweight RL-inspired trading policy — linear Q-function fitted on paper trade history.

Framing: offline contextual bandit / fitted Q-iteration.
- State: entry conditions at time of trade (rr_ratio, confidence, entry_score, kscore, regime, style)
- Action: BUY (only action taken; WAIT is the implicit alternative)
- Reward: pct_return from the closed trade
- Policy: Q(s, BUY) = w^T * phi(s) via Ridge regression;  BUY if Q > threshold

We only observe (state, BUY, reward) pairs — no WAIT outcomes exist.
Ridge regression on the BUY samples estimates the expected return conditional on entry state.
If Q(s) < threshold → the policy recommends WAIT (expected return below median quality).

No stable-baselines3 / gym dependency: policy weights stored as plain JSON.
"""
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from common.logging import get_logger

log = get_logger("rl_agent")

_POLICY_FILE = Path("/data/models/rl_policy.json")

_STYLE_MAP  = {"SHORT": 0, "SWING": 1, "LONG": 2, "GROWTH": 3}
_REGIME_MAP = {"bull": 0, "neutral": 1, "choppy": 2, "risk_off": 3, "bear": 4}

FEATURE_NAMES = [
    "rr_norm", "conf_norm", "score_norm", "kscore_norm", "regime_enc",
    "style_SHORT", "style_SWING", "style_LONG", "style_GROWTH",
]

_MIN_TRADES = 50


def _feature_vector(
    rr_ratio: float,
    confidence: float,
    entry_score: float,
    kscore: float,
    style: str,
    regime: str,
) -> list[float]:
    rr_norm     = min(rr_ratio / 5.0, 2.0)
    conf_norm   = min(confidence, 100.0) / 100.0
    score_norm  = entry_score / 8.0
    kscore_norm = (min(max(kscore, 0.0), 100.0) - 50.0) / 50.0
    regime_enc  = _REGIME_MAP.get(regime.lower(), 1) / 4.0

    style_idx   = _STYLE_MAP.get(style.upper(), 1)
    style_feats = [1.0 if i == style_idx else 0.0 for i in range(4)]

    return [rr_norm, conf_norm, score_norm, kscore_norm, regime_enc] + style_feats


class RLPolicy:
    """Loaded RL policy — use rl_recommend() to make predictions."""

    def __init__(self, data: dict):
        self._data = data
        self._weights = np.array(data["weights"])
        self._intercept = float(data["intercept"])
        self._threshold = float(data.get("threshold", 0.0))

    # ── Prediction ────────────────────────────────────────────────────────────

    def q_value(
        self,
        rr_ratio: float,
        confidence: float,
        entry_score: float,
        kscore: float,
        style: str,
        regime: str,
    ) -> float:
        phi = np.array(_feature_vector(rr_ratio, confidence, entry_score, kscore, style, regime))
        return float(np.dot(self._weights, phi) + self._intercept)

    def recommend(
        self,
        rr_ratio: float,
        confidence: float,
        entry_score: float,
        kscore: float,
        style: str,
        regime: str,
    ) -> dict[str, Any]:
        q = self.q_value(rr_ratio, confidence, entry_score, kscore, style, regime)
        action = "BUY" if q >= self._threshold else "WAIT"
        return {
            "action": action,
            "q_value": round(q, 4),
            "threshold": round(self._threshold, 4),
            "available": True,
            "n_trades": self._data.get("n_trades", 0),
        }

    @property
    def meta(self) -> dict:
        return self._data


# ── Module-level policy cache ──────────────────────────────────────────────────

_policy_cache: RLPolicy | None = None


def load_rl_policy() -> RLPolicy | None:
    """Load the trained policy from disk. Returns None when not yet trained."""
    global _policy_cache
    if _policy_cache is not None:
        return _policy_cache
    try:
        if _POLICY_FILE.exists():
            data = json.loads(_POLICY_FILE.read_text())
            if data.get("n_trades", 0) >= _MIN_TRADES:
                _policy_cache = RLPolicy(data)
                log.info("rl_agent.policy_loaded", n_trades=data["n_trades"])
    except Exception as exc:
        log.warning("rl_agent.load_failed", error=str(exc))
    return _policy_cache


def reload_rl_policy() -> None:
    """Force reload on next call (e.g., after a fresh training run)."""
    global _policy_cache
    _policy_cache = None


def rl_recommend(
    rr_ratio: float,
    confidence: float,
    entry_score: int | float,
    kscore: float,
    style: str,
    regime: str,
    policy: RLPolicy | None = None,
) -> dict[str, Any]:
    """Return RL action recommendation for the given entry conditions.

    {"action": "BUY"|"WAIT"|"UNKNOWN", "q_value": float, "available": bool}
    """
    if policy is None:
        policy = load_rl_policy()
    if policy is None:
        return {"action": "UNKNOWN", "q_value": 0.0, "available": False}
    return policy.recommend(rr_ratio, confidence, float(entry_score), kscore, style, regime)


# ── Training ──────────────────────────────────────────────────────────────────

def train_rl_agent(trades: list) -> dict[str, Any]:
    """Fit a linear Q-function on closed paper trades.

    Parameters
    ----------
    trades : list of PaperTrade ORM rows (detached from session)

    Returns dict of weights (also saved to _POLICY_FILE) or {"error": ...}.
    """
    try:
        from sklearn.linear_model import Ridge
    except ImportError:
        return {"error": "scikit-learn not installed in market-data"}

    rows: list[list[float]] = []
    rewards: list[float] = []
    for t in trades:
        rr     = float(t.rr_ratio_at_entry    or 2.0)
        conf   = float(t.confidence_at_entry   or 50.0)
        score  = float(t.entry_score           or 3)
        kscore = float(t.kscore_at_entry       or 50.0)
        style  = str(t.trading_style           or "SWING")
        regime = str(t.market_regime_at_entry  or "neutral")
        reward = float(t.pct_return            or 0.0)
        rows.append(_feature_vector(rr, conf, score, kscore, style, regime))
        rewards.append(reward)

    if len(rows) < _MIN_TRADES:
        return {"error": f"Need ≥{_MIN_TRADES} closed trades with pct_return; have {len(rows)}"}

    X = np.array(rows)
    y = np.array(rewards)

    # Ridge regression: predict expected % return from entry conditions
    model = Ridge(alpha=1.0)
    model.fit(X, y)

    # Threshold: BUY only when predicted return is above the 40th percentile of training preds
    # (top 60% of expected setups are acted on, bottom 40% are skipped)
    preds = model.predict(X)
    threshold = float(np.percentile(preds, 40))

    win_mask = y > 0
    result: dict[str, Any] = {
        "weights":       model.coef_.tolist(),
        "intercept":     float(model.intercept_),
        "threshold":     threshold,
        "n_trades":      len(rows),
        "win_rate":      float(win_mask.mean()),
        "avg_win_pct":   float(y[win_mask].mean()) if win_mask.any() else 0.0,
        "avg_loss_pct":  float(y[~win_mask].mean()) if (~win_mask).any() else 0.0,
        "feature_names": FEATURE_NAMES,
        "trained_at":    datetime.now(timezone.utc).isoformat(),
    }

    # Feature importance: absolute weight magnitude (after normalised features)
    importance = {name: round(abs(w), 5) for name, w in zip(FEATURE_NAMES, model.coef_)}
    result["feature_importance"] = dict(sorted(importance.items(), key=lambda kv: -kv[1]))

    _POLICY_FILE.parent.mkdir(parents=True, exist_ok=True)
    _POLICY_FILE.write_text(json.dumps(result, indent=2))
    log.info(
        "rl_agent.trained",
        n_trades=len(rows),
        win_rate=round(result["win_rate"], 3),
        threshold=round(threshold, 4),
    )

    reload_rl_policy()
    return result


def run_rl_training() -> dict[str, Any]:
    """Load closed paper trades from DB and train the RL policy. Called by scheduler."""
    try:
        from sqlalchemy import select
        from db import SessionLocal, PaperTrade
    except ImportError as exc:
        return {"error": f"DB import failed: {exc}"}

    with SessionLocal() as session:
        trades = session.execute(
            select(PaperTrade).where(
                PaperTrade.stage == "closed",
                PaperTrade.pct_return.is_not(None),
                PaperTrade.rr_ratio_at_entry.is_not(None),
                PaperTrade.confidence_at_entry.is_not(None),
                PaperTrade.entry_score.is_not(None),
            )
        ).scalars().all()
        # Detach: copy to plain objects before session closes
        trade_snapshots = [_TradeSnapshot(t) for t in trades]

    return train_rl_agent(trade_snapshots)


class _TradeSnapshot:
    """Lightweight stand-in for a PaperTrade row, safe after session close."""
    __slots__ = (
        "rr_ratio_at_entry", "confidence_at_entry", "entry_score",
        "kscore_at_entry", "trading_style", "market_regime_at_entry", "pct_return",
    )

    def __init__(self, t: Any) -> None:
        self.rr_ratio_at_entry    = t.rr_ratio_at_entry
        self.confidence_at_entry  = t.confidence_at_entry
        self.entry_score          = t.entry_score
        self.kscore_at_entry      = t.kscore_at_entry
        self.trading_style        = t.trading_style
        self.market_regime_at_entry = t.market_regime_at_entry
        self.pct_return           = t.pct_return
