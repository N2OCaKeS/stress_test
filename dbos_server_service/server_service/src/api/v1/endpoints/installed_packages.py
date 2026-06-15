"""Live-просмотр установленных пакетов сервера — без БД-таблицы.

В отличие от `disks` / `server_accounts` (CRUD с persistent state) пакеты
не хранятся в server_service: каждый запрос идёт через worker по SSH к
самому серверу. Это намеренно — пакетов на хосте тысячи, дублировать их
в нашей БД и держать в синхроне с реальностью дороже, чем поднять live
probe раз в надобность.

Endpoint — единственный, POST (потому что фактически создаём worker-task,
GET был бы вводящим в заблуждение для side-effect'а). Доступ — `(server,
view)`: пакеты на конкретном сервере = атрибут сервера, отдельной
matrix-entity больше нет.

Worker-task `installed_packages.list` использует `SshClient.run`:
* Debian/Astra/Ubuntu — `dpkg-query -W -f='${Package} ${Version}\\n' '<pattern>'`
* RHEL/CentOS — `rpm -qa --queryformat '%{NAME} %{VERSION}\\n' '<pattern>'`
Выбор делает сам worker (`which dpkg || which rpm`). Pattern — shell glob,
не regex (мы НЕ оборачиваем в `re.escape` — dpkg / rpm сами умеют `*?[]`).

URL vs action_kind: путь — `/installed-packages` (kebab, человекочитаемо
для оператора и Swagger UI), `task_kind` / `audit_action` —
`installed_packages.list` (snake_case, машинный ключ для SIEM/registry).
Эта пара намеренно различна: коллекция URL'ов сервиса гомогенна в kebab-case
(`/server-accounts`, `/ipmi-controllers`, `/os-versions`), а task_kind/audit
живут в namespace'е `<entity>.<verb>` и сохраняют historical snake_case
(используется как ключ в worker'е и в audit-registry).
"""

import re

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.v1.endpoints._dispatch import dispatch_server_ssh_task
from src.api.v1.endpoints.worker_dispatch import require_server_prepared
from src.core.constants import Action, EntityType, ServerStatus
from src.core.exceptions import (
    AuthorizationError,
    ConflictError,
    DomainValidationError,
    NotFoundError,
)
from src.dependencies.auth import CurrentUserIdentity
from src.dependencies.db import get_db
from src.schemas.server import ServerTaskDispatchResponse
from src.services import audit_service, permissions
from src.services import server as server_svc
from src.services.audit_helpers import emit_denied_on_authz_error

router = APIRouter(prefix="/servers/{server_id}")


# Pattern — shell glob, разрешаем только безопасный набор символов. Никаких
# пробелов, кавычек, `;`, `$`, `&`, `|`, обратных кавычек: всё, что в shell
# могло бы привести к injection. Glob-метасимволы `*?[]` — оставляем, dpkg
# и rpm их интерпретируют сами.
_PATTERN_RE = re.compile(r"^[A-Za-z0-9._\-+*?\[\]]+$")

# Жёсткий cap на число возвращаемых строк. На стандартной Astra-коробке
# `dpkg -l` отдаёт порядка 2-3 тысяч пакетов; 10k с запасом покрывает
# серверы с дополнительными репозиториями и одновременно отбивает явные
# DoS-pattern'ы типа одиночной звёздочки против обслуживающих устройств с
# распухшим pkg-DB. Worker применяет cap уже на стороне SSH-команды
# (`head -n`), endpoint лишь прокидывает значение в payload.
_MAX_INSTALLED_PACKAGES_ROWS = 10000


@router.post(
    "/installed-packages",
    response_model=ServerTaskDispatchResponse,
    status_code=202,
    summary="Live-список установленных пакетов через worker (SSH + dpkg/rpm)",
    description=(
        "Публикует задачу `installed_packages.list` в taskiq-broker. Worker идёт "
        "на сервер по SSH под управляющим пользователем (после prepare) и "
        "выполняет `dpkg-query` (Debian/Ubuntu/Astra) либо `rpm -qa` (RHEL) с "
        "glob-паттерном. Сервер обязан быть подготовлен (`is_managed`), иначе "
        "409 PREPARE_REQUIRED. Результат — `{packages: [{name, version}, ...]}` "
        "— кладётся в `task.result`. Endpoint ничего в БД не сохраняет (по "
        "дизайну: live truth, БД-кэш не выгоден при тысячах пакетов на хост). "
        "Pattern — shell glob (`htop`, `linux-image*`), не regex. "
        "Идемпотентность через `Idempotency-Key`."
    ),
    responses={
        202: {"description": "Задача принята, возвращается task_id."},
        400: {"description": "INVALID_PATTERN / IDEMPOTENCY_KEY_TOO_LONG."},
        403: {"description": "Нет роли с `view` на server либо чужой department."},
        404: {"description": "Сервер не найден / чужой dept (скрыто за 404)."},
        409: {"description": "SERVER_DECOMMISSIONED / PREPARE_REQUIRED (сервер не prepared) / TASK_IDEMPOTENT_CONFLICT / IDEMPOTENCY_KEY_REUSE_CONFLICT."},
        503: {"description": "Worker недоступен (WORKER_UNREACHABLE / WORKER_REDIS_NOT_CONFIGURED)."},
    },
)
async def list_installed_packages(
    server_id: str,
    identity: CurrentUserIdentity,
    request: Request,
    pattern: str = Query(
        default="*",
        min_length=1,
        max_length=128,
        description="Shell-glob паттерн (`htop`, `linux-image*`, `*-dev`). По умолчанию `*` — все пакеты.",
    ),
    db: AsyncSession = Depends(get_db),
) -> ServerTaskDispatchResponse:
    """Live-просмотр пакетов через worker.

    Доступ: `(server, view)` + dept-isolation сервера. Cross-dept → 404.
    Сервер обязан быть prepared.

    Возможные ошибки: 400 INVALID_PATTERN, 403 PERMISSION_DENIED,
    404 SERVER_NOT_FOUND, 409 SERVER_DECOMMISSIONED, 409 PREPARE_REQUIRED,
    409 TASK_IDEMPOTENT_CONFLICT, 503 WORKER_UNREACHABLE.

    Связано: `server_worker/src/tasks/installed_packages.py::installed_packages_list`.
    """
    audit_action = "installed_packages.list"

    # Pattern-валидация ДО visibility — простая sanity-проверка, не раскрывает
    # существование сервера. Без неё shell-injection в dpkg-cmd через
    # `pattern=; rm -rf /` теоретически возможен, хотя `asyncssh.run` не
    # запускает shell. Defence-in-depth.
    if not _PATTERN_RE.match(pattern):
        raise DomainValidationError(
            error_code="INVALID_PATTERN",
            message="pattern must match [A-Za-z0-9._\\-+*?\\[\\]]+",
        )

    # 1. Role-check на (server, view) — ДО visibility, чтобы 403 не работал
    # как existence-oracle (canon permission → visibility, как в `_dispatch_for_server`).
    with emit_denied_on_authz_error(
        audit_action,
        target_id=server_id,
        target_type="server",
        extra_details={"server_id": server_id},
        identity=identity,
    ):
        await permissions.require_action(db, identity, EntityType.SERVER, Action.VIEW)

    # 2. Visibility + dept isolation.
    try:
        server = await server_svc.get_server(db, identity, server_id)
    except (NotFoundError, AuthorizationError) as exc:
        # NotFoundError — visibility-404 (cross-dept / нет row): failure+allowed=True.
        # AuthorizationError — нет VIEW при наличии других прав: denied+allowed=False.
        if isinstance(exc, NotFoundError):
            audit_service.emit(
                audit_action, target_id=server_id, target_type="server",
                status="failure", allowed=True,
                details={"reason": "not_found_or_cross_dept"},
            )
        else:
            audit_service.emit(
                audit_action, target_id=server_id, target_type="server",
                status="denied", allowed=False,
                details={"reason": "no_view_permission"},
            )
        raise

    # 3. Decommissioned-gate — на списанном сервере SSH всё равно не пройдёт.
    if server.status == ServerStatus.DECOMMISSIONED:
        audit_service.emit(
            audit_action, target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={"reason": "decommissioned"},
        )
        raise ConflictError(
            error_code="SERVER_DECOMMISSIONED",
            message="Server is decommissioned and cannot accept worker operations",
        )

    # 3.5. Prepare-gate: live-probe идёт по SSH под управляющим ключом (после
    # prepare). Неподготовленный сервер → 409 PREPARE_REQUIRED. Тот же гейт,
    # что у inventory.sync / users.inventory.
    require_server_prepared(server, audit_action=audit_action)

    # 4. Dispatch + audit — общая обвязка в `_dispatch.dispatch_server_ssh_task`.
    # На managed-сервере worker заходит по ключу — аккаунта в payload нет.
    # `pattern`/`max_rows` едут доп-payload'ом, `pattern` дублируется в success.
    task_id, _ = await dispatch_server_ssh_task(
        db=db, identity=identity, request=request,
        server=server,
        task_kind="installed_packages.list",
        audit_action=audit_action,
        resolved_account_id=None,
        extra_payload={
            "pattern": pattern,
            "max_rows": _MAX_INSTALLED_PACKAGES_ROWS,
        },
        success_extra_details={"pattern": pattern},
    )
    return ServerTaskDispatchResponse(task_id=task_id, status="queued")
