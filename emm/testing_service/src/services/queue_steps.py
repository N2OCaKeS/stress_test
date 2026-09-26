"""Шаги многоступенчатого теста в очереди.

Item исполняет шаги теста по порядку на одной брони стенда:

1. Цикл подготовки (`queue._start_or_continue_cycle`) всегда начинает с
   шага 0 — после restore стенд чистый. Настройка стенда первого шага уходит
   в обычный `prepare-for-test`.
2. `claim_next` собирает задание текущего шага (`current_step_index`):
   слоты шага, его `starter_suffix`, способ запуска (`full` — `starter.sh`,
   `rerun` — скрипт повторного запуска профиля) и поле `step` (C3).
3. Успех шага, после которого есть ещё шаги (`advance_after_success`),
   переводит item на следующий. Если у следующего шага есть настройка
   стенда — операция «настройка без restore» server_service (C2; у ВМ —
   без отката снимка), item на
   это время в `preparing`; callback (`handle_stand_setup_completed`)
   возвращает его в `ready`. Без настройки — сразу `ready`.
4. Вердикт — только после последнего шага: промежуточный успех —
   это код выхода 0. Провал любого шага или настройки — провал item'а
   (обычная retry-логика, retry начинает с шага 0).

Лог — один на item: воркер подписывает сегмент шагом, сюда пишутся
сегменты настройки стенда между шагами.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import QueueItemState, QueueOrchestrationEventKind, StepRunMode
from src.core.exceptions import AppException
from src.models import QueueItem, TestStep
from src.repositories import queue_item as repo
from src.repositories import test_definition as test_definition_repo
from src.repositories import test_stand as stand_repo
from src.schemas.queue import StandSetupCompletedCallback
from src.schemas.test_log import LogSegmentRequest
from src.services import (
    audit_service,
    creds_stash,
    provisioning_profile as provisioning_svc,
    queue_orchestration_log,
    server_client,
    stand_target,
    test_account as test_account_svc,
    test_log,
    test_step,
    variable_resolver,
)

logger = logging.getLogger(__name__)

_FAILED_STEP_STAND_SETUP = "stand_setup"
_FAILED_STEP_TEST_ACCOUNT = "test_account"


async def load_steps(db: AsyncSession, test_id: str) -> list[TestStep]:
    """Шаги теста для исполнения (всегда хотя бы один)."""
    return await test_step.steps_for_run(db, test_id)


def current_step(steps: list[TestStep], item: QueueItem) -> tuple[int, TestStep]:
    """Индекс и шаг, который исполняет item; шаги удалили посреди прогона — последний."""
    index = min(max(item.current_step_index or 0, 0), len(steps) - 1)
    return index, steps[index]


def step_label(index: int, count: int, name: str | None) -> str:
    """«шаг 2/4 «maxcpus=16»» — для ошибок и лога; у одношагового теста пусто."""
    if count <= 1:
        return ""
    label = f"шаг {index + 1}/{count}"
    return f"{label} «{name}»" if name else label


async def with_step(db: AsyncSession, item: QueueItem, error: str | None) -> str | None:
    """Ошибка провала с указанием шага; у одношагового теста — как есть."""
    steps = await load_steps(db, item.test_id)
    if len(steps) <= 1 or not error:
        return error
    index, step = current_step(steps, item)
    label = step_label(index, len(steps), step.name)
    return f"{label[0].upper()}{label[1:]}: {error}"


def step_context(steps: list[TestStep], index: int) -> dict[str, str]:
    """Значения шага для резолва (`ResolveContext.locals`): `STEP_INDEX` (с 1), `STEP_COUNT`, `STEP_NAME`."""
    return {"STEP_INDEX": str(index + 1), "STEP_COUNT": str(len(steps)), "STEP_NAME": steps[index].name or ""}


def is_rerun(step: TestStep) -> bool:
    return step.run_mode == StepRunMode.RERUN


async def _stash_creds_and_ready(db: AsyncSession, item: QueueItem, stand) -> None:
    """Item → `ready` со свежим стэшем кред (claim забирает его одноразово)."""
    stash_key = creds_stash.new_stash_key(item.id)
    await creds_stash.store_creds(stash_key, {
        "test_account_credential_id": await test_account_svc.get_credential_id(db, stand.department_id),
    })
    item.state = QueueItemState.READY
    item.creds_stash_key = stash_key
    item.error = None
    await db.commit()


async def _log_setup_segment(
    db: AsyncSession, item_id: str, stand, label: str, *, ok: bool, output: str, started_at,
) -> None:
    """Сегмент «настройка стенда перед шагом» в лог item'а (коммитит `db`).

    Best-effort: сбой записи лога не срывает очередь. Вызывать до
    `SELECT … FOR UPDATE` строки item'а — вставка лога проверяет FK на неё.
    """
    now = datetime.now(timezone.utc)
    try:
        await test_log.append_segment(db, item_id, LogSegmentRequest(
            kind="checkpoint", label=label, status="OK" if ok else "FATAL",
            command_text_masked=None, output=output, host=stand.legacy_token or stand.server_id or stand.id,
            started_at=started_at or now, finished_at=now,
        ))
    except Exception as exc:  # noqa: BLE001 — лог best-effort
        await db.rollback()
        logger.warning("stand setup log segment failed for queue_item %s: %s", item_id, exc)


async def advance_after_success(db: AsyncSession, item: QueueItem, stand) -> bool:
    """Шаг успешно выполнен — перейти к следующему. `False` — это был последний шаг.

    Вызывается из `queue.complete_item` на успехе SSH-сессии, до вердикта.
    `prepare_only` (testenv) исполняет только первый шаг.
    """
    from src.services import queue  # поздний импорт: queue импортирует этот модуль

    if item.prepare_only:
        return False
    steps = await load_steps(db, item.test_id)
    finished = item.current_step_index or 0
    next_index = finished + 1
    item.step_count = len(steps)
    if next_index >= len(steps):
        return False

    step = steps[next_index]
    item.current_step_index = next_index
    item.error = None
    audit_service.emit(
        "queue_item.step_completed", target_id=item.id, target_type="queue_item",
        status="success", allowed=True,
        details={
            "stand_id": stand.id, "step_index": finished, "step_name": steps[finished].name,
            "next_step_index": next_index, "step_count": len(steps),
        },
    )
    label = step_label(next_index, len(steps), step.name)

    if provisioning_svc.stand_setup_is_empty(step.stand_setup):
        await _stash_creds_and_ready(db, item, stand)
        audit_service.emit(
            "queue_item.ready", target_id=item.id, target_type="queue_item",
            status="success", allowed=True, details={"step_index": next_index, "stand_setup": False},
        )
        return True

    test = await test_definition_repo.get_by_id(db, item.test_id)
    try:
        ctx = variable_resolver.ResolveContext(
            db=db, department_id=stand.department_id, test=test, stand=stand,
            launch_context={
                **(item.launch_context or {}),
                "TEST_BRANCH": (test.category if test else "") or "",
                "STARTER_SUFFIX": step.starter_suffix or "",
            },
            debug=item.debug_mode, step=step, locals=step_context(steps, next_index),
        )
        stand_setup = await provisioning_svc.resolve_stand_setup(ctx, step.stand_setup)
        provisioning = await provisioning_svc.effective_values(db, test, stand.department_id) if test else None
    except AppException as exc:
        await queue._fail_and_advance(
            db, item, stand, failed_step=_FAILED_STEP_STAND_SETUP,
            error=f"{label}: {exc.error_code}: {exc.message}",
            audit_action="queue_item.prepare_failed", is_first_ever=False,
        )
        return True
    try:
        account = await test_account_svc.resolve_account(db, stand.department_id)
    except AppException as exc:
        await queue._fail_and_advance(
            db, item, stand, failed_step=_FAILED_STEP_TEST_ACCOUNT,
            error=f"{label}: {exc.error_code}: {exc.message}",
            audit_action="queue_item.prepare_failed", is_first_ever=False,
        )
        return True

    # correlation_id — до вызова: callback может обогнать ответ на запрос.
    # У каждой попытки свой (server_service идемпотентен по нему).
    correlation_id = f"{item.id}:step{next_index}:{uuid4().hex[:8]}"
    item.state = QueueItemState.PREPARING
    item.stand_setup_correlation_id = correlation_id
    await db.commit()
    try:
        resp = await server_client.start_stand_setup_for(
            stand_target.target_of(stand),
            correlation_id=correlation_id,
            requested_by_department_id=stand.department_id,
            test_username=account.login,
            stand_setup=stand_setup,
            provisioning=provisioning,
        )
    except AppException as exc:
        await queue_orchestration_log.record(
            db, stand.id, QueueOrchestrationEventKind.PREPARE_REQUEST_FAILED, queue_item_id=item.id,
            detail=f"stand-setup {exc.error_code}: {exc.message}",
        )
        await queue._fail_and_advance(
            db, item, stand, failed_step=_FAILED_STEP_STAND_SETUP,
            error=f"{label}: {exc.error_code}: {exc.message}",
            audit_action="queue_item.prepare_start_failed", is_first_ever=False,
        )
        return True
    audit_service.emit(
        "queue_item.stand_setup_requested", target_id=item.id, target_type="queue_item",
        status="success", allowed=True,
        details={
            "stand_id": stand.id, "step_index": next_index, "correlation_id": correlation_id,
            "stand_setup_request_id": (resp or {}).get("stand_setup_request_id"),
            "kernel_cmdline_extra": stand_setup.get("kernel_cmdline_extra") if stand_setup else None,
        },
    )
    return True


async def handle_stand_setup_completed(
    db: AsyncSession, stand_setup_request_id: str, body: StandSetupCompletedCallback,
) -> QueueItem | None:
    """Callback server_service о настройке стенда перед шагом.

    Item не найден или уже не ждёт этой настройки (повтор callback'а,
    item сняли/поставили на паузу) — идемпотентный no-op.
    """
    from src.services import queue  # поздний импорт: queue импортирует этот модуль

    item = await repo.get_by_stand_setup_correlation_id(db, body.correlation_id, for_update=False)
    if item is None or item.state != QueueItemState.PREPARING:
        return None
    stand = await stand_repo.get_by_id(db, item.stand_id)
    if stand is None:
        return None
    steps = await load_steps(db, item.test_id)
    index, step = current_step(steps, item)
    label = step_label(index, len(steps), step.name) or "шаг"
    await _log_setup_segment(
        db, item.id, stand, f"Настройка стенда перед: {label}", ok=body.succeeded,
        output=body.error or "", started_at=item.updated_at,
    )
    # Дальше — под локом строки item'а (повторный callback мог обогнать).
    item = await repo.get_by_stand_setup_correlation_id(db, body.correlation_id)
    if item is None or item.state != QueueItemState.PREPARING:
        await db.rollback()
        return None

    if body.succeeded:
        await _stash_creds_and_ready(db, item, stand)
        audit_service.emit(
            "queue_item.ready", target_id=item.id, target_type="queue_item",
            status="success", allowed=True,
            details={"step_index": index, "stand_setup": True, "stand_setup_request_id": stand_setup_request_id},
        )
        return item

    await queue_orchestration_log.record(
        db, stand.id, QueueOrchestrationEventKind.PREPARE_REQUEST_FAILED, queue_item_id=item.id,
        detail=f"stand-setup {body.failed_step or ''}: {body.error or 'stand setup failed'}",
    )
    await queue._fail_and_advance(
        db, item, stand,
        failed_step=body.failed_step or _FAILED_STEP_STAND_SETUP,
        error=f"Настройка стенда перед {label}: {body.error or 'server_service reported failure'}",
        audit_action="queue_item.prepare_failed",
        is_first_ever=False,
    )
    return item
