"""Добавление одного существующего теста EMM в конкретный СТП test-run (§D6/D7 плана миграции).

Узкий, per-test аналог `services/stp.py::generate_stp_runs` — тот bulk-путь
переключает состав целого отдела/РЦ, этот обслуживает ручной случай QA-лида
«вот этот один тест ещё не в этом прогоне, добавь». Шаги:

1. Найти или завести `stp_test_case` (локальную карточку + Zephyr testcase,
   если у карточки ещё нет `zephyr_id`) — "не создавать повторный testcase,
   если связь уже существует" (§D6) означает именно это: если
   `stp_test_case_repo.get_by_code` уже нашёл строку с непустым `zephyr_id`,
   Zephyr не дёргаем вовсе.
2. Добавить testcase в Zephyr test-run (`zephyr_client.add_test_cases_to_run`),
   если он ещё не там — тот же метод, что использует переключение состава
   changelog→full в `services/stp.py`.
3. Завести локальную ячейку `stp_cells` для пары (case, run).
4. Переопубликовать СТП-матрицу (`services/stp_matrix.py::publish_stp_matrix`)
   — она сама перечитывает все ячейки прогона из БД, так что шага 3
   достаточно, чтобы новый тест попал в таблицу; здесь только триггерим
   republish.

Состояние операции — `models.StpAddTestOperation`, одна строка на пару
`(test_definition_id, stp_test_run_id)`. Каждый шаг помечается пройденным
СРАЗУ после успеха (отдельный commit), поэтому провал на шаге N не теряет
шаги 1..N-1: повторный вызов `add_test_to_stp` находит существующую строку
и продолжает с первого `False`-флага. Шаг 4 умышленно не considered a hard
failure только на сетевых/конфигурационных сбоях Confluence — легитимные
skip-статусы `publish_stp_matrix` (не настроено, нет прогонов) считаются
пройденным шагом: republish в принципе невозможен/бессмысленен для этого
отдела, и без него сама операция ЕСТЬ выполнена целиком с точки зрения
Zephyr+локальной СТП. Провалившийся именно `failed`-статус публикации —
единственный случай, когда шаг 4 остаётся непройденным и общий статус
операции не становится `succeeded` (§D7: "частичный успех в Zephyr при
сбое life не выдаётся за завершённую синхронизацию").
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import Action, EntityType, StpAddTestOperationStatus, StpCellStatus, StpMatrixPublicationStatus
from src.core.exceptions import AppException, NotFoundError
from src.dependencies.auth import Identity
from src.models import StpAddTestOperation
from src.repositories import department_integration_settings as dis_repo
from src.repositories import stp_add_test_operation as op_repo
from src.repositories import stp_cell as stp_cell_repo
from src.repositories import stp_test_case as stp_test_case_repo
from src.repositories import stp_test_run as stp_test_run_repo
from src.repositories import test_definition as test_definition_repo
from src.repositories import test_stand as test_stand_repo
from src.services import audit_service, permissions, secret_client, stp_matrix, zephyr_client
from src.services.zephyr_client import ZephyrRunItem
from src.utils.ids import stp_add_test_operation_id as new_op_id
from src.utils.ids import stp_cell_id as new_cell_id
from src.utils.ids import stp_test_case_id as new_case_id

logger = logging.getLogger(__name__)


async def _resolve_jira_ctx(db: AsyncSession, department_id: str) -> tuple[str, str] | None:
    """`(jira_base_url, bearer_token)` отдела, либо `None` — не настроено/недоступно.

    Дублирует `services/stp.py::_resolve_jira_bearer` (тот модуль его не
    экспортирует, а перекраивать чужую зону ради общего хелпера — вне этой
    задачи) — тот же самый источник и то же самое правило: отсутствующие
    настройки и провал reveal трактуются одинаково, как "шаг недоступен".
    """
    settings = await dis_repo.get_by_department(db, department_id)
    if settings is None or not settings.credential_id or not settings.jira_base_url:
        return None
    try:
        _login, secret = await secret_client.reveal_credential(settings.credential_id)
    except AppException as exc:
        logger.warning(
            "stp_add_test: reveal_credential failed dept=%s cred=%s: %s",
            department_id, settings.credential_id, exc.message,
        )
        return None
    if not secret:
        return None
    return settings.jira_base_url, secret


async def _fail(db: AsyncSession, op: StpAddTestOperation, message: str) -> StpAddTestOperation:
    op = await op_repo.update(db, op, {
        "status": StpAddTestOperationStatus.FAILED, "last_error": message[:2000],
    })
    await db.commit()
    await db.refresh(op)
    return op


async def add_test_to_stp(
    db: AsyncSession, identity: Identity, *, test_id: str, stp_test_run_id: str,
) -> StpAddTestOperation:
    """Добавить тест `test_id` в СТП test-run `stp_test_run_id`, шаг за шагом, идемпотентно.

    Гейт — тот же, что и у `/stp/generate`: `(stp_test_run, *, create)`, эта
    операция концептуально то же самое действие в узком масштабе одного
    теста.
    """
    await permissions.require_action(db, identity, EntityType.STP_TEST_RUN, Action.CREATE)

    test = await test_definition_repo.get_by_id(db, test_id)
    if test is None:
        raise NotFoundError(error_code="TEST_DEFINITION_NOT_FOUND", message="Test definition not found")

    run = await stp_test_run_repo.get_by_id(db, stp_test_run_id)
    if run is None:
        raise NotFoundError(error_code="STP_TEST_RUN_NOT_FOUND", message="Stp test run not found")

    stand = await test_stand_repo.get_by_id(db, run.stand_id)
    department_id = stand.department_id if stand is not None else identity.department_id

    op = await op_repo.get_by_test_and_run(db, test_definition_id=test.id, stp_test_run_id=run.id)
    if op is None:
        op = await op_repo.create(db, {
            "id": new_op_id(),
            "department_id": department_id,
            "test_definition_id": test.id,
            "stp_test_run_id": run.id,
            "created_by": identity.user_id,
        })
        await db.commit()
        await db.refresh(op)

    if op.status == StpAddTestOperationStatus.SUCCEEDED:
        # Уже полностью синхронизировано прошлым вызовом — чистый no-op,
        # ни Zephyr, ни Confluence второй раз не дёргаем.
        return op

    # ── Шаг 1: testcase в локальном каталоге + Zephyr ───────────────────────
    if not op.zephyr_testcase_created:
        case = await stp_test_case_repo.get_by_code(db, test.code)
        if case is None or not case.zephyr_id:
            jira_ctx = await _resolve_jira_ctx(db, department_id)
            if jira_ctx is None:
                return await _fail(
                    db, op,
                    "JIRA_INTEGRATION_NOT_AVAILABLE: department_integration_settings not configured or credential reveal failed",
                )
            base_url, bearer_token = jira_ctx
            owner_key = None
            if test.owner:
                owner_key = await zephyr_client.resolve_user_key(
                    base_url=base_url, bearer_token=bearer_token, username=test.owner,
                )
            folder = f"/stress_test/{test.category}" if test.category else "/stress_test"
            try:
                zephyr_key = await zephyr_client.create_test_case(
                    base_url=base_url, bearer_token=bearer_token,
                    name=test.full_name, folder=folder, owner_key=owner_key,
                )
            except AppException as exc:
                return await _fail(db, op, f"{exc.error_code}: {exc.message}")

            if case is None:
                case = await stp_test_case_repo.create(db, {
                    "id": new_case_id(), "code": test.code, "title": test.full_name,
                    "zephyr_id": zephyr_key, "department_id": department_id,
                    "created_by": identity.user_id,
                })
            else:
                case = await stp_test_case_repo.update(db, case, {"zephyr_id": zephyr_key})
            await db.commit()
            await db.refresh(case)

        op = await op_repo.update(db, op, {
            "zephyr_testcase_created": True, "stp_test_case_id": case.id,
        })
        await db.commit()
        await db.refresh(op)
    else:
        case = None
        if op.stp_test_case_id:
            case = await stp_test_case_repo.get_by_id(db, op.stp_test_case_id)
        if case is None:
            case = await stp_test_case_repo.get_by_code(db, test.code)
        if case is None:
            return await _fail(db, op, "stp_test_case disappeared after step 1 had already completed")

    # ── Шаг 2: testcase в Zephyr test-run ───────────────────────────────────
    existing_cell = await stp_cell_repo.get_by_case_and_run(
        db, stp_test_case_id=case.id, stp_test_run_id=run.id,
    )
    if not op.zephyr_added_to_run:
        if existing_cell is not None:
            # Уже привязан — например, попал в этот ран более ранним bulk
            # /stp/generate. Ничего добавлять не нужно, просто отмечаем шаг.
            op = await op_repo.update(db, op, {"zephyr_added_to_run": True})
        else:
            if not run.zephyr_test_run_key:
                return await _fail(
                    db, op,
                    "ZEPHYR_TEST_RUN_KEY_MISSING: stp_test_run has no zephyr_test_run_key",
                )
            jira_ctx = await _resolve_jira_ctx(db, department_id)
            if jira_ctx is None:
                return await _fail(
                    db, op,
                    "JIRA_INTEGRATION_NOT_AVAILABLE: department_integration_settings not configured or credential reveal failed",
                )
            base_url, bearer_token = jira_ctx
            assignee_key = None
            if test.owner:
                assignee_key = await zephyr_client.resolve_user_key(
                    base_url=base_url, bearer_token=bearer_token, username=test.owner,
                )
            item = ZephyrRunItem(
                test_case_key=case.zephyr_id, environment=run.kernel, assigned_to_key=assignee_key,
            )
            try:
                await zephyr_client.add_test_cases_to_run(
                    base_url=base_url, bearer_token=bearer_token,
                    test_run_key=run.zephyr_test_run_key, items=[item],
                )
            except AppException as exc:
                return await _fail(db, op, f"{exc.error_code}: {exc.message}")
            op = await op_repo.update(db, op, {"zephyr_added_to_run": True})
        await db.commit()
        await db.refresh(op)

    # ── Шаг 3: локальная ячейка ──────────────────────────────────────────────
    if not op.stp_cell_created:
        cell = existing_cell or await stp_cell_repo.get_by_case_and_run(
            db, stp_test_case_id=case.id, stp_test_run_id=run.id,
        )
        if cell is None:
            cell = await stp_cell_repo.create(db, {
                "id": new_cell_id(), "stp_test_case_id": case.id, "stp_test_run_id": run.id,
                "status": StpCellStatus.NOT_RUN, "is_active": True,
            })
            await db.commit()
            await db.refresh(cell)
        op = await op_repo.update(db, op, {"stp_cell_created": True, "stp_cell_id": cell.id})
        await db.commit()
        await db.refresh(op)

    # ── Шаг 4: republish СТП-матрицы (Confluence/life) ──────────────────────
    if not op.life_published:
        try:
            publication = await stp_matrix.publish_stp_matrix(
                db, identity, department_id=department_id, os_version_id=run.os_version_id,
            )
        except AppException as exc:
            return await _fail(
                db, op, f"life publish error: {getattr(exc, 'error_code', type(exc).__name__)}: {exc.message}",
            )
        if publication.status == StpMatrixPublicationStatus.FAILED:
            return await _fail(db, op, f"life publish failed: {publication.error or 'unknown error'}")
        # posted / skipped_not_configured / skipped_no_test_runs — все три
        # терминальны для этого шага, только сам FAILED блокирует succeeded.
        op = await op_repo.update(db, op, {"life_published": True})
        await db.commit()
        await db.refresh(op)

    op = await op_repo.update(db, op, {
        "status": StpAddTestOperationStatus.SUCCEEDED, "last_error": None,
    })
    await db.commit()
    await db.refresh(op)

    audit_service.emit(
        "stp_add_test_operation.complete",
        target_id=op.id, target_type="stp_add_test_operation",
        status="success", allowed=True,
        details={
            "department_id": department_id, "test_definition_id": test.id,
            "stp_test_run_id": run.id,
        },
    )
    return op
