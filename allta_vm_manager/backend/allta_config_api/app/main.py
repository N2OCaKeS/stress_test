from app.api.v1.routes import router
from fastapi import FastAPI

app = FastAPI(title="Allta Config API",
    version="1.0.0",
    root_path="/api/config",          # префикс, под которым проксирует Nginx
    openapi_url="/v1/openapi.json", # с учётом версии API
    docs_url="/v1/docs",
    redoc_url="/v1/redoc",)

app.include_router(router, prefix="/v1/config",tags=["config"])
