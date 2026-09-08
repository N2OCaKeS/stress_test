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

from src.api.v1.endpoints.acs_settings import (
    internal_router as acs_settings_internal_router,
    router as acs_settings_router,
)
from src.api.v1.endpoints.admin_encryption import router as admin_encryption_router
from src.api.v1.endpoints.boxes import router as boxes_router
from src.api.v1.endpoints.admin_password_policy import (
    router as admin_password_policy_router,
)
from src.api.v1.endpoints.console import router as console_router
from src.api.v1.endpoints.console import vm_router as vm_console_router
from src.api.v1.endpoints.console_macros import router as console_macros_router
from src.api.v1.endpoints.health import router as health_router
from src.api.v1.endpoints.host_disk import router as host_disk_router
from src.api.v1.endpoints.host_services import router as host_services_router
from src.api.v1.endpoints.host_services_settings import (
    router as host_services_settings_router,
)
from src.api.v1.endpoints.ipmi import list_router as ipmi_list_router
from src.api.v1.endpoints.ipmi import list_router_legacy as ipmi_list_router_legacy
from src.api.v1.endpoints.ipmi import router as ipmi_router
from src.api.v1.endpoints.installed_packages import (
    bulk_router as installed_packages_bulk_router,
    packages_action_router as installed_packages_action_router,
    router as installed_packages_router,
)
from src.api.v1.endpoints.internal import router as internal_router
from src.api.v1.endpoints.internal_prepare_for_test import (
    router as internal_prepare_for_test_router,
    worker_router as internal_prepare_for_test_worker_router,
)
from src.api.v1.endpoints.internal_service_reservation import (
    router as internal_service_reservation_router,
)
from src.api.v1.endpoints.inventory import users_router as users_inventory_router
from src.api.v1.endpoints.management_user_config import (
    router as management_user_config_router,
)
from src.api.v1.endpoints.ops import router as ops_router
from src.api.v1.endpoints.os_versions import router as os_versions_router
from src.api.v1.endpoints.permissions import router as permissions_router
from src.api.v1.endpoints.resource_permissions import (
    router as resource_permissions_router,
)
from src.api.v1.endpoints.secrets_migration import router as secrets_migration_router
from src.api.v1.endpoints.server_accounts import router as server_accounts_router
from src.api.v1.endpoints.server_categories import router as server_categories_router
from src.api.v1.endpoints.servers import router as servers_router
from src.api.v1.endpoints.system_settings import (
    internal_router as system_settings_internal_router,
    router as system_settings_router,
)
from src.api.v1.endpoints.tasks import router as tasks_router
from src.api.v1.endpoints.vms import (
    router as vms_router,
    router_images as vms_images_router,
    router_ip_pools as vms_ip_pools_router,
    router_presets as vms_presets_router,
    router_servers as vms_servers_router,
)
from src.api.v1.endpoints.worker_dispatch import (
    router_accounts as worker_dispatch_accounts_router,
    router_ipmi as worker_dispatch_ipmi_router,
    router_servers as worker_dispatch_servers_router,
    router_servers_bulk as worker_dispatch_servers_bulk_router,
)

router = APIRouter()
router.include_router(health_router, tags=["health"])
# Локальная заполняемость диска на хосте самого server_service (не managed
# test-серверов) — GET /host/diskspace, для карточки на главной. Любой
# аутентифицированный актор, без DB.
router.include_router(host_disk_router, tags=["host"])
# Статус ASTRA-сервисов (внешний HTTP+DNS) и ALLTA systemd-юнитов (по SSH на
# хост) + control (start/stop/restart) под account_admin. GET открыт любому
# аутентифицированному актору, control — только account_admin.
router.include_router(host_services_router, tags=["host"])
router.include_router(servers_router, tags=["servers"])
router.include_router(server_accounts_router, tags=["server-accounts"])
# VM-домен: /vms (CRUD + питание + бронь + by-number) и prepare-vms-hub под
# /servers/{id}. Регистрируется после servers_router — пути не коллидируют
# (by-number сервера живёт в самом servers_router перед /{server_id}).
router.include_router(vms_router, tags=["vms"])
router.include_router(vms_servers_router, tags=["vms"])
# Каталог боксов-образов ВМ (глобальный): list + refresh с FTP-конфига.
router.include_router(vms_images_router, tags=["vms"])
# Пулы IP-адресов ВМ (IPAM): CRUD под правом vm.net_manage.
router.include_router(vms_ip_pools_router, tags=["vms"])
# Пресеты стандартных ВМ: CRUD под правом vm.preset_manage.
router.include_router(vms_presets_router, tags=["vms"])
# Каталог боксов-заготовок для создания ВМ (пер-департамент CRUD под матрицей box).
router.include_router(boxes_router, tags=["boxes"])
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
# Массовые изменяющие операции с пакетами — `POST /servers/packages/bulk-action`.
# Тот же приём со статическим сегментом `packages`, что у bulk-запроса.
router.include_router(installed_packages_action_router, tags=["installed-packages"])
router.include_router(users_inventory_router, tags=["server-accounts"])
router.include_router(os_versions_router, tags=["os-versions"])
router.include_router(server_categories_router, tags=["server-categories"])
router.include_router(permissions_router, tags=["permissions"])
router.include_router(resource_permissions_router, tags=["resource-permissions"])
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
# Интерактивная консоль ВМ (тот же Redis-мост). WS /vms/{id}/console/ws —
# ssh/serial-терминал через hub в гостя под учёткой.
router.include_router(vm_console_router, tags=["console"])
# Макросы консоли — справочник сохранённых команд (личные + системные в отделе).
router.include_router(console_macros_router, tags=["console-macros"])
# Internal — без tags, include_in_schema=False (скрыт из публичного OpenAPI).
router.include_router(internal_router)
router.include_router(secrets_migration_router)
# Ops — отдельный s2s-канал под shared-secret (X-Service-Identity), для
# rotation_runner и подобных. Тоже скрыт из OpenAPI.
router.include_router(ops_router)
# Бронь сервера от имени сервиса (testing_service/acs) — тот же shared-secret
# канал, что и ops, но по серверам: acquire/release/смена стадии.
router.include_router(internal_service_reservation_router)
# Асинхронный контракт `prepare-for-test`: вход от testing_service (тот же
# shared-secret канал) + callback воркера о новых шагах пайплайна.
router.include_router(internal_prepare_for_test_router)
router.include_router(internal_prepare_for_test_worker_router)
# Admin-эндпоинты ротации ключей шифрования для account_admin. Инфраструктура,
# не бизнес-данные — явное исключение из platform_admin_guard business-блока.
router.include_router(admin_encryption_router)
# Настраиваемая парольная политика server-аккаунтов — платформенный singleton
# под account_admin. Сервисная настройка уровня платформы, тоже исключение из
# business-блока.
router.include_router(admin_password_policy_router)
# Конфиг управляющей учётки — платформенный singleton под account_admin.
# Сервисная настройка уровня платформы, тоже исключение из business-блока.
router.include_router(management_user_config_router)
# Настройки проб статуса (частота ping/ssh/ipmi-опроса) — платформенный
# singleton под account_admin. Тоже сервисная настройка, исключение из
# business-блока. Плюс internal-read для воркера (скрыт из OpenAPI).
router.include_router(system_settings_router)
router.include_router(system_settings_internal_router)
# Настройки доступа к ACS (снимки дисков физических серверов) — тот же
# паттерн singleton-под-account_admin + internal-read для воркера, плюс
# per-department opt-in (/settings/acs/departments).
router.include_router(acs_settings_router)
router.include_router(acs_settings_internal_router)
# Настройки SSH-доступа к хосту для host-service control (ALLTA-юниты) —
# платформенный singleton под account_admin, тот же паттерн, что у ACS.
router.include_router(host_services_settings_router)
