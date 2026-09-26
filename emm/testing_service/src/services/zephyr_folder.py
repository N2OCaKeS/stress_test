"""Папка Zephyr и имя прогона СТП: шаблоны, поиск/создание, ручная правка.

Путь папки и имя test-run'а больше не собираются в коде (`folder =
f"/stress_test/{release}/{rc_number}"` в `services/stp.py`): это шаблоны
`department_integration_settings.zephyr_folder_path_template` /
`zephyr_run_name_template`, резолвятся тем же механизмом, что и команда теста
(`services/variable_resolver.py`, `ResolveContext`).

Контекст резолва: `launch_context = {"RC": os_version_id, ...}` — то же
соглашение, что у очереди (`RC` — id версии ОС, человеческое имя дают
переменные-источники `os_version`: `RC_NAME`, `RC_RELEASE`).

* Путь папки — одна папка на пару «отдел × версия ОС», поэтому в контексте
  нет ни стенда, ни теста, ни режима/ядра. Шаблон, сославшийся на
  `{MODE}`/`{STAND_TOKEN}`, упадёт понятной ошибкой резолва.
* Имя прогона — на каждый стенд/режим/ядро: `RC`, `MODE`, `KERNEL` и стенд.

**Поиск id папки.** ATM REST 1.0 не умеет искать папку по пути, поэтому id
берётся из test-run'ов, уже лежащих в этой папке
(`zephyr_client.find_test_run_folder_id` поверх `search_test_runs`) —
«предположение по умолчанию» задачи. Порядок при генерации СТП:

1. Запись `is_manual=true` — не трогаем, прогоны заводятся в её папку.
2. Автоматическая запись с тем же путём и известным id — переиспользуем, в
   Zephyr не ходим.
3. Иначе ищем по прогонам в папке; не нашли — создаём папку
   (`POST /rest/atm/1.0/folder`, легаси `liballta.py:2119-2166`: сначала
   родитель, потом сама папка) и берём id из ответа.
4. После заведения прогонов, если id всё ещё неизвестен (папка уже была, но
   пустая: создание отказало «уже есть», а искать было не по чему), — ищем
   ещё раз: теперь в папке лежат наши прогоны.

Не получилось — запись с путём и пустым id плюс ошибка
`ZEPHYR_FOLDER_NOT_FOUND` с подсказкой «создайте папку или задайте id
вручную». Прогоны СТП при этом создаются: id папки нужен только скриптам
(`FOLDER_TREE_ID`), не самому созданию прогона.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import Action, EntityType
from src.core.exceptions import AppException, AuthorizationError, DomainValidationError
from src.dependencies.auth import Identity
from src.models import DepartmentIntegrationSettings, TestStand, ZephyrFolder
from src.models.department_integration_settings import (
    DEFAULT_ZEPHYR_FOLDER_PATH_TEMPLATE,
    DEFAULT_ZEPHYR_RUN_NAME_TEMPLATE,
)
from src.repositories import department_integration_settings as dis_repo
from src.repositories import global_variable as global_variable_repo
from src.repositories import zephyr_folder as repo
from src.services import audit_service, permissions, variable_resolver, zephyr_client
from src.utils.ids import zephyr_folder_id as new_id

logger = logging.getLogger(__name__)

FOLDER_PATH_TEMPLATE_FIELD = "zephyr_folder_path_template"
RUN_NAME_TEMPLATE_FIELD = "zephyr_run_name_template"
TEMPLATE_DEFAULTS: dict[str, str] = {
    FOLDER_PATH_TEMPLATE_FIELD: DEFAULT_ZEPHYR_FOLDER_PATH_TEMPLATE,
    RUN_NAME_TEMPLATE_FIELD: DEFAULT_ZEPHYR_RUN_NAME_TEMPLATE,
}


@dataclass(frozen=True)
class FolderError:
    """Почему id папки не получен. Не исключение: генерация СТП идёт дальше."""

    error_code: str
    message: str

    def as_dict(self) -> dict:
        return {"error_code": self.error_code, "message": self.message}


def template_of(settings: DepartmentIntegrationSettings | None, field: str) -> str:
    """Шаблон отдела либо легаси-дефолт колонки (строки настроек ещё нет)."""
    value = getattr(settings, field, None) if settings is not None else None
    return value if value else TEMPLATE_DEFAULTS[field]


async def _render(ctx: variable_resolver.ResolveContext, template: str, field: str) -> str:
    try:
        result = await variable_resolver.render(ctx, template)
    except AppException as exc:
        exc.details = {**(exc.details or {}), "template_field": field}
        raise
    if result.sensitive:
        # Путь и имя уходят в Zephyr и в БД открытым текстом.
        raise DomainValidationError(
            error_code="ZEPHYR_TEMPLATE_SENSITIVE",
            message=f"{field} must not reference sensitive variables",
            details={"template_field": field},
        )
    value = result.value.strip()
    if not value:
        raise DomainValidationError(
            error_code="ZEPHYR_TEMPLATE_EMPTY",
            message=f"{field} resolved to an empty string",
            details={"template_field": field, "template": template},
        )
    return value


def normalize_folder_path(path: str) -> str:
    """`stress_test/1.8/` → `/stress_test/1.8`: пути папок ATM — абсолютные, без хвостового `/`."""
    parts = [p for p in path.strip().split("/") if p]
    return "/" + "/".join(parts)


async def render_folder_path(
    db: AsyncSession, *, department_id: str, os_version_id: str,
    settings: DepartmentIntegrationSettings | None = None,
) -> str:
    """Путь папки Zephyr для пары (отдел, версия ОС) по шаблону отдела."""
    if settings is None:
        settings = await dis_repo.get_by_department(db, department_id)
    ctx = variable_resolver.ResolveContext(
        db=db, department_id=department_id, test=None, stand=None,
        launch_context={"RC": os_version_id},
    )
    path = normalize_folder_path(
        await _render(ctx, template_of(settings, FOLDER_PATH_TEMPLATE_FIELD), FOLDER_PATH_TEMPLATE_FIELD)
    )
    if path == "/":
        raise DomainValidationError(
            error_code="ZEPHYR_TEMPLATE_EMPTY",
            message=f"{FOLDER_PATH_TEMPLATE_FIELD} resolved to the root folder",
            details={"template_field": FOLDER_PATH_TEMPLATE_FIELD},
        )
    return path


async def effective_folder_path(
    db: AsyncSession, *, department_id: str, os_version_id: str,
    settings: DepartmentIntegrationSettings | None = None,
) -> tuple[str, ZephyrFolder | None]:
    """Путь, в который генерация СТП заводит прогоны (и где их ищет pull из life).

    Ручная запись задаёт и путь: если человек указал id папки, прогоны должны
    лечь именно в неё, иначе скрипты по этому `folderTreeId` их не найдут.
    Иначе — путь по шаблону отдела.
    """
    record = await repo.get_by_department_and_os_version(db, department_id, os_version_id)
    if record is not None and record.is_manual:
        return record.folder_path, record
    path = await render_folder_path(db, department_id=department_id, os_version_id=os_version_id, settings=settings)
    return path, record


async def render_run_name(
    db: AsyncSession, *, department_id: str, os_version_id: str, mode: str, kernel: str,
    stand: TestStand, settings: DepartmentIntegrationSettings | None,
) -> str:
    """Имя Zephyr test-run'а стенда по шаблону отдела."""
    ctx = variable_resolver.ResolveContext(
        db=db, department_id=stand.department_id or department_id, test=None, stand=stand,
        launch_context={"RC": os_version_id, "MODE": mode, "KERNEL": kernel},
    )
    return await _render(ctx, template_of(settings, RUN_NAME_TEMPLATE_FIELD), RUN_NAME_TEMPLATE_FIELD)


# ── поиск / создание в Zephyr ────────────────────────────────────────────────

def _not_found(path: str) -> FolderError:
    return FolderError(
        error_code="ZEPHYR_FOLDER_NOT_FOUND",
        message=(
            f"Zephyr folder {path} was not found and could not be created: "
            "create it in Zephyr (and re-run «find again») or set its id manually"
        ),
    )


async def _create_with_parents(*, base_url: str, bearer_token: str, path: str) -> str | None:
    """Завести папку и недостающих родителей, вернуть id самой папки.

    Легаси `add_testrun_folder` (`liballta.py:2143-2166`) делал так же: нет
    родителя `/stress_test/<release>` — сначала он, потом `<release>/<rc>`.
    Отказ на родителе (обычно «уже есть») не мешает создать дочернюю.
    """
    segments = [p for p in path.split("/") if p]
    for depth in range(1, len(segments)):
        await zephyr_client.create_test_run_folder(
            base_url=base_url, bearer_token=bearer_token, folder="/" + "/".join(segments[:depth]),
        )
    return await zephyr_client.create_test_run_folder(base_url=base_url, bearer_token=bearer_token, folder=path)


async def _save(
    db: AsyncSession, record: ZephyrFolder | None, *, department_id: str, os_version_id: str,
    changes: dict,
) -> ZephyrFolder:
    if record is None:
        record = await repo.create(db, {
            "id": new_id(), "department_id": department_id, "os_version_id": os_version_id, **changes,
        })
    else:
        record = await repo.update(db, record, changes)
    await db.commit()
    await db.refresh(record)
    return record


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def sync_folder(
    db: AsyncSession, *, department_id: str, os_version_id: str, folder_path: str,
    jira_ctx: tuple[str, str] | None, allow_create: bool, force: bool = False,
    actor_id: str | None = None,
) -> tuple[ZephyrFolder | None, FolderError | None]:
    """Найти (и при `allow_create` — создать) папку в Zephyr и сохранить её id.

    `force=True` — «найти заново»: игнорирует и ручную запись, и уже
    сохранённый id (ручная запись после этого становится автоматической).
    """
    record = await repo.get_by_department_and_os_version(db, department_id, os_version_id)
    if record is not None and not force:
        if record.is_manual:
            return record, None
        if record.folder_tree_id and record.folder_path == folder_path:
            return record, None

    if jira_ctx is None:
        return record, FolderError(
            error_code="JIRA_INTEGRATION_NOT_AVAILABLE",
            message="department_integration_settings not configured or credential reveal failed",
        )
    base_url, bearer_token = jira_ctx
    try:
        folder_id = await zephyr_client.find_test_run_folder_id(
            base_url=base_url, bearer_token=bearer_token, folder=folder_path,
        )
        if folder_id is None and allow_create:
            folder_id = await _create_with_parents(base_url=base_url, bearer_token=bearer_token, path=folder_path)
    except AppException as exc:
        logger.warning(
            "zephyr_folder: lookup failed dept=%s os_version=%s path=%s: %s",
            department_id, os_version_id, folder_path, exc.message,
        )
        return record, FolderError(error_code=exc.error_code, message=exc.message)

    changes: dict = {"folder_path": folder_path, "is_manual": False, "updated_by": actor_id}
    if folder_id is not None:
        changes.update(folder_tree_id=folder_id, resolved_at=_now())
    elif record is None or record.folder_path != folder_path or force:
        # id прежнего пути к новому не относится.
        changes.update(folder_tree_id=None, resolved_at=None)
    record = await _save(db, record, department_id=department_id, os_version_id=os_version_id, changes=changes)
    return record, (None if record.folder_tree_id else _not_found(folder_path))


# ── HTTP use cases ───────────────────────────────────────────────────────────

def as_dict(record: ZephyrFolder | None, *, department_id: str, os_version_id: str,
            folder_path: str | None = None, error: FolderError | None = None) -> dict:
    if record is None:
        return {
            "id": None, "department_id": department_id, "os_version_id": os_version_id,
            "folder_path": folder_path, "folder_tree_id": None, "is_manual": False,
            "resolved_at": None, "updated_by": None, "updated_at": None,
            "error": error.as_dict() if error else None,
        }
    return {
        "id": record.id, "department_id": record.department_id, "os_version_id": record.os_version_id,
        "folder_path": record.folder_path, "folder_tree_id": record.folder_tree_id,
        "is_manual": record.is_manual, "resolved_at": record.resolved_at,
        "updated_by": record.updated_by, "updated_at": record.updated_at,
        "error": error.as_dict() if error else None,
    }


async def get_effective(db: AsyncSession, identity: Identity, department_id: str, os_version_id: str) -> dict:
    """Запись пары (отдел, версия ОС). Нет записи — не 404: путь по шаблону (если резолвится), id пуст."""
    permissions.require_own_department(identity, department_id)
    record = await repo.get_by_department_and_os_version(db, department_id, os_version_id)
    if record is not None:
        return as_dict(record, department_id=department_id, os_version_id=os_version_id)
    try:
        path = await render_folder_path(db, department_id=department_id, os_version_id=os_version_id)
        error = None
    except AppException as exc:
        path, error = None, FolderError(error_code=exc.error_code, message=exc.message)
    return as_dict(None, department_id=department_id, os_version_id=os_version_id, folder_path=path, error=error)


async def _require_write(db: AsyncSession, identity: Identity, department_id: str, action: str) -> None:
    """Тот же гейт, что у `/stp/generate`: папка — часть генерации СТП отдела."""
    try:
        await permissions.require_department_action(
            db, identity, department_id, EntityType.STP_TEST_RUN, Action.CREATE,
        )
    except AuthorizationError:
        audit_service.emit(
            action, target_type="zephyr_folder", status="denied", allowed=False,
            details={"reason": "permission_denied", "department_id": department_id},
        )
        raise


async def set_manual(
    db: AsyncSession, identity: Identity, *, department_id: str, os_version_id: str,
    folder_tree_id: str, folder_path: str | None,
) -> dict:
    """Задать id папки руками. Такая запись переживает повторную генерацию СТП."""
    await _require_write(db, identity, department_id, "zephyr_folder.update")
    folder_tree_id = folder_tree_id.strip()
    if not folder_tree_id:
        raise DomainValidationError(
            error_code="ZEPHYR_FOLDER_ID_REQUIRED", message="folder_tree_id must not be empty",
        )
    record = await repo.get_by_department_and_os_version(db, department_id, os_version_id)
    if folder_path and folder_path.strip():
        path = normalize_folder_path(folder_path)
    elif record is not None:
        path = record.folder_path
    else:
        path = await render_folder_path(db, department_id=department_id, os_version_id=os_version_id)
    record = await _save(db, record, department_id=department_id, os_version_id=os_version_id, changes={
        "folder_path": path, "folder_tree_id": folder_tree_id, "is_manual": True,
        "resolved_at": _now(), "updated_by": identity.user_id,
    })
    audit_service.emit(
        "zephyr_folder.update", target_id=record.id, target_type="zephyr_folder",
        status="success", allowed=True,
        details={
            "department_id": department_id, "os_version_id": os_version_id,
            "folder_path": path, "folder_tree_id": folder_tree_id,
        },
    )
    return as_dict(record, department_id=department_id, os_version_id=os_version_id)


async def refresh(db: AsyncSession, identity: Identity, *, department_id: str, os_version_id: str) -> dict:
    """«Найти заново»: путь по текущему шаблону, поиск/создание в Zephyr, ручная метка снимается."""
    await _require_write(db, identity, department_id, "zephyr_folder.refresh")
    # Поздний импорт: `services/stp.py` сам импортирует этот модуль.
    from src.services.stp import _resolve_jira_bearer

    path = await render_folder_path(db, department_id=department_id, os_version_id=os_version_id)
    jira_ctx = await _resolve_jira_bearer(db, department_id)
    record, error = await sync_folder(
        db, department_id=department_id, os_version_id=os_version_id, folder_path=path,
        jira_ctx=jira_ctx, allow_create=True, force=True, actor_id=identity.user_id,
    )
    audit_service.emit(
        "zephyr_folder.refresh", target_id=record.id if record else None, target_type="zephyr_folder",
        status="success" if error is None else "failure", allowed=True,
        details={
            "department_id": department_id, "os_version_id": os_version_id, "folder_path": path,
            "folder_tree_id": record.folder_tree_id if record else None,
            "reason": error.error_code if error else None,
        },
    )
    return as_dict(record, department_id=department_id, os_version_id=os_version_id, folder_path=path, error=error)


async def validate_template_change(db: AsyncSession, field: str, value: str) -> None:
    """Проверка шаблона при сохранении настроек: ссылки на существующие переменные, путь — абсолютный."""
    codes = variable_resolver.template_codes(value)
    if codes:
        known = {v.code for v in await global_variable_repo.list_every(db)}
        unknown = sorted({c for c in codes if c not in known})
        if unknown:
            raise DomainValidationError(
                error_code="VARIABLE_TEMPLATE_UNKNOWN",
                message=f"{field} references unknown variables: {', '.join(unknown)}",
                details={"field": field, "unknown": unknown},
            )
    if field == FOLDER_PATH_TEMPLATE_FIELD and not value.strip().startswith("/"):
        raise DomainValidationError(
            error_code="ZEPHYR_TEMPLATE_INVALID",
            message=f"{field} must be an absolute Zephyr folder path starting with '/'",
            details={"field": field},
        )
