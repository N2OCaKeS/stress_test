"""Use cases настроек тестирования отдела (§2.4 плана миграции).

Настройки department-scoped: `test_username` уезжает в prepare-for-test и
определяет, какого SSH-пользователя `server_service` провижнит на стендах
отдела, поэтому чужой отдел их не читает и не пишет. Чтение —
`require_own_department`, запись — `require_department_action` на
`(department_test_settings, *, update)`.

Отсутствие строки в БД не 404: сервис отдаёт дефолты (`DEFAULT_*`), реальная
строка появляется только на первый `PUT`.

`preflight` — проверка внешних сервисов перед запуском теста. Её
читает и `claim_next` (`services/queue.py`), чтобы отдать воркеру: значения
из БД действуют на следующий claim без перезапуска воркера.

`zephyr_verdict_*`, `verdict_without_zephyr_run` читает очередь,
когда воркер закончил SSH-сессию: ждать ли статус из Zephyr, сколько и
что делать, если прогона в Zephyr нет.
"""

import logging

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import Action, EntityType
from src.core.exceptions import AuthorizationError
from src.dependencies.auth import Identity
from src.models import DepartmentTestSettings
from src.repositories import department_test_settings as repo
from src.schemas.department_test_settings import DepartmentTestSettingsUpdate, PreflightSettings
from src.services import audit_service, permissions
from src.utils.ids import department_test_settings_id as new_id

DEFAULT_RETRY_ENABLED = True
DEFAULT_TEST_USERNAME = "u"
DEFAULT_ACTIVITY_REPORT_AUTO_GENERATE = False
# Отдел без строки в БД получает то же легаси-правило, что миграция
# `d5c2e8a41f93` сидит в `server_default` колонки (`allta_back.py:393`:
# режим → ядро → имя тест-кейса).
DEFAULT_CAMPAIGN_SORT_RULE: tuple[dict, ...] = (
    {"key": "mode", "direction": "asc"},
    {"key": "kernel", "direction": "asc"},
    {"key": "test_case_name", "direction": "asc"},
)


# Вердикт из Zephyr: ждать итогового статуса 35 минут,
# опрос раз в 60 с; не дождались — `failed`. Запуск без прогона в Zephyr —
# вердикт `unknown`. Те же значения — `server_default` колонок
# (миграция `b8e4f1a7c265`).
DEFAULT_ZEPHYR_VERDICT_WAIT_SECONDS = 2100
DEFAULT_ZEPHYR_VERDICT_POLL_SECONDS = 60
DEFAULT_ZEPHYR_VERDICT_UNFINISHED_OUTCOME = "failed"
DEFAULT_VERDICT_WITHOUT_ZEPHYR_RUN = "unknown"

_VERDICT_DEFAULTS: dict = {
    "zephyr_verdict_wait_seconds": DEFAULT_ZEPHYR_VERDICT_WAIT_SECONDS,
    "zephyr_verdict_poll_seconds": DEFAULT_ZEPHYR_VERDICT_POLL_SECONDS,
    "zephyr_verdict_unfinished_outcome": DEFAULT_ZEPHYR_VERDICT_UNFINISHED_OUTCOME,
    "verdict_without_zephyr_run": DEFAULT_VERDICT_WITHOUT_ZEPHYR_RUN,
    # Живой лог: прежние константы `testing_worker/ssh_executor`.
    "log_chunk_interval_seconds": 2.5,
    "log_chunk_max_bytes": 4096,
}


def _default_campaign_sort_rule() -> list[dict]:
    return [dict(item) for item in DEFAULT_CAMPAIGN_SORT_RULE]


logger = logging.getLogger("testing_service.department_test_settings")


def effective_preflight(raw: dict | None, department_id: str | None = None) -> dict:
    """Настройки preflight в форме CONTRACTS.md C3 — дефолты, если не заданы.

    Дефолты — поля `PreflightSettings` (легаси-значения, см. схему и миграцию
    `tp19_department_preflight`). Испорченный руками JSON в БД не должен ронять
    claim очереди: предупреждение в лог и легаси-дефолты.
    """
    if raw is None:
        return PreflightSettings().model_dump()
    try:
        return PreflightSettings.model_validate(raw).model_dump()
    except ValidationError as exc:
        logger.warning(
            "department_test_settings.preflight of %s is invalid, using defaults: %s",
            department_id, exc,
        )
        return PreflightSettings().model_dump()


async def get_settings_row(db: AsyncSession, department_id: str) -> DepartmentTestSettings | None:
    """SELECT сырой строки, без подстановки дефолтов. `None` — штатный случай."""
    return await repo.get_by_department(db, department_id)


async def get_effective(db: AsyncSession, department_id: str) -> dict:
    """Эффективные настройки отдела — дефолты, если строки ещё нет.

    Используется и API-эндпоинтом (обёртка в `DepartmentTestSettingsResponse`),
    и сервисом очереди (`services/queue.py`), которому нужны только
    `retry_enabled`/`test_username`, без Pydantic-обёртки.
    """
    row = await repo.get_by_department(db, department_id)
    if row is None:
        return {
            "id": None,
            "department_id": department_id,
            "retry_enabled": DEFAULT_RETRY_ENABLED,
            "test_username": DEFAULT_TEST_USERNAME,
            "test_account_credential_id": None,
            "activity_report_auto_generate": DEFAULT_ACTIVITY_REPORT_AUTO_GENERATE,
            "campaign_sort_rule": _default_campaign_sort_rule(),
            "preflight": effective_preflight(None),
            **_VERDICT_DEFAULTS,
            "created_at": None,
            "updated_at": None,
        }
    return {
        "id": row.id,
        "department_id": row.department_id,
        "retry_enabled": row.retry_enabled,
        "test_username": row.test_username,
        "test_account_credential_id": row.test_account_credential_id,
        "activity_report_auto_generate": row.activity_report_auto_generate,
        "campaign_sort_rule": list(row.campaign_sort_rule or _default_campaign_sort_rule()),
        "preflight": effective_preflight(row.preflight, department_id),
        **{key: getattr(row, key) for key in _VERDICT_DEFAULTS},
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


async def get_effective_for(db: AsyncSession, identity: Identity, department_id: str) -> dict:
    """То же, что `get_effective`, но для HTTP-чтения — с гейтом по отделу.

    Отдельная функция, а не флаг: `get_effective` дёргает очередь
    (`services/queue.py`) уже без пользовательского контекста, ей гейт
    неприменим.
    """
    permissions.require_own_department(identity, department_id)
    return await get_effective(db, department_id)


async def upsert(
    db: AsyncSession,
    identity: Identity,
    department_id: str,
    payload: DepartmentTestSettingsUpdate,
) -> DepartmentTestSettings:
    """PUT — создаёт строку при первом вызове, иначе обновляет заданные поля."""
    try:
        await permissions.require_department_action(
            db, identity, department_id, EntityType.DEPARTMENT_TEST_SETTINGS, Action.UPDATE,
        )
    except AuthorizationError:
        audit_service.emit(
            "department_test_settings.update",
            target_id=department_id, target_type="department_test_settings",
            status="denied", allowed=False,
            details={"reason": "permission_denied"},
        )
        raise

    changes = payload.model_dump(exclude_unset=True, mode="json")
    row = await repo.get_by_department(db, department_id)
    if row is None:
        data = {
            "id": new_id(),
            "department_id": department_id,
            "retry_enabled": changes.pop("retry_enabled", DEFAULT_RETRY_ENABLED),
            "test_username": changes.pop("test_username", DEFAULT_TEST_USERNAME),
            "activity_report_auto_generate": changes.pop(
                "activity_report_auto_generate", DEFAULT_ACTIVITY_REPORT_AUTO_GENERATE,
            ),
            "campaign_sort_rule": changes.pop("campaign_sort_rule", None) or _default_campaign_sort_rule(),
            # Не задано в первом PUT — NULL, то есть легаси-дефолты.
            "preflight": changes.pop("preflight", None),
            **{key: changes.pop(key, default) for key, default in _VERDICT_DEFAULTS.items()},
        }
        row = await repo.create(db, data)
    elif changes:
        await repo.update(db, row, changes)

    await db.commit()
    await db.refresh(row)
    audit_service.emit(
        "department_test_settings.update",
        target_id=department_id, target_type="department_test_settings",
        status="success", allowed=True,
        details={"fields": list(changes.keys())},
    )
    return row
