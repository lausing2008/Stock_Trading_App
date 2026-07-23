from common.service import create_app

from .api.routes import router
from .api.calibration import router as calibration_router
from .api.outcomes import router as outcomes_router

# calibration_router/outcomes_router must be registered BEFORE router — router contains the
# catch-all GET /{symbol} route, which would otherwise shadow their literal paths (e.g.
# /confidence-calibration, /tune_status) since FastAPI matches routes in registration order.
app = create_app("signal-engine", routers=[calibration_router, outcomes_router, router])
