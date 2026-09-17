"""СТП: каталог тест-кейсов, генерация Zephyr test-run'ов, прогоны, ячейки (§2.5, §6, §D2-D5 плана миграции).

Тест-кейсы — чтение в пределах своего отдела плюс платформенные кейсы без
владельца (`department_id IS NULL`), запись — матрица `(stp_test_case, *, ...)`.
Прогоны/ячейки скоупятся через отдел стенда — своего `department_id` у
`stp_test_runs` нет (см. модель). `/stp/generate` — админский вызов,
матрица `(stp_test_run, *, create)`, принимает явный `scope` (changelog/full,
§D4/D5) — первый вызов заводит состав, повтор с тем же `scope` идемпотентен,
с другим — переключает уже существующий состав (см. `services/stp.py`).
`/stp/composition` — текущий `scope`+`revision` пары (отдел, РЦ), чтение
своему отделу. Ячейки — ручной override под `(stp_cell, *, update)`, событийное
обновление идёт мимо HTTP (см. `services/queue.py` → `services/stp_status.py`).
`/stp/matrix/publish` — ручная публикация сводной таблицы РЦ в Confluence,
department-scoped `(stp_test_run, *, publish)` (см. `services/stp_matrix.py`).
`/stp/test-runs/{run_id}/add-test` — добавить один тест EMM в конкретный
СТП-прогон (§D6/D7): заводит недостающий Zephyr testcase, добавляет его в
Zephyr test-run, локальную ячейку и переопубликовывает СТП-матрицу, шаг за
шагом с retry (см. `services/stp_add_test.py`). Тот же гейт, что у
`/stp/generate` — это тот же create-жест над `stp_test_run`, только на один
тест вместо целого состава.
`/stp/pull-from-life/preview` и `/stp/pull-from-life/import` — обратное
направление (§D8): прочитать уже существующие в Zephyr test-run'ы отдела
(свои, легаси или заведённые руками) и импортировать/сверить их с EMM, не
публикуя ничего обратно в life (см. `services/stp_pull_from_life.py`). Тот же
гейт `(stp_test_run, *, create)`, что и у остальных админских СТП-операций —
preview тоже дёргает Jira/Zephyr живым запросом с кредами отдела, поэтому не
открыт всем подряд, даже будучи чтением.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.auth import CurrentUserIdentity
from src.dependencies.db import get_db
from src.schemas.common import OkResponse, PaginatedResponse
from src.schemas.stp import (
    StpCellManualUpdate,
    StpCellResponse,
    StpCompositionResponse,
    StpGenerateRequest,
    StpGenerateResponse,
    StpMatrixPublishRequest,
    StpMatrixPublishResponse,
    StpTestCaseCreate,
    StpTestCaseResponse,
    StpTestCaseUpdate,
    StpTestRunResponse,
)
from src.schemas.stp_add_test import StpAddTestOperationResponse, StpAddTestRequest
from src.schemas.stp_pull_from_life import (
    StpPullImportRequest,
    StpPullImportResponse,
    StpPullPreviewRequest,
    StpPullPreviewResponse,
)
from src.services import stp as stp_svc
from src.services import stp_add_test as stp_add_test_svc
from src.services import stp_matrix as stp_matrix_svc
from src.services import stp_pull_from_life as stp_pull_svc
from src.services import stp_status
from src.services import stp_test_case as stp_test_case_svc

router = APIRouter(prefix="/stp")


# ── Тест-кейсы ────────────────────────────────────────────────────────────────


@router.get(
    "/test-cases",
    response_model=PaginatedResponse[StpTestCaseResponse],
    summary="Каталог тест-кейсов СТП",
    description="Зеркало Zephyr Scale test-case. Свой отдел + платформенные кейсы.",
)
async def list_stp_test_cases(
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    department_id: str | None = Query(default=None),
) -> PaginatedResponse[StpTestCaseResponse]:
    items, total = await stp_test_case_svc.list_stp_test_cases(
        db, identity, limit=limit, offset=offset, department_id=department_id,
    )
    return PaginatedResponse[StpTestCaseResponse](
        items=[StpTestCaseResponse.model_validate(i) for i in items],
        total=total, limit=limit, offset=offset,
    )


@router.post(
    "/test-cases",
    response_model=StpTestCaseResponse,
    status_code=201,
    summary="Завести тест-кейс СТП",
    description="UNIQUE(code) — повтор → 409. `zephyr_id` опционален, заполняется оператором позже.",
    responses={403: {"description": "Нет роли с `create`."}, 409: {"description": "Дубль по code."}},
)
async def create_stp_test_case(
    body: StpTestCaseCreate,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> StpTestCaseResponse:
    obj = await stp_test_case_svc.create_stp_test_case(db, identity, body)
    return StpTestCaseResponse.model_validate(obj)


@router.get(
    "/test-cases/{case_id}",
    response_model=StpTestCaseResponse,
    summary="Карточка тест-кейса СТП",
)
async def get_stp_test_case(
    case_id: str, identity: CurrentUserIdentity, db: AsyncSession = Depends(get_db),
) -> StpTestCaseResponse:
    obj = await stp_test_case_svc.get_stp_test_case(db, identity, case_id)
    return StpTestCaseResponse.model_validate(obj)


@router.patch(
    "/test-cases/{case_id}",
    response_model=StpTestCaseResponse,
    summary="Обновить тест-кейс СТП",
    responses={403: {"description": "Нет `update`."}, 404: {"description": "Не найден."}},
)
async def update_stp_test_case(
    case_id: str,
    body: StpTestCaseUpdate,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> StpTestCaseResponse:
    obj = await stp_test_case_svc.update_stp_test_case(db, identity, case_id, body)
    return StpTestCaseResponse.model_validate(obj)


@router.delete(
    "/test-cases/{case_id}",
    response_model=OkResponse,
    summary="Удалить тест-кейс СТП",
    responses={403: {"description": "Нет `delete`."}, 404: {"description": "Не найден."}},
)
async def delete_stp_test_case(
    case_id: str, identity: CurrentUserIdentity, db: AsyncSession = Depends(get_db),
) -> OkResponse:
    await stp_test_case_svc.delete_stp_test_case(db, identity, case_id)
    return OkResponse()


# ── Генерация + прогоны ────────────────────────────────────────────────────────


@router.post(
    "/generate",
    response_model=StpGenerateResponse,
    summary="Сгенерировать/переключить состав СТП-прогонов для отдела/РЦ/режима/ядра",
    description=(
        "Админский вызов (не автоматический вебхук). Явный `scope` (changelog/full, "
        "§D4/D5) — не выводится из вида RC. Первый вызов заводит Zephyr test-run на "
        "стенд; повтор с тем же `scope` — идемпотентный no-op; повтор с другим "
        "`scope` реконциливает существующий состав (добавляет недостающие тест-кейсы "
        "в существующий Zephyr-ран, деактивирует выпавшие, без потери статуса/истории) "
        "и увеличивает `stp_compositions.revision`. Провал одного стенда не рушит "
        "остальные — см. `errors` в ответе."
    ),
    responses={403: {"description": "Нет роли с `create`."}, 422: {"description": "scope не changelog/full."}},
)
async def generate_stp(
    body: StpGenerateRequest,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> StpGenerateResponse:
    runs, errors = await stp_svc.generate_stp_runs(
        db, identity,
        os_version_id=body.os_version_id, mode=body.mode, kernel=body.kernel,
        scope=body.scope, department_id=body.department_id or identity.department_id,
    )
    return StpGenerateResponse(
        test_runs=[StpTestRunResponse.model_validate(r) for r in runs],
        errors=errors,
    )


@router.get(
    "/composition",
    response_model=StpCompositionResponse,
    summary="Текущий активный состав СТП (scope + revision) отдела для одной РЦ",
    description=(
        "Доступен пользователям этого же отдела. Отсутствие строки — не 404, а "
        "дефолт `scope: null, revision: 0` (состав ещё ни разу не генерировался)."
    ),
)
async def get_stp_composition(
    os_version_id: str,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
    department_id: str | None = Query(default=None),
) -> StpCompositionResponse:
    data = await stp_svc.get_stp_composition_effective(
        db, identity, department_id or identity.department_id, os_version_id,
    )
    return StpCompositionResponse(**data)


@router.post(
    "/matrix/publish",
    response_model=StpMatrixPublishResponse,
    summary="Опубликовать сводную СТП-матрицу РЦ в Confluence",
    description=(
        "Ручной триггер (не автоматический вебхук, как и /stp/generate). Собирает "
        "все stp_test_runs отдела для этого РЦ в одну HTML-таблицу и публикует "
        "её страницей в per-department Confluence-иерархии "
        "(department_integration_settings.stp_matrix_confluence_*). "
        "Не настроено/нет прогонов — понятный skip-статус в ответе, не 500."
    ),
    responses={403: {"description": "Нет роли с `publish` в этом отделе."}},
)
async def publish_stp_matrix(
    body: StpMatrixPublishRequest,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> StpMatrixPublishResponse:
    obj = await stp_matrix_svc.publish_stp_matrix(
        db, identity,
        department_id=body.department_id or identity.department_id,
        os_version_id=body.os_version_id,
    )
    return StpMatrixPublishResponse.model_validate(obj)


@router.get(
    "/test-runs",
    response_model=PaginatedResponse[StpTestRunResponse],
    summary="Список СТП-прогонов (Zephyr test-run'ов)",
)
async def list_stp_test_runs(
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    stand_id: str | None = Query(default=None),
    os_version_id: str | None = Query(default=None),
) -> PaginatedResponse[StpTestRunResponse]:
    items, total = await stp_svc.list_stp_test_runs(
        db, identity, limit=limit, offset=offset, stand_id=stand_id, os_version_id=os_version_id,
    )
    return PaginatedResponse[StpTestRunResponse](
        items=[StpTestRunResponse.model_validate(i) for i in items],
        total=total, limit=limit, offset=offset,
    )


@router.get(
    "/test-runs/{run_id}",
    response_model=StpTestRunResponse,
    summary="Карточка СТП-прогона",
    responses={404: {"description": "Не найден."}},
)
async def get_stp_test_run(
    run_id: str, identity: CurrentUserIdentity, db: AsyncSession = Depends(get_db),
) -> StpTestRunResponse:
    run, _cells = await stp_svc.get_stp_test_run(db, identity, run_id)
    return StpTestRunResponse.model_validate(run)


@router.get(
    "/test-runs/{run_id}/cells",
    response_model=list[StpCellResponse],
    summary="Ячейки СТП-прогона",
    responses={404: {"description": "Прогон не найден."}},
)
async def list_stp_test_run_cells(
    run_id: str, identity: CurrentUserIdentity, db: AsyncSession = Depends(get_db),
) -> list[StpCellResponse]:
    _run, cells = await stp_svc.get_stp_test_run(db, identity, run_id)
    return [StpCellResponse.model_validate(c) for c in cells]


@router.post(
    "/test-runs/{run_id}/add-test",
    response_model=StpAddTestOperationResponse,
    summary="Добавить один тест EMM в конкретный СТП-прогон",
    description=(
        "§D6/D7: узкий per-test аналог /stp/generate — заводит недостающий "
        "Zephyr testcase (переиспользует существующую связь, если она уже "
        "есть), добавляет его в Zephyr test-run, локальную ячейку и "
        "переопубликовывает СТП-матрицу. Долговечно: повтор на ту же пару "
        "(test_id, run_id) продолжает с первого не пройденного шага, не "
        "дублирует работу ни локально, ни в Zephyr. Частичный сбой (например, "
        "провал публикации в Confluence при уже готовом Zephyr-составе) "
        "отражается в ответе как `status: failed` с уже пройденными шагами, "
        "не выдаётся за завершённую синхронизацию."
    ),
    responses={
        403: {"description": "Нет роли с `create`."},
        404: {"description": "Тест или СТП-прогон не найдены."},
    },
)
async def add_test_to_stp(
    run_id: str,
    body: StpAddTestRequest,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> StpAddTestOperationResponse:
    op = await stp_add_test_svc.add_test_to_stp(
        db, identity, test_id=body.test_id, stp_test_run_id=run_id,
    )
    return StpAddTestOperationResponse.model_validate(op)


@router.post(
    "/pull-from-life/preview",
    response_model=StpPullPreviewResponse,
    summary="Предпросмотр импорта СТП из уже существующих Zephyr test-run'ов",
    description=(
        "§D8: ищет test-run'ы отдела в той же папке Zephyr, что и /stp/generate, "
        "парсит их имена той же конвенцией, что EMM использует для своих собственных "
        "прогонов, и показывает, что случится при импорте — без единой записи в БД."
    ),
    responses={403: {"description": "Нет роли с `create`."}},
)
async def preview_pull_from_life(
    body: StpPullPreviewRequest,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> StpPullPreviewResponse:
    return await stp_pull_svc.preview_pull_from_life(
        db, identity,
        department_id=body.department_id or identity.department_id,
        os_version_id=body.os_version_id,
    )


@router.post(
    "/pull-from-life/import",
    response_model=StpPullImportResponse,
    summary="Импортировать/сверить выбранные Zephyr test-run'ы в СТП EMM",
    description=(
        "§D8: заводит/переиспользует локальные stp_test_run/stp_test_case/stp_cell "
        "для выбранных (или всех найденных, если `zephyr_keys` пуст) test-run'ов. "
        "Идемпотентно — повтор на тот же ключ находит уже существующие строки, не "
        "дублирует их. Локальная ячейка с расходящимся статусом не перезаписывается "
        "молча — попадает в `conflicts` соответствующего результата."
    ),
    responses={403: {"description": "Нет роли с `create`."}},
)
async def import_pull_from_life(
    body: StpPullImportRequest,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> StpPullImportResponse:
    return await stp_pull_svc.import_pull_from_life(
        db, identity,
        department_id=body.department_id or identity.department_id,
        os_version_id=body.os_version_id,
        zephyr_keys=body.zephyr_keys,
    )


# ── Ячейки: ручной override ─────────────────────────────────────────────────────


@router.patch(
    "/cells/{cell_id}",
    response_model=StpCellResponse,
    summary="Ручной override статуса ячейки СТП",
    description="Не трогает Zephyr — легаси-паттерн: ручная правка локальна.",
    responses={403: {"description": "Нет `update`."}, 404: {"description": "Ячейка не найдена."}},
)
async def override_stp_cell(
    cell_id: str,
    body: StpCellManualUpdate,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> StpCellResponse:
    obj = await stp_status.manual_override(db, identity, cell_id, body.status)
    return StpCellResponse.model_validate(obj)
