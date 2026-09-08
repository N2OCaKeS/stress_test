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
     дедлайна — оба перехода обязательны, "не ушёл" не прощается (см.
     `_wait_out_and_back`);
  4. только для restore — дополнительно реально залогиниться bootstrap-кредой
     версии ОС и дождаться `systemctl is-system-running == running`, с
     ретраями на протяжении всего окна (`_verify_bootstrap_login`): открытый
     SSH-порт ещё не значит, что restore реально закончился — Clonezilla сама
     держит SSH открытым (под чужими live-кредами) весь процесс восстановления,
     а после него ACS ещё и сама делает hard reset и может перезагрузить
     сервер повторно, если тот придёт в `degraded`-состояние (см. `acs`-ветку,
     `clonezilla_func.py::backup_image`/`socket_available`);
  5. сообщить server_service исход через `submit_acs_snapshot_created`/
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

from src.clients.ssh import SshClient, SshError
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
    подтверждена.

    Уход в Clonezilla ждём СТРОГО (`acs_down_wait_seconds`): не дождались —
    возвращаем `False` сразу, не переходя к ожиданию возврата. Раньше здесь
    было "не дождались — не фатально, идём ждать возврата всё равно" —
    это гонка: ACS перед самим ребутом делает несколько SSH/IPMI-вызовов
    настройки boot order (минута-две), и если наш опрос "ушёл ли" стартует
    раньше реального ребута, сервер в этот момент ещё жив со старой ОС —
    следующая же проверка "вернулся ли" видит его отвечающим НЕМЕДЛЕННО,
    хотя реального restore ещё не было и в помине. Именно так `restore`
    один раз уже отрапортовал success через 5 минут вместо реальных 30-40.

    Возврат в рабочую ОС ждём с общим дедлайном (`acs_reachability_timeout_seconds`).
    Оба этапа — необходимое, но не достаточное условие: сам факт "порт
    открылся" ещё не значит "restore гарантированно закончился и bootstrap-
    креды рабочие" — для restore это дополнительно проверяется реальным
    SSH-логином (`_verify_bootstrap_login`), отдельно от этой функции.
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
            "of ACS accepting the request — treating as failure, not "
            "proceeding to wait for return",
            host, settings.acs_down_wait_seconds,
        )
        return False

    return await _await_reachability(
        host,
        ssh_port=ssh_port,
        want_up=True,
        deadline_seconds=settings.acs_reachability_timeout_seconds,
        poll_interval_seconds=settings.acs_reachability_poll_interval_seconds,
    )


async def verify_system_running(
    host: str, session_factory, *, label: str,
) -> None:
    """Поллить `systemctl is-system-running == running` до готовности хоста.

    Общая механика для всех «сервер должен вернуться сам собой» ожиданий:
    ретраим и на отказ SSH (сеть/чужие live-креды/идёт очередной ребут), и на
    любой ответ кроме `running` (`degraded`, `starting`). Бюджет —
    `acs_bootstrap_verify_retries` × `acs_bootstrap_verify_interval_seconds`,
    один на все ожидания подъёма: заводить второй такой же рядом смысла нет.

    `session_factory` — callable без аргументов, возвращающий готовый
    `SshClient` (bootstrap-креда после restore, управляющая сессия после
    ребута смены ядра). `label` уходит в логи, чтобы две разные ветки
    ожидания различались.

    Поднимает `SshError` последней попытки, если бюджет исчерпан.
    """
    settings = get_settings()
    last_exc: Exception | None = None
    last_status = ""
    for attempt in range(1, settings.acs_bootstrap_verify_retries + 1):
        try:
            async with session_factory() as ssh:
                _rc, stdout, _stderr = await ssh.run("systemctl is-system-running")
            last_status = stdout.strip()
            if last_status == "running":
                return
            last_exc = None
            logger.info(
                "%s: verify attempt %s/%s for host=%s logged in but system not "
                "ready yet (systemctl is-system-running=%r)",
                label, attempt, settings.acs_bootstrap_verify_retries, host,
                last_status,
            )
        except SshError as exc:
            last_exc = exc
            logger.info(
                "%s: verify attempt %s/%s failed for host=%s: %s",
                label, attempt, settings.acs_bootstrap_verify_retries, host,
                type(exc).__name__,
            )
        if attempt < settings.acs_bootstrap_verify_retries:
            await asyncio.sleep(settings.acs_bootstrap_verify_interval_seconds)
    if last_exc is not None:
        raise last_exc
    raise SshError(
        error_code="SSH_SYSTEM_NOT_READY",
        host=host,
        message=(
            f"system never reported 'running' "
            f"(last systemctl is-system-running={last_status!r})"
        ),
    )


async def _verify_bootstrap_login(
    host: str, ssh_port: int, os_version_id: str,
) -> None:
    """Реально залогиниться bootstrap-кредой версии ОС после restore.

    Открытый SSH-порт (что уже подтвердила `_wait_out_and_back`) ничего не
    доказывает: пока ACS реально гоняет восстановление, целевой сервер
    висит в Clonezilla-окружении, которое ДЕРЖИТ SSH открытым весь процесс
    (это может быть 30-40+ минут) — просто под чужими live-кредами, не
    bootstrap. Поэтому обычный отказ логина (auth failed) здесь — НЕ признак
    неготовности гостя, а признак "мы всё ещё смотрим на Clonezilla, а не на
    восстановленную ОС".

    Более того, сама ACS после того, как Clonezilla заканчивает и рапортует
    успех, делает СВОЙ ЖЁСТКИЙ ребут контроллером (см. `change_boot_order.reset()`
    в `clonezilla_func.py` ветки `acs`), а затем поллит `systemctl
    is-system-running` — если тот вернул `degraded`, ACS САМА перезагружает
    сервер ещё раз (до 4 доп. попыток). Значит даже успешный SSH-логин сразу
    после restore ещё не гарантирует стабильности: гостя могут перезагрузить
    у нас из-под ног. Проверяем не просто "залогинились", а тот же сигнал
    готовности, которым руководствуется сама ACS — `systemctl is-system-
    running == running`; любой другой ответ (`degraded`, `starting`, ошибка
    команды) трактуем как "ещё не готово" и продолжаем ждать наравне с
    обычным `SshError` (сеть недоступна / Clonezilla / идёт очередной ребут).

    Поднимает `SshError` последней попытки, если все ретраи
    (`acs_bootstrap_verify_retries`/`_interval_seconds`) исчерпаны — caller
    оборачивает это в `succeeded=False`.
    """
    creds = await server_service_client.get_os_version_bootstrap_password(os_version_id)

    def _session() -> SshClient:
        return SshClient(
            host=host,
            username=creds["ssh_username"],
            password=creds["password"],
            port=ssh_port,
        )

    await verify_system_running(
        host, _session, label="acs snapshot restore: bootstrap SSH",
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
                    f"server did not go offline and/or become reachable again "
                    f"within the expected window after ACS accepted the "
                    f"restore request",
                )
            # Открытый SSH-порт ещё не значит, что restore реально закончился
            # и bootstrap-креды рабочие — единственное, что это доказывает,
            # это реальный логин. Без host'а reachability-проверки уже были
            # best-effort skip'нуты выше — тут по той же причине пропускаем.
            if host:
                await _verify_bootstrap_login(host, ssh_port, os_version_id)
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
