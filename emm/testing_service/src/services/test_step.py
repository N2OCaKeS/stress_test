"""Шаги теста: API шагов и выбор шага для слотов и очереди.

Шаги — часть редактирования теста, как и слоты команды: чтение наследует
видимость теста, запись проверяет `(test_definition, *, update)`.

Инварианты:

* у теста всегда есть хотя бы один шаг — первый заводится вместе с тестом
  (`create_first_step`), последний удалить нельзя (`TEST_STEP_LAST`);
* первый шаг — `run_mode=full`: после restore на стенде ещё нет
  склонированного кода (`TEST_STEP_FIRST_MUST_BE_FULL`).

API теста (`/test-definitions`) по-прежнему принимает и отдаёт
`stand_setup`/`starter_suffix` — это значения первого шага
(`attach_first_step`, `apply_first_step_fields`): одношаговый тест
редактируется как раньше.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import Action, EntityType, StepRunMode
from src.core.exceptions import AuthorizationError, ConflictError, DomainValidationError, NotFoundError
from src.dependencies.auth import Identity
from src.models import TestDefinition, TestStep
from src.repositories import test_command_arg as arg_repo
from src.repositories import test_definition as test_definition_repo
from src.repositories import test_step as repo
from src.schemas.test_step import TestStepCreate, TestStepUpdate
from src.services import audit_service, permissions
from src.utils.ids import test_command_arg_id, test_step_id

_FIRST_STEP_FIELDS = ("stand_setup", "starter_suffix")


# ── вспомогательное для теста, слотов и очереди ──────────────────────────────

async def create_first_step(
    db: AsyncSession, test_id: str, *, stand_setup: dict | None = None, starter_suffix: str | None = None,
) -> TestStep:
    """Первый шаг нового теста. commit — на caller'е."""
    return await repo.create(db, {
        "id": test_step_id(), "test_id": test_id, "position": 0, "name": "",
        "run_mode": StepRunMode.FULL, "stand_setup": stand_setup, "starter_suffix": starter_suffix,
    })


async def ensure_first_step(db: AsyncSession, test_id: str) -> TestStep:
    """Первый шаг теста; нет ни одного (тест заведён мимо API) — заводится. Без commit."""
    steps = await repo.list_by_test(db, test_id)
    if steps:
        return steps[0]
    return await create_first_step(db, test_id)


async def step_for_slots(db: AsyncSession, test_id: str, step_id: str | None) -> TestStep:
    """Шаг, чьи слоты читаются/правятся: явный (должен быть шагом этого теста) или первый."""
    if step_id is None:
        return await ensure_first_step(db, test_id)
    step = await repo.get_by_id(db, step_id)
    if step is None or step.test_id != test_id:
        raise NotFoundError(error_code="TEST_STEP_NOT_FOUND", message="Test step not found")
    return step


async def attach_first_step(db: AsyncSession, tests: list[TestDefinition]) -> None:
    """Проставить `stand_setup`/`starter_suffix` первого шага на карточки для ответа API."""
    firsts = await repo.first_by_tests(db, [t.id for t in tests])
    for test in tests:
        step = firsts.get(test.id)
        test.stand_setup = step.stand_setup if step is not None else None
        test.starter_suffix = step.starter_suffix if step is not None else None


def pop_first_step_fields(data: dict) -> dict:
    """Вынуть из тела запроса теста поля, которые живут в первом шаге."""
    return {key: data.pop(key) for key in _FIRST_STEP_FIELDS if key in data}


async def apply_first_step_fields(db: AsyncSession, test_id: str, fields: dict) -> None:
    """Записать `stand_setup`/`starter_suffix` из API теста в первый шаг. Без commit."""
    if not fields:
        return
    step = await ensure_first_step(db, test_id)
    await repo.update(db, step, fields)


async def steps_for_run(db: AsyncSession, test_id: str) -> list[TestStep]:
    """Шаги теста для исполнения; тест без шагов — один пустой шаг (как одношаговый)."""
    steps = await repo.list_by_test(db, test_id)
    if steps:
        return steps
    return [TestStep(
        id="", test_id=test_id, position=0, name="", run_mode=StepRunMode.FULL,
        starter_suffix=None, stand_setup=None,
    )]


def _check_first_is_full(steps: list[TestStep]) -> None:
    if steps and steps[0].run_mode != StepRunMode.FULL:
        raise DomainValidationError(
            error_code="TEST_STEP_FIRST_MUST_BE_FULL",
            message="Первый шаг запускается после восстановления стенда — только через команду запуска профиля (full)",
            details={"step_id": steps[0].id},
        )


def _renumber(steps: list[TestStep]) -> None:
    for position, step in enumerate(steps):
        step.position = position


# ── API ──────────────────────────────────────────────────────────────────────

async def _require_test(db: AsyncSession, test_id: str, *, for_update: bool = False) -> TestDefinition:
    test = await test_definition_repo.get_by_id(db, test_id, for_update=for_update)
    if test is None:
        raise NotFoundError(error_code="TEST_DEFINITION_NOT_FOUND", message="Test definition not found")
    return test


async def _require_update(db: AsyncSession, identity: Identity, action: str, test_id: str, target_id: str | None) -> None:
    try:
        await permissions.require_action(db, identity, EntityType.TEST_DEFINITION, Action.UPDATE)
    except AuthorizationError:
        audit_service.emit(
            action, target_id=target_id, target_type="test_step",
            status="denied", allowed=False, details={"reason": "permission_denied", "test_id": test_id},
        )
        raise


async def _require_step(db: AsyncSession, test_id: str, step_id: str) -> TestStep:
    step = await repo.get_by_id(db, step_id)
    if step is None or step.test_id != test_id:
        raise NotFoundError(error_code="TEST_STEP_NOT_FOUND", message="Test step not found")
    return step


async def list_steps(db: AsyncSession, identity: Identity, test_id: str) -> list[TestStep]:
    """Шаги теста по порядку. Видимость — как у самого теста."""
    test = await _require_test(db, test_id)
    permissions.require_own_department(identity, test.department_id)
    steps = await repo.list_by_test(db, test_id)
    if not steps:
        await ensure_first_step(db, test_id)
        await db.commit()
        steps = await repo.list_by_test(db, test_id)
    return steps


async def create_step(db: AsyncSession, identity: Identity, test_id: str, payload: TestStepCreate) -> TestStep:
    """Добавить шаг (в конец или на `position`), при желании — с копией слотов другого шага."""
    await _require_update(db, identity, "test_step.create", test_id, None)
    await _require_test(db, test_id, for_update=True)
    steps = await repo.list_by_test(db, test_id)
    if not steps:
        steps = [await ensure_first_step(db, test_id)]
    source = None
    if payload.copy_args_from_step_id:
        source = await _require_step(db, test_id, payload.copy_args_from_step_id)

    # Суффикс не задан — как у первого шага: фазы одного теста обычно
    # запускают `run.py` одним флагом (kernels — `-kn kernel`).
    suffix = payload.starter_suffix if "starter_suffix" in payload.model_fields_set else steps[0].starter_suffix
    step = await repo.create(db, {
        "id": test_step_id(), "test_id": test_id, "position": len(steps),
        "name": payload.name.strip(), "starter_suffix": suffix or None,
        "run_mode": payload.run_mode,
        "stand_setup": payload.stand_setup.model_dump(mode="json") if payload.stand_setup else None,
    })
    index = len(steps) if payload.position is None else min(payload.position, len(steps))
    ordered = [*steps[:index], step, *steps[index:]]
    _renumber(ordered)
    _check_first_is_full(ordered)
    copied = 0
    if source is not None:
        for slot in await arg_repo.list_by_step(db, source.id):
            await arg_repo.create(db, {
                "id": test_command_arg_id(), "test_id": test_id, "step_id": step.id, "position": slot.position,
                "kind": slot.kind, "literal_value": slot.literal_value,
                "variable_id": slot.variable_id, "override_value": slot.override_value,
            })
            copied += 1
    await db.commit()
    await db.refresh(step)
    audit_service.emit(
        "test_step.create", target_id=step.id, target_type="test_step",
        status="success", allowed=True,
        details={"test_id": test_id, "position": step.position, "run_mode": step.run_mode, "copied_args": copied},
    )
    return step


async def update_step(
    db: AsyncSession, identity: Identity, test_id: str, step_id: str, payload: TestStepUpdate,
) -> TestStep:
    """PATCH шага: имя, суффикс, способ запуска, настройка стенда."""
    await _require_update(db, identity, "test_step.update", test_id, step_id)
    await _require_test(db, test_id, for_update=True)
    step = await _require_step(db, test_id, step_id)
    changes = payload.model_dump(exclude_unset=True, mode="json")
    if not changes:
        return step
    if "run_mode" in changes and changes["run_mode"] is None:
        raise DomainValidationError(error_code="TEST_STEP_INVALID", message="run_mode cannot be null")
    if "name" in changes:
        changes["name"] = (changes["name"] or "").strip()
    if "starter_suffix" in changes:
        changes["starter_suffix"] = changes["starter_suffix"] or None
    await repo.update(db, step, changes)
    _check_first_is_full(await repo.list_by_test(db, test_id))
    await db.commit()
    await db.refresh(step)
    audit_service.emit(
        "test_step.update", target_id=step.id, target_type="test_step",
        status="success", allowed=True, details={"test_id": test_id, "fields": sorted(changes)},
    )
    return step


async def delete_step(db: AsyncSession, identity: Identity, test_id: str, step_id: str) -> None:
    """Удалить шаг вместе с его слотами. Последний шаг удалить нельзя."""
    await _require_update(db, identity, "test_step.delete", test_id, step_id)
    await _require_test(db, test_id, for_update=True)
    step = await _require_step(db, test_id, step_id)
    steps = await repo.list_by_test(db, test_id)
    if len(steps) <= 1:
        raise ConflictError(
            error_code="TEST_STEP_LAST",
            message="У теста должен остаться хотя бы один шаг",
            details={"step_id": step_id},
        )
    rest = [s for s in steps if s.id != step.id]
    _check_first_is_full(rest)
    await repo.delete(db, step)
    _renumber(rest)
    await db.commit()
    audit_service.emit(
        "test_step.delete", target_id=step_id, target_type="test_step",
        status="success", allowed=True, details={"test_id": test_id},
    )


async def reorder_steps(db: AsyncSession, identity: Identity, test_id: str, step_ids: list[str]) -> list[TestStep]:
    """Переставить шаги: `step_ids` — все шаги теста в новом порядке."""
    await _require_update(db, identity, "test_step.reorder", test_id, None)
    await _require_test(db, test_id, for_update=True)
    if len(step_ids) != len(set(step_ids)):
        raise DomainValidationError(
            error_code="TEST_STEP_ORDER_DUPLICATE_IDS", message="Шаг указан в новом порядке дважды",
        )
    steps = await repo.list_by_test(db, test_id)
    by_id = {step.id: step for step in steps}
    if set(step_ids) != set(by_id):
        raise ConflictError(
            error_code="TEST_STEP_ORDER_STALE",
            message="Шаги теста изменились — обновите список и повторите перестановку",
            details={
                "unexpected": [i for i in step_ids if i not in by_id],
                "missing": [i for i in by_id if i not in set(step_ids)],
            },
        )
    ordered = [by_id[i] for i in step_ids]
    _check_first_is_full(ordered)
    previous = [step.id for step in steps]
    _renumber(ordered)
    await db.commit()
    audit_service.emit(
        "test_step.reorder", target_id=test_id, target_type="test_definition",
        status="success", allowed=True,
        details={"test_id": test_id, "previous_order": previous, "new_order": list(step_ids)},
    )
    return await repo.list_by_test(db, test_id)
