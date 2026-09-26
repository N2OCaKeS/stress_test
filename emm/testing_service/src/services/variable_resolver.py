"""Резолвер глобальных переменных: источники, шаблоны, маскировка.

Переменная каталога описывает, ОТКУДА взять значение (`source`) и НА ЧТО она
ссылается в этом источнике (`source_ref`). Формулы (заголовок страницы
Confluence, имя test cycle, рубрика отчёта) — это данные: переменные-шаблоны
`{"template": "... {CODE} ..."}`, которые сид-миграция заполняет легаси-
формулами. В этом модуле только механизм: как достать значение каждого
источника и как собрать шаблон.

Единица работы — `ResolveContext`: один объект на один claim (или одно превью
команды). Внутри него кешируются значения переменных, каталог, ответ
connection-info и reveal-ы secret_service, поэтому одна и та же переменная,
упомянутая в десятке слотов и шаблонов, резолвится один раз.

Маскировка: каждое значение несёт признак `sensitive`. Он истинен, если сама
переменная `is_sensitive` или в неё через шаблон попала хоть одна sensitive-
переменная. Маскированная версия такого значения — `***` целиком: частичное
затирание подстроки не гарантирует, что секрет не восстановится из остатка.

Расширение: новый источник (`zephyr_folder` —, `role` —, …)
добавляет значение в `GlobalVariableSource`, валидатор своего `source_ref` в
`_REF_VALIDATORS` и резолвер в `_RESOLVERS` — остальной код его не знает.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import GlobalVariableSource as Source
from src.core.exceptions import ConflictError, DomainValidationError
from src.models import (
    DepartmentIntegrationSettings,
    GlobalVariable,
    TestCommandArg,
    TestDefinition,
    TestStand,
)
from src.repositories import department_integration_settings as dis_repo
from src.repositories import department_test_settings as dts_repo
from src.repositories import global_variable as global_variable_repo
from src.repositories import zephyr_folder as zephyr_folder_repo
from src.services import secret_client, server_client
from src.services import test_account as test_account_svc
from src.services.stand_target import target_of

MASK = "***"

# Подстановка — `{CODE}` с кодом в формате кода переменной. Всё остальное в
# фигурных скобках (`{foo}`, `{ 1 }`, JSON) остаётся текстом как есть: шаблоны
# пишут люди, и случайная скобка не должна превращаться в ошибку.
_PLACEHOLDER_RE = re.compile(r"\{([A-Z][A-Z0-9_]*)\}")

# Белый список полей `test_definitions` для `test_field`. `short_name` — колонка
# из (D6); пустая — срабатывает `fallback`.
TEST_FIELDS: frozenset[str] = frozenset(
    {"short_name", "full_name", "changelog_component", "category", "code"}
)

# Поля стенда. `number` — цифры из `legacy_token` (`stand3` → `3`, легаси
# `allta_back.py:169`), `host` — из connection-info server_service, `id` —
# внутренний id стенда (fallback для стендов без легаси-имени).
STAND_FIELDS: frozenset[str] = frozenset({"legacy_token", "number", "host", "id"})
# Поля `stand_ref`: стенд указан в самой переменной.
STAND_REF_FIELDS: frozenset[str] = frozenset({"host", "legacy_token", "number"})

# Колонки `department_integration_settings`, на которые можно сослаться.
# Технические колонки отрезаны; всё прочее — настройки отдела.
_DI_EXCLUDED_COLUMNS = frozenset({"id", "department_id", "created_at", "updated_at"})
DEPARTMENT_INTEGRATION_FIELDS: frozenset[str] = frozenset(
    c.name for c in DepartmentIntegrationSettings.__table__.columns
    if c.name not in _DI_EXCLUDED_COLUMNS
)
CREDENTIAL_PARTS: frozenset[str] = frozenset({"login", "secret"})

OS_VERSION_FIELDS: frozenset[str] = frozenset({"name", "rc_number", "is_urgent_update"})
TEST_ACCOUNT_FIELDS: frozenset[str] = frozenset({"login", "password", "home"})
# Поля записи `zephyr_folders`: id папки для `-fti` и её путь.
ZEPHYR_FOLDER_FIELDS: frozenset[str] = frozenset({"folder_tree_id", "folder_path"})

# Условие применения шаблона: `debug` — только в debug-запуске, `not_debug` —
# только в обычном. Вне условия значение шаблона — пустая строка.
TEMPLATE_CONDITIONS: frozenset[str] = frozenset({"debug", "not_debug"})


def is_credential_field(name: str) -> bool:
    """Поле-ссылка на credential secret_service (`credential_id`, `git_credential_id`, …)."""
    return name == "credential_id" or name.endswith("_credential_id")


def template_codes(text: str | None) -> list[str]:
    """Коды переменных, на которые ссылается строка-шаблон, в порядке появления."""
    if not text:
        return []
    return _PLACEHOLDER_RE.findall(text)


# ── значения ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Resolved:
    """Значение переменной (или токена) и признак «в нём есть секрет»."""

    value: str
    sensitive: bool = False

    @property
    def masked(self) -> str:
        return MASK if self.sensitive else self.value


@dataclass
class ResolveContext:
    """Контекст резолва одного claim'а (CONTRACTS.md C1).

    `department_id` — отдел СТЕНДА: чьи интеграции и учётки использовать.
    `step`/`roles` — точки расширения, здесь не читаются.
    """

    db: AsyncSession
    department_id: str | None
    test: TestDefinition | None
    stand: TestStand | None
    launch_context: dict[str, Any]
    debug: bool = False
    step: Any = None
    roles: dict[str, TestStand] | None = None
    # Значения, известные только на claim (пути файлов профиля запуска,
    # id item'а —). Перекрывают переменные каталога с тем же кодом.
    locals: dict[str, str] = field(default_factory=dict)

    _values: dict[str, Resolved] = field(default_factory=dict, repr=False)
    _catalog: dict[str, GlobalVariable] | None = field(default=None, repr=False)
    _connection: dict | None = field(default=None, repr=False)
    _integration_loaded: bool = field(default=False, repr=False)
    _integration: DepartmentIntegrationSettings | None = field(default=None, repr=False)
    _reveals: dict[str, tuple[str, str]] = field(default_factory=dict, repr=False)
    _os_version: server_client.OsVersionInfo | None = field(default=None, repr=False)
    # `stand_ref`: стенды и connection-info чужих стендов по id.
    _ref_stands: dict[str, TestStand | None] = field(default_factory=dict, repr=False)
    _ref_connections: dict[str, dict] = field(default_factory=dict, repr=False)

    async def catalog(self) -> dict[str, GlobalVariable]:
        if self._catalog is None:
            self._catalog = {v.code: v for v in await global_variable_repo.list_every(self.db)}
        return self._catalog

    async def connection(self) -> dict:
        if self._connection is None:
            stand = _require_stand(self, "host")
            self._connection = await server_client.get_stand_connection_info(target_of(stand))
        return self._connection

    async def ref_stand(self, stand_id: str) -> TestStand | None:
        """Стенд пула по id для `stand_ref`; стенд задания — без запроса в БД."""
        if self.stand is not None and self.stand.id == stand_id:
            return self.stand
        if stand_id not in self._ref_stands:
            self._ref_stands[stand_id] = await self.db.get(TestStand, stand_id)
        return self._ref_stands[stand_id]

    async def ref_connection(self, stand: TestStand) -> dict:
        if self.stand is not None and self.stand.id == stand.id:
            return await self.connection()
        if stand.id not in self._ref_connections:
            self._ref_connections[stand.id] = await server_client.get_stand_connection_info(target_of(stand))
        return self._ref_connections[stand.id]

    async def integration(self) -> DepartmentIntegrationSettings | None:
        if not self._integration_loaded:
            if self.department_id:
                self._integration = await dis_repo.get_by_department(self.db, self.department_id)
            self._integration_loaded = True
        return self._integration

    async def os_version(self, needed_by: str) -> server_client.OsVersionInfo:
        """Карточка версии ОС из `launch_context["RC"]`, одна на контекст."""
        if self._os_version is None:
            rc = self.launch_context.get("RC")
            if _is_empty(rc):
                raise DomainValidationError(
                    error_code="LAUNCH_CONTEXT_VARIABLE_MISSING",
                    message="launch_context is missing a value for 'RC'",
                    details={"code": "RC", "needed_by": needed_by},
                )
            self._os_version = await server_client.resolve_os_version_info(str(rc))
        return self._os_version

    async def reveal(self, credential_id: str) -> tuple[str, str]:
        if credential_id not in self._reveals:
            self._reveals[credential_id] = await secret_client.reveal_credential(credential_id)
        return self._reveals[credential_id]

    def resolved_values(self) -> dict[str, Resolved]:
        """Уже зарезолвленные переменные каталога в порядке резолва (для превью запуска)."""
        return dict(self._values)


# ── ошибки ───────────────────────────────────────────────────────────────────

def _missing(code: str, message: str, **details) -> DomainValidationError:
    return DomainValidationError(
        error_code="VARIABLE_VALUE_MISSING", message=message, details={"code": code, **details},
    )


def _not_configured(code: str, source: str, message: str) -> DomainValidationError:
    return DomainValidationError(
        error_code="VARIABLE_SOURCE_NOT_CONFIGURED",
        message=message,
        details={"code": code, "source": source},
    )


def _require_stand(ctx: ResolveContext, what: str) -> TestStand:
    if ctx.stand is None:
        raise DomainValidationError(
            error_code="VARIABLE_CONTEXT_MISSING",
            message=f"Stand is not known in this resolve context (needed for stand.{what})",
            details={"needs": "stand"},
        )
    return ctx.stand


# ── резолв по источникам ─────────────────────────────────────────────────────

Resolver = Callable[[ResolveContext, GlobalVariable, tuple[str, ...]], Awaitable[Resolved]]


def _ref(variable: GlobalVariable) -> dict:
    return variable.source_ref or {}


async def _from_launch_context(ctx, variable, _stack) -> Resolved:
    code = variable.code
    if code not in ctx.launch_context or ctx.launch_context[code] is None:
        raise DomainValidationError(
            error_code="LAUNCH_CONTEXT_VARIABLE_MISSING",
            message=f"launch_context is missing a value for '{code}'",
            details={"code": code},
        )
    return Resolved(str(ctx.launch_context[code]))


async def _from_static(ctx, variable, stack) -> Resolved:
    ref = _ref(variable)
    if "value" in ref:
        return Resolved(str(ref["value"] if ref["value"] is not None else ""))
    # `source_ref = null` — значение, как и раньше, приходит снаружи.
    return await _from_launch_context(ctx, variable, stack)


async def _from_template(ctx, variable, stack) -> Resolved:
    ref = _ref(variable)
    when = ref.get("when")
    if when == "debug" and not ctx.debug or when == "not_debug" and ctx.debug:
        return Resolved("")
    return await render(ctx, str(ref.get("template") or ""), _stack=stack)


def _field_chain(ref: dict) -> list[str]:
    chain = [ref["field"]]
    if ref.get("fallback"):
        chain.append(ref["fallback"])
    return chain


def _is_empty(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


async def _from_test_field(ctx, variable, _stack) -> Resolved:
    if ctx.test is None:
        raise DomainValidationError(
            error_code="VARIABLE_CONTEXT_MISSING",
            message="Test is not known in this resolve context",
            details={"code": variable.code, "needs": "test"},
        )
    chain = _field_chain(_ref(variable))
    for name in chain:
        value = getattr(ctx.test, name, None)
        if not _is_empty(value):
            return Resolved(str(value))
    raise _missing(
        variable.code, f"Test field {'/'.join(chain)} is empty for test '{ctx.test.code}'",
        fields=chain, test_code=ctx.test.code,
    )


async def _stand_field(ctx: ResolveContext, name: str) -> str | None:
    stand = _require_stand(ctx, name)
    if name == "legacy_token":
        return stand.legacy_token
    if name == "id":
        return stand.id
    if name == "number":
        digits = "".join(ch for ch in (stand.legacy_token or "") if ch.isdigit())
        return digits or None
    if name == "host":
        return (await ctx.connection()).get("host")
    return None


async def _from_stand(ctx, variable, _stack) -> Resolved:
    chain = _field_chain(_ref(variable))
    for name in chain:
        value = await _stand_field(ctx, name)
        if not _is_empty(value):
            return Resolved(str(value))
    raise _missing(
        variable.code, f"Stand field {'/'.join(chain)} is empty for stand '{ctx.stand.id}'",
        fields=chain, stand_id=ctx.stand.id,
    )


async def _from_stand_ref(ctx, variable, _stack) -> Resolved:
    """Поле конкретного стенда из `source_ref.stand_id`."""
    ref = _ref(variable)
    stand_id, name = ref.get("stand_id"), ref.get("field")
    stand = await ctx.ref_stand(stand_id)
    if stand is None:
        raise _missing(
            variable.code, f"Stand '{stand_id}' referenced by variable '{variable.code}' does not exist",
            stand_id=stand_id,
        )
    if name == "legacy_token":
        value = stand.legacy_token
    elif name == "number":
        value = "".join(ch for ch in (stand.legacy_token or "") if ch.isdigit())
    else:
        value = (await ctx.ref_connection(stand)).get("host")
    if _is_empty(value):
        raise _missing(
            variable.code, f"Stand field {name} is empty for stand '{stand_id}'",
            fields=[name], stand_id=stand_id,
        )
    return Resolved(str(value))


async def _from_department_integration(ctx, variable, _stack) -> Resolved:
    ref = _ref(variable)
    chain = _field_chain(ref)
    settings = await ctx.integration()
    name, value = chain[0], None
    for candidate in chain:
        value = getattr(settings, candidate, None) if settings is not None else None
        if not _is_empty(value):
            name = candidate
            break
    if _is_empty(value):
        raise DomainValidationError(
            error_code="DEPARTMENT_INTEGRATION_NOT_CONFIGURED",
            message=(
                f"department_integration_settings.{'/'.join(chain)} is not configured "
                "for the stand's department"
            ),
            details={
                "code": variable.code, "field": chain[0], "fields": chain,
                "department_id": ctx.department_id,
            },
        )
    part = ref.get("credential_part")
    if not part:
        return Resolved(str(value))
    login, secret = await ctx.reveal(str(value))
    revealed = login if part == "login" else secret
    if _is_empty(revealed):
        raise _missing(
            variable.code, f"Credential {name} has an empty {part}",
            field=name, credential_part=part,
        )
    # Секрет — всегда sensitive, даже если переменная заведена без флага
    # (валидация этого не допускает, но старые строки могли бы).
    return Resolved(revealed, sensitive=part == "secret")


def is_urgent_update_version(info: server_client.OsVersionInfo) -> bool:
    """Версия — срочное обновление (UU): флаг карточки или маркер `UU` в имени.

    Легаси определял UU только по имени (`x.y.z.UU.n.m`, `allta_back.py:352-359`),
    флаг `is_urgent_update` на карточке ставится вручную и может быть не
    проставлен — поэтому достаточно любого из двух признаков.
    """
    return info.is_urgent_update or any(part.upper() == "UU" for part in info.name.split("."))


def os_version_name_segments(
    info: server_client.OsVersionInfo, segments: int | None, uu_segments: int | None,
) -> str:
    """Имя версии, усечённое до первых N сегментов по правилу из `source_ref`.

    У UU-версии берётся `uu_segments` (если задан), иначе `segments`; `null`
    обоих — имя целиком. Сегментов меньше N — имя как есть (легаси `else`-ветка
    `allta_back.py:360-361` тоже оставляла версию нетронутой).
    """
    count = uu_segments if uu_segments and is_urgent_update_version(info) else segments
    if not count:
        return info.name
    return ".".join(info.name.split(".")[:count])


async def _from_os_version(ctx, variable, _stack) -> Resolved:
    """Поле карточки версии ОС из `launch_context["RC"]` (`osv_<hex>`).

    `RC` остаётся id каталога server_service (им параметризуются
    `prepare-for-test` и ACS); человеческое имя (`1.8.1.6`) и производные —
    отсюда. Карточка запрашивается один раз на контекст (`ctx.os_version()`).
    """
    ref = _ref(variable)
    info = await ctx.os_version(variable.code)
    name = ref.get("field")
    if name == "name":
        return Resolved(os_version_name_segments(info, ref.get("segments"), ref.get("uu_segments")))
    if name == "is_urgent_update":
        return Resolved("true" if is_urgent_update_version(info) else "false")
    if name == "rc_number":
        if _is_empty(info.rc_number):
            raise _missing(
                variable.code, f"OS version '{info.name}' has no rc_number",
                field="rc_number", os_version_id=ctx.launch_context.get("RC"),
            )
        return Resolved(str(info.rc_number))
    raise _not_configured(variable.code, Source.OS_VERSION, f"Unknown os_version field '{name}'")


async def _from_test_account(ctx, variable, _stack) -> Resolved:
    """Поле тестовой учётки отдела стенда.

    `login` → `TEST_USER`, `password` → `TEST_PASSWORD` (всегда sensitive),
    `home` → `TEST_HOME`/`HOME_DIR` по шаблону учётки. Credential
    раскрывается один раз на контекст (`ctx.reveal`). Учётки нет —
    `TEST_ACCOUNT_NOT_CONFIGURED` с путём в администрирование.
    """
    name = _ref(variable)["field"]
    if not ctx.department_id:
        raise DomainValidationError(
            error_code="VARIABLE_CONTEXT_MISSING",
            message="Department is not known in this resolve context (needed for test_account)",
            details={"code": variable.code, "needs": "department"},
        )
    credential_id = await test_account_svc.require_credential_id(ctx.db, ctx.department_id)
    login, secret = await ctx.reveal(credential_id)
    account = test_account_svc.decode_secret(login, secret)
    if name == "login":
        return Resolved(account.login)
    if name == "password":
        return Resolved(account.password, sensitive=True)
    row = await dts_repo.get_by_department(ctx.db, ctx.department_id)
    return Resolved(test_account_svc.render_home(
        row.test_account_home_template if row is not None else None, account.login,
    ))


async def _from_zephyr_folder(ctx, variable, _stack) -> Resolved:
    """Папка Zephyr отдела стенда для версии ОС из `launch_context["RC"]`.

    Запись заводит генерация СТП (`services/zephyr_folder.py`) или человек в
    UI. Нет записи или в ней нет нужного поля — ошибка с подсказкой, а не
    пустая строка: скрипт с пустым `-fti` не найдёт свой прогон в Zephyr.
    """
    name = _ref(variable)["field"]
    os_version_id = ctx.launch_context.get("RC")
    if _is_empty(os_version_id):
        raise DomainValidationError(
            error_code="LAUNCH_CONTEXT_VARIABLE_MISSING",
            message="launch_context is missing a value for 'RC'",
            details={"code": "RC", "needed_by": variable.code},
        )
    if not ctx.department_id:
        raise DomainValidationError(
            error_code="VARIABLE_CONTEXT_MISSING",
            message="Department is not known in this resolve context (needed for zephyr_folder)",
            details={"code": variable.code, "needs": "department"},
        )
    record = await zephyr_folder_repo.get_by_department_and_os_version(
        ctx.db, ctx.department_id, str(os_version_id),
    )
    value = getattr(record, name, None) if record is not None else None
    if _is_empty(value):
        raise _missing(
            variable.code,
            f"Zephyr folder {name} is not known for this department and OS version: "
            "generate the STP or set the folder id manually",
            field=name, department_id=ctx.department_id, os_version_id=str(os_version_id),
            hint="сгенерируйте СТП или задайте id папки Zephyr вручную на странице СТП",
        )
    return Resolved(str(value))


_RESOLVERS: dict[str, Resolver] = {
    Source.LAUNCH_CONTEXT: _from_launch_context,
    Source.PER_TEST_OVERRIDE: _from_launch_context,
    Source.SECRET_SERVICE: _from_launch_context,
    Source.STATIC: _from_static,
    Source.TEMPLATE: _from_template,
    Source.TEST_FIELD: _from_test_field,
    Source.STAND: _from_stand,
    Source.DEPARTMENT_INTEGRATION: _from_department_integration,
    Source.OS_VERSION: _from_os_version,
    Source.TEST_ACCOUNT: _from_test_account,
    Source.ZEPHYR_FOLDER: _from_zephyr_folder,
    Source.STAND_REF: _from_stand_ref,
}


async def resolve_variable(
    ctx: ResolveContext, variable: GlobalVariable, *, _stack: tuple[str, ...] = (),
) -> Resolved:
    """Значение переменной в контексте, с кешем и защитой от циклов."""
    code = variable.code
    if code in ctx._values:
        return ctx._values[code]
    if code in _stack:
        raise DomainValidationError(
            error_code="VARIABLE_TEMPLATE_CYCLE",
            message=f"Template cycle: {' -> '.join((*_stack, code))}",
            details={"cycle": [*_stack, code]},
        )
    resolver = _RESOLVERS.get(variable.source)
    if resolver is None:
        raise _not_configured(code, variable.source, f"Unknown variable source '{variable.source}'")
    result = await resolver(ctx, variable, (*_stack, code))
    if variable.is_sensitive and not result.sensitive:
        result = Resolved(result.value, sensitive=True)
    ctx._values[code] = result
    return result


async def resolve_code(ctx: ResolveContext, code: str, *, _stack: tuple[str, ...] = ()) -> Resolved:
    if code in ctx.locals:
        return Resolved(ctx.locals[code])
    variable = (await ctx.catalog()).get(code)
    if variable is None:
        raise DomainValidationError(
            error_code="VARIABLE_TEMPLATE_UNKNOWN",
            message=f"Template references unknown variable '{code}'",
            details={"unknown": [code], "referenced_from": list(_stack)},
        )
    return await resolve_variable(ctx, variable, _stack=_stack)


async def render(ctx: ResolveContext, text: str, *, _stack: tuple[str, ...] = ()) -> Resolved:
    """Подставить `{CODE}` в строку. Sensitive, если подставлена хоть одна sensitive-переменная."""
    parts: list[str] = []
    sensitive = False
    pos = 0
    for match in _PLACEHOLDER_RE.finditer(text):
        parts.append(text[pos:match.start()])
        value = await resolve_code(ctx, match.group(1), _stack=_stack)
        parts.append(value.value)
        sensitive = sensitive or value.sensitive
        pos = match.end()
    parts.append(text[pos:])
    return Resolved("".join(parts), sensitive=sensitive)


async def render_args(ctx: ResolveContext, text: str) -> list[Resolved]:
    """Шаблон списка аргументов процесса: токены по пробелам, подстановки в каждом.

    Делится ДО подстановки: значение, ставшее пустым (`{STARTER_SUFFIX}` у
    теста без суффикса), остаётся отдельным пустым позиционным аргументом, а
    значение с пробелом не разваливается на несколько аргументов.
    """
    return [await render(ctx, token) for token in text.split()]


async def resolve_slot_value(
    ctx: ResolveContext, variable: GlobalVariable, override_value: str | None,
) -> Resolved:
    """Значение variable-слота: `override_value` (с подстановками) или сама переменная.

    `override_value` — «переопределение на тесте» (D5): тот же шаблон, что у
    переменной-`template`, но заданный на слоте. Маскировка привязана к
    переменной слота: sensitive-переменная прячется, даже если её значение
    пришло через override.
    """
    if override_value is None:
        return await resolve_variable(ctx, variable)
    result = await render(ctx, str(override_value))
    return Resolved(result.value, sensitive=result.sensitive or variable.is_sensitive)


# ── валидация при сохранении ─────────────────────────────────────────────────

def _invalid(source: str, message: str, **details) -> DomainValidationError:
    return DomainValidationError(
        error_code="VARIABLE_SOURCE_REF_INVALID",
        message=message,
        details={"source": source, **details},
    )


def _require_dict(source: str, ref: Any) -> dict:
    if not isinstance(ref, dict):
        raise _invalid(source, f"source={source} requires a source_ref object")
    return ref


def _check_keys(source: str, ref: dict, allowed: set[str]) -> None:
    extra = sorted(set(ref) - allowed)
    if extra:
        raise _invalid(source, f"Unknown source_ref keys for source={source}: {extra}", allowed=sorted(allowed))


def _check_field(source: str, ref: dict, key: str, allowed: frozenset[str], *, required: bool = True) -> None:
    value = ref.get(key)
    if value is None and not required:
        return
    if value not in allowed:
        raise _invalid(
            source, f"source_ref.{key} must be one of {sorted(allowed)}",
            field=value, allowed=sorted(allowed),
        )


def _v_no_ref(source: str, ref: Any, _sensitive: bool) -> dict | None:
    if ref not in (None, {}):
        raise _invalid(source, f"source={source} takes no source_ref")
    return None


def _v_static(source: str, ref: Any, _sensitive: bool) -> dict | None:
    if ref in (None, {}):
        return None
    ref = _require_dict(source, ref)
    _check_keys(source, ref, {"value"})
    if not isinstance(ref.get("value"), str):
        raise _invalid(source, "source_ref.value must be a string")
    return ref


def _v_template(source: str, ref: Any, _sensitive: bool) -> dict:
    ref = _require_dict(source, ref)
    _check_keys(source, ref, {"template", "when"})
    if not isinstance(ref.get("template"), str):
        raise _invalid(source, "source_ref.template must be a string")
    _check_field(source, ref, "when", TEMPLATE_CONDITIONS, required=False)
    return ref


def _v_fields_with_fallback(allowed: frozenset[str]):
    def validator(source: str, ref: Any, _sensitive: bool) -> dict:
        ref = _require_dict(source, ref)
        _check_keys(source, ref, {"field", "fallback"})
        _check_field(source, ref, "field", allowed)
        _check_field(source, ref, "fallback", allowed, required=False)
        return ref
    return validator


def _v_department_integration(source: str, ref: Any, sensitive: bool) -> dict:
    ref = _require_dict(source, ref)
    _check_keys(source, ref, {"field", "fallback", "credential_part"})
    _check_field(source, ref, "field", DEPARTMENT_INTEGRATION_FIELDS)
    _check_field(source, ref, "fallback", DEPARTMENT_INTEGRATION_FIELDS, required=False)
    fallback = ref.get("fallback")
    if fallback is not None and is_credential_field(fallback) != is_credential_field(ref["field"]):
        # `credential_part` применяется к обоим полям цепочки — смешивать
        # ссылку на credential с обычной строкой нельзя.
        raise _invalid(
            source, "source_ref.fallback must be of the same kind as field "
            "(both *_credential_id or both plain settings)",
            field=ref["field"], fallback=fallback,
        )
    part = ref.get("credential_part")
    if is_credential_field(ref["field"]):
        if part not in CREDENTIAL_PARTS:
            raise _invalid(
                source, f"source_ref.credential_part must be one of {sorted(CREDENTIAL_PARTS)} "
                f"for credential field '{ref['field']}'",
                allowed=sorted(CREDENTIAL_PARTS),
            )
        if part == "secret" and not sensitive:
            raise _invalid(
                source, "A variable revealing a credential secret must be is_sensitive=true",
                hint="включите is_sensitive — иначе секрет попадёт в логи прогона",
            )
    elif part is not None:
        raise _invalid(source, f"credential_part is only valid for *_credential_id fields, not '{ref['field']}'")
    return ref


def _v_os_version(source: str, ref: Any, _sensitive: bool) -> dict:
    ref = _require_dict(source, ref)
    _check_keys(source, ref, {"field", "segments", "uu_segments"})
    _check_field(source, ref, "field", OS_VERSION_FIELDS)
    for key in ("segments", "uu_segments"):
        value = ref.get(key)
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 1):
            raise _invalid(source, f"source_ref.{key} must be a positive integer or null")
    return ref


def _v_test_account(source: str, ref: Any, sensitive: bool) -> dict:
    ref = _require_dict(source, ref)
    _check_keys(source, ref, {"field"})
    _check_field(source, ref, "field", TEST_ACCOUNT_FIELDS)
    if ref["field"] == "password" and not sensitive:
        raise _invalid(
            source, "A variable revealing the test account password must be is_sensitive=true",
            hint="включите is_sensitive — иначе пароль попадёт в логи прогона",
        )
    return ref


def _v_zephyr_folder(source: str, ref: Any, _sensitive: bool) -> dict:
    ref = _require_dict(source, ref)
    _check_keys(source, ref, {"field"})
    _check_field(source, ref, "field", ZEPHYR_FOLDER_FIELDS)
    return ref


def _v_stand_ref(source: str, ref: Any, _sensitive: bool) -> dict:
    ref = _require_dict(source, ref)
    _check_keys(source, ref, {"stand_id", "field"})
    stand_id = ref.get("stand_id")
    if not isinstance(stand_id, str) or not stand_id.strip() or len(stand_id) > 64:
        raise _invalid(source, "source_ref.stand_id must be a stand id")
    _check_field(source, ref, "field", STAND_REF_FIELDS)
    return {"stand_id": stand_id.strip(), "field": ref["field"]}


_REF_VALIDATORS: dict[str, Callable[[str, Any, bool], dict | None]] = {
    Source.LAUNCH_CONTEXT: _v_no_ref,
    Source.PER_TEST_OVERRIDE: _v_no_ref,
    Source.SECRET_SERVICE: _v_no_ref,
    Source.STATIC: _v_static,
    Source.TEMPLATE: _v_template,
    Source.TEST_FIELD: _v_fields_with_fallback(TEST_FIELDS),
    Source.STAND: _v_fields_with_fallback(STAND_FIELDS),
    Source.DEPARTMENT_INTEGRATION: _v_department_integration,
    Source.OS_VERSION: _v_os_version,
    Source.TEST_ACCOUNT: _v_test_account,
    Source.ZEPHYR_FOLDER: _v_zephyr_folder,
    Source.STAND_REF: _v_stand_ref,
}


def validate_source_ref(source: str, source_ref: Any, *, is_sensitive: bool) -> dict | None:
    """Проверить, что `source_ref` соответствует `source`. Возвращает нормализованную ссылку."""
    validator = _REF_VALIDATORS.get(source)
    if validator is None:
        raise _invalid(source, f"Unknown variable source '{source}'")
    return validator(source, source_ref, is_sensitive)


def _template_of(variable_source: str, source_ref: dict | None) -> str | None:
    if variable_source != Source.TEMPLATE or not source_ref:
        return None
    return source_ref.get("template")


def _find_cycle(graph: dict[str, list[str]], start: str) -> list[str] | None:
    """Путь цикла, достижимого из `start`, или `None`."""
    path: list[str] = []
    on_path: set[str] = set()
    done: set[str] = set()

    def visit(node: str) -> list[str] | None:
        if node in on_path:
            return path[path.index(node):] + [node]
        if node in done:
            return None
        path.append(node)
        on_path.add(node)
        for nxt in graph.get(node, ()):
            found = visit(nxt)
            if found:
                return found
        path.pop()
        on_path.discard(node)
        done.add(node)
        return None

    return visit(start)


async def validate_catalog_change(
    db: AsyncSession,
    *,
    code: str,
    source: str,
    source_ref: dict | None,
    original_code: str | None = None,
) -> None:
    """Проверить, что сохранение переменной не ломает шаблоны каталога.

    * шаблон ссылается только на существующие коды (`VARIABLE_TEMPLATE_UNKNOWN`);
    * граф подстановок без циклов (`VARIABLE_TEMPLATE_CYCLE`);
    * переименование кода, на который ссылаются шаблоны или `override_value`
      слотов, запрещено (`GLOBAL_VARIABLE_IN_USE`, 409) — иначе они молча
      перестанут резолвиться.
    """
    catalog = {v.code: v for v in await global_variable_repo.list_every(db)}
    if original_code is not None and original_code != code:
        await ensure_not_referenced(db, original_code, action="rename")
        catalog.pop(original_code, None)

    graph = {
        c: template_codes(_template_of(v.source, v.source_ref))
        for c, v in catalog.items()
    }
    graph[code] = template_codes(_template_of(source, source_ref))

    unknown = sorted({ref for ref in graph[code] if ref not in graph})
    if unknown:
        raise DomainValidationError(
            error_code="VARIABLE_TEMPLATE_UNKNOWN",
            message=f"Template references unknown variables: {', '.join(unknown)}",
            details={"code": code, "unknown": unknown},
        )
    cycle = _find_cycle(graph, code)
    if cycle:
        raise DomainValidationError(
            error_code="VARIABLE_TEMPLATE_CYCLE",
            message=f"Template cycle: {' -> '.join(cycle)}",
            details={"code": code, "cycle": cycle},
        )


async def validate_override_template(db: AsyncSession, override_value: str | None) -> None:
    """`override_value` слота ссылается только на существующие переменные."""
    codes = template_codes(override_value)
    if not codes:
        return
    known = {v.code for v in await global_variable_repo.list_every(db)}
    unknown = sorted({c for c in codes if c not in known})
    if unknown:
        raise DomainValidationError(
            error_code="VARIABLE_TEMPLATE_UNKNOWN",
            message=f"override_value references unknown variables: {', '.join(unknown)}",
            details={"unknown": unknown},
        )


async def ensure_not_referenced(db: AsyncSession, code: str, *, action: str) -> None:
    """409, если `{code}` упомянут в шаблоне другой переменной или в `override_value` слота."""
    placeholder = "{" + code + "}"
    templates = [
        v.code for v in await global_variable_repo.list_every(db)
        if v.code != code and placeholder in (_template_of(v.source, v.source_ref) or "")
    ]
    stmt = (
        select(TestCommandArg.test_id)
        .where(TestCommandArg.override_value.contains(placeholder, autoescape=True))
        .limit(20)
    )
    slot_tests = sorted({row for row in (await db.execute(stmt)).scalars()})
    if templates or slot_tests:
        raise ConflictError(
            error_code="GLOBAL_VARIABLE_IN_USE",
            message=f"Variable '{code}' is referenced by templates and cannot be {action}d",
            details={"code": code, "referenced_by_variables": templates, "referenced_by_tests": slot_tests},
        )

