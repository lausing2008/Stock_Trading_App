"""Technical Analysis Service — indicators + patterns + trendlines + S/R."""
from common.service import create_app

from .api.routes import router

app = create_app("technical-analysis-service", routers=[router])
