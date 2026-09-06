"""Статус и control (start/stop/restart) ALLTA systemd-юнитов на хосте emm, через SSH.

Портирует `allta_services_list` из legacy `allta_app`
(`allta_app/allta_image_conf.py:700`) — тот же список юнитов, тот же
`systemctl status`, но здесь напрямую по SSH на дедicated-аккаунт с
forced-command guard'ом (`scripts/host-control/emm-host-service-guard.sh`),
а не через локальный systemd-сокет.

`ALLTA_HOST_UNITS` — единственный источник истины для allowlist'а: любой
`unit_id`, пришедший снаружи (control-эндпоинт), сверяется с этим списком
ПЕРЕД тем, как что-либо ещё происходит. Симметричный allowlist есть и на
хосте, в самом guard-скрипте — приложение доверяет ему не больше, чем себе:
если один из двух списков расширят, а второй забудут, юнит либо не заработает
(guard отклонит), либо останется невидимым в UI (наш allowlist его не знает).

Соединение — одно на вызов (status опрашивает все 12 юнитов через отдельные
channel'ы одного SSH-коннекта, `asyncssh` поддерживает несколько channel'ов на
соединение), без pool'инга — по тому же принципу, что `server_worker`'овский
`clients/ssh.py`: `known_hosts=None` (хост регулярно переустанавливается,
TOFU непрактичен), одно короткоживущее соединение на операцию.
"""

import asyncio
from datetime import datetime, timezone

import asyncssh

from src.core.exceptions import AppException, DomainValidationError, NotFoundError, ServiceUnavailableError
from src.schemas.host_services import AlltaServiceStatus
from src.services import host_services_settings as settings_svc

# (unit_id, systemd unit name) — allowlist. Три места должны совпадать, иначе
# юнит либо не заработает end-to-end, либо не появится в UI: этот список,
# `ALLOWED_UNITS` в `scripts/host-control/emm-host-service-guard.sh`, и
# соответствующая строка в sudoers.d на хосте.
ALLTA_HOST_UNITS: list[tuple[str, str]] = [
    ("acs", "acs.service"),
    ("allta_auth", "allta_auth.service"),
    ("allta_infocollector", "allta_infocollector.service"),
    ("allta", "allta.service"),
    ("allta_vm", "allta_vm.service"),
    ("changelog", "changelog.service"),
    ("devpi", "devpi.service"),
    ("grafana_prometheus", "grafana_prometheus.service"),
    ("node_exporter", "node_exporter.service"),
    ("portainer", "portainer.service"),
    ("statistics", "statistics.service"),
    ("docker_registry", "docker_registry.service"),
]

_UNIT_MAP: dict[str, str] = dict(ALLTA_HOST_UNITS)

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


async def get_allta_status(db) -> list[AlltaServiceStatus]:
    """Статус всех 12 ALLTA-юнитов. Не настроено → `not_configured`, без SSH."""
    settings = await settings_svc.get_settings(db)
    if not settings.configured:
        now = _now()
        return [
            AlltaServiceStatus(id=unit_id, label=unit_id, status="not_configured", checked_at=now, error=None)
            for unit_id, _service_name in ALLTA_HOST_UNITS
        ]

    try:
        private_key = await settings_svc.get_decrypted_private_key(db)
        conn = await _connect(settings, private_key)
    except Exception as exc:  # noqa: BLE001 — любой сбой SSH деградирует все 12 строк, не роняет endpoint
        now = _now()
        error = f"{type(exc).__name__}: {exc}"
        return [
            AlltaServiceStatus(id=unit_id, label=unit_id, status="unknown", checked_at=now, error=error)
            for unit_id, _service_name in ALLTA_HOST_UNITS
        ]

    try:
        results = await asyncio.gather(
            *(conn.run(f"systemctl is-active {service_name}", check=False) for _unit_id, service_name in ALLTA_HOST_UNITS)
        )
    finally:
        conn.close()
        await conn.wait_closed()

    now = _now()
    items: list[AlltaServiceStatus] = []
    for (unit_id, _service_name), result in zip(ALLTA_HOST_UNITS, results):
        state = (result.stdout or "").strip()
        status = "up" if state == _ACTIVE_STATE else "down"
        items.append(AlltaServiceStatus(id=unit_id, label=unit_id, status=status, checked_at=now, error=None))
    return items


async def control_unit(db, unit_id: str, action: str) -> dict:
    """Start/stop/restart одного ALLTA-юнита по SSH через forced-command guard.

    Порядок проверок — намеренно такой: allowlist юнита ПЕРЕД чем-либо ещё
    (даже до чтения настроек), затем action (endpoint уже гарантирует его
    типом path-параметра, здесь — defence in depth), затем настройки/SSH.
    """
    service_name = _UNIT_MAP.get(unit_id)
    if service_name is None:
        raise NotFoundError(
            error_code="HOST_UNIT_UNKNOWN",
            message=f"Unknown host unit: {unit_id}",
            details={"unit": unit_id},
        )
    if action not in _ALLOWED_ACTIONS:
        raise DomainValidationError(
            error_code="HOST_UNIT_ACTION_INVALID",
            message=f"Unsupported action: {action}",
            details={"unit": unit_id, "action": action},
        )

    private_key = await settings_svc.get_decrypted_private_key(db)
    settings = await settings_svc.get_settings(db)

    try:
        conn = await _connect(settings, private_key)
    except Exception as exc:  # noqa: BLE001 — SSH-транспорт недоступен, не guard-отказ
        raise ServiceUnavailableError(
            error_code="HOST_SERVICE_SSH_UNAVAILABLE",
            message=f"SSH connection to host failed: {type(exc).__name__}",
            details={"unit": unit_id},
        ) from exc

    try:
        result = await conn.run(f"systemctl {action} {service_name}", check=False)
    finally:
        conn.close()
        await conn.wait_closed()

    stderr = (result.stderr or "").strip()
    if result.exit_status != 0 or stderr:
        # Guard-скрипт или сам systemctl отклонили команду (не в allowlist'е
        # на хосте, sudoers не даёт прав, юнит не найден и т.п.) — 502, это
        # сбой апстрима (хоста), не нашего запроса.
        raise AppException(
            error_code="HOST_SERVICE_CONTROL_FAILED",
            message=f"systemctl {action} {service_name} failed",
            details={"stderr": result.stderr, "exit_status": result.exit_status},
            http_status=502,
        )

    return {"ok": True, "unit": unit_id, "action": action, "output": (result.stdout or "").strip()}
