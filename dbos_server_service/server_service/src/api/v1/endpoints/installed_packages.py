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
from src.core.config import get_settings
from src.core.constants import Action, EntityType, ServerStatus
from src.core.exceptions import (
    AppException,
    AuthorizationError,
    ConflictError,
    DomainValidationError,
    NotFoundError,
    ServiceUnavailableError,
)
from src.dependencies.auth import CurrentUserIdentity
from src.dependencies.db import get_db
from src.schemas.server import (
    BulkInstalledPackagesRequest,
    BulkInstalledPackagesResponse,
    BulkInstalledPackagesServerResult,
    BulkPackagesActionRequest,
    BulkPackagesActionResponse,
    BulkPackagesActionServerResult,
    ServerTaskDispatchResponse,
)
from src.services import audit_service, permissions, reservation
from src.services import server as server_svc
from src.services.audit_helpers import emit_denied_on_authz_error

router = APIRouter(prefix="/servers/{server_id}")
# Bulk-запрос не привязан к одному server_id, поэтому едет отдельным роутером
# без `{server_id}`-префикса. Путь `/servers/installed-packages/bulk` не
# конфликтует с `/servers/{server_id}/...`: FastAPI матчит статический сегмент
# `installed-packages` раньше path-параметра.
bulk_router = APIRouter(prefix="/servers")

# Массовые ИЗМЕНЯЮЩИЕ операции с пакетами едут отдельным роутером без
# `{server_id}`-префикса (как bulk-запрос). Путь `/servers/packages/bulk-action`
# не конфликтует с `/servers/{server_id}/...`: статический сегмент матчится
# раньше path-параметра.
packages_action_router = APIRouter(prefix="/servers")

# Маппинг action → (task_kind, audit_action). Машинные ключи task_kind'а
# совпадают с worker-тасками (`installed_packages.install/remove/update`),
# audit_action — namespace `server.packages_*` (мутация на сервере), как
# `server.power_*`.
_PACKAGES_ACTION_MAP: dict[str, tuple[str, str]] = {
    "install": ("installed_packages.install", "server.packages_install"),
    "remove": ("installed_packages.remove", "server.packages_remove"),
    "update": ("installed_packages.update", "server.packages_update"),
}


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
        description=(
            "Shell-glob паттерн(ы). Несколько масок — через пробел "
            "(`bash* ssh*`): worker матчит ПО ЛЮБОЙ (OR). По умолчанию `*` — все пакеты."
        ),
    ),
    db: AsyncSession = Depends(get_db),
) -> ServerTaskDispatchResponse:
    """Live-просмотр пакетов через worker.

    Доступ: `(server, view)` + dept-isolation сервера. Cross-dept → 404.
    Сервер обязан быть prepared.

    Возможные ошибки: 400 INVALID_PATTERN, 403 PERMISSION_DENIED,
    404 SERVER_NOT_FOUND, 409 SERVER_DECOMMISSIONED, 409 PREPARE_REQUIRED,
    409 TASK_IDEMPOTENT_CONFLICT, 503 WORKER_UNREACHABLE.

    Endpoint возвращает 202 и task_id — реальный результат (или ошибка)
    приходит в `task.last_error`/`task.status` при поллинге. Если сервер
    помечен подготовленным, но управляющая SSH-сессия на боксе не поднялась
    (типично — ОС переустановили, ключ управляющего пользователя утрачен),
    worker завершает задачу с `SERVER_MANAGEMENT_AUTH_FAILED` и подсказкой
    про повторный prepare (см. ниже по worker-слою).

    Связано: `server_worker/src/tasks/installed_packages.py::installed_packages_list`.
    """
    audit_action = "installed_packages.list"

    # Pattern-валидация ДО visibility — простая sanity-проверка, не раскрывает
    # существование сервера. Без неё shell-injection в dpkg-cmd через
    # `pattern=; rm -rf /` теоретически возможен, хотя `asyncssh.run` не
    # запускает shell. Defence-in-depth.
    #
    # Маски разделяются whitespace'ом (`bash* ssh*`) — каждую валидируем
    # отдельно, в payload уходит список `patterns` (worker OR-матчит по ним).
    patterns = pattern.split()
    if not patterns or not all(_PATTERN_RE.match(p) for p in patterns):
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
    # `patterns`/`max_rows` едут доп-payload'ом; worker OR-матчит по списку масок.
    # Дублируем `pattern` (raw query) для back-compat в payload и success-audit.
    task_id, _ = await dispatch_server_ssh_task(
        db=db, identity=identity, request=request,
        server=server,
        task_kind="installed_packages.list",
        audit_action=audit_action,
        resolved_account_id=None,
        extra_payload={
            "patterns": patterns,
            "pattern": pattern,
            "max_rows": _MAX_INSTALLED_PACKAGES_ROWS,
        },
        success_extra_details={"pattern": pattern},
    )
    return ServerTaskDispatchResponse(task_id=task_id, status="queued")


@bulk_router.post(
    "/installed-packages/bulk",
    response_model=BulkInstalledPackagesResponse,
    status_code=202,
    summary="Массовый live-запрос пакетов с нескольких серверов в сводную таблицу",
    description=(
        "Для каждого сервера из `server_ids` диспатчит `installed_packages.list` "
        "(тот же per-server SSH-probe, что у одиночного "
        "`POST /servers/{id}/installed-packages`) и собирает плоский per-server "
        "список с per-server статусом. Гейты как у одиночного, но per-server: "
        "`(server, view)` + dept-visibility (cross-dept скрыт под `not_found`), "
        "prepare-gate (неподготовленный → `prepare_required`, не 409 на весь "
        "батч), decommissioned → `decommissioned`. Reserve-гейт не нужен "
        "(read-only). Паттерны (shell glob) общие на весь батч: `patterns` — "
        "список (1..20), пакет матчит ЛЮБОЙ (OR); одиночный `pattern` оставлен "
        "для back-compat (приоритет у `patterns`).\n\n"
        "Модель async: dispatch только ставит задачи, реальные пакеты лежат в "
        "`task.result` каждого сервера — UI добирает их поллингом "
        "`GET /tasks/{task_id}` и сам раскладывает в pivot «пакеты×серверы» либо "
        "«серверы×пакеты». В ответе `packages` поэтому всегда пуст.\n\n"
        "Cap на число серверов — `INSTALLED_PACKAGES_BULK_MAX_SERVERS` (дефолт "
        "50); превышение → 413. Лимит строк пакетов per-server тот же, что у "
        "одиночного (`_MAX_INSTALLED_PACKAGES_ROWS`), применяется воркером."
    ),
    responses={
        202: {"description": "Батч обработан, per-server статусы в `results`."},
        400: {"description": "INVALID_PATTERN."},
        403: {"description": "Нет роли с `view` на server."},
        413: {"description": "BULK_PACKAGES_TOO_LARGE — server_ids длиннее cap'а."},
    },
)
async def bulk_installed_packages(
    body: BulkInstalledPackagesRequest,
    identity: CurrentUserIdentity,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> BulkInstalledPackagesResponse:
    """Массовый запрос пакетов: один dispatch на сервер, сводный per-server ответ.

    Доступ: `(server, view)` — проверяется один раз для всего батча (как у
    одиночного, role-check зависит только от ролей caller'а, не от target'а),
    дальше per-server visibility/decommissioned/prepare. Серверы, не
    прошедшие per-server гейт, попадают в результат со статусом
    (`not_found`/`decommissioned`/`prepare_required`/`auth_failed`), а не валят
    весь батч.

    Возможные ошибки: 400 INVALID_PATTERN, 403 PERMISSION_DENIED,
    413 BULK_PACKAGES_TOO_LARGE. Per-server проблемы — не HTTP-ошибки, а
    статусы в `results`.

    Связано: `list_installed_packages` (одиночный путь), worker-task
    `server_worker/src/tasks/installed_packages.py::installed_packages_list`.
    """
    audit_action = "installed_packages.list"
    # `patterns`/`pattern` сведены в один список (приоритет у `patterns`,
    # одиночный — back-compat, дефолт `["*"]`).
    patterns = body.effective_patterns()

    # Pattern-валидация ДО visibility — тот же allow-list, что у одиночного,
    # на каждый паттерн отдельно. Любой битый → 422, ни одного dispatch'а.
    if not all(_PATTERN_RE.match(p) for p in patterns):
        raise DomainValidationError(
            error_code="INVALID_PATTERN",
            message="each pattern must match [A-Za-z0-9._\\-+*?\\[\\]]+",
        )

    # Дедуп с сохранением порядка: повторный server_id в теле — одна задача,
    # одна строка в ответе. Порядок входного списка сохраняем для UI.
    seen: set[str] = set()
    server_ids: list[str] = []
    for sid in body.server_ids:
        if sid not in seen:
            seen.add(sid)
            server_ids.append(sid)

    # Cap на размер батча — каждый сервер порождает SSH-probe, не даём одному
    # запросу залить worker-пул. Считаем по уникальным id (после дедупа).
    cap = get_settings().installed_packages_bulk_max_servers
    if len(server_ids) > cap:
        audit_service.emit(
            audit_action, target_id=None, target_type="server",
            status="failure", allowed=True,
            details={
                "reason": "bulk_packages_too_large",
                "requested_count": len(server_ids),
                "cap": cap,
            },
        )
        raise AppException(
            error_code="BULK_PACKAGES_TOO_LARGE",
            message=(
                f"Bulk packages request of {len(server_ids)} servers exceeds "
                f"cap {cap}; split into smaller batches"
            ),
            http_status=413,
        )

    # Role-check один раз для всего батча — он смотрит только на роли caller'а,
    # не на конкретный server_id, поэтому per-server повторять не нужно (и это
    # не делает 403 existence-oracle'ом). 403 при отсутствии роли валит весь
    # батч — это про caller'а, а не про отдельный сервер.
    with emit_denied_on_authz_error(
        audit_action,
        target_id=None,
        target_type="server",
        extra_details={"bulk": True, "requested_count": len(server_ids)},
        identity=identity,
    ):
        await permissions.require_action(db, identity, EntityType.SERVER, Action.VIEW)

    results: list[BulkInstalledPackagesServerResult] = []
    dispatched = 0
    for server_id in server_ids:
        # Visibility + dept isolation. Cross-dept / нет row → статус not_found,
        # не 404 на весь батч (UI покажет, какой сервер недоступен).
        try:
            server = await server_svc.get_server(db, identity, server_id)
        except (NotFoundError, AuthorizationError):
            audit_service.emit(
                audit_action, target_id=server_id, target_type="server",
                status="failure", allowed=True,
                details={"reason": "not_found_or_cross_dept", "bulk": True},
            )
            results.append(BulkInstalledPackagesServerResult(
                server_id=server_id, status="not_found",
            ))
            continue

        # Списанный сервер — SSH не пройдёт; в результат статусом, не 409.
        if server.status == ServerStatus.DECOMMISSIONED:
            audit_service.emit(
                audit_action, target_id=server_id, target_type="server",
                status="failure", allowed=True,
                details={"reason": "decommissioned", "bulk": True},
            )
            results.append(BulkInstalledPackagesServerResult(
                server_id=server.id, hostname=server.hostname,
                os_version_id=server.os_version_id, status="decommissioned",
            ))
            continue

        # Prepare-gate: неподготовленный сервер не отдаёт пакеты по SSH. В
        # одиночном это 409 PREPARE_REQUIRED, здесь — статус, чтобы не уронить
        # батч. `require_server_prepared` эмитит свой failure-audit и raise'ит
        # ConflictError(PREPARE_REQUIRED) — ловим его per-server.
        try:
            require_server_prepared(server, audit_action=audit_action)
        except ConflictError:
            results.append(BulkInstalledPackagesServerResult(
                server_id=server.id, hostname=server.hostname,
                os_version_id=server.os_version_id, status="prepare_required",
            ))
            continue

        # Dispatch. На managed-сервере worker заходит по ключу — аккаунта нет.
        # Недоступность worker'а на отдельном сервере (broker down при
        # dispatch'е) уходит в статус auth_failed, а не валит остальные.
        try:
            task_id, _ = await dispatch_server_ssh_task(
                db=db, identity=identity, request=request,
                server=server,
                task_kind="installed_packages.list",
                audit_action=audit_action,
                resolved_account_id=None,
                extra_payload={
                    "patterns": patterns,
                    "max_rows": _MAX_INSTALLED_PACKAGES_ROWS,
                },
                success_extra_details={"patterns": patterns, "bulk": True},
            )
        except (ServiceUnavailableError, ConflictError):
            # dispatch_server_ssh_task уже заэмитил failure-audit. auth_failed —
            # общий «сервер не отдал данные» бакет для UI (broker недоступен /
            # idempotent-конфликт по этому серверу).
            results.append(BulkInstalledPackagesServerResult(
                server_id=server.id, hostname=server.hostname,
                os_version_id=server.os_version_id, status="auth_failed",
            ))
            continue

        dispatched += 1
        results.append(BulkInstalledPackagesServerResult(
            server_id=server.id, hostname=server.hostname,
            os_version_id=server.os_version_id, status="ok", task_id=task_id,
        ))

    return BulkInstalledPackagesResponse(
        pattern=patterns[0],
        patterns=patterns,
        requested=len(server_ids),
        dispatched=dispatched,
        results=results,
    )


@packages_action_router.post(
    "/packages/bulk-action",
    response_model=BulkPackagesActionResponse,
    status_code=202,
    summary="Массовая мутация пакетов (install/remove/update) на нескольких серверах",
    description=(
        "Для каждого сервера из `server_ids` диспатчит изменяющую задачу "
        "`installed_packages.{install|remove|update}`: worker под управляющим "
        "пользователем по SSH с sudo выполняет `apt-get`/`dnf`/`apk`. "
        "`action` — install / remove / update; `packages` обязателен для "
        "install/remove, для update опционален (пусто = обновить всё). Имена "
        "пакетов валидируются строгим allow-list'ом `[A-Za-z0-9._+-]` (без "
        "glob).\n\n"
        "Право `(server, manage_packages)` — деструктив поверх view. Гейты "
        "per-server: dept-visibility (cross-dept → `not_found`), prepare-gate "
        "(неподготовленный → `prepare_required`), reserve-gate (занятый чужим "
        "оператором → `reserved`), decommissioned → `decommissioned`. Один "
        "битый сервер не валит батч.\n\n"
        "Модель async: dispatch только ставит задачи; реальный результат "
        "(что применено, exit-код) UI добирает поллингом `GET /tasks/{task_id}`. "
        "Cap серверов — `INSTALLED_PACKAGES_BULK_MAX_SERVERS`; превышение → 413."
    ),
    responses={
        202: {"description": "Батч обработан, per-server статусы в `results`."},
        403: {"description": "Нет роли с `manage_packages` на server."},
        413: {"description": "BULK_PACKAGES_TOO_LARGE — server_ids длиннее cap'а."},
        422: {"description": "Невалидное имя пакета / пустой packages для install/remove / неизвестный action."},
    },
)
async def bulk_packages_action(
    body: BulkPackagesActionRequest,
    identity: CurrentUserIdentity,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> BulkPackagesActionResponse:
    """Массовая мутация пакетов: одна задача на сервер, сводный per-server ответ.

    Доступ: `(server, manage_packages)` — проверяется один раз для батча (право
    зависит только от ролей caller'а). Дальше per-server visibility /
    decommissioned / prepare / reserve. Серверы, не прошедшие гейт, попадают в
    результат со статусом, а не валят весь батч.

    Возможные ошибки: 403 PERMISSION_DENIED, 413 BULK_PACKAGES_TOO_LARGE,
    422 (валидация тела). Per-server проблемы — статусы в `results`.

    Связано: worker-таски `installed_packages.{install,remove,update}`,
    bulk-запрос `bulk_installed_packages` (read-only).
    """
    task_kind, audit_action = _PACKAGES_ACTION_MAP[body.action]

    # Дедуп с сохранением порядка — повторный server_id в одну задачу/строку.
    seen: set[str] = set()
    server_ids: list[str] = []
    for sid in body.server_ids:
        if sid not in seen:
            seen.add(sid)
            server_ids.append(sid)

    # Cap на размер батча — тот же, что у bulk-запроса (каждый сервер = отдельный
    # SSH-dispatch, не заливаем worker-пул одним запросом).
    cap = get_settings().installed_packages_bulk_max_servers
    if len(server_ids) > cap:
        audit_service.emit(
            audit_action, target_id=None, target_type="server",
            status="failure", allowed=True,
            details={
                "reason": "bulk_packages_too_large",
                "requested_count": len(server_ids),
                "cap": cap,
                "action": body.action,
            },
        )
        raise AppException(
            error_code="BULK_PACKAGES_TOO_LARGE",
            message=(
                f"Bulk packages action of {len(server_ids)} servers exceeds "
                f"cap {cap}; split into smaller batches"
            ),
            http_status=413,
        )

    # Role-check один раз для всего батча — `manage_packages` зависит только от
    # ролей caller'а. Нет права → 403 на весь запрос.
    with emit_denied_on_authz_error(
        audit_action,
        target_id=None,
        target_type="server",
        extra_details={"bulk": True, "requested_count": len(server_ids), "action": body.action},
        identity=identity,
    ):
        await permissions.require_action(
            db, identity, EntityType.SERVER, Action.MANAGE_PACKAGES,
        )

    results: list[BulkPackagesActionServerResult] = []
    dispatched = 0
    for server_id in server_ids:
        # Visibility + dept isolation. Cross-dept / нет row → not_found.
        try:
            server = await server_svc.get_server(db, identity, server_id)
        except (NotFoundError, AuthorizationError):
            audit_service.emit(
                audit_action, target_id=server_id, target_type="server",
                status="failure", allowed=True,
                details={"reason": "not_found_or_cross_dept", "bulk": True},
            )
            results.append(BulkPackagesActionServerResult(
                server_id=server_id, status="not_found",
            ))
            continue

        # Списанный сервер — worker-операции не принимает.
        if server.status == ServerStatus.DECOMMISSIONED:
            audit_service.emit(
                audit_action, target_id=server_id, target_type="server",
                status="failure", allowed=True,
                details={"reason": "decommissioned", "bulk": True},
            )
            results.append(BulkPackagesActionServerResult(
                server_id=server.id, hostname=server.hostname, status="decommissioned",
            ))
            continue

        # Prepare-gate: мутация идёт по SSH под управляющим ключом. Неподготовленный
        # сервер → статус, не 409 на весь батч.
        try:
            require_server_prepared(server, audit_action=audit_action)
        except ConflictError:
            results.append(BulkPackagesActionServerResult(
                server_id=server.id, hostname=server.hostname, status="prepare_required",
            ))
            continue

        # Reserve-gate: мутация пакетов — деструктив. Сервер, занятый чужим
        # оператором (busy) → статус reserved, не валим батч. ensure_not_reserved_for
        # эмитит свой WARNING-аудит `server.reservation_denied` и raise'ит 409.
        try:
            reservation.ensure_not_reserved_for(identity, server, action=audit_action)
        except ConflictError:
            results.append(BulkPackagesActionServerResult(
                server_id=server.id, hostname=server.hostname, status="reserved",
            ))
            continue

        # Dispatch. На managed-сервере worker заходит по ключу — аккаунта нет.
        # `packages`/`action` едут доп-payload'ом; недоступность worker'а на
        # отдельном сервере уходит в статус queued-miss (не валит остальные).
        try:
            task_id, _ = await dispatch_server_ssh_task(
                db=db, identity=identity, request=request,
                server=server,
                task_kind=task_kind,
                audit_action=audit_action,
                resolved_account_id=None,
                extra_payload={
                    "packages": list(body.packages),
                    "operation": body.action,
                },
                success_extra_details={
                    "operation": body.action,
                    "package_count": len(body.packages),
                    "bulk": True,
                },
            )
        except (ServiceUnavailableError, ConflictError):
            # dispatch_server_ssh_task уже заэмитил failure-audit. Не-ok сервер
            # помечаем not_found-бакетом «не отдал данные» — для UI это «retry».
            results.append(BulkPackagesActionServerResult(
                server_id=server.id, hostname=server.hostname, status="not_found",
            ))
            continue

        dispatched += 1
        results.append(BulkPackagesActionServerResult(
            server_id=server.id, hostname=server.hostname, status="ok", task_id=task_id,
        ))

    return BulkPackagesActionResponse(
        action=body.action,
        packages=list(body.packages),
        requested=len(server_ids),
        dispatched=dispatched,
        results=results,
    )
