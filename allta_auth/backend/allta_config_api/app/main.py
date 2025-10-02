from app.api.v1.routes import router
from app.api.v1.heal_checker import router as health
from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI

app = FastAPI(title="Allta Config API",
    version="1.0.0",
    root_path="/api/config",          # префикс, под которым проксирует Nginx
    openapi_url="/v1/openapi.json", # с учётом версии API
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
    "https://allta.devos.astralinux.ru",    
    "http://allta.devos.astralinux.ru:21500",
    "http://allta.devos.astralinux.ru:21501"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

app.include_router(health, prefix="", tags=["Health"])
app.include_router(router, prefix="/v1/config",tags=["Config"])
