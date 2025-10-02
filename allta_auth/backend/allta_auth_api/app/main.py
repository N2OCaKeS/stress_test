from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.v1.routes.auth import router as auth_router
from app.api.v1.routes.user import router as user_router
from app.api.v1.routes.admin import router as admin_router
from app.api.v1.routes.api_token import router as api_token
from app.api.v1.routes.heal_checker import router as health

app = FastAPI(title="Allta Auth API",
    version="1.0.0",
    root_path="/api/auth",          # префикс, под которым проксирует Nginx
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
    "https://allta.devos.astralinux.ru",
    "http://allta.devos.astralinux.ru:21500",
    "http://allta.devos.astralinux.ru:21501"    
    ]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)
app.include_router(health, prefix="", tags=["Health"])
app.include_router(auth_router, prefix="", tags=["Auth"])
app.include_router(user_router, prefix="/v1", tags=["User"])
app.include_router(admin_router, prefix="/v1", tags=["Admin"])
app.include_router(api_token, prefix="/v1", tags=["Api key"])