from common.service import create_app

from .api.routes import router

app = create_app("ml-prediction-service", routers=[router])
