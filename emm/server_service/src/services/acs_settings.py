"""Use cases для настроек доступа к ACS — платформенный singleton + per-department opt-in.

`AcsSettings` — одна строка (`SINGLETON_ID`) с URL и зашифрованным паролем
clonezilla-сервера. `AcsDepartmentAccess` — маленькая таблица флагов "отделу
разрешены снимки ACS" (ворота в дополнение к обычной action-матрице — сам
dispatch create/restore будет проверять и то, и другое в следующей волне).

Список отделов для `/settings/acs/departments` server_service берёт из
`repositories.server.list_distinct_department_ids` — своей таблицы
`departments` у сервиса нет (та живёт в auth_service), а фронтенд уже умеет
подтягивать полный каталог отделов оттуда напрямую (`GET /departments`) и
сшивать с этим списком на своей стороне. См. подробности в отчёте волны.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import Action, EntityType
from src.core.exceptions import AuthorizationError, BadRequestError, ServiceUnavailableError
from src.models.acs_department_access import AcsDepartmentAccess
from src.models.acs_settings import SINGLETON_ID, AcsSettings
from src.repositories import server as server_repo
from src.schemas.acs_settings import (
    AcsDepartmentAccessItem,
    AcsDepartmentAccessListResponse,
    AcsDepartmentAccessUpdate,
    AcsInternalSettingsResponse,
    AcsSettingsResponse,
    AcsSettingsUpdate,
)
from src.schemas.identity import IdentityContext
from src.services import audit_service, permissions, secrets_service
from src.utils.ids import acs_department_access_id


# ── AcsSettings (singleton) ─────────────────────────────────────────────────


def _to_response(row: AcsSettings) -> AcsSettingsResponse:
    return AcsSettingsResponse(
        enabled=row.enabled,
        acs_url=row.acs_url,
        password_is_set=bool(row.acs_password_encrypted),
    )


def _defaults() -> AcsSettingsResponse:
    return AcsSettingsResponse(enabled=False, acs_url=None, password_is_set=False)


async def _get_row(db: AsyncSession) -> AcsSettings | None:
    return await db.get(AcsSettings, SINGLETON_ID)


async def get_acs_settings(db: AsyncSession) -> AcsSettingsResponse:
    """Текущие настройки ACS. Нет строки → дефолты (выключено, ничего не задано)."""
    row = await _get_row(db)
    if row is None:
        return _defaults()
    return _to_response(row)


async def update_acs_settings(
    db: AsyncSession,
    payload: AcsSettingsUpdate,
) -> AcsSettingsResponse:
    """Upsert настроек ACS (частичное слияние) + шифрование пароля на входе.

    Неприсланные поля сохраняют текущее значение. Пустой `acs_url` трактуется
    как явная очистка (администратор стирает поле в форме). Пароль:
    `acs_password` задан → шифруется и заменяет текущий; `clear_password=True`
    (и `acs_password` не задан) → стирает сохранённый пароль; иначе —
    не трогаем. Включить `enabled=True` без сохранённых url+пароля нельзя —
    400 `ACS_ENABLE_REQUIRES_CONFIG`. Аудит `settings.acs_updated`.
    """
    row = await _get_row(db)
    if row is None:
        row = AcsSettings(id=SINGLETON_ID, enabled=False, acs_url=None, acs_password_encrypted=None)
        db.add(row)

    if payload.enabled is not None:
        row.enabled = payload.enabled
    if payload.acs_url is not None:
        stripped = payload.acs_url.strip()
        row.acs_url = stripped or None

    if payload.acs_password:
        row.acs_password_encrypted = secrets_service.encrypt(
            payload.acs_password, aad=secrets_service.aad_for_acs_password(SINGLETON_ID)
        )
    elif payload.clear_password:
        row.acs_password_encrypted = None

    if row.enabled and (not row.acs_url or not row.acs_password_encrypted):
        raise BadRequestError(
            error_code="ACS_ENABLE_REQUIRES_CONFIG",
            message="Cannot enable ACS snapshots without acs_url and a stored password",
            details={"acs_url_set": bool(row.acs_url), "password_set": bool(row.acs_password_encrypted)},
        )

    await db.commit()
    await db.refresh(row)

    audit_service.emit(
        "settings.acs_updated",
        target_id=SINGLETON_ID,
        target_type="acs_settings",
        status="success",
        allowed=True,
        details={
            "enabled": row.enabled,
            "acs_url_set": bool(row.acs_url),
            "password_set": bool(row.acs_password_encrypted),
        },
    )

    return _to_response(row)


async def get_acs_settings_for_worker(
    db: AsyncSession,
    identity: IdentityContext,
) -> AcsInternalSettingsResponse:
    """Read настроек ACS для server_worker (internal-эндпоинт).

    Право: `(server, *, prepare_callback)` — тот же глобальный callback-грант
    worker_bot'а, что у `/internal/settings/probes`. Если ACS выключен или
    url/пароль не заданы — 503 `ACS_DISABLED` (временно недоступно, не
    "ресурса не существует": админ может включить в любой момент).
    """
    try:
        await permissions.require_action(
            db, identity, EntityType.SERVER, Action.PREPARE_CALLBACK,
        )
    except AuthorizationError:
        audit_service.emit(
            "settings.acs_updated",
            target_id=SINGLETON_ID,
            target_type="acs_settings",
            status="denied", allowed=False,
            details={"reason": "permission_denied", "op": "worker_read"},
        )
        raise

    row = await _get_row(db)
    if row is None or not row.enabled or not row.acs_url or not row.acs_password_encrypted:
        raise ServiceUnavailableError(
            error_code="ACS_DISABLED",
            message="ACS snapshots are disabled or not configured",
        )

    acs_url, acs_password = await _resolve_credentials(db, row)
    return AcsInternalSettingsResponse(acs_url=acs_url, acs_password=acs_password)


async def _resolve_credentials(db: AsyncSession, row: AcsSettings) -> tuple[str, str]:
    """Расшифрованные (url, password) уже загруженной строки `AcsSettings`.

    Общий хвост для worker-read (`get_acs_settings_for_worker`, гейт
    `prepare_callback`) и прямого server_service-чтения (`get_acs_credentials`,
    список снимков сервера) — оба гоняют свой permission/availability-check
    выше по стеку, здесь только расшифровка + lazy-переширфровка протухшего
    конверта.
    """
    result = secrets_service.decrypt_with_meta(
        row.acs_password_encrypted, aad=secrets_service.aad_for_acs_password(SINGLETON_ID)
    )
    if result.needs_reencrypt:
        await secrets_service.lazy_reencrypt_owner_column(
            db,
            table="acs_settings",
            column="acs_password_encrypted",
            row_id=SINGLETON_ID,
            old_blob=row.acs_password_encrypted,
            plaintext=result.plaintext,
            aad=secrets_service.aad_for_acs_password(SINGLETON_ID),
        )
    return row.acs_url, result.plaintext


async def get_acs_credentials(db: AsyncSession) -> tuple[str, str]:
    """(url, password) ACS для синхронных вызовов из server_service, не через worker.

    Caller — `GET /servers/{id}/acs-snapshots` (живой список снимков читается
    прямо здесь, без dispatch в worker). Permission (`acs_snapshot_list`) и
    доступность (platform+department, `_ensure_acs_available` в
    `worker_dispatch.py`) уже проверены выше по стеку — здесь только чтение
    строки настроек + расшифровка. 503 `ACS_DISABLED`, если строки нет /
    выключено / не заполнено (тот же контракт, что у worker-read).
    """
    row = await _get_row(db)
    if row is None or not row.enabled or not row.acs_url or not row.acs_password_encrypted:
        raise ServiceUnavailableError(
            error_code="ACS_DISABLED",
            message="ACS snapshots are disabled or not configured",
        )
    return await _resolve_credentials(db, row)


# ── AcsDepartmentAccess (per-department opt-in) ─────────────────────────────


async def is_department_acs_enabled(db: AsyncSession, department_id: str) -> bool:
    """True iff отделу явно включён доступ к снимкам ACS.

    Узкая точечная проверка для dispatch create/restore — в отличие от
    `list_acs_department_access`, не тянет полный список кандидатов, только
    факт по одному department_id. Отсутствие строки — как и выключенный флаг —
    трактуется как «доступа нет» (дефолт closed).
    """
    stmt = select(AcsDepartmentAccess).where(
        AcsDepartmentAccess.department_id == department_id
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    return bool(row is not None and row.is_enabled)


async def list_acs_department_access(db: AsyncSession) -> AcsDepartmentAccessListResponse:
    """Список отделов-кандидатов + их текущий флаг доступа к снимкам ACS.

    Кандидаты = union(отделы с хотя бы одним сервером, отделы, у которых флаг
    уже когда-либо выставлялся). Второе нужно, чтобы выключенный отдел не
    "терялся" из списка, если его последний сервер снесли.
    """
    rows = list((await db.execute(select(AcsDepartmentAccess))).scalars())
    by_department = {row.department_id: row for row in rows}

    department_ids = set(by_department.keys())
    department_ids.update(await server_repo.list_distinct_department_ids(db))

    items = []
    for department_id in sorted(department_ids):
        row = by_department.get(department_id)
        if row is None:
            items.append(
                AcsDepartmentAccessItem(
                    department_id=department_id, is_enabled=False, updated_at=None, created_by=None,
                )
            )
        else:
            items.append(
                AcsDepartmentAccessItem(
                    department_id=row.department_id,
                    is_enabled=row.is_enabled,
                    updated_at=row.updated_at,
                    created_by=row.created_by,
                )
            )
    return AcsDepartmentAccessListResponse(items=items)


async def update_acs_department_access(
    db: AsyncSession,
    payload: AcsDepartmentAccessUpdate,
    identity: IdentityContext,
) -> AcsDepartmentAccessListResponse:
    """Upsert одного или нескольких флагов is_enabled по department_id.

    Один SELECT+INSERT/UPDATE на элемент батча, один commit в конце. Аудит —
    одно агрегированное событие `settings.acs_departments_updated` со списком
    затронутых отделов (не по одному на строку, чтобы не заспамить лог при
    массовом тогле).
    """
    touched: list[str] = []
    for item in payload.items:
        stmt = select(AcsDepartmentAccess).where(AcsDepartmentAccess.department_id == item.department_id)
        row = (await db.execute(stmt)).scalar_one_or_none()
        if row is None:
            row = AcsDepartmentAccess(
                id=acs_department_access_id(),
                department_id=item.department_id,
                is_enabled=item.is_enabled,
                created_by=identity.user_id,
            )
            db.add(row)
        else:
            row.is_enabled = item.is_enabled
        touched.append(item.department_id)

    await db.commit()

    audit_service.emit(
        "settings.acs_departments_updated",
        target_id=",".join(touched),
        target_type="acs_department_access",
        status="success",
        allowed=True,
        details={"department_ids": touched, "count": len(touched)},
    )

    return await list_acs_department_access(db)
