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
"""

from __future__ import annotations

import logging
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import Action, EntityType, TestRunStatus
from src.core.exceptions import AppException, AuthorizationError, DomainValidationError, NotFoundError
from src.dependencies.auth import Identity
from src.models import TestRun, TestRunEntry
from src.repositories import queue_item as queue_item_repo
from src.repositories import test_definition as test_definition_repo
from src.repositories import test_run as repo
from src.schemas.test_run import TestRunPartialError
from src.services import audit_service, permissions, queue as queue_svc, test_run_status
from src.utils.ids import test_run_id as new_id

logger = logging.getLogger(__name__)


async def create_test_run(
    db: AsyncSession,
    identity: Identity,
    *,
    os_version_id: str,
    mode: str,
    kernel: str,
    test_run_stands: list[str],
    final: bool = False,
) -> tuple[TestRun, list[str], list[TestRunPartialError]]:
    """Завести кампанию + поставить в очередь все закреплённые тесты каждого стенда пула.

    Возвращает `(test_run, stands_without_tests, enqueue_errors)` — оба
    списка могут быть непустыми одновременно с успешно созданной кампанией:
    частичные провалы не откатывают уже поставленные в очередь стенды.
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

    test_run_stands = list(dict.fromkeys(test_run_stands))
    run = await repo.create(db, {
        "id": new_id(),
        "os_version_id": os_version_id,
        "mode": mode,
        "kernel": kernel,
        "department_id": department_id,
        "test_run_stands": list(test_run_stands),
        "status": TestRunStatus.QUEUED,
        "final": final,
        "composition_source": "pinned_catalog",
        "created_by": identity.user_id,
    })
    tests = await test_definition_repo.list_by_pinned_stands(db, test_run_stands)
    entries = [TestRunEntry(
        id=f"entry_{uuid4().hex}", test_run_id=run.id, stand_id=test.pinned_stand_id,
        test_id=test.id, test_code=test.code, test_name=test.full_name,
    ) for test in tests]
    db.add_all(entries)
    await db.commit()
    await db.refresh(run)

    launch_context = {"RC": os_version_id, "KERNEL": kernel, "MODE": mode}
    populated_stands = {entry.stand_id for entry in entries}
    stands_without_tests = [stand_id for stand_id in test_run_stands if stand_id not in populated_stands]
    enqueue_errors: list[TestRunPartialError] = []
    for entry in entries:
        try:
            await queue_svc.enqueue(
                db, identity, entry.test_id, launch_context=launch_context,
                debug_mode=False, test_run_id=run.id, test_run_entry_id=entry.id,
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
