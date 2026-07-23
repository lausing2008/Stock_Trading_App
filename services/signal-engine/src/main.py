from common.service import create_app

from .api.routes import router
from .api.calibration import router as calibration_router
from .api.outcomes import router as outcomes_router

app = create_app("signal-engine", routers=[router, calibration_router, outcomes_router])
