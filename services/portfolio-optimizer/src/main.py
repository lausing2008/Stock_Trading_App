from common.service import create_app

from .api.routes import router
from .api.risk import router as risk_router

app = create_app("portfolio-optimizer", routers=[router, risk_router])
