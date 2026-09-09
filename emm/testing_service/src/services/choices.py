"""Резолв `choices_source` глобальной переменной в список значений.

`choices_source` — не список, а способ его получить. Поддерживаются два
префикса:

* ``static:<json>`` — фиксированное множество. JSON разбирается **в момент
  резолва**, а не при сохранении переменной: строка это данные, менять её
  можно без релиза. Внутри допустим либо массив строк
  (``static:["orel","smolensk"]``), либо массив объектов
  ``[{"value": "orel", "label": "Орёл"}]``.
* ``dynamic:<resolver>`` — именованный резолвер из ``RESOLVERS``, который
  спрашивает актуальный список у источника в момент открытия списка в UI.

Добавить источник = добавить функцию в ``RESOLVERS``: сигнатура
``async (params: dict[str, str]) -> list[dict]``, где ключи словаря — query-
параметры эндпоинта ``/global-variables/{id}/choices``. Параметризованный
резолвер сам достаёт из ``params`` то, что ему нужно, и отбивает
``CHOICES_PARAM_REQUIRED``, если параметра нет. Резолвер
``dynamic:department_credential`` (§3.5 плана миграции) появится вместе с
исполнением тестов и просто добавится в этот же словарь.

Новое значение внутри уже существующего источника (очередной РЦ) — чистые
данные: ни кода, ни правки каталога переменных.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable

from src.core.exceptions import BadRequestError, DomainValidationError
from src.services import server_client

logger = logging.getLogger(__name__)

STATIC_PREFIX = "static:"
DYNAMIC_PREFIX = "dynamic:"

# Резолвер получает query-параметры эндпоинта choices и отдаёт список
# {"value": ..., "label": ...}.
Resolver = Callable[[dict[str, str]], Awaitable[list[dict]]]


def _require_param(params: dict[str, str], name: str) -> str:
    """Достать обязательный параметр резолвера или отбить 400."""
    value = (params or {}).get(name)
    if not value:
        raise BadRequestError(
            error_code="CHOICES_PARAM_REQUIRED",
            message=f"Query parameter '{name}' is required for this choices source",
            details={"parameter": name},
        )
    return value


async def resolve_os_versions(params: dict[str, str]) -> list[dict]:
    """Живой каталог OS-версий из server_service. Параметров не требует."""
    versions = await server_client.list_os_versions()
    items: list[dict] = []
    for version in versions:
        value = version.get("id")
        if not value:
            continue
        items.append({"value": str(value), "label": str(version.get("name") or value)})
    return items


async def resolve_kernels(params: dict[str, str]) -> list[dict]:
    """Ядра конкретной OS-версии. Обязателен `os_version_id`.

    Список ядер ведётся в карточке версии на стороне server_service, отдельной
    ручки под него нет — берём карточку и достаём поле.
    """
    os_version_id = _require_param(params, "os_version_id")
    version = await server_client.get_os_version(os_version_id)
    kernels = version.get("kernels") or []
    return [{"value": str(k), "label": str(k)} for k in kernels if str(k).strip()]


RESOLVERS: dict[str, Resolver] = {
    "os_versions": resolve_os_versions,
    "kernels": resolve_kernels,
}


def is_supported(choices_source: str) -> bool:
    """Понимаем ли мы такую строку-источник.

    Для `static:` проверяется только префикс — содержимое это данные, они
    разбираются при резолве. Для `dynamic:` имя резолвера должно быть
    зарегистрировано: принципиально новый источник — это код, а не данные.
    """
    if choices_source.startswith(STATIC_PREFIX):
        return True
    if choices_source.startswith(DYNAMIC_PREFIX):
        return choices_source[len(DYNAMIC_PREFIX):].strip() in RESOLVERS
    return False


def _parse_static(payload: str) -> list[dict]:
    """Разобрать хвост `static:` в список вариантов."""
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise DomainValidationError(
            error_code="CHOICES_SOURCE_INVALID",
            message=f"static choices source is not valid JSON: {exc.msg}",
        ) from exc
    if not isinstance(parsed, list):
        raise DomainValidationError(
            error_code="CHOICES_SOURCE_INVALID",
            message="static choices source must be a JSON array",
        )
    items: list[dict] = []
    for entry in parsed:
        if isinstance(entry, dict):
            value = entry.get("value")
            if value is None:
                raise DomainValidationError(
                    error_code="CHOICES_SOURCE_INVALID",
                    message="static choices entry object must have a 'value' key",
                )
            label = entry.get("label", value)
        elif isinstance(entry, (str, int, float, bool)):
            value = entry
            label = entry
        else:
            raise DomainValidationError(
                error_code="CHOICES_SOURCE_INVALID",
                message="static choices entry must be a scalar or an object",
            )
        items.append({"value": str(value), "label": str(label)})
    return items


async def resolve(choices_source: str, params: dict[str, str] | None = None) -> list[dict]:
    """Резолвит строку-источник в список `{value, label}`."""
    source = choices_source.strip()
    if source.startswith(STATIC_PREFIX):
        return _parse_static(source[len(STATIC_PREFIX):])
    if source.startswith(DYNAMIC_PREFIX):
        name = source[len(DYNAMIC_PREFIX):].strip()
        resolver = RESOLVERS.get(name)
        if resolver is None:
            raise DomainValidationError(
                error_code="CHOICES_RESOLVER_UNKNOWN",
                message=f"Unknown dynamic choices resolver: {name!r}",
                details={"known": sorted(RESOLVERS)},
            )
        return await resolver(params or {})
    raise DomainValidationError(
        error_code="CHOICES_SOURCE_INVALID",
        message="choices_source must start with 'static:' or 'dynamic:'",
    )
