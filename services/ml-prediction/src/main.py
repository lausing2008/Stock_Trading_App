from pathlib import Path

from common.config import get_settings
from common.logging import get_logger
from common.service import create_app

from .api.routes import router

log = get_logger("ml-prediction")


async def _startup():
    settings = get_settings()
    model_dir = Path(settings.model_dir)
    xgb_dir = model_dir / "xgboost"
    if not xgb_dir.exists():
        log.warning("ml.startup.no_models", model_dir=str(model_dir), msg="No XGBoost models found — run POST /ml/train_all first")
        return
    trained = list(xgb_dir.glob("*.joblib"))
    log.info("ml.startup.models_found", count=len(trained), model_dir=str(xgb_dir))
    if len(trained) == 0:
        log.warning("ml.startup.no_models", model_dir=str(xgb_dir), msg="model_dir exists but is empty — run POST /ml/train_all first")


app = create_app("ml-prediction-service", routers=[router], on_startup=_startup)
