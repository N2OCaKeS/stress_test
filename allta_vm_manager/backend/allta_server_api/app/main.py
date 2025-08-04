from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.routes.os_versions import router as os_versions_router
from app.api.v1.routes.manage_servers import router as manage_servers_router
from app.api.v1.routes.control_servers import router as control_servers_router
from app.api.v1.routes.heal_checker import router as health
app = FastAPI(title="Allta Server API",
    version="1.0.0",
    root_path="/api/server",
    openapi_url="/v1/openapi.json",
    docs_url="/v1/docs",
    redoc_url="/v1/redoc",)
origins = [
    "http://localhost:8000",
    "http://localhost:8001",
    "http://localhost:8002",
    "http://localhost:8003",
    "http://localhost:8080",    
    "http://127.0.0.1:8000",
    "http://127.0.0.1:8001",
    "http://127.0.0.1:8002", 
    "http://127.0.0.1:8003",    
    "http://127.0.0.1:8080",               
    "http://allta.devos.astralinux.ru",
    "https://allta.devos.astralinux.ru",]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

app.include_router(health, prefix="", tags=["Health"])
app.include_router(os_versions_router, prefix="/v1", tags=["OS"])
app.include_router(manage_servers_router, prefix="/v1", tags=["Server"])
app.include_router(control_servers_router, prefix="/v1", tags=["Control"])