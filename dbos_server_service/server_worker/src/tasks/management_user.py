"""Синхронизация управляющей учётки на подготовленном сервере (`management_user_sync`).

Ставится high-priority фан-аутом из server_service при изменении конфига
управляющей учётки (PUT `/management-user-config`). Сервер уже прошёл prepare
(`is_managed`), поэтому заходим под управляющим пользователем по ключу и
недеструктивно досинхронизируем его учётку: группы + extra_create_commands
нужного режима + публичный ключ управления — повторный идемпотентный bootstrap.

Деструктива тут нет: rename управляющей учётки (смена `login`) этим хендлером
НЕ выполняется. Если в payload `rename_pending=True`, мы это логируем и
синхронизируем только под ТЕКУЩИМ управляющим пользователем сервера — сам
rename/cutover делает отдельный хендлер (фаза C). Имя управляющей учётки и
пер-режимный конфиг едут в payload'е из server_service (источник истины —
ManagementUserConfig); env остаётся фолбэком для старых dispatch'ей.
"""

import logging

from src.main import broker
from src.services import ssh_client
from src.core.config import get_settings
from src.tasks._runner import run_task

logger = logging.getLogger(__name__)

# В audit пускаем только нечувствительные поля результата.
AUDIT_SAFE_FIELDS: set[str] = {
    "server_id", "management_user", "management_mode", "synced", "rename_pending",
}


@broker.task("management_user_sync")
async def management_user_sync(task_id: str) -> None:
    """Досинхронизировать управляющую учётку на managed-сервере.

    Параметры: `task_id`. Payload — `server_id`, `management_login`,
    `management_modes` (пер-режимный конфиг), SSH-адресация (`host`/`ssh_port`),
    `is_managed`/`management_user`, опц. `rename_pending`,
    `target_department_id`.

    Поток: собрать management key-session-креды → `ssh_client.sync_management_user`
    (детект режима + идемпотентный re-bootstrap групп/команд/ключа). Деструктива
    нет, rename не выполняется.

    Возвращает: `{server_id, management_user, management_mode, synced,
    rename_pending}`.

    Связано с: `management_user_config.sync` фан-аут в server_service.
    """
    async def _impl(payload: dict) -> dict:
        server_id = payload["server_id"]
        settings = get_settings()
        # Имя управляющей учётки: на этом сервере она уже заведена под
        # management_user (из prepare). Конфиг шлёт `management_login` — это
        # целевое имя. Если оно отличается, это rename-кейс — его мы тут НЕ
        # делаем: синхронизируем под существующим управляющим пользователем
        # сервера, а rename оставляем фазе C. Так sync не пытается завести
        # вторую управляющую учётку до cutover'а.
        rename_pending = bool(payload.get("rename_pending"))
        server_management_user = payload.get("management_user")
        config_login = payload.get("management_login")
        management_user = (
            server_management_user
            or config_login
            or settings.ssh_management_user
        )
        if rename_pending:
            logger.info(
                "management_user_sync on %s: login rename pending "
                "(server=%s config=%s) — syncing under current management user, "
                "rename deferred to cutover phase",
                server_id, server_management_user, config_login,
            )
        modes = payload.get("management_modes") or {}
        public_key = settings.ssh_management_public_key

        creds: dict = {"is_managed": True, "management_user": management_user}
        host = payload.get("host") or payload.get("ssh_host")
        if host:
            creds["host"] = host
        port = payload.get("ssh_port") or payload.get("port")
        if port:
            creds["ssh_port"] = port

        result = await ssh_client.sync_management_user(
            creds, server_id,
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
