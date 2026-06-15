"""Сборка v1-роутера: подключаем все endpoint-файлы."""

from fastapi import APIRouter

from src.api.v1.endpoints.credentials import router as credentials_router
from src.api.v1.endpoints.dept_grants import router as dept_grants_router
from src.api.v1.endpoints.health import router as health_router
from src.api.v1.endpoints.internal import ops_router as internal_ops_router
from src.api.v1.endpoints.internal import router as internal_router
from src.api.v1.endpoints.role_acls import router as role_acls_router
from src.api.v1.endpoints.secrets_migration import router as secrets_migration_router
from src.api.v1.endpoints.user_acls import router as user_acls_router

router = APIRouter()
router.include_router(health_router, tags=["health"])
router.include_router(credentials_router)
router.include_router(role_acls_router)
router.include_router(user_acls_router)
router.include_router(dept_grants_router)
router.include_router(internal_router)
router.include_router(internal_ops_router)
router.include_router(secrets_migration_router)
