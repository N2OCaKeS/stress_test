from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.routes.heal_checker import router as health
from app.api.v1.routes.initialize_server import router as init_server
from app.api.v1.routes.ip_ranges import router as ip_range_routes
from app.api.v1.routes.vm import router as vm
from app.api.v1.routes.snapshot import router as snapshot

app = FastAPI(title="Allta VM API",
    version="1.0.0",
    root_path="/api/vm",
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
app.include_router(init_server, prefix="/v1", tags=["Server"])
app.include_router(ip_range_routes, prefix="/v1", tags=["IP Ranges"])
app.include_router(vm, prefix="/v1", tags=["VM"])
app.include_router(snapshot, prefix="/v1", tags=["Snapshot"])
