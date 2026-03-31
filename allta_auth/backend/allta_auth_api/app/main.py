from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.routes.access_control import router as access_control_router
from app.api.v1.routes.admin import router as admin_router
from app.api.v1.routes.api_token import router as api_token
from app.api.v1.routes.auth import router as auth_router
from app.api.v1.routes.heal_checker import router as health
from app.api.v1.routes.integrations import router as integrations_router
from app.api.v1.routes.oauth_client import router as oauth_client_router
from app.api.v1.routes.user import router as user_router


openapi_tags = [
    {"name": "Авторизация", "description": "Логин/логаут и проверка JWT."},
    {"name": "Пользователь", "description": "Профиль и смена пароля текущего пользователя."},
    {"name": "Пользователи (админ)", "description": "Управление пользователями для администраторов."},
    {"name": "Роли и права", "description": "Справочник прав и управление ролями."},
    {"name": "Группы", "description": "Управление группами, участниками и правами групп."},
    {"name": "API токены", "description": "Бессрочные API-токены пользователей."},
    {"name": "Интеграции: Docker Registry", "description": "Выдача токенов для Docker Registry."},
    {
        "name": "Интеграции: Service Auth",
        "description": "Универсальные проверки доступа для внешних сервисов (whoami/basic/devpi).",
    },
    {"name": "Интеграции: OAuth", "description": "Универсальный OAuth2 вход для внешних сервисов."},
    {"name": "OAuth клиенты", "description": "Админ-управление OAuth-клиентами."},
    {"name": "Служебные", "description": "Технические endpoint (health checks)."},
]

app = FastAPI(
    title="Allta Auth API",
    version="1.0.0",
    root_path="/api/auth",  # префикс, под которым проксирует Nginx
    openapi_url="/v1/openapi.json",
    docs_url="/v1/docs",
    redoc_url="/v1/redoc",
    openapi_tags=openapi_tags,
)

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
    "https://allta.devos.astralinux.ru:21500",
    "http://allta.devos.astralinux.ru:21501",
    "https://allta.devos.astralinux.ru:21501",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

app.include_router(health, prefix="")
app.include_router(auth_router, prefix="")
app.include_router(user_router, prefix="/v1")
app.include_router(admin_router, prefix="/v1")
app.include_router(access_control_router, prefix="/v1")
app.include_router(oauth_client_router, prefix="/v1")
app.include_router(api_token, prefix="/v1")
app.include_router(integrations_router, prefix="/v1")
