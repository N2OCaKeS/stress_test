"""Вердикт теста из Zephyr.

Исход теста в легаси — статус, который **сам скрипт** выставил тест-кейсу в
прогоне Zephyr (`allta_app_full/backup_image.py:495-530`, 91/92); `run.py`
всех веток выходит с 0. Здесь — всё, что нужно очереди, чтобы этот статус
прочитать, без самой машины состояний (она в `services/queue.py`):

* таблица `zephyr_status_mappings` (набор отдела или по умолчанию) и
  перевод сырого статуса в `passed`/`failed`/`not_finished`;
* поиск прогона и тест-кейса для item'а (`resolve_target`) — та же связка,
  что у `stp_status.sync_cell_from_queue_item`: `stp_test_run` item'а →
  `zephyr_test_run_key`, тест → `stp_test_cases` по коду → `zephyr_id`
  (или название кейса, если ключа нет);
* чтение статуса (`fetch_status`) — существующим
  `zephyr_client.get_test_run`, второго клиента нет;
* API маппинга: чтение — свой отдел, запись — те же права, что у настроек
  тестирования отдела (`department_test_settings:update`).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import Action, EntityType, VerdictOutcome
from src.core.exceptions import AuthorizationError
from src.dependencies.auth import Identity
from src.models import QueueItem
from src.repositories import department_integration_settings as dis_repo
from src.repositories import stp_test_case as stp_test_case_repo
from src.repositories import stp_test_run as stp_test_run_repo
from src.repositories import test_definition as test_definition_repo
from src.repositories import zephyr_status_mapping as repo
from src.schemas.zephyr_status_mapping import ZephyrStatusMappingUpdate
from src.services import audit_service, permissions, secret_client, zephyr_client
from src.utils.ids import zephyr_status_mapping_id as new_id


def normalize_status(value: str) -> str:
    """Ключ сравнения статусов: без регистра и пробелов по краям."""
    return value.strip().casefold()


async def _effective_rows(db: AsyncSession, department_id: str):
    rows = await repo.list_for_department(db, department_id)
    if rows:
        return rows, False
    return await repo.list_for_department(db, None), True


async def load_mapping(db: AsyncSession, department_id: str) -> dict[str, str]:
    """Действующий маппинг отдела: `normalize_status(статус)` → исход."""
    rows, _is_default = await _effective_rows(db, department_id)
    return {normalize_status(row.zephyr_status): row.outcome for row in rows}


def classify(raw_status: str | None, mapping: dict[str, str]) -> str:
    """Сырой статус Zephyr → `VerdictOutcome`.

    Пусто или статуса нет в таблице — `not_finished`: скрипт мог ещё не
    выставить итог (или выставил экзотический статус), опрос продолжается до
    таймаута, а там решает `zephyr_verdict_unfinished_outcome` (T3).
    """
    if not raw_status:
        return VerdictOutcome.NOT_FINISHED
    return mapping.get(normalize_status(raw_status), VerdictOutcome.NOT_FINISHED)


@dataclass(frozen=True)
class ZephyrTarget:
    """Где искать статус item'а: прогон + тест-кейс + доступ к Jira отдела."""

    base_url: str
    credential_id: str
    test_run_key: str
    test_case_key: str | None
    test_case_title: str


async def resolve_target(db: AsyncSession, item: QueueItem, department_id: str) -> ZephyrTarget | None:
    """Прогон и кейс Zephyr для item'а; `None` — прогона в Zephyr у запуска нет.

    Debug-запуск в СТП не входит — прогона нет по определению. Иначе прогон
    берётся из `stp_test_run` item'а (у обычного запуска он есть всегда, см.
    `launch_stp.require_membership`), а для старых item'ов без ссылки — тем
    же поиском по контексту, что у `stp_status`. Без ключа прогона в Zephyr,
    без кейса или без доступа к Jira отдела читать нечего.
    """
    if item.debug_mode:
        return None
    ctx = item.launch_context or {}
    if item.stp_test_run_id:
        run = await stp_test_run_repo.get_by_id(db, item.stp_test_run_id)
    else:
        rc, kernel, mode = ctx.get("RC"), ctx.get("KERNEL"), ctx.get("MODE")
        if not (rc and kernel and mode):
            return None
        run = await stp_test_run_repo.find_latest_for_context(
            db, stand_id=item.stand_id, os_version_id=rc, mode=mode, kernel=kernel,
        )
    if run is None or not run.zephyr_test_run_key:
        return None
    test = await test_definition_repo.get_by_id(db, item.test_id)
    if test is None:
        return None
    case = await stp_test_case_repo.get_by_code(db, test.code)
    if case is None:
        return None
    settings = await dis_repo.get_by_department(db, department_id)
    if settings is None or not settings.credential_id or not settings.jira_base_url:
        return None
    return ZephyrTarget(
        base_url=settings.jira_base_url,
        credential_id=settings.credential_id,
        test_run_key=run.zephyr_test_run_key,
        test_case_key=case.zephyr_id,
        test_case_title=case.title,
    )


async def fetch_status(target: ZephyrTarget) -> str | None:
    """Текущий сырой статус тест-кейса в прогоне; `None` — кейса в прогоне нет.

    Кейс ищется по ключу Zephyr, а если у `stp_test_case` ключа нет — по
    названию (легаси `zefir.py` искал по имени, `TEST_CASE_NAME`). Сбои
    сети/Zephyr пробрасываются как `AppException` — решает вызывающий.
    """
    _login, bearer_token = await secret_client.reveal_credential(target.credential_id)
    detail = await zephyr_client.get_test_run(
        base_url=target.base_url, bearer_token=bearer_token or "",
        test_run_key=target.test_run_key,
    )
    if target.test_case_key:
        matches = [e for e in detail.items if e.test_case_key == target.test_case_key]
    else:
        title = normalize_status(target.test_case_title)
        matches = [
            e for e in detail.items
            if e.test_case_name and normalize_status(e.test_case_name) == title
        ]
    return matches[0].status_raw if matches else None


# ---------------------------------------------------------------- API маппинга


async def get_for(db: AsyncSession, identity: Identity, department_id: str) -> dict:
    """GET — действующий маппинг отдела (свой отдел)."""
    permissions.require_own_department(identity, department_id)
    rows, is_default = await _effective_rows(db, department_id)
    return {
        "department_id": department_id,
        "is_default": is_default,
        "items": [{"zephyr_status": r.zephyr_status, "outcome": r.outcome} for r in rows],
    }


async def _require_update(db: AsyncSession, identity: Identity, department_id: str, audit_action: str) -> None:
    try:
        await permissions.require_department_action(
            db, identity, department_id, EntityType.DEPARTMENT_TEST_SETTINGS, Action.UPDATE,
        )
    except AuthorizationError:
        audit_service.emit(
            audit_action,
            target_id=department_id, target_type="zephyr_status_mapping",
            status="denied", allowed=False,
            details={"reason": "permission_denied"},
        )
        raise


async def replace_for(
    db: AsyncSession, identity: Identity, department_id: str, payload: ZephyrStatusMappingUpdate,
) -> dict:
    """PUT — заменить набор отдела целиком."""
    await _require_update(db, identity, department_id, "zephyr_status_mapping.update")
    await repo.delete_for_department(db, department_id)
    for item in payload.items:
        await repo.create(db, {
            "id": new_id(),
            "department_id": department_id,
            "zephyr_status": item.zephyr_status,
            "outcome": str(item.outcome),
        })
    await db.commit()
    audit_service.emit(
        "zephyr_status_mapping.update",
        target_id=department_id, target_type="zephyr_status_mapping",
        status="success", allowed=True,
        details={"items": [
            {"zephyr_status": i.zephyr_status, "outcome": str(i.outcome)} for i in payload.items
        ]},
    )
    return await get_for(db, identity, department_id)


async def reset_for(db: AsyncSession, identity: Identity, department_id: str) -> dict:
    """DELETE — удалить строки отдела, дальше действует набор по умолчанию."""
    await _require_update(db, identity, department_id, "zephyr_status_mapping.reset")
    deleted = await repo.delete_for_department(db, department_id)
    await db.commit()
    audit_service.emit(
        "zephyr_status_mapping.reset",
        target_id=department_id, target_type="zephyr_status_mapping",
        status="success", allowed=True,
        details={"deleted": deleted},
    )
    return await get_for(db, identity, department_id)
