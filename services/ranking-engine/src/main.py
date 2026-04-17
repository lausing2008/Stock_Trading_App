from common.service import create_app

from .api.routes import router

app = create_app("ranking-engine", routers=[router])
