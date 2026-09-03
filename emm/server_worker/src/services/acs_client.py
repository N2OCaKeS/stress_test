"""HTTP-клиент к ACS — внешнему сервису снимков дисков физических серверов.

Worker ходит в ACS напрямую, не через server_service-прокси: именно worker
переживает многоминутное окно, пока ACS сама рулит ребутом/PXE/boot order,
снимая или восстанавливая полный образ диска через Clonezilla/DRBL.
server_service лишь отдаёт транспортные данные (`acs_url`/пароль) через
`server_service_client.get_acs_settings()`.

Контракт ACS — тот же внешний API, что использует `server_service/src/
services/acs_client.py` (там клиент нужен для будущих server_service-side
вызовов, здесь — независимая реализация под worker-стиль клиентов, см.
`clients/redfish.py`/`clients/ssh.py`): `POST {base_url}/clonezilla-snap/
save-disk` и `POST {base_url}/clonezilla-snap/restore-backup`, оба принимают
query-параметры `password_clonezilla_server`, `stand_name`, `version_name`.

Успешный ответ означает только «ACS приняла задачу» — само снятие/
восстановление идёт асинхронно на стороне ACS (ребут в Clonezilla-окружение,
работа с диском, возврат рабочего boot order). Caller
(`tasks/acs_snapshots.py`) дальше сам поллит сетевую доступность сервера.

`httpx.AsyncClient` создаётся per-call (one-shot per операция) — снимки
делаются нечасто и не оправдывают отдельный pooled-канал в `http_pool.py`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

_SAVE_DISK_PATH = "/clonezilla-snap/save-disk"
_RESTORE_BACKUP_PATH = "/clonezilla-snap/restore-backup"


@dataclass
class AcsError(Exception):
    """Ошибка вызова ACS.

    `status_code=None` — транспортный сбой (timeout/connect), ACS вообще не
    ответила. `status_code` заполнен — ACS ответила не-2xx, `details["body"]`
    несёт обрезанное тело ответа для диагностики.
    """

    error_code: str
    message: str
    status_code: int | None = None
    details: dict = field(default_factory=dict)

    def __str__(self) -> str:
        parts = [f"{self.error_code}: {self.message}"]
        if self.status_code is not None:
            parts.append(f"status={self.status_code}")
        return " ".join(parts)


def _safe_body(response: httpx.Response) -> str:
    """Тело ответа ACS для диагностики — обрезаем на случай большого HTML."""
    try:
        return response.text[:2000]
    except Exception:  # noqa: BLE001 — диагностика, не критичный путь
        return ""


async def _post(
    base_url: str,
    path: str,
    *,
    password: str,
    stand_name: str,
    version_name: str,
    timeout: float,
) -> dict:
    """POST на ACS с паролем/stand/version query-параметрами.

    Транспортный сбой и не-2xx ответ оборачиваются в `AcsError` — caller
    (`tasks/acs_snapshots.py`) не должен ловить сырой `httpx`-исключение.
    Тело успешного ответа парсится как JSON, если получилось — иначе `{}`
    (ACS может отвечать пустым телом при 200/202).
    """
    url = f"{base_url.rstrip('/')}{path}"
    params = {
        "password_clonezilla_server": password,
        "stand_name": stand_name,
        "version_name": version_name,
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            response = await client.post(url, params=params)
        except httpx.TimeoutException as exc:
            raise AcsError(
                error_code="ACS_TIMEOUT",
                message="ACS did not respond in time",
                details={"path": path},
            ) from exc
        except httpx.ConnectError as exc:
            raise AcsError(
                error_code="ACS_UNREACHABLE",
                message="Unable to connect to ACS",
                details={"path": path},
            ) from exc
        except httpx.HTTPError as exc:
            raise AcsError(
                error_code="ACS_TRANSPORT_ERROR",
                message=f"Unexpected error talking to ACS: {type(exc).__name__}",
                details={"path": path},
            ) from exc

    if response.status_code >= 400:
        raise AcsError(
            error_code="ACS_REJECTED",
            message=f"ACS returned {response.status_code}",
            status_code=response.status_code,
            details={"path": path, "body": _safe_body(response)},
        )
    try:
        return response.json()
    except ValueError:
        return {}


async def save_disk(
    base_url: str,
    password: str,
    *,
    stand_name: str,
    version_name: str,
    timeout: float = 30.0,
) -> dict:
    """`POST {base_url}/clonezilla-snap/save-disk` — снять полный образ диска стенда.

    ACS отвечает сразу и дальше асинхронно гоняет свою цепочку (ребут в
    Clonezilla, snapshot, восстановление рабочего boot order). Успешный ответ
    означает только "ACS приняла задачу", не "снимок готов" — за фактическим
    завершением следит caller через reachability-поллинг.
    """
    return await _post(
        base_url, _SAVE_DISK_PATH,
        password=password, stand_name=stand_name, version_name=version_name,
        timeout=timeout,
    )


async def restore_backup(
    base_url: str,
    password: str,
    *,
    stand_name: str,
    version_name: str,
    timeout: float = 30.0,
) -> dict:
    """`POST {base_url}/clonezilla-snap/restore-backup` — восстановить диск из снимка.

    Как и `save_disk`, ACS принимает задачу и отрабатывает асинхронно (ребут,
    Clonezilla restore, возврат рабочего boot order). Полностью переписывает
    целевой диск — самая деструктивная операция на этом канале.
    """
    return await _post(
        base_url, _RESTORE_BACKUP_PATH,
        password=password, stand_name=stand_name, version_name=version_name,
        timeout=timeout,
    )
