"""Сборка v1-роутера: подключаем все endpoint-файлы."""

from fastapi import APIRouter

from src.api.v1.endpoints.health import router as health_router
from src.api.v1.endpoints.ipmi import list_router as ipmi_list_router
from src.api.v1.endpoints.ipmi import router as ipmi_router
from src.api.v1.endpoints.installed_packages import router as installed_packages_router
from src.api.v1.endpoints.internal import router as internal_router
from src.api.v1.endpoints.inventory import router as inventory_router
from src.api.v1.endpoints.inventory import users_router as users_inventory_router
from src.api.v1.endpoints.os_versions import router as os_versions_router
from src.api.v1.endpoints.permissions import router as permissions_router
from src.api.v1.endpoints.secrets_migration import router as secrets_migration_router
from src.api.v1.endpoints.server_accounts import router as server_accounts_router
from src.api.v1.endpoints.servers import router as servers_router
from src.api.v1.endpoints.worker_dispatch import (
    router_accounts as worker_dispatch_accounts_router,
    router_ipmi as worker_dispatch_ipmi_router,
    router_servers as worker_dispatch_servers_router,
)

router = APIRouter()
router.include_router(health_router, tags=["health"])
router.include_router(servers_router, tags=["servers"])
router.include_router(server_accounts_router, tags=["server-accounts"])
router.include_router(ipmi_router, tags=["ipmi"])
router.include_router(ipmi_list_router, tags=["ipmi"])
router.include_router(installed_packages_router, tags=["installed-packages"])
router.include_router(inventory_router, tags=["inventory"])
router.include_router(users_inventory_router, tags=["server-accounts"])
router.include_router(os_versions_router, tags=["os-versions"])
router.include_router(permissions_router, tags=["permissions"])
# Worker-dispatch endpoints: power.status / inventory.sync + admin-initiated
# rotate'ы для server_account / ipmi_controller. Все — тонкие dispatch'еры
# через `worker_client.dispatch_task` (см. `endpoints/worker_dispatch.py`).
# Префиксы остаются в стандартном `/servers/{id}` / `/server-accounts/{id}` /
# `/ipmi-controllers/{id}`.
router.include_router(worker_dispatch_servers_router, tags=["servers"])
router.include_router(worker_dispatch_accounts_router, tags=["server-accounts"])
router.include_router(worker_dispatch_ipmi_router, tags=["ipmi-controllers"])
# Internal — без tags, include_in_schema=False (скрыт из публичного OpenAPI).
router.include_router(internal_router)
router.include_router(secrets_migration_router)
