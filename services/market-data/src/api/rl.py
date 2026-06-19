"""AL-1: RL agent API — training trigger, status, and per-symbol recommendation."""
import threading

from fastapi import APIRouter, BackgroundTasks, Depends, Query

from db.models import User
from .auth import get_admin_user, get_current_user
from common.logging import get_logger

log = get_logger("rl_api")

router = APIRouter(prefix="/rl-agent", tags=["rl-agent"])

_train_lock = threading.Lock()
_train_running = False


def _train_and_reload() -> None:
    global _train_running
    try:
        from ..services.rl_agent import run_rl_training
        result = run_rl_training()
        if "error" in result:
            log.warning("rl_agent.api_train_failed", error=result["error"])
        else:
            log.info("rl_agent.api_train_done", n_trades=result["n_trades"])
    except Exception as exc:
        log.exception("rl_agent.api_train_exception", exc=str(exc))
    finally:
        _train_running = False


@router.get("/status")
def rl_status(_: User = Depends(get_current_user)) -> dict:
    """Return RL policy training status and metadata."""
    from ..services.rl_agent import load_rl_policy, _POLICY_FILE
    policy = load_rl_policy()
    if policy is None:
        return {
            "status": "not_trained",
            "is_running": _train_running,
            "policy_file": str(_POLICY_FILE),
            "note": "POST /rl-agent/train (admin) to train on closed paper trades.",
        }
    m = policy.meta
    return {
        "status": "ready",
        "is_running": _train_running,
        "n_trades": m.get("n_trades"),
        "win_rate": m.get("win_rate"),
        "avg_win_pct": m.get("avg_win_pct"),
        "avg_loss_pct": m.get("avg_loss_pct"),
        "threshold": m.get("threshold"),
        "trained_at": m.get("trained_at"),
        "feature_importance": m.get("feature_importance"),
    }


@router.post("/train")
def rl_train(
    background_tasks: BackgroundTasks,
    _: User = Depends(get_admin_user),
) -> dict:
    """Trigger RL agent training on all closed paper trades (background task).

    Requires ≥50 closed trades with pct_return. Runs Ridge regression on entry
    features (rr_ratio, confidence, entry_score, kscore, regime, style) → pct_return.
    Saves policy to /data/models/rl_policy.json.
    """
    global _train_running
    with _train_lock:
        if _train_running:
            return {"status": "already_running"}
        _train_running = True
    background_tasks.add_task(_train_and_reload)
    return {"status": "started"}


@router.get("/recommend")
def rl_recommend_endpoint(
    rr_ratio: float = Query(..., description="Risk:reward ratio at entry"),
    confidence: float = Query(..., description="Signal confidence 0–100"),
    entry_score: int = Query(3, description="Additive entry score"),
    kscore: float = Query(50.0, description="K-score ranking 0–100"),
    style: str = Query("SWING"),
    regime: str = Query("neutral"),
    _: User = Depends(get_current_user),
) -> dict:
    """Return RL policy recommendation for the given entry conditions (debug / manual use)."""
    from ..services.rl_agent import rl_recommend
    return rl_recommend(rr_ratio, confidence, entry_score, kscore, style, regime)
