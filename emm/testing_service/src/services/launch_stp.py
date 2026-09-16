"""Поиск теста в конкретном контексте СТП перед обычным запуском."""

from sqlalchemy.ext.asyncio import AsyncSession
from src.core.exceptions import DomainValidationError
from src.repositories import (
    stp_test_case as cases,
    stp_test_run as runs,
    stp_cell as cells,
)


async def find_context_run(
    db: AsyncSession,
    stand_id: str,
    context: dict,
    run_id: str | None = None,
):
    """Резолвит `StpTestRun` под точный `(stand, RC, kernel, mode)`, без проверки
    конкретного теста — нужен, чтобы отличить «СТП для этого контекста вообще
    не генерировалась» от «СТП есть, но этого теста в ней нет» (§E2)."""
    run = (
        await runs.get_by_id(db, run_id)
        if run_id
        else await runs.find_latest_for_context(
            db,
            stand_id=stand_id,
            os_version_id=context.get("RC"),
            kernel=context.get("KERNEL"),
            mode=context.get("MODE"),
        )
    )
    if not run or (run.stand_id, run.os_version_id, run.kernel, run.mode) != (
        stand_id,
        context.get("RC"),
        context.get("KERNEL"),
        context.get("MODE"),
    ):
        return None
    return run


async def find_membership(
    db: AsyncSession,
    test_code: str,
    stand_id: str,
    context: dict,
    run_id: str | None = None,
):
    case = await cases.get_by_code(db, test_code)
    if not case:
        return None
    run = await find_context_run(db, stand_id, context, run_id)
    if run is None:
        return None
    cell = await cells.get_by_case_and_run(
        db, stp_test_case_id=case.id, stp_test_run_id=run.id
    )
    # `is_active=false` — ячейка исключена текущим составом СТП (сужение до
    # changelog, §D4/D5); ячейка и её история сохраняются, но это больше не
    # активный состав, поэтому обычный запуск должен видеть её как отсутствие
    # членства, как если бы ячейки не было вовсе.
    return run if cell and cell.is_active else None


async def require_membership(
    db: AsyncSession,
    test_code: str,
    stand_id: str,
    context: dict,
    run_id: str | None = None,
):
    """Гейт обычного (не debug) запуска. Различает два разных отказа (§E2):

    - тест не входит в активный состав уже существующей СТП для этого
      контекста — `TEST_NOT_IN_STP`, несёт `stp_test_run_id` в `details`, эту
      СТП есть куда добавить тест (`POST /stp/test-runs/{id}/add-test`);
    - СТП для этого `(stand, RC, kernel, mode)` не генерировалась вовсе —
      `STP_RUN_NOT_FOUND`, добавлять некуда, сперва нужна генерация состава.
    """
    run = await find_membership(db, test_code, stand_id, context, run_id)
    if run is not None:
        return run

    context_run = await find_context_run(db, stand_id, context, run_id)
    if context_run is None:
        raise DomainValidationError(
            error_code="STP_RUN_NOT_FOUND",
            message="Для этого стенда/РЦ/ядра/режима ещё не сгенерирована СТП — сначала выполните генерацию.",
        )
    raise DomainValidationError(
        error_code="TEST_NOT_IN_STP",
        message="Тест отсутствует в выбранной СТП. Добавьте его в СТП или используйте debug.",
        details={"stp_test_run_id": context_run.id},
    )
