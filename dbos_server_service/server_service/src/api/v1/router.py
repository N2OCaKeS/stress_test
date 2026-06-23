"""Сборка v1-роутера: подключаем все endpoint-файлы.

User-facing endpoints, требующие user identity, защищены гардом
`require_user_context` через тип-алиас `CurrentUserIdentity`
(см. `src.dependencies.auth`): OAuth m2m identity (subject_type=oauth_client)
отбивается с 403 USER_CONTEXT_REQUIRED ДО того, как endpoint увидит запрос.

Гард применяется на уровне endpoint-функции (через подмену `CurrentIdentity`
на `CurrentUserIdentity` в сигнатуре), а НЕ на уровне роутера — иначе
публичные anonymous-ручки (`/os-versions` GET, `/health`) обязали бы Bearer,
что сломало бы их контракт.

Симметрично `auth_service.require_user_context` и
`secret_service.require_user_context`.
"""

from fastapi import APIRouter

from src.api.v1.endpoints.admin_encryption import router as admin_encryption_router
from src.api.v1.endpoints.console import router as console_router
from src.api.v1.endpoints.health import router as health_router
from src.api.v1.endpoints.ipmi import list_router as ipmi_list_router
from src.api.v1.endpoints.ipmi import list_router_legacy as ipmi_list_router_legacy
from src.api.v1.endpoints.ipmi import router as ipmi_router
from src.api.v1.endpoints.installed_packages import (
    bulk_router as installed_packages_bulk_router,
    router as installed_packages_router,
)
from src.api.v1.endpoints.internal import router as internal_router
from src.api.v1.endpoints.inventory import users_router as users_inventory_router
from src.api.v1.endpoints.ops import router as ops_router
from src.api.v1.endpoints.os_versions import router as os_versions_router
from src.api.v1.endpoints.permissions import router as permissions_router
from src.api.v1.endpoints.secrets_migration import router as secrets_migration_router
from src.api.v1.endpoints.server_accounts import router as server_accounts_router
from src.api.v1.endpoints.servers import router as servers_router
from src.api.v1.endpoints.tasks import router as tasks_router
from src.api.v1.endpoints.worker_dispatch import (
    router_accounts as worker_dispatch_accounts_router,
    router_ipmi as worker_dispatch_ipmi_router,
    router_servers as worker_dispatch_servers_router,
    router_servers_bulk as worker_dispatch_servers_bulk_router,
)

router = APIRouter()
router.include_router(health_router, tags=["health"])
router.include_router(servers_router, tags=["servers"])
router.include_router(server_accounts_router, tags=["server-accounts"])
router.include_router(ipmi_router, tags=["ipmi"])
router.include_router(ipmi_list_router, tags=["ipmi"])
# Legacy snake_case `/ipmi_controllers` — алиас на тот же handler, скрыт из
# OpenAPI. Существующим клиентам даёт время мигрировать на kebab-case
# `/ipmi-controllers`; в OpenAPI публикуется только канонический путь.
router.include_router(ipmi_list_router_legacy, tags=["ipmi"])
router.include_router(installed_packages_router, tags=["installed-packages"])
# Bulk-запрос пакетов — отдельный роутер без `{server_id}`-префикса
# (`POST /servers/installed-packages/bulk`). Регистрируем ДО servers_router'а
# нельзя (он уже выше), но статический сегмент `installed-packages` всё равно
# матчится раньше `{server_id}`-параметра в `/servers/...`.
router.include_router(installed_packages_bulk_router, tags=["installed-packages"])
router.include_router(users_inventory_router, tags=["server-accounts"])
router.include_router(os_versions_router, tags=["os-versions"])
router.include_router(permissions_router, tags=["permissions"])
# Cancel worker-task'и. Один endpoint — POST /tasks/{id}/cancel. Сами
# task-row'ы живут в server_worker (cross-DB engine из worker_client).
router.include_router(tasks_router, tags=["tasks"])
# Worker-dispatch endpoints: power.status / inventory.sync + admin-initiated
# rotate'ы для server_account / ipmi_controller. Все — тонкие dispatch'еры
# через `worker_client.dispatch_task` (см. `endpoints/worker_dispatch.py`).
# Префиксы остаются в стандартном `/servers/{id}` / `/server-accounts/{id}` /
# `/ipmi-controllers/{id}`.
router.include_router(worker_dispatch_servers_router, tags=["servers"])
# Массовый prepare — отдельный роутер без `{server_id}` в префиксе (иначе
# путь `/servers/prepare/bulk` коллидировал бы с `/servers/{id}/...`).
router.include_router(worker_dispatch_servers_bulk_router, tags=["servers"])
router.include_router(worker_dispatch_accounts_router, tags=["server-accounts"])
router.include_router(worker_dispatch_ipmi_router, tags=["ipmi-controllers"])
# Интерактивная SSH-консоль (WebSocket-мост к worker'у через Redis pub/sub).
# WS /servers/{id}/console/ws — RBAC (server, console) + prepared-gate.
router.include_router(console_router, tags=["console"])
# Internal — без tags, include_in_schema=False (скрыт из публичного OpenAPI).
router.include_router(internal_router)
router.include_router(secrets_migration_router)
# Ops — отдельный s2s-канал под shared-secret (X-Service-Identity), для
# rotation_runner и подобных. Тоже скрыт из OpenAPI.
router.include_router(ops_router)
# Admin-эндпоинты ротации ключей шифрования для account_admin. Инфраструктура,
# не бизнес-данные — явное исключение из platform_admin_guard business-блока.
router.include_router(admin_encryption_router)
