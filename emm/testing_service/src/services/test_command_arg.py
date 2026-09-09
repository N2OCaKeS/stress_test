"""Use cases слотов конструктора команд (§3.2-3.3 плана миграции).

Редактирование слотов не заводит собственную защищаемую сущность — это часть
редактирования теста, которому они принадлежат, поэтому все write-операции
проверяют `(test_definition, *, update)`, а не отдельную матрицу.

`resolve_command()` — резолв уже сохранённых слотов в список аргументов
процесса на момент запуска. Возвращает `list[str]`, а не строку: это
принципиально для §3.2 плана миграции — воркер исполняет команду без
`shell=True`, поэтому не должно быть ни одной точки, где аргументы
склеиваются в единую строку до передачи в subprocess.
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import Action, CommandArgKind, EntityType
from src.core.exceptions import AuthorizationError, DomainValidationError, NotFoundError
from src.dependencies.auth import Identity
from src.models import TestCommandArg
from src.repositories import global_variable as global_variable_repo
from src.repositories import test_command_arg as repo
from src.repositories import test_definition as test_definition_repo
from src.schemas.test_command_arg import TestCommandArgCreate, TestCommandArgUpdate
from src.services import audit_service, permissions
from src.utils.ids import test_command_arg_id as new_id

logger = logging.getLogger(__name__)


async def _require_test(db: AsyncSession, test_id: str):
    """404, если тест не существует — слоты не бывают сиротами."""
    test = await test_definition_repo.get_by_id(db, test_id)
    if test is None:
        raise NotFoundError(
            error_code="TEST_DEFINITION_NOT_FOUND",
            message="Test definition not found",
        )
    return test


def _validate_kind_combo(
    kind: str, literal_value: str | None, variable_id: str | None,
) -> None:
    """literal ⟺ только literal_value; variable ⟺ только variable_id."""
    if kind == CommandArgKind.LITERAL:
        if not literal_value:
            raise DomainValidationError(
                error_code="COMMAND_ARG_KIND_MISMATCH",
                message="kind=literal requires a non-empty literal_value",
            )
        if variable_id is not None:
            raise DomainValidationError(
                error_code="COMMAND_ARG_KIND_MISMATCH",
                message="kind=literal must not set variable_id",
            )
    elif kind == CommandArgKind.VARIABLE:
        if not variable_id:
            raise DomainValidationError(
                error_code="COMMAND_ARG_KIND_MISMATCH",
                message="kind=variable requires a non-empty variable_id",
            )
        if literal_value is not None:
            raise DomainValidationError(
                error_code="COMMAND_ARG_KIND_MISMATCH",
                message="kind=variable must not set literal_value",
            )
    else:
        raise DomainValidationError(
            error_code="COMMAND_ARG_KIND_MISMATCH",
            message=f"Unknown kind: {kind!r}",
        )


async def _require_variable(db: AsyncSession, variable_id: str) -> None:
    """404, если слот ссылается на несуществующую переменную."""
    variable = await global_variable_repo.get_by_id(db, variable_id)
    if variable is None:
        raise NotFoundError(
            error_code="GLOBAL_VARIABLE_NOT_FOUND",
            message="Global variable not found",
        )


async def list_command_args(db: AsyncSession, test_id: str) -> list[TestCommandArg]:
    """Слоты теста по порядку. Read без проверки прав и без аудита."""
    await _require_test(db, test_id)
    return await repo.list_by_test(db, test_id)


async def create_command_arg(
    db: AsyncSession,
    identity: Identity,
    test_id: str,
    payload: TestCommandArgCreate,
) -> TestCommandArg:
    """Добавить слот в конец команды (или на явную `position`)."""
    try:
        await permissions.require_action(db, identity, EntityType.TEST_DEFINITION, Action.UPDATE)
    except AuthorizationError:
        audit_service.emit(
            "test_command_arg.create",
            target_type="test_command_arg",
            status="denied", allowed=False,
            details={"reason": "permission_denied", "test_id": test_id},
        )
        raise

    await _require_test(db, test_id)
    _validate_kind_combo(payload.kind, payload.literal_value, payload.variable_id)
    if payload.kind == CommandArgKind.VARIABLE:
        await _require_variable(db, payload.variable_id)

    position = payload.position
    if position is None:
        current_max = await repo.max_position(db, test_id)
        position = 0 if current_max is None else current_max + 1

    data = payload.model_dump(mode="json", exclude={"position"})
    data["id"] = new_id()
    data["test_id"] = test_id
    data["position"] = position
    obj = await repo.create(db, data)
    await db.commit()
    await db.refresh(obj)
    audit_service.emit(
        "test_command_arg.create",
        target_id=obj.id, target_type="test_command_arg",
        status="success", allowed=True,
        details={"test_id": test_id, "kind": obj.kind, "position": obj.position},
    )
    return obj


async def update_command_arg(
    db: AsyncSession,
    identity: Identity,
    test_id: str,
    arg_id: str,
    payload: TestCommandArgUpdate,
) -> TestCommandArg:
    """PATCH слота: значение, тип или позиция. Пустой диф → возврат без UPDATE."""
    try:
        await permissions.require_action(db, identity, EntityType.TEST_DEFINITION, Action.UPDATE)
    except AuthorizationError:
        audit_service.emit(
            "test_command_arg.update",
            target_id=arg_id, target_type="test_command_arg",
            status="denied", allowed=False,
            details={"reason": "permission_denied", "test_id": test_id},
        )
        raise

    await _require_test(db, test_id)
    obj = await repo.get_by_id(db, arg_id)
    if obj is None or obj.test_id != test_id:
        audit_service.emit(
            "test_command_arg.update",
            target_id=arg_id, target_type="test_command_arg",
            status="failure", allowed=True,
            details={"reason": "not_found", "test_id": test_id},
        )
        raise NotFoundError(
            error_code="TEST_COMMAND_ARG_NOT_FOUND",
            message="Test command arg not found",
        )

    changes = payload.model_dump(exclude_unset=True, mode="json")
    if not changes:
        return obj

    merged_kind = changes.get("kind", obj.kind)
    merged_literal = changes["literal_value"] if "literal_value" in changes else obj.literal_value
    merged_variable = changes["variable_id"] if "variable_id" in changes else obj.variable_id
    _validate_kind_combo(merged_kind, merged_literal, merged_variable)
    if merged_kind == CommandArgKind.VARIABLE:
        await _require_variable(db, merged_variable)

    await repo.update(db, obj, changes)
    await db.commit()
    await db.refresh(obj)
    audit_service.emit(
        "test_command_arg.update",
        target_id=obj.id, target_type="test_command_arg",
        status="success", allowed=True,
        details={"test_id": test_id, "fields": list(changes.keys())},
    )
    return obj


async def delete_command_arg(
    db: AsyncSession,
    identity: Identity,
    test_id: str,
    arg_id: str,
) -> None:
    """Удалить слот из команды."""
    try:
        await permissions.require_action(db, identity, EntityType.TEST_DEFINITION, Action.UPDATE)
    except AuthorizationError:
        audit_service.emit(
            "test_command_arg.delete",
            target_id=arg_id, target_type="test_command_arg",
            status="denied", allowed=False,
            details={"reason": "permission_denied", "test_id": test_id},
        )
        raise

    await _require_test(db, test_id)
    obj = await repo.get_by_id(db, arg_id)
    if obj is None or obj.test_id != test_id:
        audit_service.emit(
            "test_command_arg.delete",
            target_id=arg_id, target_type="test_command_arg",
            status="failure", allowed=True,
            details={"reason": "not_found", "test_id": test_id},
        )
        raise NotFoundError(
            error_code="TEST_COMMAND_ARG_NOT_FOUND",
            message="Test command arg not found",
        )
    await repo.delete(db, obj)
    await db.commit()
    audit_service.emit(
        "test_command_arg.delete",
        target_id=arg_id, target_type="test_command_arg",
        status="success", allowed=True,
        details={"test_id": test_id},
    )


async def _resolve_variable_slot(db: AsyncSession, slot: TestCommandArg, launch_context: dict[str, str]):
    """Находит переменную слота и её резолвленное (немаскированное) значение.

    Общая часть `resolve_command`/`resolve_command_masked`: обе идут по одним
    и тем же слотам и должны согласиться на одном и том же значении для
    variable-слота, отличаясь только тем, показывают его как есть или прячут
    за `is_sensitive`.
    """
    variable = await global_variable_repo.get_by_id(db, slot.variable_id)
    if variable is None:
        raise NotFoundError(
            error_code="GLOBAL_VARIABLE_NOT_FOUND",
            message="Global variable not found",
            details={"variable_id": slot.variable_id, "arg_id": slot.id},
        )

    if slot.override_value is not None:
        value = str(slot.override_value)
    else:
        if variable.code not in launch_context:
            raise DomainValidationError(
                error_code="LAUNCH_CONTEXT_VARIABLE_MISSING",
                message=f"launch_context is missing a value for '{variable.code}'",
                details={"code": variable.code, "arg_id": slot.id},
            )
        value = str(launch_context[variable.code])

    return variable, value


async def resolve_command(
    db: AsyncSession,
    test_id: str,
    launch_context: dict[str, str],
) -> list[str]:
    """Резолвит слоты теста в список аргументов процесса, по порядку `position`.

    `launch_context` — плоский `{variable_code: value}`, собранный воркером из
    реальных источников запуска (стенд, снэпшот, secret_service reveal — вне
    периметра этой функции). literal-слоты идут как есть; variable-слоты берут
    `override_value`, если он задан, иначе ищут значение переменной по её
    `code` в `launch_context`. Отсутствие нужного кода — ошибка запуска, не
    молчаливый пропуск аргумента.
    """
    await _require_test(db, test_id)
    slots = await repo.list_by_test(db, test_id)

    args: list[str] = []
    for slot in slots:
        if slot.kind == CommandArgKind.LITERAL:
            args.append(str(slot.literal_value))
            continue

        _variable, value = await _resolve_variable_slot(db, slot, launch_context)
        args.append(value)

    return args


async def resolve_command_masked(
    db: AsyncSession,
    test_id: str,
    launch_context: dict[str, str],
) -> list[str]:
    """Та же логика, что `resolve_command`, но для логов (§8.1 плана миграции).

    Слот variable, чья переменная заведена с `is_sensitive=true`, отдаёт
    `***` вместо реального значения — даже если значение пришло через
    `override_value`, маскировка привязана к самой переменной, а не к
    способу, которым слот её получил. `testing_worker` не видит
    `is_sensitive` вообще (это метаданные каталога, которых у него нет),
    поэтому маскированную версию обязан посчитать `testing_service`.
    """
    await _require_test(db, test_id)
    slots = await repo.list_by_test(db, test_id)

    args: list[str] = []
    for slot in slots:
        if slot.kind == CommandArgKind.LITERAL:
            args.append(str(slot.literal_value))
            continue

        variable, value = await _resolve_variable_slot(db, slot, launch_context)
        args.append("***" if variable.is_sensitive else value)

    return args
