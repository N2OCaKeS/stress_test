"""Обновление ОС Astra на сервере (`server.astra_update`).

Управляющая операция над подготовленным сервером: заходим по SSH под
управляющим пользователем с sudo, ПОЛНОСТЬЮ перезаписываем
`/etc/apt/sources.list` репозиториями выбранной версии ОС (их прокидывает
server_service в payload) и гоним `apt update && astra-update -A -T -r`.

Команда `astra-update` идёт долго (минуты) — per-команда таймаут на
SSH-exec не задаётся, `SshClient.run` ждёт завершения процесса. На время
обновления server_service держит сервер в `busy_state='updating'` и отбивает
любые другие операции; снимает блокировку callback этого task'а.

Задача одноразовая (server_service ставит max_attempts=1): авто-retry вреден —
повтор доломал бы полуобновлённый бокс. Поэтому исход (успех/ошибка) в любом
случае докладываем через `submit_astra_update_result`, чтобы снять
updating-блокировку. На успехе server_service ещё привязывает сервер к целевой
версии и запускает inventory.sync.
"""

import logging

from src.clients.ssh import SshError
from src.main import broker
from src.services import server_service_client, ssh_client
from src.tasks._account_helpers import resolve_ssh_creds
from src.tasks._runner import run_task

logger = logging.getLogger(__name__)

# В audit пускаем только операционные счётчики — ни репозитории, ни содержимое
# sources.list наружу не уходят (репо-URL'ы CVE-relevant и раздувают storage).
AUDIT_SAFE_FIELDS: set[str] = {"server_id", "os_version_id", "returncode", "succeeded"}

# Connect-time коды, означающие «до сервера дошли, но управляющая сессия не
# поднялась» — на managed-сервере это почти всегда reimage. Тот же набор и
# ремап, что в `installed_packages` (managed-мутации).
_CONNECT_FAILURE_CODES = frozenset(
    {"SSH_AUTH_FAILED", "SSH_CONNECT_FAILED", "SSH_TIMEOUT"},
)

# Команда обновления: под одним `sudo sh -c '...'`, чтобы обе части (`apt-get
# update` и `astra-update`) шли с правами root — иначе sudo накрыл бы только
# первый сегмент до `&&`. DEBIAN_FRONTEND глушит debconf-промпты. Флаги
# astra-update заданы владельцем (`-A -T -r`).
_UPDATE_COMMAND = (
    "sh -c 'DEBIAN_FRONTEND=noninteractive apt-get update && "
    "astra-update -A -T -r'"
)


def _sanitize_repositories(repositories, host: str) -> str:
    """Свести список репозиториев к тексту sources.list (по строке на репо).

    Содержимое уходит на сервер через stdin (`tee`), а не в shell-команду,
    поэтому shell-инъекции нет. Но отбиваем управляющие символы и переводы
    строки ВНУТРИ отдельной записи: одна запись — одна строка sources.list,
    встроенный `\\n` расклеил бы её на две и мог протащить постороннюю
    директиву. Пустой список сюда не доходит — server_service отбивает его
    ещё на dispatch'е (409 OS_VERSION_NO_REPOSITORIES).
    """
    if not isinstance(repositories, list) or not repositories:
        raise SshError(
            error_code="ASTRA_UPDATE_NO_REPOSITORIES",
            host=host,
            message="astra_update payload has empty repositories list",
        )
    lines: list[str] = []
    for repo in repositories:
        if not isinstance(repo, str) or not repo.strip():
            raise SshError(
                error_code="ASTRA_UPDATE_INVALID_REPOSITORY",
                host=host,
                message="repository entry must be a non-empty string",
            )
        if "\n" in repo or "\r" in repo or "\0" in repo:
            raise SshError(
                error_code="ASTRA_UPDATE_INVALID_REPOSITORY",
                host=host,
                message="repository entry must not contain newline or NUL",
            )
        lines.append(repo.strip())
    # Завершающий перевод строки — sources.list ожидает LF в конце файла.
    return "\n".join(lines) + "\n"


@broker.task("server.astra_update")
async def server_astra_update(task_id: str) -> None:
    """Обновить ОС Astra на сервере до выбранной версии каталога.

    Что делает: заходит по SSH под управляющим пользователем (сервер managed),
    перезаписывает `/etc/apt/sources.list` репозиториями из payload и выполняет
    `apt-get update && astra-update -A -T -r` под sudo. Исход докладывает
    server_service'у (снятие updating-блокировки + привязка версии + inventory
    на успехе).

    Параметры: `task_id`. Payload — `server_id`, `os_version_id`,
    `repositories` (непустой список строк), `host`/`ssh_port`,
    `is_managed`, `management_user`, `target_department_id`.

    Возвращает: `{server_id, os_version_id, returncode, succeeded}`.

    Возможные ошибки: `SSH_MANAGEMENT_AUTH_FAILED` (reimage),
    `ASTRA_UPDATE_SOURCES_WRITE_FAILED`, `ASTRA_UPDATE_FAILED` (ненулевой exit
    apt/astra-update), прочие SSH-ошибки. На любой ошибке — best-effort
    failed-callback (снимает блокировку), затем task падает FAILED.

    Связано: server_service `POST /servers/{id}/astra-update`, callback
    `record_server_astra_updated`, audit action `server.astra_update`.
    """
    async def _impl(payload: dict) -> dict:
        server_id = payload["server_id"]
        os_version_id = payload["os_version_id"]
        target_dept = payload.get("target_department_id")
        is_managed = bool(payload.get("is_managed"))

        try:
            creds = await resolve_ssh_creds(
                payload,
                server_id,
                account_id=None,
                target_dept=target_dept,
                is_managed=is_managed,
            )
            ssh_client.apply_session_hints(creds, payload)
            # Подтянуть per-server управляющий ключ до сборки сессии.
            await ssh_client.attach_management_creds(creds, server_id)
            host = creds.get("host") or creds.get("ssh_host") or server_id

            sources_content = _sanitize_repositories(
                payload.get("repositories"), str(host),
            )

            logger.info(
                "server.astra_update on %r os_version_id=%s repos=%d",
                host, os_version_id, sources_content.count("\n"),
            )
            session = ssh_client.build_session(creds, server_id)
            try:
                await session.connect()
            except SshError as exc:
                if is_managed and exc.error_code in _CONNECT_FAILURE_CODES:
                    await session.close()
                    raise _remap_managed_connect_error(exc) from exc
                await session.close()
                raise
            async with session as ssh:
                # Перезапись sources.list: содержимое подаём на stdin, `tee`
                # пишет файл под sudo. Ключ-сессия без пароля — stdin несёт
                # только контент (sudo пароля не спрашивает).
                rc, _, stderr = await ssh.run(
                    "tee /etc/apt/sources.list > /dev/null",
                    sudo=True,
                    stdin_payload=sources_content,
                )
                if rc != 0:
                    raise SshError(
                        error_code="ASTRA_UPDATE_SOURCES_WRITE_FAILED",
                        host=str(host),
                        returncode=rc,
                        stderr=(stderr or "").strip(),
                        message="failed to overwrite /etc/apt/sources.list",
                    )
                # apt update && astra-update — долгая операция под sudo.
                rc, stdout, stderr = await ssh.run(_UPDATE_COMMAND, sudo=True)
                if rc != 0:
                    raise SshError(
                        error_code="ASTRA_UPDATE_FAILED",
                        host=str(host),
                        cmd_sanitized="apt-get update && astra-update -A -T -r",
                        returncode=rc,
                        stderr=(stderr or stdout).strip(),
                        message="apt update / astra-update failed",
                    )
        except Exception:
            # Одноразовая задача (max_attempts=1) — best-effort снимаем
            # updating-блокировку через failed-callback, потом пробрасываем
            # ошибку (task → FAILED). Ошибку callback'а глушим, чтобы не
            # затереть исходную причину падения.
            try:
                await server_service_client.submit_astra_update_result(
                    server_id, os_version_id, succeeded=False,
                    target_department_id=target_dept,
                )
            except Exception:  # noqa: BLE001
                logger.warning(
                    "astra_update failed-callback errored server_id=%s; "
                    "server may stay updating until manual release",
                    server_id, exc_info=True,
                )
            raise

        # Успех: докладываем server_service — он снимает блокировку, привязывает
        # версию и гонит inventory.sync. Если callback упадёт — task уйдёт
        # FAILED, но ОС уже обновлена; оператор снимет блокировку вручную.
        await server_service_client.submit_astra_update_result(
            server_id, os_version_id, succeeded=True,
            target_department_id=target_dept,
        )
        return {
            "server_id": server_id,
            "os_version_id": os_version_id,
            "returncode": rc,
            "succeeded": True,
        }

    await run_task(
        task_id,
        audit_action="server.astra_update",
        audit_target_type="server",
        impl=_impl,
        audit_safe_fields=AUDIT_SAFE_FIELDS,
    )


def _remap_managed_connect_error(exc: SshError) -> SshError:
    """Превратить connect-фейл управляющей сессии в actionable ошибку.

    Тот же контракт, что в `installed_packages._remap_managed_connect_error`:
    на managed-сервере connect/auth/timeout почти всегда значит reimage —
    даём оператору понятный `SERVER_MANAGEMENT_AUTH_FAILED` вместо сырого
    SSH_AUTH_FAILED с пустыми cmd/stderr.
    """
    return SshError(
        error_code="SERVER_MANAGEMENT_AUTH_FAILED",
        host=exc.host,
        message=(
            "управляющая SSH-сессия к подготовленному серверу не поднялась "
            "(возможно, ОС переустановлена и управляющий пользователь/ключ "
            "утрачены) — требуется повторный prepare"
        ),
        details={"underlying_error_code": exc.error_code},
    )
