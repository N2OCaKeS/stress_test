"""Очередь прогонов теста на стенде (§2.4, §5, §5.5 плана миграции).

Один цикл — от постановки в очередь до освобождения стенда — устроен так:

1. `enqueue()` кладёт `queue_item` в очередь стенда. Если это первый активный
   элемент очереди (стенд был свободен от работы testing_service) — сразу
   запускает цикл подготовки: `acquire-for-service` + `prepare-for-test`.
2. `handle_prepare_completed()` — callback `server_service`а. На успехе
   стэширует креды в Redis и переводит item в `ready` (ждёт `testing_worker`).
   На провале — retry-логика, при исчерпании — переход к следующему item'у
   очереди стенда или `release-for-service`, если очередь опустела.
3. `claim_next()` — `testing_worker` атомарно забирает один `ready`-item,
   достаёт креды (одноразово), собирает содержимое `dates.conf`, git-токен
   для `starter.sh` и команду его запуска, узнаёт host стенда.
4. `complete_item()` — исход SSH-сессии. Та же retry-логика на провале, та же
   логика продолжения/освобождения очереди.

Бронь стенда держится server_service'ом (`busy_state`) на весь цикл — от
первого `acquire-for-service` до `release-for-service`, когда очередь этого
стенда полностью опустела. Переход между item'ами одной очереди НЕ снимает
бронь — только переключает стадию (`service-status`), это и есть основной
приём этого модуля: `_start_or_continue_cycle()` сначала пробует
`service-status` (бронь уже держится с предыдущего item'а), и только если
server_service отвечает `SERVER_NOT_BUSY` (бронь ещё не бралась — самый
первый item очереди), падает обратно на `acquire-for-service`. Это избавляет
от необходимости отдельно отслеживать «держим ли мы сейчас бронь» — вопрос
решает сам server_service, у которого для этого есть авторитетный источник
истины (`Server.busy_state`).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import (
    ACTIVE_QUEUE_STATES,
    IN_FLIGHT_QUEUE_STATES,
    QueueInterruptAction,
    QueueItemState,
    QueueOrchestrationEventKind,
    TERMINAL_TEST_RUN_STATUSES,
    TestReadiness,
)
from src.core.exceptions import (
    AppException,
    ConflictError,
    DomainValidationError,
    NotFoundError,
    ServiceUnavailableError,
)
from src.dependencies.auth import Identity
from src.models import QueueItem, TestRun, TestRunEntry
from src.repositories import department_integration_settings as dis_repo
from src.repositories import queue_item as repo
from src.repositories import test_definition as test_definition_repo
from src.repositories import test_stand as stand_repo
from src.schemas.queue import PrepareForTestCompletedCallback, QueueClaimItem, QueueCompletedRequest
from src.services import (
    audit_service,
    creds_stash,
    department_test_settings as dts_svc,
    launch_context as launch_context_svc,
    queue_orchestration_log,
    run_summary,
    secret_client,
    server_client,
    statistics_recalc,
    stp_status,
    test_run_status,
    launch_stp,
)
from src.services.test_command_arg import resolve_dates_content, resolve_dates_content_masked
from src.utils.ids import queue_item_id as new_id

logger = logging.getLogger(__name__)

_REQUIRED_LAUNCH_CONTEXT_KEYS = ("RC", "KERNEL", "MODE")


async def enqueue(
    db: AsyncSession,
    identity: Identity,
    test_id: str,
    *,
    launch_context: dict[str, str] | None = None,
    debug_mode: bool = False,
    prepare_only: bool = False,
    stand_id: str | None = None,
    test_run_id: str | None = None,
    test_run_entry_id: str | None = None,
    retry_source: QueueItem | None = None,
    client_request_id: str | None = None,
    request_fingerprint: str | None = None,
    stp_test_run_id: str | None = None,
) -> QueueItem:
    """Поставить тест в очередь стенда.

    Вне debug-режима стенд берётся из `test_definitions.pinned_stand_id`
    (тест обязан быть к чему-то привязан). В debug-режиме привязка снимается
    — `stand_id` обязателен и приходит от вызывающего.

    `launch_context` сохраняется как есть, без резолва глобальных переменных
    (это отдельная забота за пределами этой волны) — но обязан нести
    `RC`/`KERNEL`/`MODE`, потому что ими параметризуется `prepare-for-test`.

    `test_run_id` — заполняется только вызовом со стороны `services/test_run.py`
    (кампания породила этот item); одиночные вызовы (UI/CLI постановка одного
    теста в очередь) оставляют его `None`, как и раньше.

    `prepare_only` — легаси testenv-режим: стенд откатывается и готовится
    (`prepare-for-test`, `prepare.sh`) как обычно, но вместо запуска теста
    `testing_worker` кладёт на стенд файл с командой, которой был бы запущен
    тест (`services/queue.py::claim_next` считает её через `starter_suffix`).
    Итоговый статус такого item'а — `QueueItemState.PREPARED`, не
    `succeeded`/`failed` (см. `complete_item`).
    """
    test = await test_definition_repo.get_by_id(db, test_id, for_update=True)
    if test is None:
        raise NotFoundError(
            error_code="TEST_DEFINITION_NOT_FOUND",
            message="Test definition not found",
        )

    if not debug_mode and test.readiness != TestReadiness.READY:
        raise DomainValidationError(
            error_code="TEST_REQUIRES_DEBUG",
            message="Обычный запуск доступен только для теста со статусом «Рабочий». Используйте debug.",
            details={"test_id": test.id, "readiness": test.readiness},
        )

    entry = await db.get(TestRunEntry, test_run_entry_id) if test_run_entry_id else None
    if test_run_entry_id and (
        entry is None or entry.test_run_id != test_run_id or entry.test_id != test_id
    ):
        raise DomainValidationError(error_code="TEST_RUN_ENTRY_MISMATCH", message="Invalid campaign entry")

    if debug_mode:
        # Debug-групповой запуск стенда (§E1) тоже заводит TestRunEntry —
        # test_run_entry_id и debug_mode здесь законно совпадают, стенд
        # приходит явно от вызывающего, а не через привязку теста/записи.
        if not stand_id:
            raise DomainValidationError(
                error_code="STAND_ID_REQUIRED_FOR_DEBUG",
                message="debug_mode requires an explicit stand_id",
            )
        resolved_stand_id = stand_id
    else:
        resolved_stand_id = retry_source.stand_id if retry_source else entry.stand_id if entry is not None else test.pinned_stand_id
        if not resolved_stand_id:
            raise DomainValidationError(
                error_code="TEST_NOT_PINNED_TO_STAND",
                message="Test has no pinned_stand_id; use debug_mode to pick a stand explicitly",
                details={"test_id": test_id},
            )

    stand = await stand_repo.get_by_id(db, resolved_stand_id, for_update=True)
    if stand is None:
        raise NotFoundError(
            error_code="TEST_STAND_NOT_FOUND",
            message="Test stand not found",
        )
    if not stand.is_active:
        raise ConflictError(
            error_code="TEST_STAND_INACTIVE",
            message="Test stand is not active",
        )
    if not stand.queue_enabled:
        raise ConflictError(
            error_code="TEST_STAND_QUEUE_DISABLED",
            message="Test stand is not enrolled in the queue",
        )

    ctx = dict(launch_context or {})
    missing = [key for key in _REQUIRED_LAUNCH_CONTEXT_KEYS if not ctx.get(key)]
    if missing:
        raise DomainValidationError(
            error_code="LAUNCH_CONTEXT_INCOMPLETE",
            message="launch_context must include RC, KERNEL and MODE for prepare-for-test",
            details={"missing": missing},
        )

    was_empty = await repo.count_active_for_stand(db, stand.id) == 0
    position = await repo.next_position_for_stand(db, stand.id)

    data = {
        "id": new_id(),
        "stand_id": stand.id,
        "test_id": test.id,
        "launch_context": ctx,
        "state": QueueItemState.QUEUED,
        "position": position,
        "is_retry": retry_source is not None,
        "retry_of_id": retry_source.id if retry_source else None,
        "client_request_id": client_request_id,
        "request_fingerprint": request_fingerprint,
        "stp_test_run_id": stp_test_run_id,
        "debug_mode": debug_mode,
        "prepare_only": prepare_only,
        "test_run_id": test_run_id,
        "test_run_entry_id": test_run_entry_id,
        "created_by": identity.user_id,
    }
    item = await repo.create(db, data)
    await db.commit()
    await db.refresh(item)
    audit_service.emit(
        "queue_item.enqueued",
        target_id=item.id, target_type="queue_item",
        status="success", allowed=True,
        details={"test_id": test.id, "stand_id": stand.id, "debug_mode": debug_mode, "prepare_only": prepare_only},
    )

    if test_run_id:
        await test_run_status.recompute(db, test_run_id)
        await db.commit()
    if was_empty:
        await _start_or_continue_cycle(db, stand, item, is_first_ever=True)
        await db.refresh(item)
    return item


async def _start_or_continue_cycle(db: AsyncSession, stand, item: QueueItem, *, is_first_ever: bool) -> None:
    """Взять/продолжить бронь стенда и запросить `prepare-for-test` для `item`.

    `is_first_ever=True` — стенд был свободен, бронь берётся впервые
    (`acquire-for-service`). Иначе бронь уже держится с предыдущего item'а
    этой же очереди — пробуем просто сменить стадию (`service-status`), и
    только если server_service отвечает `SERVER_NOT_BUSY` (бронь почему-то не
    держится), падаем обратно на `acquire-for-service`.
    """
    if await _reject_unready_item(db, stand, item, is_first_ever=is_first_ever):
        return
    settings = await dts_svc.get_effective(db, stand.department_id)
    ctx = item.launch_context or {}
    rc = ctx.get("RC")
    kernel = ctx.get("KERNEL")
    mode = ctx.get("MODE")
    note = f"ACS|revert|{rc}|{kernel}"

    try:
        if is_first_ever:
            await server_client.acquire_for_service(
                stand.server_id, busy_state="acs", busy_note=note,
                requested_by_department_id=stand.department_id,
            )
        else:
            try:
                await server_client.set_service_status(stand.server_id, busy_state="acs", busy_note=note)
            except ConflictError as exc:
                if exc.error_code == "SERVER_NOT_BUSY":
                    await server_client.acquire_for_service(
                        stand.server_id, busy_state="acs", busy_note=note,
                        requested_by_department_id=stand.department_id,
                    )
                else:
                    raise
        resp = await server_client.start_prepare_for_test(
            stand.server_id,
            os_version_id=rc,
            kernel=kernel,
            mode=mode,
            test_username=settings["test_username"],
            requested_by_department_id=stand.department_id,
            correlation_id=item.id,
        )
    except AppException as exc:
        # ConflictError здесь означает буквально "стенд занят" — server_service
        # отбил acquire/service-status конфликтом (чужая бронь, ручной ACS).
        # Отдельный kind от прочих провалов: причина ясна сама по себе, ждать
        # освобождения, а не чинить интеграцию с server_service.
        event_kind = (
            QueueOrchestrationEventKind.STAND_BUSY_BLOCKED
            if isinstance(exc, ConflictError)
            else QueueOrchestrationEventKind.PREPARE_REQUEST_FAILED
        )
        await queue_orchestration_log.record(
            db, stand.id, event_kind, queue_item_id=item.id,
            detail=f"{exc.error_code}: {exc.message}",
        )
        await _fail_and_advance(
            db, item, stand,
            failed_step=None,
            error=f"{exc.error_code}: {exc.message}",
            audit_action="queue_item.prepare_start_failed",
            is_first_ever=is_first_ever,
        )
        return

    item.state = QueueItemState.PREPARING
    item.prepare_request_id = resp.get("prepare_request_id")
    await db.commit()
    audit_service.emit(
        "queue_item.prepare_requested",
        target_id=item.id, target_type="queue_item",
        status="success", allowed=True,
        details={"stand_id": stand.id, "prepare_request_id": item.prepare_request_id},
    )


async def _reject_unready_item(db: AsyncSession, stand, item: QueueItem, *, is_first_ever: bool) -> bool:
    if item.debug_mode:
        return False
    test = await test_definition_repo.get_by_id(db, item.test_id, for_update=True)
    error = "TEST_REQUIRES_DEBUG: статус теста изменён; обычный запуск запрещён."
    if test is not None and test.readiness == TestReadiness.READY:
        if not item.stp_test_run_id or await launch_stp.find_membership(db, test.code, item.stand_id, item.launch_context, item.stp_test_run_id):
            return False
        error = "TEST_NOT_IN_STP: тест больше не входит в выбранную СТП."
    item.state = QueueItemState.FAILED
    item.failed_step = "launch_guard"
    item.error = error
    item.finished_at = datetime.now(timezone.utc)
    if item.creds_stash_key:
        await creds_stash.pop_creds(item.creds_stash_key)
        item.creds_stash_key = None
    await db.commit()
    audit_service.emit(
        "queue_item.launch_rejected", target_id=item.id, target_type="queue_item",
        status="denied", allowed=False,
        details={"test_id": item.test_id, "readiness": test.readiness if test else None},
    )
    if item.test_run_id:
        await test_run_status.recompute(db, item.test_run_id)
        await db.commit()
    # Проверка допуска не является результатом теста и не обновляет СТП.
    if is_first_ever:
        next_item = await repo.get_next_queued_for_stand(db, stand.id)
        if next_item is not None:
            await _start_or_continue_cycle(db, stand, next_item, is_first_ever=True)
    else:
        await _advance_stand_queue(db, stand)
    return True


async def _fail_item_and_maybe_retry(
    db: AsyncSession,
    item: QueueItem,
    department_id: str,
    *,
    failed_step: str | None,
    error: str | None,
    audit_action: str,
) -> QueueItem | None:
    """Провалить `item` терминально и, если положено, создать retry в конце очереди."""
    settings = await dts_svc.get_effective(db, department_id)
    item.state = QueueItemState.FAILED
    item.failed_step = failed_step
    item.error = (error or "")[:2048] or None
    item.finished_at = datetime.now(timezone.utc)
    await db.flush()

    audit_service.emit(
        audit_action,
        target_id=item.id, target_type="queue_item",
        status="failure", allowed=True,
        details={"failed_step": failed_step, "error": item.error, "is_retry": item.is_retry},
    )

    if not settings["retry_enabled"] or item.is_retry:
        return None

    position = await repo.next_position_for_stand(db, item.stand_id)
    retry_item = await repo.create(db, {
        "id": new_id(),
        "stand_id": item.stand_id,
        "test_id": item.test_id,
        "launch_context": item.launch_context,
        "state": QueueItemState.QUEUED,
        "position": position,
        "is_retry": True,
        "retry_of_id": item.id,
        "debug_mode": item.debug_mode,
        "prepare_only": item.prepare_only,
        "test_run_id": item.test_run_id,
        "test_run_entry_id": item.test_run_entry_id,
        "stp_test_run_id": item.stp_test_run_id,
        "created_by": item.created_by,
    })
    audit_service.emit(
        "queue_item.retry_created",
        target_id=retry_item.id, target_type="queue_item",
        status="success", allowed=True,
        details={"retry_of_id": item.id, "stand_id": item.stand_id},
    )
    return retry_item


async def _fail_and_advance(
    db: AsyncSession,
    item: QueueItem,
    stand,
    *,
    failed_step: str | None,
    error: str | None,
    audit_action: str,
    is_first_ever: bool,
) -> None:
    """Провалить `item` + либо продолжить очередь стенда, либо освободить бронь.

    `is_first_ever=True` значит бронь никогда не бралась для этого цикла —
    в этом случае освобождать нечего, даже если queue опустела.
    """
    retry_item = await _fail_item_and_maybe_retry(
        db, item, stand.department_id,
        failed_step=failed_step, error=error, audit_action=audit_action,
    )
    await db.commit()
    await stp_status.sync_cell_from_queue_item(db, item)
    if retry_item is None and item.test_run_id:
        # Провал без retry — терминально для этого item'а. Если он часть
        # кампании, статус мог только что перейти в failed/partially_failed.
        new_status = await test_run_status.recompute(db, item.test_run_id)
        await db.commit()
        await _maybe_post_run_summary(db, new_status, item.test_run_id)
    if retry_item is not None:
        await _start_or_continue_cycle(db, stand, retry_item, is_first_ever=is_first_ever)
    elif not is_first_ever:
        await _advance_stand_queue(db, stand)


async def _maybe_post_run_summary(db: AsyncSession, new_status: str | None, test_run_id: str) -> None:
    """Best-effort триггеры на переходе кампании в терминал (§2.7, §9.2, §9.3).

    Два независимых best-effort вызова: end-of-run комментарий в Confluence
    (`run_summary`) и фоновый пересчёт статистики (`statistics_recalc`) —
    ровно один раз на кампанию, здесь же, а не на каждый отдельный item, так
    что retry/повторы её тестов по ходу прогона не плодят лишних пересчётов.
    Оба уже best-effort сами по себе (сохраняют свой статус на любой сбой),
    но оборачиваем ещё раз здесь и по отдельности — сбой одного не должен
    ни ронять другой, ни как-либо влиять на уже завершённый прогон, который
    эта функция вызывается финализировать.
    """
    if new_status not in TERMINAL_TEST_RUN_STATUSES:
        return
    try:
        await run_summary.post_run_summary(db, test_run_id)
    except Exception as exc:  # noqa: BLE001 — best-effort, не должно ронять queue.py
        logger.warning(
            "run_summary.post_run_summary raised for test_run %s: %s", test_run_id, exc,
        )
    try:
        await statistics_recalc.schedule_recalc(db, "test_run", test_run_id=test_run_id)
    except Exception as exc:  # noqa: BLE001 — best-effort, не должно ронять queue.py
        logger.warning(
            "statistics_recalc.schedule_recalc raised for test_run %s: %s", test_run_id, exc,
        )


async def _advance_stand_queue(db: AsyncSession, stand) -> None:
    """Взять следующий `queued`-item этого стенда, либо перевести бронь в
    `testing_done`, если очередь пуста.

    Не отпускаем сервер сразу в `free` — стенд паркуется в промежуточном
    статусе, который снимает вручную любой пользователь через
    `POST /servers/{id}/acknowledge-testing-done` на server_service.
    """
    next_item = await repo.get_next_queued_for_stand(db, stand.id)
    if next_item is None:
        try:
            await server_client.release_for_service_as_done(stand.server_id)
        except AppException as exc:
            # Best-effort: бронь может повиснуть, если server_service недоступен
            # именно в этот момент. Наблюдаемость — через лог + WARNING-аудит
            # событий выше по цепочке; активной сверки/sweep'а на эту волну не
            # заводили (см. отчёт волны, раздел "вопросы").
            logger.warning(
                "release-for-service-as-done failed for stand %s (server %s): %s",
                stand.id, stand.server_id, exc,
            )
        return
    await _start_or_continue_cycle(db, stand, next_item, is_first_ever=False)


async def handle_prepare_completed(
    db: AsyncSession, prepare_request_id: str, body: PrepareForTestCompletedCallback,
) -> QueueItem:
    """Callback server_service'а о завершении `prepare-for-test`."""
    item = await repo.get_by_prepare_request_id(db, prepare_request_id)
    if item is None:
        raise NotFoundError(
            error_code="QUEUE_ITEM_NOT_FOUND_FOR_PREPARE_REQUEST",
            message="No queue item is waiting on this prepare_request_id",
            details={"prepare_request_id": prepare_request_id},
        )
    if item.state != QueueItemState.PREPARING:
        # Повторный callback по уже обработанному запросу — идемпотентный
        # no-op (server_service ретраит доставку несколько раз).
        return item

    stand = await stand_repo.get_by_id(db, item.stand_id)
    if stand is None:
        # Не должно случаться — FK RESTRICT не даёт снести стенд, пока на
        # него ссылается хоть один queue_item. Оставлено как защита от
        # рассинхрона данных, не рабочий путь.
        raise ServiceUnavailableError(
            error_code="TEST_STAND_MISSING",
            message="Test stand referenced by this queue item no longer exists",
        )

    if body.succeeded:
        stash_key = creds_stash.new_stash_key(item.id)
        await creds_stash.store_creds(stash_key, {
            "test_username": body.test_username,
            "test_password": body.test_password,
            "test_ssh_private_key": body.test_ssh_private_key,
        })
        item.state = QueueItemState.READY
        item.creds_stash_key = stash_key
        item.error = None
        await db.commit()
        if body.warning:
            logger.warning(
                "prepare-for-test succeeded with a warning for queue_item %s: %s",
                item.id, body.warning,
            )
        audit_service.emit(
            "queue_item.ready",
            target_id=item.id, target_type="queue_item",
            status="success", allowed=True,
            details={"prepare_request_id": prepare_request_id, "has_warning": bool(body.warning)},
        )
        return item

    await queue_orchestration_log.record(
        db, stand.id, QueueOrchestrationEventKind.PREPARE_REQUEST_FAILED, queue_item_id=item.id,
        detail=f"{body.failed_step or ''}: {body.error or 'prepare-for-test callback reported failure'}",
    )
    await _fail_and_advance(
        db, item, stand,
        failed_step=body.failed_step, error=body.error,
        audit_action="queue_item.prepare_failed",
        is_first_ever=False,
    )
    return item


async def get_active_queue_item(db: AsyncSession, stand_id: str) -> QueueItem | None:
    """Активный item очереди стенда, если есть (§8.6 — кнопка live-лога в консоли сервера)."""
    return await repo.get_active_for_stand(db, stand_id)


async def resolve_estimated_finish_at(db: AsyncSession, item: QueueItem) -> datetime | None:
    """Оценка, когда стенд освободится — `started_at + timeout_seconds` теста.

    Это worst-case по таймауту, не средняя историческая длительность (её
    сегодня никто не считает). `None`, если item ещё не стартовал или у его
    теста таймаут не задан (тогда `testing_worker` берёт свой дефолт, но
    testing_service о нём не знает — оценивать нечем).
    """
    if item.started_at is None:
        return None
    test = await test_definition_repo.get_by_id(db, item.test_id)
    if test is None or test.timeout_seconds is None:
        return None
    return item.started_at + timedelta(seconds=test.timeout_seconds)


_STARTER_SCRIPT_PATH = "/home/u/starter.sh"


async def _resolve_git_token(db: AsyncSession, department_id: str) -> str:
    """git-токен для `starter.sh` (клонирует ветку монорепо на стенде под `$2`).

    `starter.sh` подставляет `$2` целиком:
    `git -c http.extraHeader="Authorization: $2" clone ...` — значит секрет
    обязан быть готовым значением заголовка, СО СХЕМОЙ (`Bearer <PAT>`).
    Bitbucket REST в HR-отчёте (`services/bitbucket_client.py`) использует тот
    же секрет иначе — как пароль basic-auth, где схема в значении недопустима.
    Форматы несовместимы, поэтому у git-заголовка своя ссылка на credential
    (`git_credential_id`); `bitbucket_credential_id` остаётся фолбэком ради
    совместимости с отделами, настроенными до разделения.

    В отличие от `stp.py::_resolve_jira_bearer`/`run_summary.py::_resolve_confluence_bearer`
    (где отсутствие credential — частичный провал одного отчёта, `None`),
    здесь недоступность токена фатальна для самого прогона — без него
    `starter.sh` не сможет склонировать ветку. Поэтому функция поднимает
    `AppException`, а `claim_next` заворачивает её в тот же `_fail_and_advance`,
    что резолв dates-контента и адреса стенда.
    """
    settings = await dis_repo.get_by_department(db, department_id)
    credential_id = None
    if settings is not None:
        credential_id = settings.git_credential_id or settings.bitbucket_credential_id
    if not credential_id:
        raise DomainValidationError(
            error_code="GIT_CREDENTIAL_NOT_CONFIGURED",
            message=(
                "department_integration_settings.git_credential_id "
                "(fallback bitbucket_credential_id) is not configured"
            ),
            details={"department_id": department_id},
        )
    _login, token = await secret_client.reveal_credential(credential_id)
    if not token:
        raise ServiceUnavailableError(
            error_code="GIT_CREDENTIAL_EMPTY",
            message="reveal_credential returned an empty secret for the git credential",
            details={"department_id": department_id, "credential_id": credential_id},
        )
    return token


async def claim_next(db: AsyncSession) -> QueueClaimItem | None:
    """Атомарно забрать один `ready`-item (по любому стенду) для `testing_worker`.

    `testing_worker` дёргает этот путь каждые несколько секунд
    (`QUEUE_POLL_INTERVAL_SECONDS`), пока очередь пуста — это заодно и
    единственный регулярный "тик" на стороне testing_service, поэтому сюда
    же подвешены обе периодические диагностические проверки
    (`queue_orchestration_log`): head-item, зависший дольше разумного порога,
    и `ready`-item, который должен был уйти воркеру, но почему-то остаётся на
    месте. См. `services/queue_orchestration_log.py`.
    """
    await queue_orchestration_log.check_stuck_items(db)
    item = await repo.claim_next_ready(db)
    if item is None:
        await queue_orchestration_log.check_claim_desync(db)
        await db.commit()
        return None

    stand = await stand_repo.get_by_id(db, item.stand_id)
    test = await test_definition_repo.get_by_id(db, item.test_id)
    if stand is None or test is None:
        # См. аналогичный комментарий в `handle_prepare_completed` — FK
        # RESTRICT делает это недостижимым в норме.
        raise ServiceUnavailableError(
            error_code="QUEUE_ITEM_DATA_MISSING",
            message="Stand or test definition referenced by this queue item no longer exists",
        )

    if await _reject_unready_item(db, stand, item, is_first_ever=False):
        return None

    creds = None
    if item.creds_stash_key:
        creds = await creds_stash.pop_creds(item.creds_stash_key)
    if creds is None:
        await _fail_and_advance(
            db, item, stand,
            failed_step=None, error="creds stash missing or expired before claim",
            audit_action="queue_item.prepare_failed",
            is_first_ever=False,
        )
        return None

    item.state = QueueItemState.RUNNING
    item.started_at = datetime.now(timezone.utc)
    await db.commit()

    ctx = dict(item.launch_context or {})
    # Вызывающий присылает только RC/KERNEL/MODE — всё остальное легаси
    # собирало из уже известного прямо перед запуском. Считаем то же самое
    # здесь, где стенд наконец известен, и кладём в launch_context, откуда
    # значения возьмёт общий резолвер слотов (см. services/launch_context.py).
    # Наложение поверх ctx, а не под ним: вычисляемое поле не должно
    # подделываться постановщиком задания.
    ctx.update(launch_context_svc.computed_values(test, stand, ctx))
    busy_note = f"{test.code}|{ctx.get('RC', '')}|{ctx.get('KERNEL', '')}"
    try:
        await server_client.set_service_status(stand.server_id, busy_state="testing", busy_note=busy_note)
    except AppException as exc:
        # Best-effort: неудачная смена стадии не должна срывать уже
        # выданное задание — SSH-сессия не зависит от busy_note, это
        # информационная метка для UI/оператора.
        logger.warning("service-status(testing) failed for queue_item %s: %s", item.id, exc)

    try:
        dates_content = await resolve_dates_content(db, item.test_id, ctx)
        dates_content_masked = await resolve_dates_content_masked(db, item.test_id, ctx)
    except AppException as exc:
        await _fail_and_advance(
            db, item, stand,
            failed_step=None, error=f"resolve_dates_content failed: {exc.message}",
            audit_action="queue_item.prepare_failed",
            is_first_ever=False,
        )
        return None

    try:
        git_token = await _resolve_git_token(db, stand.department_id)
    except AppException as exc:
        await _fail_and_advance(
            db, item, stand,
            failed_step=None, error=f"git token resolution failed: {exc.message}",
            audit_action="queue_item.prepare_failed",
            is_first_ever=False,
        )
        return None

    dates_filename = f"dates_{item.id}.conf"
    command = [
        "sudo", "bash", _STARTER_SCRIPT_PATH,
        test.category or "", git_token, dates_filename, ctx.get("RC", ""), test.starter_suffix or "",
    ]
    command_masked = [
        "sudo", "bash", _STARTER_SCRIPT_PATH,
        test.category or "", "***", dates_filename, ctx.get("RC", ""), test.starter_suffix or "",
    ]

    try:
        connection = await server_client.get_connection_info(stand.server_id)
    except AppException as exc:
        await _fail_and_advance(
            db, item, stand,
            failed_step=None, error=f"connection-info failed: {exc.message}",
            audit_action="queue_item.prepare_failed",
            is_first_ever=False,
        )
        return None

    audit_service.emit(
        "queue_item.claimed",
        target_id=item.id, target_type="queue_item",
        status="success", allowed=True,
        details={"stand_id": stand.id, "test_id": test.id},
    )
    return QueueClaimItem(
        queue_item_id=item.id,
        host=connection["host"],
        test_username=creds.get("test_username"),
        test_password=creds.get("test_password"),
        test_ssh_private_key=creds.get("test_ssh_private_key"),
        command=command,
        command_masked=command_masked,
        dates_content=dates_content,
        dates_content_masked=dates_content_masked,
        dates_filename=dates_filename,
        command_timeout_seconds=test.timeout_seconds,
        debug_mode=item.debug_mode,
        is_retry=item.is_retry,
        prepare_only=item.prepare_only,
        starter_suffix=test.starter_suffix or "",
    )


async def complete_item(db: AsyncSession, queue_item_id: str, body: QueueCompletedRequest) -> QueueItem:
    """Исход SSH-исполнения от `testing_worker`."""
    item = await repo.get_by_id_for_update(db, queue_item_id)
    if item is None:
        raise NotFoundError(
            error_code="QUEUE_ITEM_NOT_FOUND",
            message="Queue item not found",
        )
    if item.state != QueueItemState.RUNNING:
        # Идемпотентный no-op — дубль уже обработанного completion'а.
        return item

    stand = await stand_repo.get_by_id(db, item.stand_id)
    if stand is None:
        raise ServiceUnavailableError(
            error_code="TEST_STAND_MISSING",
            message="Test stand referenced by this queue item no longer exists",
        )

    # Любой терминальный переход снимает заявку на прерывание: она могла
    # остаться, если тест успел закончиться сам, пока воркер её вычитывал.
    item.interrupt_action = None

    if body.interrupted is not None:
        await _complete_interrupted(db, item, stand, body.interrupted)
        return item

    if body.succeeded:
        # `prepare_only` — testenv-режим: SSH-сессия успешна (starter.sh
        # честно выполнил prepare.sh и вышел раньше run.py), но это не
        # результат теста, поэтому терминал отдельный от `succeeded`.
        item.state = QueueItemState.PREPARED if item.prepare_only else QueueItemState.SUCCEEDED
        item.finished_at = datetime.now(timezone.utc)
        item.error = None
        await db.commit()
        await stp_status.sync_cell_from_queue_item(db, item)
        if item.test_run_id:
            new_status = await test_run_status.recompute(db, item.test_run_id)
            await db.commit()
            await _maybe_post_run_summary(db, new_status, item.test_run_id)
        audit_service.emit(
            "queue_item.completed",
            target_id=item.id, target_type="queue_item",
            status="success", allowed=True,
            details={"exit_code": body.exit_code, "stand_id": stand.id, "prepare_only": item.prepare_only},
        )
        await _advance_stand_queue(db, stand)
        return item

    error = body.error or (
        f"SSH run failed with exit_code={body.exit_code}"
        if body.exit_code is not None else "SSH run failed"
    )
    await _fail_and_advance(
        db, item, stand,
        failed_step=None, error=error,
        audit_action="queue_item.completed",
        is_first_ever=False,
    )
    return item


async def _complete_interrupted(db: AsyncSession, item: QueueItem, stand, action: str) -> None:
    """Воркер отчитался, что оборвал SSH-сессию по заявке оператора.

    Исхода у теста нет, поэтому ни СТП/Zephyr (`stp_status`), ни retry-логика
    здесь не участвуют — это не провал теста, а снятие его с исполнения.
    """
    item.interrupt_action = None
    if action == QueueInterruptAction.PAUSE:
        item.state = QueueItemState.PAUSED
        await db.commit()
        audit_service.emit(
            "queue_item.paused",
            target_id=item.id, target_type="queue_item",
            status="success", allowed=True,
            details={"stand_id": stand.id, "from_state": QueueItemState.RUNNING},
        )
        # Стенд остаётся за нами и стоит до явного resume-queue — очередь не
        # продолжаем, бронь не снимаем.
        return

    item.state = QueueItemState.SKIPPED
    item.finished_at = datetime.now(timezone.utc)
    item.error = None
    await db.commit()
    if item.test_run_id:
        new_status = await test_run_status.recompute(db, item.test_run_id)
        await db.commit()
        await _maybe_post_run_summary(db, new_status, item.test_run_id)
    audit_service.emit(
        "queue_item.skipped",
        target_id=item.id, target_type="queue_item",
        status="success", allowed=True,
        details={"stand_id": stand.id, "from_state": QueueItemState.RUNNING},
    )
    await _advance_stand_queue(db, stand)


async def get_interrupt_action(db: AsyncSession, queue_item_id: str) -> str | None:
    """Заявка на прерывание для `testing_worker` — обычный SELECT, без лока."""
    item = await repo.get_by_id(db, queue_item_id)
    if item is None:
        return None
    return item.interrupt_action


async def request_interrupt(db: AsyncSession, item: QueueItem, stand, action: str) -> QueueItem:
    """Пропустить (`skip`) либо остановить (`pause`) элемент очереди.

    Пока элемент реально исполняется на стенде (`running`), оборвать его может
    только `testing_worker` — здесь остаётся заявка в `interrupt_action`,
    которую он заберёт следующим `interrupt-check`, и элемент возвращается как
    есть (всё ещё `running`). Во всех остальных активных состояниях никакой
    SSH-сессии ещё нет, прерывать физически нечего — переход происходит сразу.
    """
    if item.state not in ACTIVE_QUEUE_STATES:
        raise ConflictError(
            error_code="QUEUE_ITEM_NOT_ACTIVE",
            message="Элемент очереди уже завершён",
            details={"state": item.state},
        )

    if item.state == QueueItemState.RUNNING:
        item.interrupt_action = action
        await db.commit()
        await db.refresh(item)
        audit_service.emit(
            "queue_item.interrupt_requested",
            target_id=item.id, target_type="queue_item",
            status="success", allowed=True,
            details={"stand_id": stand.id, "action": action},
        )
        return item

    from_state = item.state
    # Стенд мог уже начать готовиться под этот элемент — креды, если их успели
    # застэшить, дальше не нужны ни пропущенному, ни поставленному на паузу
    # (пауза после resume проходит подготовку заново).
    if item.creds_stash_key:
        await creds_stash.pop_creds(item.creds_stash_key)
        item.creds_stash_key = None

    if action == QueueInterruptAction.PAUSE:
        item.state = QueueItemState.PAUSED
        await db.commit()
        await db.refresh(item)
        audit_service.emit(
            "queue_item.paused",
            target_id=item.id, target_type="queue_item",
            status="success", allowed=True,
            details={"stand_id": stand.id, "from_state": from_state},
        )
        return item

    item.state = QueueItemState.SKIPPED
    item.finished_at = datetime.now(timezone.utc)
    item.error = None
    await db.commit()
    if item.test_run_id:
        new_status = await test_run_status.recompute(db, item.test_run_id)
        await db.commit()
        await _maybe_post_run_summary(db, new_status, item.test_run_id)
    audit_service.emit(
        "queue_item.skipped",
        target_id=item.id, target_type="queue_item",
        status="success", allowed=True,
        details={"stand_id": stand.id, "from_state": from_state},
    )
    # Пропустить можно и не головной элемент очереди — тогда цикл стенда занят
    # кем-то другим, и трогать его нельзя: второй prepare-for-test на тот же
    # стенд сломал бы подготовку, а пустая очередь ошибочно сняла бы бронь
    # из-под ещё идущего теста.
    if not await repo.has_in_flight_for_stand(db, stand.id):
        await _advance_stand_queue(db, stand)
    await db.refresh(item)
    return item


async def resume_stand_queue(db: AsyncSession, stand) -> QueueItem:
    """Вернуть поставленный на паузу элемент в конец очереди стенда и поехать дальше."""
    item = await repo.get_paused_for_stand(db, stand.id)
    if item is None:
        raise ConflictError(
            error_code="STAND_NOT_PAUSED",
            message="У стенда нет остановленного элемента очереди",
            details={"stand_id": stand.id},
        )

    item.state = QueueItemState.QUEUED
    item.position = await repo.next_position_for_stand(db, stand.id)
    item.started_at = None
    item.error = None
    await db.commit()
    audit_service.emit(
        "queue_item.resumed",
        target_id=item.id, target_type="queue_item",
        status="success", allowed=True,
        details={"stand_id": stand.id, "position": item.position},
    )
    if not await repo.has_in_flight_for_stand(db, stand.id):
        await _advance_stand_queue(db, stand)
    await db.refresh(item)
    return item


async def clear_queue(db: AsyncSession, stand) -> int:
    """Убрать из очереди стенда всё, что ещё ни разу не бралось в работу.

    Трогает только `queued` — по определению это item, до которого цикл
    стенда ещё не дошёл (стенд занят головным item'ом этой же очереди или
    его нет вовсе, но тогда очередь и так пуста). Активный/приостановленный/
    исполняющийся item эта операция не задевает — за ним бронь стенда, снять
    его можно только по одному через skip/pause/delete. Строки удаляются
    насовсем, не переводятся в терминал — это и есть отличие «очистить» от
    массового skip.
    """
    items = await repo.list_queued_for_stand(db, stand.id)
    if not items:
        return 0
    affected_runs = {item.test_run_id for item in items if item.test_run_id}
    for item in items:
        await repo.delete(db, item)
    await db.commit()
    audit_service.emit(
        "test_stand.queue_cleared",
        target_id=stand.id, target_type="test_stand",
        status="success", allowed=True,
        details={"stand_id": stand.id, "count": len(items)},
    )
    for run_id in affected_runs:
        await test_run_status.recompute(db, run_id)
    if affected_runs:
        await db.commit()
    return len(items)


async def retry_failed(db: AsyncSession, identity: Identity, stand) -> tuple[list[QueueItem], int]:
    """Повторить одним вызовом все ещё не перезапущенные упавшие item'ы стенда.

    Каждый кандидат проверяется и заводится независимо — тест, ушедший в
    другой отдел, снятый со СТП, или уже перезапущенный кем-то ещё, просто
    пропускается. Так массовый retry не срывается целиком из-за одного
    проблемного item'а, в отличие от одиночного `public_queue.retry()`,
    который для того же случая честно возвращает 4xx одному вызывающему.
    """
    candidates = await repo.list_failed_for_stand(db, stand.id)
    retried: list[QueueItem] = []
    skipped = 0
    for candidate in candidates:
        source = await repo.get_by_id_for_update(db, candidate.id)
        if source is None or source.state != QueueItemState.FAILED or await repo.has_successor(db, source.id):
            skipped += 1
            continue
        if source.test_run_id:
            run = await db.get(TestRun, source.test_run_id)
            if not run or run.department_id != identity.department_id:
                skipped += 1
                continue
        test = await test_definition_repo.get_by_id(db, source.test_id, for_update=True)
        if not test or (test.department_id and test.department_id != stand.department_id):
            skipped += 1
            continue
        try:
            stp = (
                None
                if source.debug_mode
                else await launch_stp.require_membership(
                    db, test.code, stand.id, source.launch_context, source.stp_test_run_id,
                )
            )
        except AppException:
            skipped += 1
            continue
        item = await enqueue(
            db, identity, source.test_id,
            launch_context=dict(source.launch_context),
            debug_mode=source.debug_mode,
            prepare_only=source.prepare_only,
            stand_id=source.stand_id,
            test_run_id=source.test_run_id,
            test_run_entry_id=source.test_run_entry_id,
            retry_source=source,
            stp_test_run_id=stp.id if stp else None,
        )
        retried.append(item)
    audit_service.emit(
        "test_stand.queue_retry_failed",
        target_id=stand.id, target_type="test_stand",
        status="success", allowed=True,
        details={"stand_id": stand.id, "retried_count": len(retried), "skipped_count": skipped},
    )
    return retried, skipped


async def delete_item(db: AsyncSession, item: QueueItem, stand) -> None:
    """Убрать элемент очереди насовсем — не путать со `skip`.

    `skip` оставляет терминальную запись (`skipped`), эта операция строку
    целиком удаляет. Годится для ещё не начавших исполняться (`queued`) и
    уже терминальных (`succeeded`/`failed`/`skipped`) item'ов, а также для
    приостановленного (`paused`) — но не для того, что прямо сейчас занимает
    стенд физической работой (`preparing`/`ready`/`running`): там сначала
    нужен `skip`/`pause` (или дождаться завершения), иначе `testing_worker`
    рано или поздно обратится за уже не существующим item'ом, а бронь стенда
    повиснет без владельца.
    """
    if item.state in IN_FLIGHT_QUEUE_STATES:
        raise ConflictError(
            error_code="QUEUE_ITEM_IN_PROGRESS",
            message="Элемент сейчас занимает стенд — сначала остановите его (skip/pause), потом удалите",
            details={"state": item.state},
        )

    item_id = item.id
    from_state = item.state
    test_run_id = item.test_run_id
    was_active = item.state in ACTIVE_QUEUE_STATES
    if item.creds_stash_key:
        await creds_stash.pop_creds(item.creds_stash_key)
        item.creds_stash_key = None
    await repo.delete(db, item)
    await db.commit()
    audit_service.emit(
        "queue_item.delete",
        target_id=item_id, target_type="queue_item",
        status="success", allowed=True,
        details={"stand_id": stand.id, "from_state": from_state},
    )
    if test_run_id:
        await test_run_status.recompute(db, test_run_id)
        await db.commit()
    # Тот же приём, что у `skip`/`resume`: только когда именно этот item
    # держал очередь стенда (был головным `queued`/`paused`), убрав его,
    # нужно поехать дальше или снять бронь. Если он был не головным (просто
    # ждал в хвосте позади активного item'а), стенд эту потерю не заметит.
    if was_active and not await repo.has_in_flight_for_stand(db, stand.id):
        await _advance_stand_queue(db, stand)
