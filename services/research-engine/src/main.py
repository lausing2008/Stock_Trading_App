from common.service import create_app

from .api.ai_proxy import router as ai_router
from .api.routes import router

app = create_app("research-engine", routers=[router, ai_router])
