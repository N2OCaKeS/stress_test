"""Прогоны — fleet-wide кампании (§2.4, §6.1 плана миграции).

`create_test_run()` разворачивает один запрос в независимые постановки в
очередь по каждому стенду пула: для каждого `stand_id` из `test_run_stands`
берутся все `test_definitions` с `pinned_stand_id == stand_id` (тест
"принадлежит" ровно одному стенду по конструкции §2.2/§5.5) и каждый ставится
в очередь через уже существующий `queue.enqueue()`. Между стендами — никакой
общей транзакции: стенд без закреплённых тестов или провал постановки одного
конкретного теста не должны рушить остальную кампанию (§5.5 "между стендами —
параллельно", ошибка не глушит очередь стенда — тот же принцип применяется и
здесь на уровень выше, между стендами кампании).

`RC` в `launch_context` — тот же `os_version_id`, что передан в запросе, без
дополнительного резолва в человекочитаемый номер релиза: `services/queue.py`
уже сегодня прокидывает `launch_context["RC"]` как есть в `os_version_id`
параметр `server_client.start_prepare_for_test()` (см. `_start_or_continue_
cycle`), то есть RC и os_version_id в этой кодовой базе — одно и то же поле
под двумя именами. Кампания просто следует уже существующему соглашению.

СТП-гейт (`launch_stp.require_membership`) применяется к каждому тесту
кампании только при `final=True` — обычный/пробный прогон пула по-прежнему
не требует предварительно опубликованного состава СТП, финальный
(официальный, релизный) обязан ему соответствовать, как и одиночный запуск
вне debug-режима.
"""

from __future__ import annotations

import hashlib
import json
import logging
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import Action, EntityType, TestReadiness, TestRunStatus
from src.core.exceptions import AppException, AuthorizationError, ConflictError, DomainValidationError, NotFoundError
from src.dependencies.auth import Identity
from src.models import TestRun, TestRunEntry
from src.repositories import queue_item as queue_item_repo
from src.repositories import test_definition as test_definition_repo
from src.repositories import test_run as repo
from src.repositories import test_run_entry as test_run_entry_repo
from src.repositories import test_stand as test_stand_repo
from src.schemas.test_run import TestRunPartialError, TestRunPreviewEntry
from src.services import audit_service, launch_stp, permissions, queue as queue_svc, test_run_status
from src.utils.ids import test_run_id as new_id

logger = logging.getLogger(__name__)


def _replay_result(run: TestRun, entries: list[TestRunEntry]) -> tuple[TestRun, list[str], list[TestRunPartialError]]:
    populated_stands = {entry.stand_id for entry in entries}
    stands_without_tests = [stand_id for stand_id in run.test_run_stands if stand_id not in populated_stands]
    enqueue_errors = [
        TestRunPartialError(
            stand_id=entry.stand_id, test_id=entry.test_id,
            error_code=entry.enqueue_error_code, message=entry.enqueue_error,
        )
        for entry in entries
        if entry.enqueue_error_code
    ]
    return run, stands_without_tests, enqueue_errors


async def create_test_run(
    db: AsyncSession,
    identity: Identity,
    *,
    os_version_id: str,
    mode: str,
    kernel: str | None,
    test_run_stands: list[str],
    final: bool = False,
    request_id: str | None = None,
) -> tuple[TestRun, list[str], list[TestRunPartialError]]:
    """Завести кампанию + поставить в очередь все закреплённые тесты каждого стенда пула.

    Возвращает `(test_run, stands_without_tests, enqueue_errors)` — оба
    списка могут быть непустыми одновременно с успешно созданной кампанией:
    частичные провалы не откатывают уже поставленные в очередь стенды.

    `request_id` — повтор с тем же значением и тем же телом возвращает уже
    созданную кампанию без повторной постановки в очередь; с другим телом —
    `ConflictError`, как у `public_queue.py::request()`.
    """
    try:
        await permissions.require_action(db, identity, EntityType.TEST_RUN, Action.CREATE)
    except AuthorizationError:
        audit_service.emit(
            "test_run.create",
            target_type="test_run",
            status="denied", allowed=False,
            details={"reason": "permission_denied"},
        )
        raise

    department_id = identity.department_id
    if not department_id:
        raise DomainValidationError(
            error_code="TEST_RUN_DEPARTMENT_REQUIRED",
            message="Caller has no department_id to attribute this test run to",
        )

    fingerprint = None
    if request_id:
        payload = {
            "os_version_id": os_version_id, "mode": mode, "kernel": kernel,
            "test_run_stands": sorted(dict.fromkeys(test_run_stands)), "final": final,
        }
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        await repo.lock_request(db, identity.user_id, request_id)
        existing = await repo.find_request(db, identity.user_id, request_id)
        if existing:
            if existing.request_fingerprint != fingerprint:
                raise ConflictError(
                    error_code="REQUEST_ID_CONFLICT",
                    message="Этот идентификатор запроса уже использован с другими параметрами",
                )
            entries = await test_run_entry_repo.list_for_run(db, existing.id)
            return _replay_result(existing, entries)

    from src.services import server_client
    if kernel:
        kernels = [kernel]
    else:
        version = await server_client.get_os_version(os_version_id)
        kernels = list(dict.fromkeys(version.get("kernels") or []))
        if not kernels:
            kernels = await server_client.resolve_os_kernels(os_version_id)
    kernel = kernels[0]
    test_run_stands = list(dict.fromkeys(test_run_stands))
    run = await repo.create(db, {
        "id": new_id(),
        "os_version_id": os_version_id,
        "mode": mode,
        "kernel": kernel,
        "kernels": kernels,
        "department_id": department_id,
        "test_run_stands": list(test_run_stands),
        "status": TestRunStatus.QUEUED,
        "final": final,
        "composition_source": "pinned_catalog",
        "created_by": identity.user_id,
        "client_request_id": request_id,
        "request_fingerprint": fingerprint,
    })
    tests = await test_definition_repo.list_by_pinned_stands(db, test_run_stands)
    entries = [TestRunEntry(
        id=f"entry_{uuid4().hex}", test_run_id=run.id, stand_id=test.pinned_stand_id,
        test_id=test.id, test_code=test.code, test_name=test.full_name, kernel=selected_kernel,
    ) for selected_kernel in kernels for test in tests]
    db.add_all(entries)
    await db.commit()
    await db.refresh(run)

    populated_stands = {entry.stand_id for entry in entries}
    stands_without_tests = [stand_id for stand_id in test_run_stands if stand_id not in populated_stands]
    enqueue_errors: list[TestRunPartialError] = []
    for entry in entries:
        try:
            ctx = {"RC": os_version_id, "KERNEL": entry.kernel, "MODE": mode}
            stp = await launch_stp.require_membership(db, entry.test_code, entry.stand_id, ctx) if final else None
            await queue_svc.enqueue(
                db, identity, entry.test_id, launch_context=ctx,
                debug_mode=False, test_run_id=run.id, test_run_entry_id=entry.id,
                stp_test_run_id=stp.id if stp else None,
            )
        except AppException as exc:
            logger.warning("test_run %s: enqueue failed for stand=%s test=%s: %s", run.id, entry.stand_id, entry.test_id, exc.message)
            entry.enqueue_error_code = exc.error_code
            entry.enqueue_error = exc.message[:2048]
            await db.commit()
            enqueue_errors.append(TestRunPartialError(
                stand_id=entry.stand_id, test_id=entry.test_id,
                error_code=exc.error_code, message=exc.message,
            ))

    new_status = await test_run_status.recompute(db, run.id, emit_audit=False)
    await db.commit()
    await db.refresh(run)

    audit_service.emit(
        "test_run.create",
        target_id=run.id, target_type="test_run",
        status="success", allowed=True,
        details={
            "department_id": department_id,
            "os_version_id": os_version_id,
            "mode": mode,
            "kernel": kernel,
            "stand_count": len(test_run_stands),
            "stands_without_tests": stands_without_tests,
            "enqueue_error_count": len(enqueue_errors),
            "status": new_status,
            "final": final,
        },
    )
    return run, stands_without_tests, enqueue_errors


async def preview_test_run(
    db: AsyncSession,
    identity: Identity,
    *,
    os_version_id: str,
    mode: str,
    kernel: str | None,
    test_run_stands: list[str],
    final: bool = False,
) -> tuple[list[str], list[TestRunPreviewEntry]]:
    """Состав кампании без побочных эффектов — что будет запущено/пропущено и почему (§E4).

    Проверяет ровно те причины пропуска, которые `create_test_run` умеет
    обрабатывать частично (readiness, активность стенда, СТП при `final`) —
    без создания `test_run`/`test_run_entry` и без постановки в очередь.
    """
    await permissions.require_action(db, identity, EntityType.TEST_RUN, Action.CREATE)

    if not identity.department_id:
        raise DomainValidationError(
            error_code="TEST_RUN_DEPARTMENT_REQUIRED",
            message="Caller has no department_id to attribute this test run to",
        )

    from src.services import server_client
    if kernel:
        kernels = [kernel]
    else:
        version = await server_client.get_os_version(os_version_id)
        kernels = list(dict.fromkeys(version.get("kernels") or []))
        if not kernels:
            kernels = await server_client.resolve_os_kernels(os_version_id)

    test_run_stands = list(dict.fromkeys(test_run_stands))
    tests = await test_definition_repo.list_by_pinned_stands(db, test_run_stands)
    populated_stands = {test.pinned_stand_id for test in tests}
    stands_without_tests = [stand_id for stand_id in test_run_stands if stand_id not in populated_stands]

    stands: dict[str, object] = {}
    entries: list[TestRunPreviewEntry] = []
    for selected_kernel in kernels:
        for test in tests:
            stand = stands.get(test.pinned_stand_id)
            if stand is None and test.pinned_stand_id not in stands:
                stand = await test_stand_repo.get_by_id(db, test.pinned_stand_id)
                stands[test.pinned_stand_id] = stand

            common = {
                "stand_id": test.pinned_stand_id, "test_id": test.id, "test_code": test.code,
                "test_name": test.full_name, "kernel": selected_kernel,
            }
            if test.readiness != TestReadiness.READY:
                entries.append(TestRunPreviewEntry(
                    **common, action="skip_debug_required",
                    reason="Тест не в статусе «Рабочий» — обычный запуск недоступен",
                ))
                continue
            if stand is None or not stand.is_active:
                entries.append(TestRunPreviewEntry(
                    **common, action="skip_stand_inactive", reason="Стенд не активен",
                ))
                continue
            if final:
                ctx = {"RC": os_version_id, "KERNEL": selected_kernel, "MODE": mode}
                stp = await launch_stp.find_membership(db, test.code, test.pinned_stand_id, ctx)
                if stp is None:
                    entries.append(TestRunPreviewEntry(
                        **common, action="skip_not_in_stp",
                        reason="Тест отсутствует в активном составе СТП",
                    ))
                    continue
            entries.append(TestRunPreviewEntry(**common, action="launch"))

    return stands_without_tests, entries


async def get_test_run(db: AsyncSession, run_id: str) -> tuple[TestRun, list]:
    """Карточка кампании + все её дочерние queue_items (§6.1 — обзор кампании)."""
    run = await repo.get_by_id(db, run_id)
    if run is None:
        raise NotFoundError(
            error_code="TEST_RUN_NOT_FOUND",
            message="Test run not found",
        )
    items = await queue_item_repo.list_by_test_run_id(db, run_id)
    return run, items


async def list_test_runs(
    db: AsyncSession,
    limit: int,
    offset: int,
    *,
    department_id: str | None = None,
    status: str | None = None,
    final: bool | None = None,
) -> tuple[list[TestRun], int]:
    """List + count кампаний под фильтрами. Открыто любому аутентифицированному актору."""
    items = await repo.list_all(
        db, limit=limit, offset=offset, department_id=department_id, status=status, final=final,
    )
    total = await repo.count_all(db, department_id=department_id, status=status, final=final)
    return items, total
