"""Ротация per-server управляющих кред (`server.rotate_management_creds`).

Сервер уже подготовлен (`is_managed`); server_service сгенерил новую пару +
пароль, записал ciphertext в БД (старый материал ушёл в `previous_mgmt_*`,
`mgmt_creds_pending_apply=True`) и положил новый plaintext в dispatch-stash
`dbos:dispatch_creds:<id>` полем-набором `{new_public_key, new_private_key,
new_password, management_user}`. Ссылка на stash едет в payload как
`creds_stash_key`.

Worker заходит на бокс ТЕКУЩИМ рабочим ключом (его отдаёт
`fetch_management_credentials`: пока pending_apply — это previous-материал, то
есть реально стоящий на боксе), ставит новый публичный ключ + пароль,
проверяет вход новым ключом (анти-локаут) и убирает старый ключ. По успеху —
callback `submit_management_creds_applied`, после которого server_service
снимает pending_apply, зануляет previous и фиксирует `mgmt_creds_rotated_at`.

Деструктив (смена ключа, которым ходят остальные операции) — под гейтом C1
`ensure_no_other_running_on_server`, как cutover управляющей учётки: пока на
сервере крутится другая задача, ротация откладывается без расхода attempts.
"""

import json
import logging
import re

from src.clients.ssh import SshError
from src.main import broker
from src.services import redis_pool, server_service_client, ssh_client
from src.services.redis_stash_crypto import (
    aad_for_redis_stash,
    decrypt_stash,
    stash_id_from_key,
)
from src.tasks._destructive_gate import ensure_no_other_running_on_server
from src.tasks._runner import run_task

logger = logging.getLogger(__name__)

# В audit пускаем только нечувствительные поля — никакого ключа/пароля.
AUDIT_SAFE_FIELDS: set[str] = {"server_id", "management_user", "rotated"}

# Формат ключа dispatch-stash'а — тот же, что у provision (`worker_client
# .dispatch_creds_key`). Жёсткий guard, чтобы скомпрометированный payload не
# увёл читателя Redis в чужой keyspace (`creds_stash_key=":/admin"`).
_DISPATCH_CREDS_KEY_RE = re.compile(r"^dbos:dispatch_creds:[A-Za-z0-9_\-]{1,128}$")


def _validate_dispatch_creds_key(stash_key: str) -> None:
    if not isinstance(stash_key, str) or not _DISPATCH_CREDS_KEY_RE.fullmatch(stash_key):
        raise SshError(
            error_code="DISPATCH_STASH_MISSING",
            host="",
            message="creds_stash_key has unexpected format",
        )


async def _read_rotate_creds(stash_key: str) -> dict:
    """Прочитать новый управляющий материал из dispatch-stash'а.

    Возвращает dict `{new_public_key, new_private_key, new_password,
    management_user}`. Нет ключа в Redis (TTL истёк) → `SshError(
    DISPATCH_STASH_MISSING)`. Битый/swap'нутый token → `AppException(
    STASH_DECRYPT_*)` пробрасывается до `_runner` (task FAILED с явным кодом).
    """
    _validate_dispatch_creds_key(stash_key)
    client = redis_pool.get_redis()
    raw = await client.get(stash_key)
    if raw is None:
        raise SshError(
            error_code="DISPATCH_STASH_MISSING",
            host="",
            message=(
                "rotation credentials are missing or expired in Redis; "
                "re-run management credentials rotation"
            ),
        )
    text = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else str(raw)
    plaintext = decrypt_stash(
        text, aad=aad_for_redis_stash(stash_id_from_key(stash_key)),
    )
    try:
        data = json.loads(plaintext)
    except (ValueError, TypeError) as exc:
        raise SshError(
            error_code="DISPATCH_STASH_MISSING",
            host="",
            message="rotation credentials payload is malformed; re-run rotation",
        ) from exc
    if not isinstance(data, dict):
        raise SshError(
            error_code="DISPATCH_STASH_MISSING",
            host="",
            message="rotation credentials payload is malformed; re-run rotation",
        )
    return data


async def _delete_rotate_creds(stash_key: str) -> None:
    """Снять dispatch-stash из Redis после успешной ротации (best-effort)."""
    try:
        _validate_dispatch_creds_key(stash_key)
    except SshError:
        return
    client = redis_pool.get_redis()
    try:
        await client.delete(stash_key)
    except Exception:  # noqa: BLE001
        logger.debug("failed to delete rotate dispatch creds key", exc_info=True)


@broker.task("server.rotate_management_creds")
async def rotate_management_creds(task_id: str) -> None:
    """Перевыкатить управляющую пару + пароль `dbos` на подготовленном сервере.

    Параметры: `task_id`. Payload — `server_id`, `creds_stash_key` (ссылка на
    dispatch-stash с новым материалом), SSH-адресация (`host`/`ssh_port`), опц.
    `management_user` (текущее имя управляющего пользователя),
    `target_department_id`.

    Поток: гейт C1 → прочитать новый материал из stash → войти текущим ключом
    (fetch отдаёт рабочий на боксе) → поставить новый pubkey + пароль →
    проверить вход новым → убрать старый → callback applied → удалить stash.

    Возвращает: `{server_id, management_user, rotated}`.
    Связано с: `server.management_creds_rotated` audit action.
    """
    async def _impl(payload: dict) -> dict:
        server_id = payload["server_id"]
        target_dept = payload.get("target_department_id")
        stash_key = payload.get("creds_stash_key")
        if not stash_key:
            raise SshError(
                error_code="DISPATCH_STASH_MISSING",
                host="",
                message="payload has no creds_stash_key reference",
            )

        # Смена ключа, которым ходят остальные операции, — деструктив: пока на
        # сервере есть другая running-задача, откладываемся (durable reschedule).
        await ensure_no_other_running_on_server(task_id, server_id)

        new_creds = await _read_rotate_creds(stash_key)
        new_public_key = new_creds.get("new_public_key")
        new_private_key = new_creds.get("new_private_key")
        new_password = new_creds.get("new_password")
        missing = [
            name for name, value in (
                ("new_public_key", new_public_key),
                ("new_private_key", new_private_key),
                ("new_password", new_password),
            ) if not value
        ]
        if missing:
            raise SshError(
                error_code="DISPATCH_STASH_MISSING",
                host="",
                message=(
                    "rotation stash is incomplete "
                    f"(missing: {', '.join(missing)}); re-run rotation"
                ),
            )

        # Текущий рабочий ключ для входа: fetch при pending_apply отдаёт
        # previous-материал, то есть реально стоящий на боксе.
        current = await server_service_client.fetch_management_credentials(
            server_id, target_dept,
        )
        management_user = (
            new_creds.get("management_user")
            or payload.get("management_user")
            or current.get("management_user")
        )

        creds: dict = {
            "is_managed": True,
            "management_user": management_user,
            "management_private_key": current.get("ssh_private_key"),
        }
        host = payload.get("host") or payload.get("ssh_host")
        if host:
            creds["host"] = host
        port = payload.get("ssh_port") or payload.get("port")
        if port:
            creds["ssh_port"] = port

        await ssh_client.rotate_management_creds_on_host(
            creds, server_id,
            management_user=management_user,
            new_public_key=new_public_key,
            new_private_key=new_private_key,
            new_password=new_password,
        )

        # Подтверждаем применение: server_service снимет pending_apply, занулит
        # previous-материал и проставит rotated_at.
        await server_service_client.submit_management_creds_applied(
            server_id, target_dept,
        )
        await _delete_rotate_creds(stash_key)
        return {
            "server_id": server_id,
            "management_user": management_user,
            "rotated": True,
        }

    await run_task(
        task_id,
        audit_action="server.management_creds_rotated",
        audit_target_type="server",
        impl=_impl,
        audit_safe_fields=AUDIT_SAFE_FIELDS,
    )
