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
"""

import re

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import Action, EntityType, ServerStatus
from src.core.exceptions import (
    AuthorizationError,
    ConflictError,
    DomainValidationError,
    NotFoundError,
    ServiceUnavailableError,
)
from src.dependencies.auth import CurrentIdentity
from src.dependencies.db import get_db
from src.dependencies.idempotency import read_idempotency_key
from src.services import audit_service, permissions, worker_client
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
    status_code=202,
    summary="Live-список установленных пакетов через worker (SSH + dpkg/rpm)",
    description=(
        "Публикует задачу `installed_packages.list` в taskiq-broker. Worker идёт "
        "на сервер по SSH (через default credentials) и выполняет "
        "`dpkg-query` (Debian/Ubuntu/Astra) либо `rpm -qa` (RHEL) с glob-паттерном. "
        "Результат — `{packages: [{name, version}, ...]}` — кладётся в `task.result`. "
        "Endpoint ничего в БД не сохраняет (по дизайну: live truth, БД-кэш не "
        "выгоден при тысячах пакетов на хост). Pattern — shell glob (`htop`, "
        "`linux-image*`), не regex. Идемпотентность через `Idempotency-Key`."
    ),
    responses={
        202: {"description": "Задача принята, возвращается task_id."},
        400: {"description": "INVALID_PATTERN — pattern содержит запрещённые символы."},
        403: {"description": "Нет роли с `view` на server либо чужой department."},
        404: {"description": "Сервер не найден / чужой dept (скрыто за 404)."},
        409: {"description": "SERVER_DECOMMISSIONED / TASK_IDEMPOTENT_CONFLICT."},
        503: {"description": "Worker недоступен (WORKER_UNREACHABLE / WORKER_REDIS_NOT_CONFIGURED)."},
    },
)
async def list_installed_packages(
    server_id: str,
    identity: CurrentIdentity,
    request: Request,
    pattern: str = Query(
        default="*",
        min_length=1,
        max_length=128,
        description="Shell-glob паттерн (`htop`, `linux-image*`, `*-dev`). По умолчанию `*` — все пакеты.",
    ),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Live-просмотр пакетов через worker.

    Доступ: `(server, view)` + dept-isolation сервера. Cross-dept → 404.

    Возможные ошибки: 400 INVALID_PATTERN, 403 PERMISSION_DENIED,
    404 SERVER_NOT_FOUND, 409 SERVER_DECOMMISSIONED, 409 TASK_IDEMPOTENT_CONFLICT,
    503 WORKER_UNREACHABLE.

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

    # 4. Dispatch + audit.
    idempotency_key = read_idempotency_key(request)
    payload: dict = {
        "server_id": server_id,
        "host": server.hostname,
        "ssh_port": server.ssh_port,
        "pattern": pattern,
        "max_rows": _MAX_INSTALLED_PACKAGES_ROWS,
        "target_department_id": server.department_id,
    }
    try:
        task_id = await worker_client.dispatch_task(
            task_kind="installed_packages.list",
            target_server_id=server_id,
            payload=payload,
            created_by=identity.user_id,
            request_id=getattr(request.state, "request_id", None),
            idempotency_key=idempotency_key,
        )
    except ConflictError:
        audit_service.emit(
            audit_action, target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={
                "reason": "idempotent_conflict",
                "task_kind": "installed_packages.list",
                "department_id": server.department_id,
            },
        )
        raise
    except ServiceUnavailableError:
        audit_service.emit(
            audit_action, target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={
                "reason": "worker_unreachable",
                "task_kind": "installed_packages.list",
                "department_id": server.department_id,
            },
        )
        raise
    audit_service.emit(
        audit_action, target_id=server_id, target_type="server",
        status="success", allowed=True,
        details={
            "task_id": task_id,
            "task_kind": "installed_packages.list",
            "pattern": pattern,
            "department_id": server.department_id,
        },
    )
    return {"task_id": task_id, "status": "queued"}
