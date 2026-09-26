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
import shlex

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import Action, CommandArgKind, DatesQuoting, EntityType
from src.core.exceptions import AppException, AuthorizationError, DomainValidationError, NotFoundError
from src.dependencies.auth import Identity
from src.models import TestCommandArg, TestDefinition, TestStand
from src.repositories import global_variable as global_variable_repo
from src.repositories import test_command_arg as repo
from src.repositories import test_definition as test_definition_repo
from src.repositories import test_step as test_step_repo
from src.schemas.test_command_arg import TestCommandArgCreate, TestCommandArgUpdate
from src.services import audit_service, permissions, test_step, variable_resolver
from src.utils.ids import test_command_arg_id as new_id

logger = logging.getLogger(__name__)


async def _require_test(db: AsyncSession, test_id: str, *, for_update: bool = False):
    """404, если тест не существует — слоты не бывают сиротами."""
    test = await test_definition_repo.get_by_id(db, test_id, for_update=for_update)
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


async def list_command_args(
    db: AsyncSession, identity: Identity, test_id: str, step_id: str | None = None,
) -> list[TestCommandArg]:
    """Слоты шага теста по порядку (без `step_id` — первого шага).

    Своего скоупа у слотов нет — видимость наследуется от теста-владельца
    (в команде теста лежат пути, имена стендов и ссылки на переменные отдела).
    """
    test = await _require_test(db, test_id)
    permissions.require_own_department(identity, test.department_id)
    step_id = (await test_step.step_for_slots(db, test_id, step_id)).id
    await db.commit()
    return await repo.list_by_step(db, step_id)


async def create_command_arg(
    db: AsyncSession,
    identity: Identity,
    test_id: str,
    payload: TestCommandArgCreate,
) -> TestCommandArg:
    """Добавить слот в конец команды шага (или на явную `position`)."""
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

    await _require_test(db, test_id, for_update=True)
    step = await test_step.step_for_slots(db, test_id, payload.step_id)
    _validate_kind_combo(payload.kind, payload.literal_value, payload.variable_id)
    if payload.kind == CommandArgKind.VARIABLE:
        await _require_variable(db, payload.variable_id)
        await variable_resolver.validate_override_template(db, payload.override_value)

    position = payload.position
    if position is None:
        current_max = await repo.max_position(db, step.id)
        position = 0 if current_max is None else current_max + 1

    data = payload.model_dump(mode="json", exclude={"position", "step_id"})
    data["id"] = new_id()
    data["test_id"] = test_id
    data["step_id"] = step.id
    data["position"] = position
    obj = await repo.create(db, data)
    await db.commit()
    await db.refresh(obj)
    audit_service.emit(
        "test_command_arg.create",
        target_id=obj.id, target_type="test_command_arg",
        status="success", allowed=True,
        details={"test_id": test_id, "step_id": obj.step_id, "kind": obj.kind, "position": obj.position},
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

    await _require_test(db, test_id, for_update=True)
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
        if "override_value" in changes:
            await variable_resolver.validate_override_template(db, changes["override_value"])

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

    await _require_test(db, test_id, for_update=True)
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


async def copy_command_args(
    db: AsyncSession, identity: Identity, test_id: str, source_test_id: str,
    *, step_id: str | None = None, source_step_id: str | None = None,
) -> list[TestCommandArg]:
    """Заменить слоты шага копией слотов шага другого теста в одной транзакции.

    Шаги не заданы — первые шаги обоих тестов (одношаговые тесты).
    """
    details = {"source_test_id": source_test_id}
    try:
        await permissions.require_action(db, identity, EntityType.TEST_DEFINITION, Action.UPDATE)
    except AuthorizationError:
        audit_service.emit(
            "test_command_arg.copy", target_id=test_id, target_type="test_definition",
            status="denied", allowed=False,
            details={**details, "reason": "permission_denied"},
        )
        raise

    try:
        if test_id == source_test_id:
            raise DomainValidationError(
                error_code="COMMAND_COPY_SAME_TEST",
                message="Choose another test to copy parameters from",
            )
        await _require_test(db, test_id, for_update=True)
        await _require_test(db, source_test_id)
        target_step = await test_step.step_for_slots(db, test_id, step_id)
        source_step = await test_step.step_for_slots(db, source_test_id, source_step_id)
        source = await repo.list_by_step(db, source_step.id)
        if not source:
            raise DomainValidationError(
                error_code="COMMAND_COPY_SOURCE_EMPTY",
                message="The source test has no command parameters",
            )

        await repo.delete_by_step(db, target_step.id)
        copied = []
        for position, arg in enumerate(source):
            copied.append(await repo.create(db, {
                "id": new_id(), "test_id": test_id, "step_id": target_step.id, "position": position,
                "kind": arg.kind, "literal_value": arg.literal_value,
                "variable_id": arg.variable_id, "override_value": arg.override_value,
            }))
        await db.commit()
    except Exception as exc:
        await db.rollback()
        audit_service.emit(
            "test_command_arg.copy", target_id=test_id, target_type="test_definition",
            status="failure", allowed=True,
            details={**details, "reason": getattr(exc, "error_code", type(exc).__name__)},
        )
        raise

    audit_service.emit(
        "test_command_arg.copy", target_id=test_id, target_type="test_definition",
        status="success", allowed=True, details={**details, "count": len(copied)},
    )
    return copied


async def _resolve_slots(
    db: AsyncSession,
    test_id: str,
    launch_context: dict[str, str],
    *,
    stand: TestStand | None = None,
    debug: bool = False,
    department_id: str | None = None,
    context: variable_resolver.ResolveContext | None = None,
    step_id: str | None = None,
) -> list[variable_resolver.Resolved]:
    """Токены шага по порядку `position` с признаком sensitive (см. `_resolve_test_slots`)."""
    _test, tokens = await _resolve_test_slots(
        db, test_id, launch_context, stand=stand, debug=debug, department_id=department_id,
        context=context, step_id=step_id,
    )
    return tokens


async def _step_slots(db: AsyncSession, test_id: str, step_id: str | None) -> list[TestCommandArg]:
    """Слоты шага `step_id`; без него — первого шага теста."""
    if step_id is None:
        steps = await test_step_repo.list_by_test(db, test_id)
        if not steps:
            return []
        step_id = steps[0].id
    return await repo.list_by_step(db, step_id)


async def _resolve_test_slots(
    db: AsyncSession,
    test_id: str,
    launch_context: dict[str, str],
    *,
    stand: TestStand | None = None,
    debug: bool = False,
    department_id: str | None = None,
    context: variable_resolver.ResolveContext | None = None,
    step_id: str | None = None,
) -> tuple[TestDefinition, list[variable_resolver.Resolved]]:
    """Общий проход по слотам шага теста: карточка теста и токены по порядку `position`.

    `step_id` — шаг многоступенчатого теста; пусто — первый шаг.

    Один `ResolveContext` на весь проход — переменная, упомянутая в
    нескольких слотах и шаблонах, резолвится (и раскрывается в
    secret_service) один раз. `department_id` по умолчанию — отдел стенда
    (CONTRACTS.md C1). `context` — уже созданный контекст вызывающего
    (`queue.claim_next` резолвит в нём ещё и аргументы `starter.sh`, чтобы
    карточка версии ОС и секреты запрашивались один раз на claim).
    """
    test = await _require_test(db, test_id)
    slots = await _step_slots(db, test_id, step_id)
    ctx = context or variable_resolver.ResolveContext(
        db=db,
        department_id=department_id or (stand.department_id if stand is not None else None),
        test=test,
        stand=stand,
        launch_context=dict(launch_context or {}),
        debug=debug,
    )

    tokens: list[variable_resolver.Resolved] = []
    for slot in slots:
        if slot.kind == CommandArgKind.LITERAL:
            tokens.append(variable_resolver.Resolved(str(slot.literal_value)))
            continue

        variable = await global_variable_repo.get_by_id(db, slot.variable_id)
        if variable is None:
            raise NotFoundError(
                error_code="GLOBAL_VARIABLE_NOT_FOUND",
                message="Global variable not found",
                details={"variable_id": slot.variable_id, "arg_id": slot.id},
            )
        try:
            tokens.append(await variable_resolver.resolve_slot_value(ctx, variable, slot.override_value))
        except AppException as exc:
            exc.details = {**exc.details, "arg_id": slot.id, "slot_variable": variable.code}
            raise

    return test, tokens


def _quote_legacy(token: str) -> str:
    # `backup_image.py:296,297,307` оборачивал в "…" значения, в которых
    # бывают пробелы (`--confluence-parent-page "{parent_page}"`,
    # `-tcas "{args.TCASE}"`); остальное шло как есть.
    return f'"{token}"' if any(ch.isspace() for ch in token) else token


_QUOTERS = {
    DatesQuoting.SHELL: shlex.quote,
    DatesQuoting.LEGACY: _quote_legacy,
    DatesQuoting.RAW: lambda token: token,
}


def join_dates_tokens(tokens: list[str], quoting: str | None) -> str:
    """Склеить токены в строку `dates.conf` по режиму `test_definitions.dates_quoting` (D4).

    `run.py` веток подставляет эту строку в команду через `shell=True`, так
    что в режиме `shell` `shlex.split(результат) == tokens` для любых
    значений. Неизвестный/пустой режим — `shell` (значение по умолчанию).
    """
    quote = _QUOTERS.get(quoting or DatesQuoting.SHELL, shlex.quote)
    return " ".join(quote(token) for token in tokens)


async def resolve_command(
    db: AsyncSession,
    test_id: str,
    launch_context: dict[str, str],
    *,
    stand: TestStand | None = None,
    debug: bool = False,
    step_id: str | None = None,
) -> list[str]:
    """Резолвит слоты теста в список аргументов процесса, по порядку `position`.

    literal-слоты идут как есть; variable-слоты берут `override_value` (с
    подстановками `{CODE}`), если он задан, иначе значение переменной из её
    источника (`services/variable_resolver.py`). `launch_context` — то, что
    выбрал постановщик (`RC`/`KERNEL`/`MODE`); `stand`/`debug` нужны
    переменным источника `stand`, `department_integration` и шаблонам с
    debug-условием. Невозможность получить значение — ошибка запуска, не
    молчаливый пропуск аргумента.
    """
    tokens = await _resolve_slots(db, test_id, launch_context, stand=stand, debug=debug, step_id=step_id)
    return [t.value for t in tokens]


async def resolve_command_masked(
    db: AsyncSession,
    test_id: str,
    launch_context: dict[str, str],
    *,
    stand: TestStand | None = None,
    debug: bool = False,
    step_id: str | None = None,
) -> list[str]:
    """Та же логика, что `resolve_command`, но для логов (§8.1 плана миграции).

    Токен, в который попала хоть одна `is_sensitive`-переменная — напрямую,
    через `override_value` или через шаблон — отдаётся как `***`.
    `testing_worker` не видит `is_sensitive` вообще, поэтому маскированную
    версию обязан посчитать `testing_service`.
    """
    tokens = await _resolve_slots(db, test_id, launch_context, stand=stand, debug=debug, step_id=step_id)
    return [t.masked for t in tokens]


async def resolve_dates(
    db: AsyncSession,
    test_id: str,
    launch_context: dict[str, str],
    *,
    stand: TestStand | None = None,
    debug: bool = False,
    context: variable_resolver.ResolveContext | None = None,
    step_id: str | None = None,
) -> tuple[str, str]:
    """Содержимое `dates.conf` и его маскированная версия за один проход резолва.

    `queue.py::claim_next` берёт обе строки сразу: два отдельных прохода
    раскрывали бы секреты в secret_service дважды. Обе строки экранируются
    одинаково, по `dates_quoting` теста (маска `***` — тоже токен).
    """
    test, tokens = await _resolve_test_slots(
        db, test_id, launch_context, stand=stand, debug=debug, context=context, step_id=step_id,
    )
    return (
        join_dates_tokens([t.value for t in tokens], test.dates_quoting),
        join_dates_tokens([t.masked for t in tokens], test.dates_quoting),
    )


async def resolve_dates_content(
    db: AsyncSession,
    test_id: str,
    launch_context: dict[str, str],
    *,
    stand: TestStand | None = None,
    debug: bool = False,
    step_id: str | None = None,
) -> str:
    """Резолвит слоты теста в содержимое файла `dates.conf` (одна строка).

    Та же логика резолва, что `resolve_command` — но слоты теста здесь
    описывают не argv конечного скрипта, а флаги, которые легаси
    `backup_image.py` раньше писало в `dates_<STAND>.conf`. `testing_worker`
    кладёт результат на стенд по SFTP до того, как позвать `starter.sh`
    (см. `services/queue.py::claim_next`). Токены экранируются по
    `test_definitions.dates_quoting` (`join_dates_tokens`, D4).
    """
    content, _masked = await resolve_dates(db, test_id, launch_context, stand=stand, debug=debug, step_id=step_id)
    return content


async def resolve_dates_content_masked(
    db: AsyncSession,
    test_id: str,
    launch_context: dict[str, str],
    *,
    stand: TestStand | None = None,
    debug: bool = False,
    step_id: str | None = None,
) -> str:
    """Та же логика, что `resolve_dates_content`, но для логов/аудита — см. `resolve_command_masked`."""
    _content, masked = await resolve_dates(db, test_id, launch_context, stand=stand, debug=debug, step_id=step_id)
    return masked
