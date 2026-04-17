from common.service import create_app

from .api.routes import router

app = create_app("signal-engine", routers=[router])
