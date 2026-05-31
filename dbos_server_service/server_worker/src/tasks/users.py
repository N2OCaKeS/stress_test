"""Инвентаризация OS-пользователей сервера по SSH.

Параллель `inventory.sync`, но цель — пользователи, а не железо. Поток:

  1. если в payload есть `account_id` — тянем пароль аккаунта через
     server_service, иначе дефолтный `ssh_login` (`root`);
  2. `ssh_client.collect_os_users` снимает `getent passwd` / `getent group` /
     `/etc/login.defs`;
  3. `os_users_facts_to_payload` фильтрует системных по `UID_MIN` и собирает
     flat-список под `UsersInventoryCallbackRequest`;
  4. `submit_users_inventory` POST'ит его обратно — server_service reconcile'ит.

Submit-фейл (network / 4xx / 5xx) НЕ роняет task'у в FAILED: список юзеров
остаётся в `task.result`, а submit-fail идёт в audit как
`submit_status=submit_failed:<code>`.
"""

import json
import logging

import redis.asyncio as aioredis

from src.clients.ssh import SshError
from src.core.config import get_settings
from src.core.constants import SCRUBBED_SENTINEL, STASH_TTL_SECONDS
from src.core.exceptions import CredentialFetchError
from src.core.identifiers import validate_task_id
from src.db.session import AsyncSessionLocal
from src.main import broker
from src.repositories import task as task_repo
from src.services import server_service_client, ssh_client
from src.tasks._runner import run_task

logger = logging.getLogger(__name__)

# Префикс для Redis-stash'а inline-creds provision'а. Симметрично
# `_ACCOUNT_ROTATE_KEY_PREFIX` в `tasks/passwords.py`: tasks/payload row в БД
# чистим в `finally` (defense-in-depth от утечки в `tasks.payload`), но между
# попытками те же `password_plaintext` / `ssh_private_key_plaintext` нужны —
# хранилище server_service выдаёт inline-креды один раз через
# dispatch-канал, повторно их запросить нельзя.
_PROVISION_INLINE_KEY_PREFIX = "dbos:provision_inline:"


async def _read_provision_inline(task_id: str) -> tuple[str | None, str | None]:
    """Прочитать stash'енные inline-креды provision'а.

    Возвращает `(password_plaintext, ssh_private_key_plaintext)`. None
    для каждого поля означает «не было в payload первой попытки» либо
    «TTL истёк». Поднимать новые retry'и при истечении TTL — задача
    оператора (server_service сгенерирует новые креды).
    """
    validate_task_id(task_id)
    settings = get_settings()
    client = aioredis.from_url(settings.redis_url)
    try:
        raw = await client.get(_PROVISION_INLINE_KEY_PREFIX + task_id)
    finally:
        await client.aclose()
    if raw is None:
        return None, None
    text = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else str(raw)
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None, None
    if not isinstance(data, dict):
        return None, None
    return data.get("password_plaintext"), data.get("ssh_private_key_plaintext")


async def _store_provision_inline(
    task_id: str,
    password_plaintext: str | None,
    ssh_private_key_plaintext: str | None,
) -> None:
    """Положить inline-креды provision'а в Redis ДО finally-scrub'а payload'а.

    Stash переживает retry'и: на следующем заходе `_impl` payload в БД уже
    содержит `"<scrubbed>"`, и единственный способ восстановить оригинал —
    Redis. Если ни password, ни private_key не пришли — stash не пишем
    (нечего сохранять, лишний ключ в Redis не нужен).
    """
    if password_plaintext is None and ssh_private_key_plaintext is None:
        return
    validate_task_id(task_id)
    settings = get_settings()
    client = aioredis.from_url(settings.redis_url)
    try:
        await client.set(
            _PROVISION_INLINE_KEY_PREFIX + task_id,
            json.dumps({
                "password_plaintext": password_plaintext,
                "ssh_private_key_plaintext": ssh_private_key_plaintext,
            }),
            ex=STASH_TTL_SECONDS,
        )
    finally:
        await client.aclose()


async def _delete_provision_inline(task_id: str) -> None:
    """Дропнуть stash после успешного submit'а. TTL подстрахует."""
    validate_task_id(task_id)
    settings = get_settings()
    client = aioredis.from_url(settings.redis_url)
    try:
        try:
            await client.delete(_PROVISION_INLINE_KEY_PREFIX + task_id)
        except Exception:  # noqa: BLE001
            logger.debug(
                "failed to delete provision inline stash", exc_info=True,
            )
    finally:
        await client.aclose()


def _unscrub(value):
    """Вернуть None, если значение — sentinel `"<scrubbed>"`.

    Защита от того, что на retry'е `_impl` прочтёт замаскированное значение
    из persisted payload и использует его как валидный секрет (chpasswd
    принял бы literal `"<scrubbed>"` и сломал бы вход на хост).
    """
    if value == SCRUBBED_SENTINEL:
        return None
    return value


async def _account_creds(
    payload: dict, server_id: str, account_id: str, target_dept: str | None,
) -> dict:
    """Собрать credentials для привилегированной операции над аккаунтом.

    На не управляемом сервере сессия идёт под самим аккаунтом по паролю, поэтому
    пароль обязателен — тянем его из server_service, при отказе задача падает
    (без пароля на SSH не зайти).

    На управляемом сервере аутентификация по ключу под управляющим
    пользователем, пароль аккаунта для входа не нужен. `login` берём из payload
    (его кладёт server_service для provision/update/deprovision), пароль не
    запрашиваем — у discovered-аккаунта его может не быть вовсе.
    """
    login = payload.get("login")
    is_managed = bool(payload.get("is_managed"))
    # Whitespace-only также невалидно — login для useradd/usermod не может быть
    # пустым после strip.
    if is_managed and (not login or not login.strip()):
        # Managed-ветка ssh_client.modify_user/delete_user подставляет
        # `creds["login"]` в `useradd/usermod/userdel` через `_validate_login`,
        # который сразу падает на `None`/пустоте с TypeError из re.match.
        # Лучше отбить тут со стабильным error_code, чем тащить уродский
        # traceback из re.
        raise SshError(
            error_code="SSH_INVALID_ARG",
            host="",
            message="login is required for managed account operations",
        )
    if is_managed and login:
        creds = {"login": login}
    else:
        # Self-сессия: пароль обязателен для входа под аккаунтом.
        creds = await server_service_client.fetch_account_password(
            server_id, account_id, target_dept,
        )
    ssh_client.apply_session_hints(creds, payload)
    return creds


async def _fetch_password_to_set(
    server_id: str, account_id: str, target_dept: str | None,
) -> str | None:
    """Best-effort пароль аккаунта, чтобы выставить его на боксе (managed).

    На управляемом сервере вход по ключу, пароль нужен только чтобы прописать
    его пользователю. У discovered-аккаунта пароля нет — server_service вернёт
    `ACCOUNT_PASSWORD_UNAVAILABLE`, тогда возвращаем `None` и шаг chpasswd
    пропускается. Транспортная ошибка пробрасывается (это уже не штатное
    «пароля нет», а недоступность сервиса).
    """
    try:
        creds = await server_service_client.fetch_account_password(
            server_id, account_id, target_dept,
        )
    except CredentialFetchError as exc:
        if exc.error_code == "ACCOUNT_PASSWORD_UNAVAILABLE":
            logger.info(
                "managed provision server_id=%s account_id=%s: no stored "
                "password, skipping chpasswd",
                server_id,
                account_id,
            )
            return None
        raise
    return creds.get("password")


# Whitelist для audit details.result. Сами логины/группы/home — потенциально
# чувствительный inventory, в audit кладём только счётчики и server_id.
# Полный список доступен админу через `Task.result`.
AUDIT_SAFE_FIELDS: set[str] = {"server_id", "user_count", "submit_status"}


@broker.task("users.inventory")
async def users_inventory(task_id: str) -> None:
    """Снять список OS-пользователей сервера по SSH и сдать в server_service.

    Параметры: `task_id`. Payload — `server_id`, опционально `account_id`,
    `ssh_login`, `target_department_id`.

    Возвращает: `{server_id, user_count, submit_status}` — в audit уходят
    только эти поля (см. AUDIT_SAFE_FIELDS), сам список юзеров — нет.

    Возможные ошибки: `CredentialFetchError` (если `account_id` задан, но
    server_service не отдал пароль), ошибки SSH-клиента.

    Связано с: `server_account.users_inventory` audit action.
    """
    async def _impl(payload: dict) -> dict:
        server_id = payload["server_id"]
        account_id = payload.get("account_id")
        target_dept = payload.get("target_department_id")
        is_managed = bool(payload.get("is_managed"))

        # На управляемом сервере сессия идёт по ключу под управляющим
        # пользователем — пароль аккаунта не нужен (его может и не быть у
        # discovered-аккаунта). Тянем только когда сессия пойдёт под аккаунтом.
        if account_id and not is_managed:
            creds = await server_service_client.fetch_account_password(
                server_id, account_id, target_dept,
            )
        else:
            creds = {"login": payload.get("ssh_login", "root")}
        ssh_client.apply_session_hints(creds, payload)

        facts = await ssh_client.collect_os_users(creds, server_id)
        users_payload = ssh_client.os_users_facts_to_payload(facts)
        user_count = len(users_payload.get("users", []))

        submit_status: str
        try:
            await server_service_client.submit_users_inventory(
                server_id, users_payload, target_dept,
            )
            submit_status = "submitted"
        except CredentialFetchError as exc:
            logger.warning(
                "users inventory submit failed server_id=%s error_code=%s; "
                "result kept in task.result",
                server_id,
                exc.error_code,
            )
            submit_status = f"submit_failed:{exc.error_code}"
        return {
            "server_id": server_id,
            "users": users_payload.get("users", []),
            "user_count": user_count,
            "submit_status": submit_status,
        }

    await run_task(
        task_id,
        audit_action="server_account.users_inventory",
        audit_target_type="server",
        impl=_impl,
        audit_safe_fields=AUDIT_SAFE_FIELDS,
    )


# Whitelist для audit details.result у provision/update/deprovision. Login и
# server_id — не секрет; пароль и состав групп в audit не уходят.
AUDIT_SAFE_FIELDS_PROVISION: set[str] = {
    "server_id", "account_id", "operation", "present_on_server",
}


@broker.task("account.provision")
async def account_provision(task_id: str) -> None:
    """Завести OS-пользователя на сервере (`useradd`) и подтвердить статус.

    Поток: собираем сессию (`_account_creds`) → `ssh_client.provision_user`
    (useradd + опц. chpasswd, groups/sudo/shell/home из payload) →
    `submit_provision_status(present=True)`.

    На управляемом сервере вход по ключу: пароль для аутентификации не нужен,
    `login` берём из payload. Пароль тянем best-effort только чтобы выставить
    его пользователю; у discovered-аккаунта пароля нет — заводим без смены
    пароля. На не управляемом сервере пароль обязателен (self-сессия).

    Параметры: `task_id`. Payload — `server_id`, `account_id`, `login`,
    `has_sudo`, `unix_groups`, `shell`, `home_dir`, опц. `target_department_id`,
    `is_managed`, `management_user`.

    Idempotent: уже существующий пользователь синхронизируется, не падает.

    Возвращает: `{server_id, account_id, operation, present_on_server}`.
    Связано с: `server_account.provision` audit action.
    """
    async def _impl(payload: dict) -> dict:
        server_id = payload["server_id"]
        account_id = payload["account_id"]
        target_dept = payload.get("target_department_id")

        # F23-B: discovered-сценарий — server_service кладёт сгенерированные
        # креды (`password_plaintext` + `ssh_public_key` + `force_replace`)
        # прямо в payload. Тот же канал используется для re-provision после
        # переустановки ОС (`force_replace=True`).
        #
        # Inline-секреты живут в Redis-stash под task_id'ом до конца ротации
        # попыток: payload-row в БД мы чистим в `finally` (защита от утечки
        # в `tasks.payload`), но retry-попытке те же значения нужны заново,
        # а server_service отдаёт их разово через dispatch-канал. На retry'е
        # `_impl` сначала смотрит в stash: если он есть — берёт оттуда, если
        # пустой (первая попытка) — пишет туда то, что пришло в payload, и
        # стартует scrub в `finally`. `_unscrub` глушит literal-sentinel
        # `"<scrubbed>"` на случай, если stash потерян (TTL/Redis-restart),
        # а retry уже видит scrubbed-payload: лучше не выставить пароль на
        # хост, чем поставить literal `"<scrubbed>"` (chpasswd не различает).
        stashed_password, stashed_private_key = await _read_provision_inline(task_id)
        inline_password = stashed_password or _unscrub(payload.get("password_plaintext"))
        inline_private_key = stashed_private_key or _unscrub(payload.get("ssh_private_key_plaintext"))
        inline_public_key = payload.get("ssh_public_key")
        force_replace = bool(payload.get("force_replace"))

        # Пишем stash до первого внешнего вызова (а значит — до первого
        # возможного исключения, после которого `finally` зачистит payload).
        # Если ничего полезного в payload не было — `_store_provision_inline`
        # сам no-op. Идемпотентно: если stash уже жил (повторный заход
        # после crash'а ровно между store и scrub) — перезаписываем тем же
        # значением.
        #
        # Условие `and` сознательно: «store только если в Redis вообще нет
        # stash'а» (первая попытка). При наличии хоть одного непустого поля
        # это уже не первый заход — payload в БД уже scrubbed, перезаписывать
        # stash тем же значением смысла нет. Партиально-пустой stash
        # (`password != None, private_key = None`) — корректное состояние:
        # provision-payload изначально содержал только пароль, второй слот
        # был None и так.
        # При добавлении третьего inline-секрета это условие потребует
        # пересмотра (сейчас оно жёстко завязано на ровно два слота).
        if stashed_password is None and stashed_private_key is None:
            await _store_provision_inline(task_id, inline_password, inline_private_key)

        # Defense-in-depth: inline-креды из discovered-сценария (F23-B) —
        # `password_plaintext` и `ssh_private_key_plaintext` — приходят
        # прямо в payload и без явной зачистки остаются в `tasks.payload`
        # до retention cleanup'а (до 30 дней). Стираем их в `finally` —
        # тогда scrub срабатывает и на happy-path'е, и на любом исключении
        # из ssh_client / submit_provision_status. Дубликат секрета между
        # попытками держит Redis-stash, не payload-row.
        # `ssh_public_key` — не секрет, его оставляем для форенсики.
        # Best-effort: если scrub упал, основной поток не валим.
        try:
            creds = await _account_creds(payload, server_id, account_id, target_dept)
            # На управляемом сервере пароль для входа не нужен (ключ), но если у
            # аккаунта есть хранимый пароль — ставим его на боксе. Тянем
            # best-effort: discovered-аккаунт без пароля заводим без смены
            # пароля, не падаем.
            new_password = inline_password or creds.get("password")
            if payload.get("is_managed") and new_password is None:
                new_password = await _fetch_password_to_set(
                    server_id, account_id, target_dept,
                )
            await ssh_client.provision_user(
                creds, server_id,
                login=creds["login"],
                new_password=new_password,
                groups=payload.get("unix_groups") or [],
                has_sudo=bool(payload.get("has_sudo")),
                shell=payload.get("shell"),
                home_dir=payload.get("home_dir"),
                public_key=inline_public_key,
                force_replace=force_replace,
            )
            await server_service_client.submit_provision_status(
                server_id, account_id, "provision", True, target_dept,
            )
            # Успех — stash больше не нужен. TTL подстрахует, явный DELETE
            # сокращает окно жизни plaintext'а в Redis.
            await _delete_provision_inline(task_id)
            return {
                "server_id": server_id,
                "account_id": account_id,
                "operation": "provision",
                "present_on_server": True,
            }
        finally:
            # Оба ключа кладёт `worker_dispatch._dispatch_account_on_host`,
            # когда `inject_provision_creds=True`. Других tasks (update_on_host,
            # deprovision) этот dispatch-путь не использует — там в payload'е
            # этих полей нет, поэтому отдельных scrub'ов им не делаем. Если
            # когда-нибудь добавим inject в update/deprovision — список
            # синхронизировать тоже там, не оставлять fallthrough.
            try:
                async with AsyncSessionLocal() as scrub_session:
                    await task_repo.scrub_payload_keys(
                        scrub_session, task_id,
                        ["password_plaintext", "ssh_private_key_plaintext"],
                    )
                    await scrub_session.commit()
            except Exception:  # noqa: BLE001
                logger.warning(
                    "failed to scrub inline provision creds from payload",
                    exc_info=True,
                    extra={"task_id": task_id},
                )

    await run_task(
        task_id,
        audit_action="server_account.provision",
        audit_target_type="server_account",
        impl=_impl,
        audit_safe_fields=AUDIT_SAFE_FIELDS_PROVISION,
    )


@broker.task("account.update_on_host")
async def account_update_on_host(task_id: str) -> None:
    """Синхронизировать атрибуты OS-пользователя на сервере (`usermod`).

    Поток: собираем сессию (`_account_creds`) → `ssh_client.modify_user`
    (usermod groups/sudo/shell) → `submit_provision_status(present=True)`.
    Пароль не меняется, поэтому на управляемом сервере он не запрашивается
    вовсе — `login` берётся из payload.

    Параметры/payload — как у `account_provision`.

    Возвращает: `{server_id, account_id, operation, present_on_server}`.
    Связано с: `server_account.update_on_host` audit action.
    """
    async def _impl(payload: dict) -> dict:
        server_id = payload["server_id"]
        account_id = payload["account_id"]
        target_dept = payload.get("target_department_id")

        creds = await _account_creds(payload, server_id, account_id, target_dept)
        await ssh_client.modify_user(
            creds, server_id,
            login=creds["login"],
            groups=payload.get("unix_groups") or [],
            has_sudo=bool(payload.get("has_sudo")),
            shell=payload.get("shell"),
        )
        await server_service_client.submit_provision_status(
            server_id, account_id, "update", True, target_dept,
        )
        return {
            "server_id": server_id,
            "account_id": account_id,
            "operation": "update",
            "present_on_server": True,
        }

    await run_task(
        task_id,
        audit_action="server_account.update_on_host",
        audit_target_type="server_account",
        impl=_impl,
        audit_safe_fields=AUDIT_SAFE_FIELDS_PROVISION,
    )


@broker.task("account.deprovision")
async def account_deprovision(task_id: str) -> None:
    """Удалить OS-пользователя с сервера (`userdel`) и подтвердить статус.

    Поток: собираем сессию (`_account_creds`) → `ssh_client.delete_user`
    (userdel, опц. --remove) → `submit_provision_status(present=False)`.
    Пароль не меняется — на управляемом сервере он не запрашивается, `login`
    берётся из payload.

    Параметры: `task_id`. Payload — `server_id`, `account_id`, `login`,
    опц. `remove_home`, `target_department_id`.

    Idempotent: отсутствующий пользователь — не ошибка.

    Возвращает: `{server_id, account_id, operation, present_on_server}`.
    Связано с: `server_account.deprovision` audit action.
    """
    async def _impl(payload: dict) -> dict:
        server_id = payload["server_id"]
        account_id = payload["account_id"]
        target_dept = payload.get("target_department_id")
        remove_home = bool(payload.get("remove_home"))

        creds = await _account_creds(payload, server_id, account_id, target_dept)
        await ssh_client.delete_user(
            creds, server_id, login=creds["login"], remove_home=remove_home,
        )
        await server_service_client.submit_provision_status(
            server_id, account_id, "deprovision", False, target_dept,
        )
        return {
            "server_id": server_id,
            "account_id": account_id,
            "operation": "deprovision",
            "present_on_server": False,
        }

    await run_task(
        task_id,
        audit_action="server_account.deprovision",
        audit_target_type="server_account",
        impl=_impl,
        audit_safe_fields=AUDIT_SAFE_FIELDS_PROVISION,
    )
