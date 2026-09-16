"""«Pull СТП из life» (§D8 плана миграции): прочитать существующие Zephyr test-run'ы
отдела и импортировать/сверить их с EMM.

Направление обратное `services/stp.py`/`services/stp_add_test.py` — те ПИШУТ в
Zephyr, этот модуль только ЧИТАЕТ (`search_test_runs`/`get_test_run`) и пишет
исключительно в локальную БД. Ничего не публикуется обратно в life — ни здесь,
ни где-то ещё в этом модуле.

Ключевой факт, на котором строится сопоставление: EMM сама называет свои
Zephyr test-run'ы `f"{os_version_id}_{mode}_{kernel}_{stand_id}"`
(`services/stp.py::_create_stand_run`), и легаси `allta_app` (`libs/zefir.py`,
функция `dates()`) парсит имена ранее заведённых прогонов ТОЙ ЖЕ схемой:
`name.replace('_', ' ').split(' ')`, token[0..3] = версия/режим/ядро/стенд.
Значит любой найденный в папке test-run с именем такой формы — свой ли,
легаси ли, заведённый руками — восстанавливается в структурированный контекст
одним и тем же способом, без отдельной эвристики на каждый источник.

Нюанс, которого нет в легаси: EMM-стенд — `test_stands.id`, формат `stand_<hex>`
(`src/utils/ids.py::test_stand_id`), а в этом идентификаторе УЖЕ есть один
подчёркивание. Наивный `name.split('_')` на собственных именах EMM даёт не 4
токена, а 5 (`"stand"` и хвост uuid отдельно). Поэтому `parse_run_name` ниже
режет `split("_", 3)` — ровно 3 разреза, всё, что после третьего
подчёркивания, остаётся одним последним токеном. Легаси-имена (стенд без
подчёркиваний, вроде `stand1`) парсятся этим же кодом ничуть не хуже: третий
разрез просто ничего не находит, весь остаток и так один токен.

Сопоставление стенда по токену из имени — это ПОИСК локального
`test_stands.id`, а не гарантия совпадения: легаси использовал свои
собственные обозначения стендов, никак не связанные с EMM-овским `stand_<hex>`,
поэтому для большинства старых прогонов токен ожидаемо не найдётся —
`needs_manual_mapping=True`, а не крах. `parse_run_name` возвращает
структурированный кортеж уже на этапе, где формат имени просто похож на
конвенцию; сопоставление стенда — отдельная, более слабая, проверка поверх.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import Action, EntityType, StpPullOperationStatus
from src.core.exceptions import AppException, ServiceUnavailableError
from src.dependencies.auth import Identity
from src.models import StpTestRun, TestStand
from src.repositories import department_integration_settings as dis_repo
from src.repositories import stp_cell as stp_cell_repo
from src.repositories import stp_pull_operation as pull_op_repo
from src.repositories import stp_test_case as stp_test_case_repo
from src.repositories import stp_test_run as stp_test_run_repo
from src.repositories import test_stand as test_stand_repo
from src.schemas.stp_pull_from_life import (
    StpPullConflict,
    StpPullImportResponse,
    StpPullPreviewItem,
    StpPullPreviewResponse,
    StpPullRunComposition,
    StpPullRunResult,
)
from src.services import permissions, secret_client, zephyr_client
from src.services.zephyr_client import ZephyrTestRunDetail, ZephyrTestRunSummary
from src.utils.ids import stp_cell_id as new_cell_id
from src.utils.ids import stp_pull_operation_id as new_pull_op_id
from src.utils.ids import stp_test_case_id as new_case_id
from src.utils.ids import stp_test_run_id as new_run_id

logger = logging.getLogger(__name__)

_NAME_NOT_PARSEABLE = "NAME_NOT_PARSEABLE"
_STAND_NOT_FOUND = "STAND_NOT_FOUND"
_STAND_WRONG_DEPARTMENT = "STAND_WRONG_DEPARTMENT"


def _derive_release(os_version_id: str) -> str:
    """Дублирует `services/stp.py::_derive_release` — тот же приём, что и
    `stp_add_test.py::_resolve_jira_ctx` уже применяет к `_resolve_jira_bearer`:
    крохотный чистый хелпер дублируется, а не импортируется из чужой зоны."""
    parts = os_version_id.split(".")
    return ".".join(parts[:2]) if len(parts) >= 2 else os_version_id


async def _resolve_jira_ctx(db: AsyncSession, department_id: str) -> tuple[str, str] | None:
    """`(jira_base_url, bearer_token)` отдела, либо `None` — не настроено/недоступно.

    Дублирует `services/stp.py::_resolve_jira_bearer`/`stp_add_test.py::
    _resolve_jira_ctx` — тот же источник, то же правило.
    """
    settings = await dis_repo.get_by_department(db, department_id)
    if settings is None or not settings.credential_id or not settings.jira_base_url:
        return None
    try:
        _login, secret = await secret_client.reveal_credential(settings.credential_id)
    except AppException as exc:
        logger.warning(
            "stp_pull_from_life: reveal_credential failed dept=%s cred=%s: %s",
            department_id, settings.credential_id, exc.message,
        )
        return None
    if not secret:
        return None
    return settings.jira_base_url, secret


def parse_run_name(name: str) -> tuple[str, str, str, str] | None:
    """`"{os_version_id}_{mode}_{kernel}_{stand_token}"` → 4-tuple, либо `None`,
    если имя не подходит под эту конвенцию (§D8 — тогда caller кладёт запись в
    ведро «нужна ручная сверка», не падает и не пропускает её молча).

    `split("_", 3)` — намеренно только 3 разреза (см. module docstring): EMM
    сама генерирует стенд-токены вида `stand_<hex>`, которые несут ещё одно
    подчёркивание внутри себя.
    """
    if not name:
        return None
    parts = name.split("_", 3)
    if len(parts) != 4:
        return None
    parts = [p.strip() for p in parts]
    if not all(parts):
        return None
    return parts[0], parts[1], parts[2], parts[3]


async def _match_stand(
    db: AsyncSession, *, stand_token: str, department_id: str,
) -> tuple[TestStand | None, str | None]:
    """Резолвит токен стенда из имени в локальный `test_stands` — либо
    возвращает причину, почему сопоставить не удалось (§D8: несопоставленный
    стенд — тоже «нужна ручная сверка», не ошибка)."""
    stand = await test_stand_repo.get_by_id(db, stand_token)
    if stand is None:
        return None, _STAND_NOT_FOUND
    if stand.department_id != department_id:
        return None, _STAND_WRONG_DEPARTMENT
    return stand, None


def _zephyr_link(base_url: str, key: str) -> str:
    """Лучшее приближение ссылки на Zephyr Scale UI для человека, не API-вызов.

    Формат `#/testPlayer/{key}` — распространённый маршрут Zephyr Scale
    Server/DC, НЕ проверен на реальном инстансе Jira этого проекта. Ломается
    — просто неверная ссылка в preview, не влияет на импорт.
    """
    return f"{base_url.rstrip('/')}/secure/Tests.jspa#/testPlayer/{key}"


async def _search_folder(
    db: AsyncSession, *, department_id: str, os_version_id: str,
) -> tuple[str, str, str, list[ZephyrTestRunSummary]]:
    """Общая часть preview/import: резолв Jira-кред, папка, поиск. Бросает
    `ServiceUnavailableError`, если интеграция не настроена — единственный
    контекст здесь (отдел+РЦ), партиционировать по стендам, как в
    `stp.py::generate_stp_runs`, нечего.

    Возвращает `(base_url, bearer_token, folder, summaries)`.
    """
    jira_ctx = await _resolve_jira_ctx(db, department_id)
    if jira_ctx is None:
        raise ServiceUnavailableError(
            error_code="JIRA_INTEGRATION_NOT_AVAILABLE",
            message="department_integration_settings not configured or credential reveal failed",
        )
    base_url, bearer_token = jira_ctx
    release = _derive_release(os_version_id)
    folder = f"/stress_test/{release}/{os_version_id}"
    summaries = await zephyr_client.search_test_runs(base_url=base_url, bearer_token=bearer_token, folder=folder)
    return base_url, bearer_token, folder, summaries


async def _preview_one(
    db: AsyncSession, *, summary: ZephyrTestRunSummary, base_url: str, bearer_token: str, department_id: str,
) -> StpPullPreviewItem:
    parsed = parse_run_name(summary.name)
    stand: TestStand | None = None
    mapping_issue: str | None = None
    parsed_os_version_id = parsed_mode = parsed_kernel = parsed_stand_token = None

    if parsed is None:
        mapping_issue = _NAME_NOT_PARSEABLE
    else:
        parsed_os_version_id, parsed_mode, parsed_kernel, parsed_stand_token = parsed
        stand, mapping_issue = await _match_stand(db, stand_token=parsed_stand_token, department_id=department_id)

    existing_run = await stp_test_run_repo.get_by_zephyr_key(db, summary.key)

    # Состав — читаем детали рана, даже если сопоставление стенда провалилось:
    # оператору полезно видеть размер состава ДО того, как он решит мапить
    # стенд руками. Сетевой сбой на этом одном ране не должен рушить весь
    # предпросмотр остальных.
    case_count = matched_case_count = 0
    try:
        detail = await zephyr_client.get_test_run(base_url=base_url, bearer_token=bearer_token, test_run_key=summary.key)
        case_count = len(detail.items)
        if detail.items:
            zephyr_ids = [item.test_case_key for item in detail.items]
            matched_cases = await stp_test_case_repo.list_by_zephyr_ids(db, zephyr_ids)
            matched_case_count = len({c.zephyr_id for c in matched_cases})
    except AppException as exc:
        logger.warning("stp_pull_from_life: preview detail fetch failed key=%s: %s", summary.key, exc.message)
        if mapping_issue is None:
            mapping_issue = "DETAIL_FETCH_FAILED"

    return StpPullPreviewItem(
        zephyr_key=summary.key,
        zephyr_link=_zephyr_link(base_url, summary.key),
        name=summary.name,
        parsed_os_version_id=parsed_os_version_id,
        parsed_mode=parsed_mode,
        parsed_kernel=parsed_kernel,
        parsed_stand_token=parsed_stand_token,
        stand_id=stand.id if stand else None,
        needs_manual_mapping=mapping_issue is not None,
        mapping_issue=mapping_issue,
        already_imported=existing_run is not None,
        stp_test_run_id=existing_run.id if existing_run else None,
        composition=StpPullRunComposition(
            case_count=case_count,
            matched_case_count=matched_case_count,
            new_case_count=case_count - matched_case_count,
        ),
    )


async def preview_pull_from_life(
    db: AsyncSession, identity: Identity, *, department_id: str, os_version_id: str,
) -> StpPullPreviewResponse:
    """`/stp/pull-from-life/preview` — ЧИСТОЕ чтение, ни одной записи в БД.

    Для каждого найденного в Zephyr test-run'а: парсит имя, сопоставляет
    стенд, смотрит, есть ли уже локальный `stp_test_run` с этим ключом, и
    считает состав (сколько тест-кейсов, сколько из них уже есть локально по
    `zephyr_id`). Провал fetch'а деталей ОДНОГО рана не должен ронять
    предпросмотр остальных — заносится как `needs_manual_mapping` с явной
    причиной вместо падения всего вызова.
    """
    await permissions.require_action(db, identity, EntityType.STP_TEST_RUN, Action.CREATE)

    base_url, bearer_token, folder, summaries = await _search_folder(
        db, department_id=department_id, os_version_id=os_version_id,
    )

    items = [
        await _preview_one(db, summary=s, base_url=base_url, bearer_token=bearer_token, department_id=department_id)
        for s in summaries
    ]

    return StpPullPreviewResponse(
        department_id=department_id,
        os_version_id=os_version_id,
        folder=folder,
        items=items,
        total_found=len(items),
        new_count=sum(1 for i in items if not i.already_imported and not i.needs_manual_mapping),
        already_imported_count=sum(1 for i in items if i.already_imported),
        needs_manual_mapping_count=sum(1 for i in items if i.needs_manual_mapping),
    )


async def _upsert_run(
    db: AsyncSession, *, summary: ZephyrTestRunSummary, department_id: str, os_version_id: str,
    folder: str, stand: TestStand, mode: str, kernel: str,
) -> tuple[StpTestRun, bool]:
    """Найти-или-завести локальный `stp_test_run` для этого Zephyr-ключа.
    Возвращает `(run, created)`. Существующая строка НЕ переписывается —
    только переиспользуется (§D8: импорт не затирает локальные данные)."""
    existing = await stp_test_run_repo.get_by_zephyr_key(db, summary.key)
    if existing is not None:
        return existing, False
    run = await stp_test_run_repo.create(db, {
        "id": new_run_id(),
        "os_version_id": os_version_id,
        "mode": mode,
        "kernel": kernel,
        "stand_id": stand.id,
        "zephyr_test_run_key": summary.key,
        "zephyr_folder_path": folder,
    })
    await db.commit()
    await db.refresh(run)
    return run, True


async def _sync_cells(
    db: AsyncSession, *, run: StpTestRun, detail: ZephyrTestRunDetail, identity: Identity, department_id: str,
) -> tuple[int, int, int, int, list[StpPullConflict]]:
    """Заводит недостающие `stp_test_case`/`stp_cell` и сверяет статус.

    Правило конфликта (§D8, «не затирает локальные изменения молча»): Zephyr
    выигрывает ТОЛЬКО для ячеек, которых ещё не было локально вовсе — они
    создаются сразу со статусом Zephyr. Для уже существующей ячейки статус
    правится, только если он совпадает с тем, что говорит Zephyr (no-op);
    расхождение — конфликт в отчёте, ячейка остаётся как есть. Локальная
    история (кто её так выставил — очередь или ручной override) для этого
    решения не имеет значения: как только человек/событие в EMM что-то в неё
    записали, Zephyr больше не источник истины для этой конкретной ячейки.
    """
    cases_created = cases_matched = cells_created = cells_matched = 0
    conflicts: list[StpPullConflict] = []

    for item in detail.items:
        case = await stp_test_case_repo.get_by_zephyr_id(db, item.test_case_key)
        if case is None:
            case = await stp_test_case_repo.create(db, {
                "id": new_case_id(),
                "code": f"zephyr:{item.test_case_key}",
                "title": item.test_case_name or item.test_case_key,
                "zephyr_id": item.test_case_key,
                "department_id": department_id,
                "created_by": identity.user_id,
            })
            await db.commit()
            await db.refresh(case)
            cases_created += 1
        else:
            cases_matched += 1

        cell = await stp_cell_repo.get_by_case_and_run(db, stp_test_case_id=case.id, stp_test_run_id=run.id)
        if cell is None:
            await stp_cell_repo.create(db, {
                "id": new_cell_id(),
                "stp_test_case_id": case.id,
                "stp_test_run_id": run.id,
                "status": item.status,
                "is_active": True,
                "updated_by": identity.user_id,
            })
            await db.commit()
            cells_created += 1
        elif cell.status == item.status:
            cells_matched += 1
        else:
            conflicts.append(StpPullConflict(
                zephyr_key=run.zephyr_test_run_key or "",
                test_case_key=item.test_case_key,
                stp_cell_id=cell.id,
                local_status=cell.status,
                zephyr_status=item.status,
            ))

    return cases_created, cases_matched, cells_created, cells_matched, conflicts


async def _import_one(
    db: AsyncSession, identity: Identity, *, summary: ZephyrTestRunSummary, base_url: str, bearer_token: str,
    department_id: str, os_version_id: str, folder: str,
) -> StpPullRunResult:
    parsed = parse_run_name(summary.name)
    if parsed is None:
        return StpPullRunResult(
            zephyr_key=summary.key, status="skipped_needs_manual_mapping", mapping_issue=_NAME_NOT_PARSEABLE,
        )
    _v, mode, kernel, stand_token = parsed
    stand, mapping_issue = await _match_stand(db, stand_token=stand_token, department_id=department_id)
    if stand is None:
        return StpPullRunResult(
            zephyr_key=summary.key, status="skipped_needs_manual_mapping", mapping_issue=mapping_issue,
        )

    op = await pull_op_repo.get_by_key(
        db, department_id=department_id, os_version_id=os_version_id, zephyr_test_run_key=summary.key,
    )
    if op is None:
        op = await pull_op_repo.create(db, {
            "id": new_pull_op_id(),
            "department_id": department_id,
            "os_version_id": os_version_id,
            "zephyr_test_run_key": summary.key,
            "created_by": identity.user_id,
        })
        await db.commit()
        await db.refresh(op)

    try:
        run, created_run = await _upsert_run(
            db, summary=summary, department_id=department_id, os_version_id=os_version_id,
            folder=folder, stand=stand, mode=mode, kernel=kernel,
        )
        detail = await zephyr_client.get_test_run(base_url=base_url, bearer_token=bearer_token, test_run_key=summary.key)
        cases_created, cases_matched, cells_created, cells_matched, conflicts = await _sync_cells(
            db, run=run, detail=detail, identity=identity, department_id=department_id,
        )
    except AppException as exc:
        await pull_op_repo.update(db, op, {
            "status": StpPullOperationStatus.FAILED,
            "last_error": f"{exc.error_code}: {exc.message}"[:2000],
        })
        await db.commit()
        return StpPullRunResult(
            zephyr_key=summary.key, status="failed", error=f"{exc.error_code}: {exc.message}",
        )

    await pull_op_repo.update(db, op, {
        "stp_test_run_id": run.id, "run_upserted": True, "cells_synced": True,
        "status": StpPullOperationStatus.SUCCEEDED, "last_error": None,
    })
    await db.commit()

    return StpPullRunResult(
        zephyr_key=summary.key, status="succeeded", stp_test_run_id=run.id,
        created_run=created_run, matched_run=not created_run,
        cases_created=cases_created, cases_matched=cases_matched,
        cells_created=cells_created, cells_matched=cells_matched,
        conflicts=conflicts,
    )


async def import_pull_from_life(
    db: AsyncSession, identity: Identity, *, department_id: str, os_version_id: str, zephyr_keys: list[str] | None,
) -> StpPullImportResponse:
    """`/stp/pull-from-life/import` — импортировать/сверить выбранные (или все
    найденные) test-run'ы отдела в этой папке.

    Каждый ран обрабатывается независимо (`_import_one`) — сбой одного
    (сетевая ошибка при чтении деталей) не останавливает остальные, тем же
    принципом частичных ошибок, что и `stp.py::generate_stp_runs`. Идемпотентно:
    второй импорт того же ключа находит существующие `stp_test_run`/
    `stp_test_case`/`stp_cell` по их сопоставляющим полям и просто
    пересчитывает `matched_*`, ничего не дублируя.
    """
    await permissions.require_action(db, identity, EntityType.STP_TEST_RUN, Action.CREATE)

    base_url, bearer_token, folder, summaries = await _search_folder(
        db, department_id=department_id, os_version_id=os_version_id,
    )
    if zephyr_keys:
        wanted = set(zephyr_keys)
        summaries = [s for s in summaries if s.key in wanted]

    results = [
        await _import_one(
            db, identity, summary=s, base_url=base_url, bearer_token=bearer_token,
            department_id=department_id, os_version_id=os_version_id, folder=folder,
        )
        for s in summaries
    ]

    response = StpPullImportResponse(department_id=department_id, os_version_id=os_version_id, results=results)
    for r in results:
        if r.status == "succeeded":
            response.created_runs += int(r.created_run)
            response.matched_runs += int(r.matched_run)
            response.cases_created += r.cases_created
            response.cases_matched += r.cases_matched
            response.cells_created += r.cells_created
            response.cells_matched += r.cells_matched
            response.conflicts_count += len(r.conflicts)
        elif r.status == "failed":
            response.failed_count += 1
        else:
            response.skipped_count += 1
    return response
