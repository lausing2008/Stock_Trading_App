from common.service import create_app

from .api.routes import router

app = create_app("research-engine", routers=[router])
