"""Бутстрап управления сервером по SSH (`server.prepare`).

Онбординг ещё не управляемого сервера: заходим под одноразовыми bootstrap-
кредами (password-auth), заводим системного управляющего пользователя DBOS,
даём ему sudo и кладём публичный ключ управления. Дальше управление — по ключу
без исходного пароля.

Bootstrap-креды (`bootstrap_login` / `bootstrap_password`) приходят в payload
через cross-DB dispatch-канал server_service'а. Они одноразовые: handler
стирает их из персистентной `tasks.payload` сразу после чтения
(`task_repo.scrub_payload_keys`), чтобы plaintext не оставался в БД воркера
после исполнения task'и (включая последующие retry/forensics).

Публичный ключ и имя управляющего пользователя берём из конфига воркера
(`SSH_MANAGEMENT_PUBLIC_KEY` / `SSH_MANAGEMENT_USER`) — серверу их знать не
нужно. По завершении POST'им callback `submit_prepared`, server_service
помечает сервер подготовленным (`is_managed`, `prepared_at`, management_user).
"""

import logging

from src.core.config import get_settings
from src.db.session import AsyncSessionLocal
from src.repositories import task as task_repo
from src.main import broker
from src.services import server_service_client, ssh_client
from src.tasks._runner import run_task

logger = logging.getLogger(__name__)

# В audit пускаем только server_id и имя управляющего юзера — bootstrap-логин
# и пароль наружу не уходят ни при каких обстоятельствах.
AUDIT_SAFE_FIELDS: set[str] = {"server_id", "management_user", "prepared"}

# Ключи bootstrap-кред в payload — стираем их из персистентной строки сразу
# после чтения.
_BOOTSTRAP_KEYS = ["bootstrap_login", "bootstrap_password"]


@broker.task("server.prepare")
async def server_prepare(task_id: str) -> None:
    """Бутстрап управления: завести управляющего пользователя + положить ключ.

    Параметры: `task_id`. Payload — `server_id`, `bootstrap_login`,
    `bootstrap_password`, опц. `target_department_id`, плюс SSH-поля
    (`host`/`port`/`known_hosts`), если server_service их положил.

    Поток: считать bootstrap-креды → стереть их из персистентной payload →
    `ssh_client.bootstrap_management_user` (useradd управляющего юзера + sudo +
    authorized_keys, idempotent) → `submit_prepared` callback.

    Idempotent: повторный prepare не падает на уже заведённом юзере / ключе.

    Возвращает: `{server_id, management_user, prepared}`.
    Связано с: `server.prepare` audit action, `server_service.internal_service
    .record_server_prepared`.
    """
    async def _impl(payload: dict) -> dict:
        server_id = payload["server_id"]
        target_dept = payload.get("target_department_id")
        bootstrap_login = payload.get("bootstrap_login")
        bootstrap_password = payload.get("bootstrap_password")

        # Стираем одноразовые креды из персистентной payload сразу после
        # чтения — отдельной сессией с commit'ом, чтобы они не пережили
        # task'у в БД воркера.
        async with AsyncSessionLocal() as session:
            await task_repo.scrub_payload_keys(session, task_id, _BOOTSTRAP_KEYS)
            await session.commit()

        settings = get_settings()
        management_user = settings.ssh_management_user
        public_key = settings.ssh_management_public_key

        creds = {
            "login": bootstrap_login,
            "password": bootstrap_password,
            "host": payload.get("host"),
            "port": payload.get("ssh_port") or payload.get("port"),
            "known_hosts": payload.get("known_hosts"),
        }
        await ssh_client.bootstrap_management_user(
            creds, server_id,
            management_user=management_user,
            public_key=public_key,
        )
        await server_service_client.submit_prepared(
            server_id, management_user, target_dept,
        )
        return {
            "server_id": server_id,
            "management_user": management_user,
            "prepared": True,
        }

    await run_task(
        task_id,
        audit_action="server.prepare",
        audit_target_type="server",
        impl=_impl,
        audit_safe_fields=AUDIT_SAFE_FIELDS,
    )
