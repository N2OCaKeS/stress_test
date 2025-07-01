from fastapi import FastAPI

from app.api.v1.routes.os_versions import router as os_versions_router
from app.api.v1.routes.manage_servers import router as manage_servers_router


app = FastAPI(title="Allta Server API",
    version="1.0.0",
    root_path="/api/server",
    openapi_url="/v1/openapi.json",
    docs_url="/v1/docs",
    redoc_url="/v1/redoc",)


app.include_router(os_versions_router, prefix="/v1", tags=["OS"])
app.include_router(manage_servers_router, prefix="/v1", tags=["Server"])