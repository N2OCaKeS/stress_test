"""Снимки дисков физических серверов через внешний ACS (`acs.snapshot_*`).

ACS — обёртка над Clonezilla/DRBL, которая сама рулит BMC/PXE/ребутом
сервера: worker ей не помогает с загрузкой, а только просит "сними диск" /
"восстанови диск" и потом ждёт, пока сервер вернётся в сеть. Оба таска идут
по одной схеме:

  1. получить `acs_url`/пароль через `server_service_client.get_acs_settings()`;
  2. позвать ACS (`acs_client.save_disk`/`restore_backup`) — она отвечает
     сразу, принимая задачу в свою асинхронную цепочку;
  3. подождать, пока сервер уйдёт в Clonezilla-окружение и вернётся обратно
     (`probe_reachability_signals` из `_reachability.py`), в пределах общего
     дедлайна;
  4. сообщить server_service исход через `submit_acs_snapshot_created`/
     `submit_acs_snapshot_restore_done`.

`stand_name` для ACS — это `payload["hostname"]` (в emm hostname уже хранит
класс сервера, `LowServer`/`MiddleServer`/... — тот же идентификатор, что
ACS использует и для группировки снимков). `payload["host"]` — отдельное
поле, IP-адрес сервера для reachability-проб; путать их нельзя.

`acs.snapshot_restore` намеренно НЕ вызывает `server.prepare` и не снимает
busy_state сам — reimage уничтожает управляющий SSH-ключ, авто-prepare и
снятие блокировки делает server_service на callback'е
`submit_acs_snapshot_restore_done` (см. план фичи, раздел 3.5 и 5).

Audit actions: `server.acs_snapshot_create` / `server.acs_snapshot_restore`
(CRITICAL — полная перезапись/снятие образа диска).
"""

from __future__ import annotations

import asyncio
import logging
import time

from src.core.config import get_settings
from src.core.constants import LAST_ERROR_MAX_LEN
from src.core.exceptions import CredentialFetchError
from src.main import broker
from src.services import acs_client
from src.services import server_service_client
from src.tasks._reachability import probe_reachability_signals
from src.tasks._runner import run_task
from src.utils.redaction import redact_error_message

logger = logging.getLogger(__name__)

# Whitelist для audit — ни пароль ACS, ни сырые сетевые сигналы наружу не
# идут, только итог операции.
AUDIT_SAFE_FIELDS: set[str] = {
    "server_id", "os_version_id", "snapshot_name", "succeeded", "reachable_after",
}


def _resolve_ssh_port(payload: dict) -> int:
    """SSH-порт для reachability-проб. Дефолт — общая настройка воркера."""
    raw = payload.get("ssh_port")
    if raw:
        try:
            return int(raw)
        except (TypeError, ValueError):
            pass
    return get_settings().power_reachability_ssh_port


async def _network_up(host: str, ssh_port: int) -> bool:
    """Хотя бы один сетевой сигнал (ping/ssh) отвечает."""
    signals = await probe_reachability_signals(host, ssh_port=ssh_port)
    return bool(signals["ping_reachable"] or signals["ssh_reachable"])


async def _await_reachability(
    host: str,
    *,
    ssh_port: int,
    want_up: bool,
    deadline_seconds: float,
    poll_interval_seconds: float,
) -> bool:
    """Поллить сеть сервера, пока не увидим желаемое состояние или не выйдет deadline.

    `want_up=True` — ждём, что сервер снова ответит (вернулся в рабочую ОС
    после Clonezilla). `want_up=False` — ждём, что сервер перестанет отвечать
    (ушёл в Clonezilla-окружение, где sshd не поднят и сеть может быть не
    настроена). Возвращает `True`, если состояние достигнуто в пределах
    `deadline_seconds`, `False` — если время вышло.
    """
    started = time.monotonic()
    while True:
        up = await _network_up(host, ssh_port)
        if up == want_up:
            return True
        if time.monotonic() - started >= deadline_seconds:
            return False
        await asyncio.sleep(poll_interval_seconds)


async def _wait_out_and_back(host: str | None, ssh_port: int) -> bool:
    """Дождаться ухода сервера в Clonezilla и возврата в рабочую ОС.

    Без `host` в payload пробовать нечего — молча пропускаем ожидание
    (best-effort диагностика, не обязательный шаг) и считаем, что реachability
    подтверждена. Уход в Clonezilla ждём best-effort (`acs_down_wait_seconds`):
    если за это время сервер не «упал», не считаем это фатальным — реальный
    ребут мог случиться между двумя тиками поллинга, идём сразу к ожиданию
    возврата. Возврат в рабочую ОС ждём строго, с общим дедлайном
    (`acs_reachability_timeout_seconds`) — не дождались, значит операция
    ACS не завершилась штатно.
    """
    if not host:
        logger.info("acs snapshot: no host in payload, skipping reachability wait")
        return True

    settings = get_settings()
    went_down = await _await_reachability(
        host,
        ssh_port=ssh_port,
        want_up=False,
        deadline_seconds=settings.acs_down_wait_seconds,
        poll_interval_seconds=settings.acs_reachability_poll_interval_seconds,
    )
    if not went_down:
        logger.warning(
            "acs snapshot: host=%s did not go unreachable within %.0fs "
            "of ACS accepting the request; proceeding to wait for return anyway",
            host, settings.acs_down_wait_seconds,
        )

    return await _await_reachability(
        host,
        ssh_port=ssh_port,
        want_up=True,
        deadline_seconds=settings.acs_reachability_timeout_seconds,
        poll_interval_seconds=settings.acs_reachability_poll_interval_seconds,
    )


def _snapshot_name(stand_name: str, version_name: str) -> str:
    """Имя снимка в терминах ACS — `{stand_name}-{version_name}`.

    Совпадает с naming'ом снимков на стороне ACS/Clonezilla (см. план фичи,
    ссылка на `new_allta_app/allta/integrations/acs.py`); нужно только для
    информационного поля в callback'е, самой ACS worker его не передаёт.
    """
    return f"{stand_name}-{version_name}"


async def _get_acs_config(payload: dict) -> tuple[str, str, str, str]:
    """Достать и провалидировать всё нужное для звонка в ACS из payload+настроек.

    Возвращает `(acs_url, acs_password, stand_name, version_name)`.
    `CredentialFetchError` пробрасывается как есть (ACS отключена/недоступна
    server_service — `_runner` смэппит её в failed/retry по общему пути).
    """
    stand_name = payload.get("hostname")
    version_name = payload.get("version_name")
    if not stand_name or not version_name:
        raise CredentialFetchError(
            error_code="ACS_PAYLOAD_INCOMPLETE",
            message="acs snapshot payload missing hostname/version_name",
            details={"has_hostname": bool(stand_name), "has_version_name": bool(version_name)},
        )
    acs_settings = await server_service_client.get_acs_settings()
    return acs_settings["acs_url"], acs_settings["acs_password"], stand_name, version_name


def _truncate_error(message: str) -> str:
    return redact_error_message(message)[:LAST_ERROR_MAX_LEN]


@broker.task("acs.snapshot_create")
async def acs_snapshot_create(task_id: str) -> None:
    """Снять полный образ диска сервера через ACS (Clonezilla `save-disk`).

    Что делает: тянет `acs_url`/пароль → `POST /clonezilla-snap/save-disk` на
    ACS → ждёт, пока сервер уйдёт в Clonezilla и вернётся в рабочую ОС →
    докладывает исход server_service.

    Параметры: `task_id`. Payload — `server_id`, `os_version_id`, `hostname`
    (stand_name для ACS), `host` (IP для reachability-проб), опционально
    `ssh_port`, `target_department_id`.

    Возвращает: `{server_id, os_version_id, snapshot_name, succeeded,
    reachable_after}`.

    Возможные ошибки: `CredentialFetchError` (server_service недоступен,
    ACS выключена/не настроена, битый payload), `AcsError` (ACS отказала
    принять задачу или недоступна по сети). В обоих случаях — best-effort
    callback с `succeeded=False`, затем задача падает.

    Связано с: `server.acs_snapshot_create` audit action.
    """
    async def _impl(payload: dict) -> dict:
        server_id = payload["server_id"]
        os_version_id = payload["os_version_id"]
        target_dept = payload.get("target_department_id")
        host = payload.get("host")
        ssh_port = _resolve_ssh_port(payload)
        snapshot_name: str | None = None

        try:
            acs_url, acs_password, stand_name, version_name = await _get_acs_config(payload)
            snapshot_name = _snapshot_name(stand_name, version_name)

            await acs_client.save_disk(
                acs_url, acs_password,
                stand_name=stand_name, version_name=version_name,
                timeout=get_settings().acs_request_timeout_seconds,
            )
            reachable_after = await _wait_out_and_back(host, ssh_port)
            if not reachable_after:
                raise TimeoutError(
                    f"server did not become reachable again within "
                    f"{get_settings().acs_reachability_timeout_seconds:.0f}s "
                    "after ACS accepted the snapshot request",
                )
        except Exception as exc:
            error_message = _truncate_error(f"{type(exc).__name__}: {exc}")
            try:
                await server_service_client.submit_acs_snapshot_created(
                    server_id, os_version_id, snapshot_name or "", False,
                    target_dept, error_message=error_message,
                )
            except Exception:  # noqa: BLE001
                logger.warning(
                    "acs.snapshot_create failed-callback errored server_id=%s",
                    server_id, exc_info=True,
                )
            raise

        await server_service_client.submit_acs_snapshot_created(
            server_id, os_version_id, snapshot_name, True, target_dept,
        )
        return {
            "server_id": server_id,
            "os_version_id": os_version_id,
            "snapshot_name": snapshot_name,
            "succeeded": True,
            "reachable_after": reachable_after,
        }

    await run_task(
        task_id,
        audit_action="server.acs_snapshot_create",
        audit_target_type="server",
        impl=_impl,
        audit_safe_fields=AUDIT_SAFE_FIELDS,
    )


@broker.task("acs.snapshot_restore")
async def acs_snapshot_restore(task_id: str) -> None:
    """Восстановить диск сервера из ACS-снимка (Clonezilla `restore-backup`).

    Что делает: тянет `acs_url`/пароль → `POST /clonezilla-snap/restore-backup`
    на ACS → ждёт, пока сервер уйдёт в Clonezilla и вернётся в сеть →
    докладывает исход server_service. Полностью переписывает целевой диск —
    самая опасная операция в этой системе.

    Параметры: `task_id`. Payload — `server_id`, `os_version_id`, `hostname`
    (stand_name для ACS), `host` (IP для reachability-проб), опционально
    `ssh_port`, `target_department_id`.

    Возвращает: `{server_id, os_version_id, snapshot_name, succeeded,
    reachable_after}`.

    ВАЖНО: эта таска НЕ вызывает `server.prepare` и не снимает `busy_state` —
    reimage уничтожает управляющий SSH-ключ, дальнейшее (bootstrap prepare,
    снятие блокировки) делает server_service на callback'е
    `submit_acs_snapshot_restore_done`. Задача worker'а — честно сообщить,
    вернулся ли сервер в сеть после restore.

    Возможные ошибки: `CredentialFetchError`, `AcsError`. В обоих случаях —
    best-effort callback с `succeeded=False` и заполненным `last_error`,
    затем задача падает: восстановление из снимка не должно проходить молча
    мимо оператора.

    Связано с: `server.acs_snapshot_restore` audit action.
    """
    async def _impl(payload: dict) -> dict:
        server_id = payload["server_id"]
        os_version_id = payload["os_version_id"]
        target_dept = payload.get("target_department_id")
        host = payload.get("host")
        ssh_port = _resolve_ssh_port(payload)
        snapshot_name: str | None = None

        try:
            acs_url, acs_password, stand_name, version_name = await _get_acs_config(payload)
            snapshot_name = _snapshot_name(stand_name, version_name)

            await acs_client.restore_backup(
                acs_url, acs_password,
                stand_name=stand_name, version_name=version_name,
                timeout=get_settings().acs_request_timeout_seconds,
            )
            reachable_after = await _wait_out_and_back(host, ssh_port)
            if not reachable_after:
                raise TimeoutError(
                    f"server did not become reachable again within "
                    f"{get_settings().acs_reachability_timeout_seconds:.0f}s "
                    "after ACS accepted the restore request",
                )
        except Exception as exc:
            error_message = _truncate_error(f"{type(exc).__name__}: {exc}")
            try:
                await server_service_client.submit_acs_snapshot_restore_done(
                    server_id, os_version_id, snapshot_name or "", False,
                    target_dept, error_message=error_message,
                )
            except Exception:  # noqa: BLE001
                logger.warning(
                    "acs.snapshot_restore failed-callback errored server_id=%s; "
                    "server may stay busy_state=acs until manual release",
                    server_id, exc_info=True,
                )
            raise

        await server_service_client.submit_acs_snapshot_restore_done(
            server_id, os_version_id, snapshot_name, True, target_dept,
        )
        return {
            "server_id": server_id,
            "os_version_id": os_version_id,
            "snapshot_name": snapshot_name,
            "succeeded": True,
            "reachable_after": reachable_after,
        }

    await run_task(
        task_id,
        audit_action="server.acs_snapshot_restore",
        audit_target_type="server",
        impl=_impl,
        audit_safe_fields=AUDIT_SAFE_FIELDS,
    )
