from fastapi import FastAPI




app = FastAPI(title="Allta VM API",
    version="1.0.0",
    root_path="/api/vm",
    openapi_url="/v1/openapi.json",
    docs_url="/v1/docs",
    redoc_url="/v1/redoc",)


# app.include_router(os_versions_router, prefix="/v1", tags=["os"])
# app.include_router(physical_servers_router, prefix="/v1", tags=["physical-server"])