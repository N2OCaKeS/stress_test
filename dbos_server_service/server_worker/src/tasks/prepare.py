"""Бутстрап управления сервером по SSH (`server.prepare`).

Онбординг ещё не управляемого сервера: заходим под одноразовыми bootstrap-
кредами (password-auth), заводим системного управляющего пользователя DBOS,
даём ему sudo и кладём публичный ключ управления. Дальше управление — по ключу
без исходного пароля.

Bootstrap-креды (`bootstrap_login` / `bootstrap_password`) НЕ хранятся в
персистентной `tasks.payload` — иначе plaintext осел бы в worker-БД. Вместо
этого server_service кладёт их в Redis под одноразовый ключ с TTL, а в payload
передаёт только ссылку (`bootstrap_creds_key`). Handler читает креды из Redis
на каждой попытке: пока жив TTL, retry работает; по истечении ключ исчезает сам.
Если ключа нет/истёк — `SSH_BOOTSTRAP_CREDS_MISSING` (task FAILED с понятным
last_error, не молчаливый краш под `root`).

Публичный ключ и имя управляющего пользователя берём из конфига воркера
(`SSH_MANAGEMENT_PUBLIC_KEY` / `SSH_MANAGEMENT_USER`) — серверу их знать не
нужно. По завершении POST'им callback `submit_prepared`, server_service
помечает сервер подготовленным (`is_managed`, `prepared_at`, management_user),
а ключ из Redis удаляем (TTL подстраховывает в любом случае).
"""

import json
import logging
import re

import redis.asyncio as aioredis

from src.clients.ssh import SshError
from src.core.config import get_settings
from src.main import broker
from src.services import server_service_client, ssh_client
from src.tasks._runner import run_task

logger = logging.getLogger(__name__)

# В audit пускаем только server_id и имя управляющего юзера — bootstrap-логин
# и пароль наружу не уходят ни при каких обстоятельствах.
AUDIT_SAFE_FIELDS: set[str] = {"server_id", "management_user", "prepared"}

# Формат ключа задаёт server_service (`worker_client.prepare_creds_key` +
# `_new_id("pcd_")` → `dbos:prepare_creds:pcd_<uuid4.hex>`). Жёстко его
# валидируем, чтобы при компрометации payload в очереди (или при отсутствии
# AUTH на Redis в staging/dev) нельзя было подсунуть произвольный ключ и
# прочитать чужие значения через `_read_bootstrap_creds`. Пускаем любое
# Тело — alnum/`_`/`-`, длиной до 128. Smin = 1 (защита от пустого тела
# уже есть); нижняя граница нужна только чтобы не пускать `dbos:prepare_creds:`
# с пустым хвостом — это покрывает `{1,128}`. Длину uuid не хардкодим на
# случай смены id-фабрики.
_BOOTSTRAP_KEY_RE = re.compile(r"^dbos:prepare_creds:[A-Za-z0-9_\-]{1,128}$")


async def _read_bootstrap_creds(creds_key: str) -> dict:
    """Прочитать bootstrap-креды из Redis по ключу-ссылке из payload.

    Креды кладёт server_service при dispatch (с TTL). Читаем на каждой попытке,
    поэтому retry работает, пока жив TTL. Нет ключа / истёк → `SshError(
    SSH_BOOTSTRAP_CREDS_MISSING)` — task завершится FAILED с понятным
    last_error, а не молчаливо зайдёт под `root` без пароля.
    """
    settings = get_settings()
    client = aioredis.from_url(settings.redis_url)
    try:
        raw = await client.get(creds_key)
    finally:
        await client.aclose()
    if raw is None:
        raise SshError(
            error_code="SSH_BOOTSTRAP_CREDS_MISSING",
            host="",
            message=(
                "bootstrap credentials are missing or expired in Redis; "
                "re-run prepare to supply them again"
            ),
        )
    return json.loads(raw)


async def _delete_bootstrap_creds(creds_key: str) -> None:
    """Удалить bootstrap-креды из Redis после успешного prepare (best-effort).

    TTL подчистит ключ в любом случае; явный DELETE просто сокращает окно
    жизни кред до минимума. Ошибку глушим — это посмертный cleanup.
    """
    settings = get_settings()
    client = aioredis.from_url(settings.redis_url)
    try:
        await client.delete(creds_key)
    except Exception:  # noqa: BLE001
        logger.debug("failed to delete bootstrap creds key", exc_info=True)
    finally:
        await client.aclose()


@broker.task("server.prepare")
async def server_prepare(task_id: str) -> None:
    """Бутстрап управления: завести управляющего пользователя + положить ключ.

    Параметры: `task_id`. Payload — `server_id`, `bootstrap_creds_key`
    (ссылка на Redis с одноразовыми bootstrap-кредами), опц.
    `target_department_id`, плюс SSH-поля (`host`/`port`), если server_service
    их положил.

    Поток: прочитать bootstrap-креды из Redis по ссылке →
    `ssh_client.bootstrap_management_user` (useradd управляющего юзера + sudo +
    NOPASSWD + authorized_keys, idempotent) → `submit_prepared` callback →
    удалить креды из Redis.

    Idempotent: повторный prepare не падает на уже заведённом юзере / ключе;
    retry работает, пока жив Redis-TTL ключа кред.

    Возвращает: `{server_id, management_user, prepared}`.
    Связано с: `server.prepare` audit action, `server_service.internal_service
    .record_server_prepared`.
    """
    async def _impl(payload: dict) -> dict:
        server_id = payload["server_id"]
        target_dept = payload.get("target_department_id")
        creds_key = payload.get("bootstrap_creds_key")
        if not creds_key:
            raise SshError(
                error_code="SSH_BOOTSTRAP_CREDS_MISSING",
                host="",
                message="payload has no bootstrap_creds_key reference",
            )
        if not isinstance(creds_key, str) or not _BOOTSTRAP_KEY_RE.fullmatch(creds_key):
            # Не доверяем строке из payload: если очередь скомпрометирована,
            # произвольный ключ дал бы читателю Redis любые значения.
            raise SshError(
                error_code="SSH_BOOTSTRAP_CREDS_MISSING",
                host="",
                message="bootstrap_creds_key has unexpected format",
            )

        # Читаем одноразовые креды из Redis на каждой попытке — пока жив TTL,
        # retry работает; истёк → SSH_BOOTSTRAP_CREDS_MISSING.
        bootstrap = await _read_bootstrap_creds(creds_key)

        settings = get_settings()
        management_user = settings.ssh_management_user
        public_key = settings.ssh_management_public_key

        creds = {
            "login": bootstrap.get("bootstrap_login"),
            "password": bootstrap.get("bootstrap_password"),
        }
        # `apply_session_hints` доклеивает host/ssh_port из payload и (для
        # consistency) is_managed/management_user — на prepare последние
        # ничего не меняют, потому что bootstrap_management_user открывает
        # password-сессию напрямую через SshClient.
        ssh_client.apply_session_hints(creds, payload)
        await ssh_client.bootstrap_management_user(
            creds, server_id,
            management_user=management_user,
            public_key=public_key,
        )
        await server_service_client.submit_prepared(
            server_id, management_user, target_dept,
        )
        await _delete_bootstrap_creds(creds_key)
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
