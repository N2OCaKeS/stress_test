"""Статус и control (start/stop/restart) systemd-юнитов на отдельском хосте, через SSH.

Раньше здесь жил `ALLTA_HOST_UNITS` — hardcoded список 12 юнитов, общий на
всю платформу. Теперь список юнитов — ДАННЫЕ (`HostServiceUnit`, per-department,
см. `services/host_services_settings.py`), а не константа: каждый отдел решает
сам, что у него крутится на хосте, и добавляет юниты через
`/settings/host-services/units`. Отдел без настроенного SSH-доступа или без
единого юнита в списке просто не видит ALLTA-раздела (`[]`), это не ошибка.

Соединение — одно на вызов (status опрашивает все юниты отдела через отдельные
channel'ы одного SSH-коннекта, `asyncssh` поддерживает несколько channel'ов на
соединение), без pool'инга — по тому же принципу, что `server_worker`'овский
`clients/ssh.py`: `known_hosts=None` (хост регулярно переустанавливается,
TOFU непрактичен), одно короткоживущее соединение на операцию.

Guard-скрипт на хосте (`scripts/host-control/emm-host-service-guard.sh`) и его
sudoers-allowlist — независимая вторая линия защиты: приложение доверяет ей
не больше, чем себе, но control здесь всё равно сначала резолвит `unit_id` в
строку, принадлежащую вызывающему отделу, ПЕРЕД тем, как что-либо ещё
происходит — это и есть первая линия, и заодно cross-department ownership-check.

`get_allta_status()` — живая SSH-проверка (используется напрямую фоновым
рефрешем и тестами). Эндпоинт `/host/services` ходит через
`get_allta_status_cached()` — per-department TTL-кэш со
stale-while-revalidate, инвалидируемый явно из `control_unit()` после
успешной control-операции.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone

import asyncssh

from src.core.exceptions import AppException, DomainValidationError, NotFoundError, ServiceUnavailableError
from src.db.session import AsyncSessionLocal
from src.models.host_service_unit import HostServiceUnit
from src.schemas.host_services import AlltaServiceStatus
from src.services import host_services_settings as settings_svc

logger = logging.getLogger("server_service.host_control")

_ALLOWED_ACTIONS = frozenset({"start", "stop", "restart"})

_SSH_CONNECT_TIMEOUT_SECONDS = 10
_ACTIVE_STATE = "active"


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _connect(settings, private_key: str) -> asyncssh.SSHClientConnection:
    key = asyncssh.import_private_key(private_key)
    return await asyncio.wait_for(
        asyncssh.connect(
            settings.ssh_host,
            port=settings.ssh_port,
            username=settings.ssh_user,
            client_keys=[key],
            known_hosts=None,
        ),
        timeout=_SSH_CONNECT_TIMEOUT_SECONDS,
    )


async def get_allta_status(db, department_id: str) -> list[AlltaServiceStatus]:
    """Статус всех юнитов, которые отдел `department_id` сам добавил себе в список.

    Не настроено (нет SSH-конфига) или список юнитов пуст → `[]`, без SSH.
    Это не ошибка — отдел без своего хоста просто не видит ALLTA-раздела.
    """
    settings = await settings_svc.get_settings(db, department_id)
    if not settings.configured:
        return []

    units = await settings_svc.list_units(db, department_id)
    if not units.items:
        return []

    try:
        private_key = await settings_svc.get_decrypted_private_key(db, department_id)
        conn = await _connect(settings, private_key)
    except Exception as exc:  # noqa: BLE001 — любой сбой SSH деградирует все строки, не роняет endpoint
        now = _now()
        error = f"{type(exc).__name__}: {exc}"
        return [
            AlltaServiceStatus(id=u.id, label=u.label, status="unknown", checked_at=now, error=error)
            for u in units.items
        ]

    try:
        results = await asyncio.gather(
            *(conn.run(f"systemctl is-active {u.unit_name}.service", check=False) for u in units.items)
        )
    finally:
        conn.close()
        await conn.wait_closed()

    now = _now()
    items: list[AlltaServiceStatus] = []
    for unit, result in zip(units.items, results):
        state = (result.stdout or "").strip()
        status = "up" if state == _ACTIVE_STATE else "down"
        items.append(AlltaServiceStatus(id=unit.id, label=unit.label, status=status, checked_at=now, error=None))
    return items


# ── Кэш для GET /host/services (stale-while-revalidate, per-department) ─────
#
# Отдельный слот на department_id — результат SSH-похода к хосту одного
# отдела не годится для другого. 30 секунд короче, чем кэш `astra_health`
# (45с): здесь есть живая control-операция (`control_unit`), после которой
# кэш инвалидируется явно, так что TTL — только страховка на случай, если
# юнит поменял состояние снаружи (руками на хосте, cron и т.п.), не через
# нашу control-ручку.
_STATUS_CACHE_TTL_SECONDS = 30.0

_status_cache: dict[str, tuple[list[AlltaServiceStatus], float]] = {}
_refreshing_departments: set[str] = set()


def invalidate_status_cache(department_id: str) -> None:
    """Сбросить кэш статуса юнитов отдела — после успешного control-действия,
    чтобы следующий GET сразу показал актуальное состояние, не дожидаясь TTL.
    """
    _status_cache.pop(department_id, None)


def clear_status_cache() -> None:
    """Сбросить весь кэш (используется в тестах между прогонами)."""
    _status_cache.clear()
    _refreshing_departments.clear()


def _entry_is_fresh(fetched_at: float) -> bool:
    return time.monotonic() - fetched_at < _STATUS_CACHE_TTL_SECONDS


def _schedule_background_refresh(department_id: str) -> None:
    if department_id in _refreshing_departments:
        return
    _refreshing_departments.add(department_id)

    async def _run() -> None:
        try:
            # Своя, не request-scoped сессия: запрос, на котором мы решили
            # обновить кэш, к моменту выполнения этой таски уже мог закрыть
            # свою сессию через Depends(get_db) — использовать её здесь
            # небезопасно.
            async with AsyncSessionLocal() as session:
                data = await get_allta_status(session, department_id)
            _status_cache[department_id] = (data, time.monotonic())
        except Exception:  # noqa: BLE001 — фоновый рефреш не должен ронять процесс
            logger.exception("background allta status refresh failed for department %s", department_id)
        finally:
            _refreshing_departments.discard(department_id)

    asyncio.create_task(_run())


async def get_allta_status_cached(db, department_id: str) -> list[AlltaServiceStatus]:
    """Статус ALLTA-юнитов отдела для `/host/services`: мгновенно из кэша,
    обновление — в фоне (см. модульный docstring и `astra_health.check_all_cached`
    для той же схемы на глобальных ASTRA-сервисах).

    Кэша для этого отдела ещё нет — ждём живую SSH-проверку один раз.
    Дальше отдаём последний известный результат немедленно; протухший кэш
    триггерит фоновый рефреш, не блокируя текущий запрос.
    """
    entry = _status_cache.get(department_id)
    if entry is None:
        data = await get_allta_status(db, department_id)
        _status_cache[department_id] = (data, time.monotonic())
        return data
    data, fetched_at = entry
    if not _entry_is_fresh(fetched_at):
        _schedule_background_refresh(department_id)
    return data


async def control_unit(db, department_id: str, unit_id: str, action: str) -> dict:
    """Start/stop/restart одного юнита отдела `department_id` по SSH через forced-command guard.

    Порядок проверок — намеренно такой: ownership юнита (существует И
    принадлежит `department_id`) ПЕРЕД чем-либо ещё — если `unit_id`
    принадлежит другому отделу, это неотличимо снаружи от несуществующего id
    (404 `HOST_UNIT_UNKNOWN`, не 403 — не подтверждаем существование чужого
    юнита). Затем action (endpoint уже гарантирует его типом path-параметра,
    здесь — defence in depth), затем настройки/SSH.
    """
    row = await db.get(HostServiceUnit, unit_id)
    if row is None or row.department_id != department_id:
        raise NotFoundError(
            error_code="HOST_UNIT_UNKNOWN",
            message=f"Unknown host unit: {unit_id}",
            details={"unit_id": unit_id},
        )
    if action not in _ALLOWED_ACTIONS:
        raise DomainValidationError(
            error_code="HOST_UNIT_ACTION_INVALID",
            message=f"Unsupported action: {action}",
            details={"unit_id": unit_id, "action": action},
        )

    private_key = await settings_svc.get_decrypted_private_key(db, department_id)
    settings = await settings_svc.get_settings(db, department_id)

    try:
        conn = await _connect(settings, private_key)
    except Exception as exc:  # noqa: BLE001 — SSH-транспорт недоступен, не guard-отказ
        raise ServiceUnavailableError(
            error_code="HOST_SERVICE_SSH_UNAVAILABLE",
            message=f"SSH connection to host failed: {type(exc).__name__}",
            details={"unit_id": unit_id},
        ) from exc

    try:
        result = await conn.run(f"systemctl {action} {row.unit_name}.service", check=False)
    finally:
        conn.close()
        await conn.wait_closed()

    stderr = (result.stderr or "").strip()
    if result.exit_status != 0 or stderr:
        # Guard-скрипт или сам systemctl отклонили команду (не в sudoers на
        # хосте, юнит не найден и т.п.) — 502, это сбой апстрима (хоста), не
        # нашего запроса.
        raise AppException(
            error_code="HOST_SERVICE_CONTROL_FAILED",
            message=f"systemctl {action} {row.unit_name}.service failed",
            details={"stderr": result.stderr, "exit_status": result.exit_status},
            http_status=502,
        )

    # Успешный control — состояние юнита только что реально изменилось,
    # следующий GET должен увидеть это сразу, а не ждать TTL кэша.
    invalidate_status_cache(department_id)
    return {"ok": True, "unit_id": unit_id, "action": action, "output": (result.stdout or "").strip()}
