"""Синхронизация управляющей учётки на подготовленном сервере (`management_user_sync`).

Ставится high-priority фан-аутом из server_service при изменении конфига
управляющей учётки (PUT `/management-user-config`). Сервер уже прошёл prepare
(`is_managed`), поэтому заходим под управляющим пользователем по ключу.

Два режима работы:

* **sync** (без смены имени): недеструктивно досинхронизируем учётку под
  текущим управляющим пользователем — группы + extra_create_commands нужного
  режима + публичный ключ управления (идемпотентный re-bootstrap);
* **cutover-rename** (`rename_pending=True` и имя в конфиге отличается от
  текущего управляющего пользователя сервера): под старым управляющим
  пользователем заводим нового, проверяем вход под ним и только после этого
  удаляем старого со всеми данными. Это деструктив — он под гейтом C1
  (`ensure_no_other_running_on_server`). Удаление старой учётки идёт с новой
  сессии, не из-под удаляемой.

Имя управляющей учётки и пер-режимный конфиг едут в payload'е из server_service
(источник истины — ManagementUserConfig); env остаётся фолбэком для старых
dispatch'ей. По успешному rename воркер сообщает server_service новое имя через
`submit_prepared` (тот идемпотентно переписывает `server.management_user`).
"""

import logging

from src.main import broker
from src.services import server_service_client, ssh_client
from src.core.config import get_settings
from src.tasks._destructive_gate import ensure_no_other_running_on_server
from src.tasks._runner import run_task

logger = logging.getLogger(__name__)

# В audit пускаем только нечувствительные поля результата.
AUDIT_SAFE_FIELDS: set[str] = {
    "server_id", "management_user", "management_mode", "synced", "rename_pending",
    "renamed", "old_removed",
}


@broker.task("management_user_sync")
async def management_user_sync(task_id: str) -> None:
    """Досинхронизировать или переименовать управляющую учётку на managed-сервере.

    Параметры: `task_id`. Payload — `server_id`, `management_login` (целевое имя),
    `management_modes` (пер-режимный конфиг), SSH-адресация (`host`/`ssh_port`),
    `is_managed`/`management_user` (текущий управляющий пользователь), опц.
    `rename_pending`, `target_department_id`.

    Поток:

    * `rename_pending=True` и `management_login` отличается от текущего
      `management_user` — cutover-rename: под старым пользователем заводим
      нового, проверяем вход, удаляем старого. Перед деструктивом — гейт C1
      (откладывается, пока на сервере есть другая running-задача). По успеху —
      `submit_prepared` с новым именем (server_service обновит
      `server.management_user`).
    * иначе (имя не меняется / уже переименовано) — идемпотентный
      недеструктивный re-bootstrap групп/команд/ключа под текущим пользователем.

    Возвращает: `{server_id, management_user, management_mode, synced,
    rename_pending}` (+ `renamed`/`old_removed` на cutover'е).

    Связано с: `management_user_config.sync` фан-аут в server_service.
    """
    async def _impl(payload: dict) -> dict:
        server_id = payload["server_id"]
        settings = get_settings()
        # Текущий управляющий пользователь сервера (из prepare) и целевое имя
        # из конфига. Если они различаются и rename_pending выставлен — это
        # cutover-rename.
        rename_pending = bool(payload.get("rename_pending"))
        server_management_user = payload.get("management_user")
        config_login = payload.get("management_login")
        modes = payload.get("management_modes") or {}
        public_key = settings.ssh_management_public_key

        host = payload.get("host") or payload.get("ssh_host")
        port = payload.get("ssh_port") or payload.get("port")

        def _base_creds(management_user: str) -> dict:
            creds: dict = {"is_managed": True, "management_user": management_user}
            if host:
                creds["host"] = host
            if port:
                creds["ssh_port"] = port
            return creds

        # Cutover-rename: имя реально меняется и фан-аут попросил переименовать.
        # Если имя уже совпадает (повторный прогон после успешного rename) —
        # это no-op-ветка обычного sync ниже (idempotent).
        is_rename = (
            rename_pending
            and config_login
            and server_management_user
            and config_login != server_management_user
        )
        if is_rename:
            # Деструктив: создаём/удаляем учётки. Гейт C1 откладывает, пока на
            # сервере крутится другая задача (durable reschedule без расхода
            # max_attempts).
            await ensure_no_other_running_on_server(task_id, server_id)
            logger.info(
                "management_user_sync on %s: cutover rename %s -> %s",
                server_id, server_management_user, config_login,
            )
            result = await ssh_client.cutover_management_user(
                _base_creds(server_management_user), server_id,
                old_management_user=server_management_user,
                new_management_user=config_login,
                public_key=public_key,
                modes=modes,
                management_private_key_path=(
                    settings.ssh_management_private_key_path or None
                ),
            )
            # По завершении rename сообщаем server_service новое имя — он
            # идемпотентно перепишет `server.management_user`. Делаем это
            # только когда старая учётка реально удалена; на успехе cutover
            # всегда возвращает old_removed=True (иначе бросает SshError, и
            # сюда мы не дойдём) — guard оставлен defensive'ом.
            if result.get("old_removed"):
                await server_service_client.submit_prepared(
                    server_id, result["management_user"],
                    payload.get("target_department_id"),
                    management_mode=result.get("management_mode"),
                )
            return {
                "server_id": server_id,
                "management_user": result["management_user"],
                "management_mode": result.get("management_mode"),
                "synced": True,
                "rename_pending": rename_pending,
                "renamed": bool(result.get("old_removed")),
                "old_removed": bool(result.get("old_removed")),
            }

        # Обычный недеструктивный sync под текущим управляющим пользователем.
        management_user = (
            server_management_user
            or config_login
            or settings.ssh_management_user
        )
        result = await ssh_client.sync_management_user(
            _base_creds(management_user), server_id,
            management_user=management_user,
            public_key=public_key,
            modes=modes,
        )
        return {
            "server_id": server_id,
            "management_user": result["management_user"],
            "management_mode": result.get("management_mode"),
            "synced": result.get("synced", True),
            "rename_pending": rename_pending,
        }

    await run_task(
        task_id,
        audit_action="management_user.sync",
        audit_target_type="server",
        impl=_impl,
        audit_safe_fields=AUDIT_SAFE_FIELDS,
    )
