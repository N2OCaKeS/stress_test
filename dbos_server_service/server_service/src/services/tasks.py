"""Read use-cases для worker-task'ов (list / detail).

Task-row'ы живут в `dev_server_worker.tasks` (отдельная БД); server_service
читает их через cross-DB engine из `worker_client`. Здесь — permission/
visibility-обвязка поверх этих read'ов:

* permission — `(task, view)` по матрице entity_permissions (reader/operator/
  admin получают грант сидинг-миграцией). Platform-admin'ы
  (`account_admin`/`loging_admin`) отрезаны `platform_admin_guard`
  middleware'ом ещё до endpoint'а — 403 PLATFORM_ADMIN_BUSINESS_DATA_DENIED.
* dept-scope — caller видит только задачи своих серверов
  (`server.department_id == caller.department_id`). Cross-dept задача
  отсутствует в листинге и маскируется под 404 в detail.
* per-user scope — внутри отдела привилегированный caller (service-роль
  `admin`/`operator` или `department_admin`) видит ВСЕ задачи отдела; обычный
  reader (или носитель кастомной роли без admin/operator) видит ТОЛЬКО свои
  задачи (`created_by == caller.user_id`). Чужая задача того же отдела для
  reader'а отсутствует в листинге и маскируется под 404 в detail.
* инфра-задачи без `target_server_id` (scheduler/heartbeat/sweep/cleanup)
  видны только service-роли `admin`/`operator`; `department_admin`
  (platform-роль) и `reader` их не видят — у них в карточке нет владельца,
  а platform-admin до сюда не доходит.

`department_id` в самой таблице tasks нет — резолвим из `server.department_id`
по `target_server_id` (один батч-SELECT, без N+1).
"""

from src.core.constants import Action, EntityType, PlatformRole, ServiceRole
from src.core.exceptions import NotFoundError
from src.repositories import server as server_repo
from src.repositories import server_account as account_repo
from src.schemas.identity import IdentityContext
from src.schemas.task import TaskRead
from src.services import permissions, worker_client

# Усечение JSONB-`result` в листинге: полную «полезную нагрузку» (например,
# список пакетов в `installed_packages.list`) в карточку строки таблицы не
# тащим — она нужна только в detail. Здесь оставляем компактное summary.
_RESULT_SUMMARY_MAX_KEYS = 20

# Sentinel: пересечение role-scope и явного created_by-фильтра пусто — caller
# просит инициатора, которого role-scope ему видеть не разрешает. Отличается
# от `None` (фильтра нет) — отдельный объект, чтобы не путать с user_id.
_EMPTY_SCOPE = object()


def _intersect_created_by(scope: str | None, requested: str | None):
    """Пересечь role-scope-ограничение по инициатору с явным фильтром UI.

    `scope` — что разрешает role-scope: конкретный user_id (reader видит
    только себя) или `None` (привилегированный caller — без ограничения).
    `requested` — явный `?created_by=` из запроса (или `None`).

    Возврат:
    * `None` — ограничения по инициатору нет (оба None);
    * конкретный user_id — фильтруем по нему;
    * `_EMPTY_SCOPE` — scope и requested противоречат друг другу (reader
      спросил чужой created_by) → выборка должна быть пустой.
    """
    if requested is None:
        return scope
    if scope is None:
        return requested
    if scope == requested:
        return scope
    return _EMPTY_SCOPE


def _summarize_result(result):
    """Усечь `task.result` для листинга.

    Полный `result` отдаётся только в detail. В листинге крупные структуры
    (списки пакетов, getent-дампы) раздували бы ответ — оставляем верхний
    уровень dict'а с усечёнными значениями-коллекциями, чтобы UI видел форму
    и размер, но не качал мегабайты.
    """
    if result is None:
        return None
    if not isinstance(result, dict):
        # Скалярный/списочный result — отдаём как есть, он короткий.
        return result
    summary: dict = {}
    for idx, (key, value) in enumerate(result.items()):
        if idx >= _RESULT_SUMMARY_MAX_KEYS:
            summary["_truncated"] = True
            break
        if isinstance(value, list):
            summary[key] = {"_count": len(value)}
        elif isinstance(value, dict):
            summary[key] = {"_keys": len(value)}
        else:
            summary[key] = value
    return summary


def _sees_infra_tasks(identity: IdentityContext) -> bool:
    """True, если caller вправе видеть инфра-задачи без сервера.

    Контракт: service-роль `admin`/`operator` — да; `reader` и
    `department_admin` (platform-роль) — нет. Платформенные admin'ы сюда не
    доходят (middleware), поэтому проверяем только service-роли и явно
    исключаем department_admin.
    """
    if identity.platform_role == PlatformRole.DEPARTMENT_ADMIN:
        return False
    roles = set(identity.roles_for_service("server_service"))
    return bool(roles & {ServiceRole.ADMIN, ServiceRole.OPERATOR})


def _sees_all_dept_tasks(identity: IdentityContext) -> bool:
    """True, если caller видит все задачи отдела, а не только свои.

    Привилегированный просмотр — у `department_admin` (platform-роль своего
    отдела) и у носителя service-роли `admin`/`operator`. Обычный `reader`
    (и любая кастомная роль с `view`, но без admin/operator) ограничен своими
    задачами — отбор по `created_by` делает list/get. Platform-admin'ы до сюда
    не доходят (middleware), отдельно их не учитываем.
    """
    if identity.platform_role == PlatformRole.DEPARTMENT_ADMIN:
        return True
    roles = set(identity.roles_for_service("server_service"))
    return bool(roles & {ServiceRole.ADMIN, ServiceRole.OPERATOR})


def _to_task_read(
    row: dict,
    dept_by_server: dict[str, str],
    hostname_by_server: dict[str, str],
    login_by_account: dict[str, str],
    *,
    summarize: bool,
) -> TaskRead:
    """Собрать `TaskRead` из БД-row (имена колонок) + резолв department_id/имён.

    `hostname_by_server` / `login_by_account` — батч-мапы id→имя, заполненные
    caller'ом. Отсутствие id в мапе (сущность удалена или id null) даёт None —
    карточка не падает.
    """
    server_id = row["target_server_id"]
    account_id = row["target_resource_id"]
    department_id = dept_by_server.get(server_id) if server_id is not None else None
    server_hostname = hostname_by_server.get(server_id) if server_id is not None else None
    account_login = login_by_account.get(account_id) if account_id is not None else None
    result = row["result"]
    return TaskRead(
        id=row["id"],
        kind=row["task_kind"],
        status=row["status"],
        server_id=server_id,
        account_id=account_id,
        server_hostname=server_hostname,
        account_login=account_login,
        department_id=department_id,
        created_by=row["created_by"],
        created_at=row["enqueued_at"],
        started_at=row["started_at"],
        finished_at=row["completed_at"],
        retry_count=row["attempt"],
        priority=row["priority"],
        last_error=row["last_error"],
        result=_summarize_result(result) if summarize else result,
    )


async def list_tasks(
    db,
    identity: IdentityContext,
    *,
    status: str | None,
    kind: str | None,
    server_id: str | None,
    created_by: str | None,
    limit: int,
    offset: int,
) -> tuple[list[TaskRead], int]:
    """Страница task'ов, видимых caller'у, + total под тем же фильтром.

    Permission `(task, view)`. Dept-scope: собираем server_id'ы отдела caller'а
    и фильтруем задачи по ним (cross-DB `IN`). `server_id`-фильтр сужает до
    одного сервера — но только если он входит в видимый набор (чужой/несущест-
    вующий → пустой результат, без утечки факта существования). Поверх отдела
    непривилегированный reader видит только свои задачи (`created_by`).

    Аргумент `created_by` — опциональный фильтр UI по инициатору. Он
    НАКЛАДЫВАЕТСЯ поверх role-scope, а не расширяет его: для непривилегированного
    reader'а role-scope уже пинит выборку на его собственный user_id, и
    запрос чужого `created_by` пересечётся в пусто (видимость не растёт).
    Привилегированный caller (admin/operator/dept_admin) с `created_by=<me>`
    получает строго свои задачи, без аргумента — все задачи отдела как прежде.
    """
    await permissions.require_action(db, identity, EntityType.TASK, Action.VIEW)
    if identity.department_id is None:
        return [], 0

    # Reader без admin/operator-роли (и не dept-admin) видит только то, что
    # поставил сам. Привилегированный caller — все задачи отдела.
    own_only = not _sees_all_dept_tasks(identity)
    scope_created_by = identity.user_id if own_only else None
    # Явный фильтр по инициатору пересекается с role-scope (AND), не заменяет
    # его: reader, спрашивающий чужой created_by, попадает на конфликт двух
    # разных значений и получает пусто — фильтр не светит чужие задачи.
    effective_created_by = _intersect_created_by(scope_created_by, created_by)
    if effective_created_by is _EMPTY_SCOPE:
        return [], 0

    allowed_ids = await server_repo.list_ids_in_departments(db, [identity.department_id])
    if server_id is not None:
        # Точечный фильтр по серверу: пересекаем с видимым набором. Не входит —
        # отдаём пусто, как будто задач нет (enumeration-guard, симметрично 404
        # на cross-dept сервере).
        scoped_ids = [server_id] if server_id in set(allowed_ids) else []
        include_infra = False
    else:
        scoped_ids = allowed_ids
        include_infra = _sees_infra_tasks(identity)

    rows, total = await worker_client.list_tasks(
        status=status,
        task_kind=kind,
        server_ids=scoped_ids,
        include_infra=include_infra,
        created_by=effective_created_by,
        limit=limit,
        offset=offset,
    )
    # department_id и hostname резолвим одним батчем по уникальным server_id'ам
    # страницы; login учёток — одним батчем по уникальным account_id'ам. Без N+1.
    page_server_ids = list({r["target_server_id"] for r in rows if r["target_server_id"]})
    page_account_ids = list({r["target_resource_id"] for r in rows if r["target_resource_id"]})
    dept_by_server = await server_repo.department_map_for_ids(db, page_server_ids)
    hostname_by_server = await server_repo.hostname_map_for_ids(db, page_server_ids)
    login_by_account = await account_repo.login_map_for_ids(db, page_account_ids)
    items = [
        _to_task_read(
            r, dept_by_server, hostname_by_server, login_by_account, summarize=True,
        )
        for r in rows
    ]
    return items, total


async def get_task(
    db,
    identity: IdentityContext,
    task_id: str,
) -> TaskRead:
    """Detail одной task'и с полным `result`/`last_error`.

    Permission `(task, view)`. Visibility: задача чужого отдела (или с
    `server_id`, которого caller не видит) маскируется под 404 TASK_NOT_FOUND.
    Инфра-задача без сервера видна только тем, кто проходит `_sees_infra_tasks`.
    Непривилегированный reader дополнительно видит только свои задачи: чужую
    задачу того же отдела маскируем под 404.
    """
    await permissions.require_action(db, identity, EntityType.TASK, Action.VIEW)

    row = await worker_client.get_task(task_id)
    if row is None:
        raise NotFoundError(error_code="TASK_NOT_FOUND", message="Task not found")

    own_only = not _sees_all_dept_tasks(identity)
    if own_only and row.get("created_by") != identity.user_id:
        # Reader видит только свои задачи — чужую того же отдела маскируем под
        # 404, симметрично dept-isolation, чтобы не светить факт её наличия.
        raise NotFoundError(error_code="TASK_NOT_FOUND", message="Task not found")

    server_id = row["target_server_id"]
    if server_id is None:
        # Инфра-задача без сервера: видна только admin/operator-роли.
        if not _sees_infra_tasks(identity):
            raise NotFoundError(error_code="TASK_NOT_FOUND", message="Task not found")
        dept_by_server: dict[str, str] = {}
        hostname_by_server: dict[str, str] = {}
    else:
        dept_by_server = await server_repo.department_map_for_ids(db, [server_id])
        task_dept = dept_by_server.get(server_id)
        # Сервер невидим (чужой отдел / удалён) → маскируем под 404, чтобы не
        # светить факт существования задачи на чужом сервере.
        if task_dept is None or task_dept != identity.department_id:
            raise NotFoundError(error_code="TASK_NOT_FOUND", message="Task not found")
        hostname_by_server = await server_repo.hostname_map_for_ids(db, [server_id])

    # Логин учётки резолвим точечно по target_resource_id (если есть). Caller
    # уже видит саму задачу с этим account_id — раскрытие логина той же
    # сущности допустимо, доп. кросс-департамент-запросов не делаем.
    account_id = row["target_resource_id"]
    login_by_account = (
        await account_repo.login_map_for_ids(db, [account_id])
        if account_id is not None
        else {}
    )

    return _to_task_read(
        row, dept_by_server, hostname_by_server, login_by_account, summarize=False,
    )
