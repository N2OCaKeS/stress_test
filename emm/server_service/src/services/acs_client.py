"""HTTP-клиент к ACS — внешнему сервису снимков дисков физических серверов.

ACS оборачивает Clonezilla/DRBL: `save-disk` снимает полный образ диска,
`restore-backup` перезаписывает диск снимком, `check-snapshots` отдаёт
directory listing уже сделанных снимков. Все три принимают пароль
clonezilla-сервера query-параметром `password_clonezilla_server`.

### Почему клиент без фиксированного `base_url`

У auth_service/loging_service `base_url` зашит в конфиг при старте процесса
(`http_pool.init_pools`). У ACS — не так: адрес и пароль лежат в динамической
настройке `AcsSettings`, которую account_admin может поменять через
`/settings/acs` в любой момент без рестарта сервиса. Поэтому pooled
`httpx.AsyncClient` здесь держится БЕЗ `base_url` — только shared timeout и
connection limits, — а каждый вызов передаёт полный URL, собранный из
`AcsSettings.acs_url`, пришедшего от caller'а. Это самый простой вариант:
не нужно пересоздавать клиент при каждом изменении настройки, не нужно
дополнительного слоя "переключаемых" пулов.

### Обработка ошибок

Зеркалит `dependencies/auth.py::_introspect`: `httpx.TimeoutException` →
`ServiceUnavailableError(ACS_TIMEOUT)`, `httpx.ConnectError` →
`ServiceUnavailableError(ACS_UNREACHABLE)`. Ненулевой HTTP-статус ответа ACS
(само по себе не сетевая ошибка — ACS ответил, просто с ошибкой) —
`ServiceUnavailableError(ACS_ERROR)` с телом ответа в `details` (без redact —
ответ ACS секретов не несёт).

### Использование

Только подготовка транспортного слоя — сам вызов из dispatch/worker-задач
(create/restore snapshot) идёт в отдельной волне (см. план фичи, п.4-5).
"""

from __future__ import annotations

import logging

import httpx

from src.core.exceptions import ServiceUnavailableError

logger = logging.getLogger(__name__)

_CHECK_SNAPSHOTS_PATH = "/clonezilla-snap/check-snapshots"
_SAVE_DISK_PATH = "/clonezilla-snap/save-disk"
_RESTORE_BACKUP_PATH = "/clonezilla-snap/restore-backup"

# Module-level pooled client. Поднимается `services.http_pool.init_pools`,
# закрывается `aclose_all`. Остаётся `None` outside app lifecycle — вызовы
# тогда идут через per-call fallback (см. `_client()`), как у introspect.
_acs_client: httpx.AsyncClient | None = None


def _client(timeout_seconds: float) -> tuple[httpx.AsyncClient, bool]:
    """Текущий pooled клиент или временный per-call fallback.

    Возвращает `(client, is_temporary)` — `is_temporary=True` означает, что
    caller обязан сам `aclose()` клиент после использования.
    """
    if _acs_client is not None:
        return _acs_client, False
    logger.debug("acs_client: pooled client missing, using per-call fallback")
    return httpx.AsyncClient(timeout=timeout_seconds), True


async def _request(
    method: str,
    url: str,
    *,
    params: dict,
    timeout_seconds: float = 15.0,
) -> httpx.Response:
    client, is_temporary = _client(timeout_seconds)
    try:
        response = await client.request(method, url, params=params, timeout=timeout_seconds)
    except httpx.TimeoutException as exc:
        raise ServiceUnavailableError(
            error_code="ACS_TIMEOUT",
            message="ACS did not respond in time",
            details={"url": url},
        ) from exc
    except httpx.ConnectError as exc:
        raise ServiceUnavailableError(
            error_code="ACS_UNREACHABLE",
            message="Unable to connect to ACS",
            details={"url": url},
        ) from exc
    except httpx.HTTPError as exc:
        raise ServiceUnavailableError(
            error_code="ACS_ERROR",
            message=f"Unexpected error talking to ACS: {type(exc).__name__}",
            details={"url": url},
        ) from exc
    finally:
        if is_temporary:
            await client.aclose()

    if response.status_code >= 400:
        raise ServiceUnavailableError(
            error_code="ACS_ERROR",
            message=f"ACS returned {response.status_code}",
            details={"url": url, "status_code": response.status_code, "body": _safe_body(response)},
        )
    return response


def _safe_body(response: httpx.Response) -> str:
    """Тело ответа ACS для `details` — обрезаем на случай неожиданно большого HTML."""
    try:
        return response.text[:2000]
    except Exception:  # noqa: BLE001 — details — best-effort диагностика, не критичный путь
        return ""


async def list_snapshots(base_url: str, password: str) -> list[str]:
    """`GET {base_url}/clonezilla-snap/check-snapshots` — список имён снимков.

    Ответ ACS — `{"snaphosts": [...]}` (да, опечатка в реальном API, не наша).
    Наружу отдаём под нормальным именем, сам typo наружу не течёт.
    """
    url = f"{base_url.rstrip('/')}{_CHECK_SNAPSHOTS_PATH}"
    response = await _request(
        "GET", url, params={"password_clonezilla_server": password},
    )
    body = response.json()
    return list(body.get("snaphosts", []))


async def save_disk(base_url: str, password: str, stand_name: str, version_name: str) -> None:
    """`POST {base_url}/clonezilla-snap/save-disk` — снять полный образ диска стенда.

    ACS отвечает сразу, дальше асинхронно гоняет свою celery-цепочку (ребут,
    Clonezilla save, восстановление boot order). Успешный ответ здесь значит
    только "ACS принял задачу", не "снимок готов".
    """
    url = f"{base_url.rstrip('/')}{_SAVE_DISK_PATH}"
    await _request(
        "POST", url,
        params={
            "version_name": version_name,
            "stand_name": stand_name,
            "password_clonezilla_server": password,
        },
    )


async def restore_backup(base_url: str, password: str, stand_name: str, version_name: str) -> None:
    """`POST {base_url}/clonezilla-snap/restore-backup` — восстановить диск стенда из снимка.

    Как и `save_disk`, ACS принимает задачу и отрабатывает асинхронно (ребут,
    Clonezilla restore, возврат в рабочий boot order).
    """
    url = f"{base_url.rstrip('/')}{_RESTORE_BACKUP_PATH}"
    await _request(
        "POST", url,
        params={
            "version_name": version_name,
            "stand_name": stand_name,
            "password_clonezilla_server": password,
        },
    )
