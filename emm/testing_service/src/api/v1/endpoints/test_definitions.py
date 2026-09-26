"""CRUD каталога тестов (§2.2 плана миграции).

Чтение (список / карточка по id / по коду) — в пределах своего отдела плюс
платформенные тесты без владельца (`department_id IS NULL`, так заводит их
импорт легаси-каталога); чужой отдел — 403 `DEPARTMENT_ISOLATION`. Запись
(create/update/delete) — под матрицей прав
`(test_definition, *, create|update|delete)`. Слоты команды теста
(`test_command_args`) — соседний роутер `test_command_args.py`, смонтированный
под тем же `{test_id}`.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.auth import CurrentUserIdentity
from src.dependencies.db import get_db
from src.schemas.common import OkResponse, PaginatedResponse
from src.schemas.launch_preview import LaunchPreviewRequest, LaunchPreviewResponse
from src.schemas.test_definition import (
    TestDefinitionCreate,
    TestDefinitionResponse,
    TestDefinitionUpdate,
)
from src.services import launch_preview as launch_preview_svc
from src.services import test_definition as svc

router = APIRouter(prefix="/test-definitions")


@router.get(
    "",
    response_model=PaginatedResponse[TestDefinitionResponse],
    summary="Список тестов каталога",
    description=(
        "Каталог тестов с опциональными фильтрами по категории/готовности. "
        "Выдача сужена до отдела вызывающего плюс платформенные тесты; "
        "`department_id` принимается только свой. Envelope `{items, total, limit, offset}`."
    ),
    responses={
        200: {"description": "Страница каталога."},
        401: {"description": "ACCESS_TOKEN_MISSING — запрос без bearer'а."},
        403: {"description": "DEPARTMENT_ISOLATION — запрошен чужой `department_id`."},
    },
)
async def list_test_definitions(
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    department_id: str | None = Query(default=None, description="Фильтр по отделу-владельцу."),
    category: str | None = Query(default=None, description="Фильтр по категории."),
    readiness: str | None = Query(default=None, description="Фильтр по статусу готовности."),
) -> PaginatedResponse[TestDefinitionResponse]:
    """List тестов. Свой отдел + платформенные."""
    items, total = await svc.list_test_definitions(
        db, identity, limit=limit, offset=offset,
        department_id=department_id, category=category, readiness=readiness,
    )
    return PaginatedResponse[TestDefinitionResponse](
        items=[TestDefinitionResponse.model_validate(i) for i in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/by-code/{code}",
    response_model=TestDefinitionResponse,
    summary="Получить тест по коду",
    description="Карточка теста по UNIQUE-коду. Свой отдел либо платформенный тест.",
    responses={
        401: {"description": "ACCESS_TOKEN_MISSING — запрос без bearer'а."},
        403: {"description": "DEPARTMENT_ISOLATION — тест чужого отдела."},
        404: {"description": "Тест не найден."},
    },
)
async def get_test_definition_by_code(
    code: str,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> TestDefinitionResponse:
    """Get теста по коду. Свой отдел либо платформенный тест."""
    obj = await svc.get_test_definition_by_code(db, identity, code)
    return TestDefinitionResponse.model_validate(obj)


@router.post(
    "",
    response_model=TestDefinitionResponse,
    status_code=201,
    summary="Завести новый тест",
    description="Добавляет запись в каталог тестов. UNIQUE(code) — повтор → 409.",
    responses={
        201: {"description": "Тест создан."},
        403: {"description": "Нет роли с `create`."},
        409: {"description": "Тест с таким `code` уже есть."},
    },
)
async def create_test_definition(
    body: TestDefinitionCreate,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> TestDefinitionResponse:
    """Create теста. Доступ: `(test_definition, *, create)`."""
    obj = await svc.create_test_definition(db, identity, body)
    return TestDefinitionResponse.model_validate(obj)


@router.get(
    "/{test_id}",
    response_model=TestDefinitionResponse,
    summary="Получить тест",
    description="Карточка теста. Свой отдел либо платформенный тест.",
    responses={
        401: {"description": "ACCESS_TOKEN_MISSING — запрос без bearer'а."},
        403: {"description": "DEPARTMENT_ISOLATION — тест чужого отдела."},
        404: {"description": "Тест не найден."},
    },
)
async def get_test_definition(
    test_id: str,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> TestDefinitionResponse:
    """Get теста по id. Свой отдел либо платформенный тест."""
    obj = await svc.get_test_definition(db, identity, test_id)
    return TestDefinitionResponse.model_validate(obj)


@router.post(
    "/{test_id}/launch-preview",
    response_model=LaunchPreviewResponse,
    summary="Превью запуска теста",
    description=(
        "Задание воркеру, которое собрал бы claim для теста на "
        "выбранных стенде, версии ОС, ядре и режиме: переменные (код → значение "
        "→ источник), `dates.conf`, файлы для стенда (starter.sh, токен, dates, "
        "маркер testenv), команда запуска и команда остановки. Секреты — `***`. "
        "Ничего не пишет в БД и не ставит в очередь. Этап, который не удался, "
        "не обрывает превью — он в `errors` (с тем же кодом, с которым упал бы "
        "claim). Права — как на чтение теста и стенда (свой отдел)."
    ),
    responses={
        200: {"description": "Превью; непустой `errors` — claim с этими параметрами провалил бы item."},
        401: {"description": "ACCESS_TOKEN_MISSING — запрос без bearer'а."},
        403: {"description": "DEPARTMENT_ISOLATION — тест или стенд чужого отдела; PERMISSION_DENIED — тест другого отдела, чем стенд."},
        404: {"description": "TEST_DEFINITION_NOT_FOUND / TEST_STAND_NOT_FOUND."},
        422: {"description": "Невалидное тело запроса."},
    },
)
async def launch_preview(
    test_id: str,
    body: LaunchPreviewRequest,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> LaunchPreviewResponse:
    """Превью задания воркеру. Доступ — чтение теста и стенда своего отдела."""
    return LaunchPreviewResponse.model_validate(
        await launch_preview_svc.preview(db, identity, test_id, body),
    )


@router.patch(
    "/{test_id}",
    response_model=TestDefinitionResponse,
    summary="Обновить тест",
    description="Частичное обновление. UNIQUE-конфликт по новому `code` → 409.",
    responses={
        403: {"description": "Нет `update`."},
        404: {"description": "Тест не найден."},
        409: {"description": "Конфликт UNIQUE(code)."},
    },
)
async def update_test_definition(
    test_id: str,
    body: TestDefinitionUpdate,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> TestDefinitionResponse:
    """PATCH теста. Доступ: `(test_definition, *, update)`."""
    obj = await svc.update_test_definition(db, identity, test_id, body)
    return TestDefinitionResponse.model_validate(obj)


@router.delete(
    "/{test_id}",
    response_model=OkResponse,
    summary="Удалить тест",
    description="Hard-delete. Каскадом сносит слоты команды этого теста (test_command_args).",
    responses={
        403: {"description": "Нет `delete`."},
        404: {"description": "Тест не найден."},
    },
)
async def delete_test_definition(
    test_id: str,
    identity: CurrentUserIdentity,
    db: AsyncSession = Depends(get_db),
) -> OkResponse:
    """Delete теста. Доступ: `(test_definition, *, delete)`."""
    await svc.delete_test_definition(db, identity, test_id)
    return OkResponse()
