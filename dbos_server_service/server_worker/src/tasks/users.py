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
import re

import redis.asyncio as aioredis  # noqa: F401 — re-exported for test monkeypatch backward compat

from src.clients.ssh import SshError
from src.core.config import get_settings  # noqa: F401 — re-exported for downstream / future tests
from src.core.constants import SCRUBBED_SENTINEL, STASH_TTL_SECONDS
from src.core.exceptions import CredentialFetchError
from src.core.identifiers import validate_task_id
from src.db.session import AsyncSessionLocal
from src.main import broker
from src.repositories import task as task_repo
from src.services import redis_pool, server_service_client, ssh_client
from src.services.redis_stash_crypto import (
    aad_for_redis_stash,
    decrypt_stash,
    stash_id_from_key,
)
from src.tasks._account_helpers import resolve_ssh_creds
from src.tasks._destructive_gate import ensure_no_other_running_on_server
from src.tasks._runner import run_task

logger = logging.getLogger(__name__)

# Префикс для Redis-stash'а inline-creds provision'а. Симметрично
# `_ACCOUNT_ROTATE_KEY_PREFIX` в `tasks/passwords.py`: tasks/payload row в БД
# чистим в `finally` (defense-in-depth от утечки в `tasks.payload`), но между
# попытками те же `password_plaintext` / `ssh_private_key_plaintext` нужны —
# хранилище server_service выдаёт inline-креды один раз через
# dispatch-канал, повторно их запросить нельзя.
_PROVISION_INLINE_KEY_PREFIX = "dbos:provision_inline:"

# Префикс stash-ключа dispatch-creds, который пишет server_service ДО
# постановки задачи в брокер (см. `worker_client.dispatch_creds_key`).
# Воркер читает по этому ключу password + ssh_private_key плейнтексты на
# первой попытке, потом DEL'ит. Жёсткий формат — чтобы guard не пустил
# `creds_stash_key=":/admin"` в чужой keyspace.
_DISPATCH_CREDS_KEY_RE = re.compile(r"^dbos:dispatch_creds:[A-Za-z0-9_\-]{1,128}$")


async def _read_provision_inline(task_id: str) -> tuple[str | None, str | None]:
    """Прочитать stash'енные inline-креды provision'а.

    Возвращает `(password_plaintext, ssh_private_key_plaintext)`. None
    для каждого поля означает «не было в payload первой попытки» либо
    «TTL истёк». Поднимать новые retry'и при истечении TTL — задача
    оператора (server_service сгенерирует новые креды).

    Stash зашифрован тем же master-key, что и dispatch-stash'и; AAD
    binding'уется к task_id (он же stash-id в этом keyspace'е). Corrupt-
    token / swap-attack → `AppException(STASH_DECRYPT_*)` пробрасывается,
    task FAILED с явным error_code.
    """
    validate_task_id(task_id)
    client = redis_pool.get_redis()
    raw = await client.get(_PROVISION_INLINE_KEY_PREFIX + task_id)
    if raw is None:
        return None, None
    text = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else str(raw)
    plaintext = decrypt_stash(text, aad=aad_for_redis_stash(task_id))
    try:
        data = json.loads(plaintext)
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

    Value — plaintext JSON. Envelope-шифрование (мастер-ключ + AES-GCM)
    не применяется: worker не держит `SERVER_ENCRYPTION_KEY`. Mitigation'ы
    — TTL, redis AUTH в prod, явный DELETE после submit, неугадываемый
    суффикс ключа. Подробнее — `AUDIT_EVENTS.md` секция Threat model.
    """
    if password_plaintext is None and ssh_private_key_plaintext is None:
        return
    validate_task_id(task_id)
    client = redis_pool.get_redis()
    # Lazy import нужен только для encrypt-стороны: импорт `encrypt_stash` на
    # module-top подтянул бы settings.redis_stash_encryption_key даже у
    # читателей stash'а, у которых ключ может быть пустым в dev-сценариях.
    from src.services.redis_stash_crypto import encrypt_stash
    payload_json = json.dumps({
        "password_plaintext": password_plaintext,
        "ssh_private_key_plaintext": ssh_private_key_plaintext,
    })
    token = encrypt_stash(payload_json, aad=aad_for_redis_stash(task_id))
    await client.set(
        _PROVISION_INLINE_KEY_PREFIX + task_id,
        token,
        ex=STASH_TTL_SECONDS,
    )


async def _delete_provision_inline(task_id: str) -> None:
    """Дропнуть stash после успешного submit'а. TTL подстрахует."""
    validate_task_id(task_id)
    client = redis_pool.get_redis()
    try:
        await client.delete(_PROVISION_INLINE_KEY_PREFIX + task_id)
    except Exception:  # noqa: BLE001
        logger.debug(
            "failed to delete provision inline stash", exc_info=True,
        )


def _validate_dispatch_creds_key(stash_key: str) -> None:
    """Жёсткий guard формата `creds_stash_key` из payload'а.

    Reject всё, что не подходит под `dbos:dispatch_creds:<id>`. Без этого
    атакующий, получивший контроль над dispatch-payload (compromised
    server_service / Redis broker без AUTH), мог бы подсунуть
    `creds_stash_key=":/admin/creds"` и заставить воркер читать чужой
    keyspace.
    """
    if not isinstance(stash_key, str) or not _DISPATCH_CREDS_KEY_RE.fullmatch(stash_key):
        raise ValueError("invalid creds_stash_key format")


async def _read_dispatch_creds(stash_key: str) -> tuple[str | None, str | None]:
    """Прочитать dispatch-creds, положенные server_service'ом перед dispatch'ем.

    Возвращает `(password_plaintext, ssh_private_key_plaintext)`. Если ключа
    нет в Redis (TTL истёк / уже прочитан и удалён) — оба поля None. Caller
    должен распознать это и либо fall-back'нуться на task-local
    `_PROVISION_INLINE_KEY_PREFIX`-stash (если первая попытка успела его
    создать), либо fail'ить с `DISPATCH_STASH_MISSING`.

    Stash в Redis лежит как envelope-token (`v<N>$<nonce>$<ct>`) под общим
    с server_service master-key. Decrypt с AAD от stash_key — если token
    битый / swap'нут / прислан с чужим AAD — `AppException(STASH_DECRYPT_*)`
    пробрасывается, task FAILED с понятным error_code (без silent-fallback
    на None — иначе fail-fast в `_impl` принял бы compromised stash за
    «missing» и пропустил бы swap-attack как штатное истечение TTL).
    """
    _validate_dispatch_creds_key(stash_key)
    client = redis_pool.get_redis()
    raw = await client.get(stash_key)
    if raw is None:
        return None, None
    text = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else str(raw)
    plaintext = decrypt_stash(
        text, aad=aad_for_redis_stash(stash_id_from_key(stash_key)),
    )
    try:
        data = json.loads(plaintext)
    except (ValueError, TypeError):
        return None, None
    if not isinstance(data, dict):
        return None, None
    return data.get("password_plaintext"), data.get("ssh_private_key_plaintext")


async def _delete_dispatch_creds(stash_key: str) -> None:
    """Снять dispatch-creds из Redis (best-effort).

    Зовём сразу после первого успешного чтения: пока stash жив, любой с
    read к Redis видит plaintext password. TTL подстрахует, явный DEL
    сокращает окно жизни plaintext'а.
    """
    try:
        _validate_dispatch_creds_key(stash_key)
    except ValueError:
        # Malformed key — ничего не удаляем (guard защищает от прыжка в
        # чужой keyspace; на malformed просто молча выходим, основной
        # поток уже отработал). На нормальном пути сюда не попадаем —
        # ключ уже прошёл валидацию в `_read_dispatch_creds`. Если попал,
        # это race / баг, debug-лог нужен на forensic.
        logger.debug(
            "_delete_dispatch_creds: malformed stash_key, skip DEL: %r",
            stash_key,
        )
        return
    client = redis_pool.get_redis()
    try:
        await client.delete(stash_key)
    except Exception:  # noqa: BLE001
        logger.debug(
            "failed to delete dispatch creds stash", exc_info=True,
        )


def _unscrub(value):
    """Вернуть None, если значение — sentinel `"<scrubbed>"`.

    Защита от того, что на retry'е `_impl` прочтёт замаскированное значение
    из persisted payload и использует его как валидный секрет (chpasswd
    принял бы literal `"<scrubbed>"` и сломал бы вход на хост).
    """
    if value == SCRUBBED_SENTINEL:
        return None
    return value


# POSIX-узкий набор: буквы/цифры/`._-`. SOURCE OF TRUTH этого pattern'а —
# `src/clients/ssh.py:_LOGIN_RE` / `_GROUP_RE`; здесь третья (внешняя) линия
# обороны на входе task'и, до `_account_creds` (fetch password) и любых
# SSH-вызовов. Если payload принёс мусор в `login`, не хотим триггерить
# лишний fetch/audit на server_service'е и только потом получать
# SSH_INVALID_LOGIN — отбиваем сразу со стабильным error_code.
# Pattern должен оставаться байт-в-байт идентичным `clients/ssh.py:_LOGIN_RE`
# и `server_service/src/schemas/server_account.py` ServerAccountCreate.login:
# любая правка одной из копий обязана быть отражена в остальных двух.
_TASK_LOGIN_RE = re.compile(r"^[A-Za-z0-9._\-]+$")


def _validate_payload_login(payload: dict) -> None:
    """Жёсткая валидация `login` из payload до любых side-effect'ов.

    `account.update_on_host` и `account.deprovision` обязаны иметь `login`
    в payload (managed-сценарий, ключевой логин). Невалидный/пустой login
    — `SshError(SSH_INVALID_LOGIN)`, task FAILED.
    """
    login = payload.get("login")
    # `fullmatch`, не `match`: `re.match` упирается в prefix и пропустил бы
    # `valid\nrm -rf /` — отбиваем такую строку как невалидную полностью.
    if not isinstance(login, str) or not login or not _TASK_LOGIN_RE.fullmatch(login):
        raise SshError(
            error_code="SSH_INVALID_LOGIN",
            host="",
            message="login is missing or contains disallowed characters",
        )


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

        # Управляемый — вход по ключу под management_user, пароль аккаунта
        # не нужен; self — пароль из server_service. Общая логика в
        # `_account_helpers.resolve_ssh_creds`.
        creds = await resolve_ssh_creds(
            payload,
            server_id,
            account_id=account_id,
            target_dept=target_dept,
            is_managed=is_managed,
        )
        ssh_client.apply_session_hints(creds, payload)

        facts = await ssh_client.collect_os_users(creds, server_id)
        users_payload = ssh_client.os_users_facts_to_payload(facts)
        user_count = len(users_payload.get("users", []))

        submit_status: str
        # reconcile-сводка от server_service: counts + структурированный diff
        # привязанных аккаунтов (`diffs`). Кладём в task.result, чтобы UI
        # достал её через GET /tasks/{id} и показал оператору для ручного
        # ревью drift'а. При submit-фейле остаётся None.
        reconcile: dict | None = None
        try:
            reconcile = await server_service_client.submit_users_inventory(
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
        result = {
            "server_id": server_id,
            "users": users_payload.get("users", []),
            "user_count": user_count,
            "submit_status": submit_status,
        }
        if isinstance(reconcile, dict):
            result["diffs"] = reconcile.get("diffs", [])
            # Незнакомые юзеры (на боксе есть, в БД не привязаны, не в
            # ignore-list'е). server_service их больше НЕ заводит автоматически —
            # отдаёт сюда, оператор решает по карточке сервера (импорт / игнор).
            result["unknown_users"] = reconcile.get("unknown_users", [])
            result["reconcile_summary"] = {
                "created": reconcile.get("created"),
                "present": reconcile.get("present"),
                "drifted": reconcile.get("drifted"),
            }
        return result

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
        # Симметрично с update_on_host / deprovision: если login пришёл в
        # payload — fail-fast валидация ДО fetch'а пароля и любых
        # side-effect'ов. Managed-ветка обязана иметь login в payload (без него
        # `_account_creds` сразу падает SSH_INVALID_ARG); self-ветка login
        # доберёт через fetch_account_password, тогда `_LOGIN_RE` отработает
        # внутри ssh_client. Здесь — третья линия обороны, симметричная
        # `update_on_host` / `deprovision`.
        if payload.get("login") is not None:
            _validate_payload_login(payload)
        server_id = payload["server_id"]
        account_id = payload["account_id"]
        target_dept = payload.get("target_department_id")

        # Discovered-сценарий + re-provision: server_service генерирует
        # password + ssh-keypair и кладёт их в Redis ДО dispatch'а под
        # `creds_stash_key` (`dbos:dispatch_creds:<id>`). В payload едет
        # только ссылка — plaintext в `dev_server_worker.tasks.payload`
        # JSONB больше не оседает. ssh_public_key и force_replace едут
        # прямо в payload (не секреты).
        #
        # Lookup-порядок на каждой попытке:
        #   1. task-local `_PROVISION_INLINE_KEY_PREFIX:<task_id>` — он
        #      переживает retry (даже если оператор успел дёрнуть DEL на
        #      dispatch-stash, retry'и ходят сюда);
        #   2. dispatch-stash по `creds_stash_key` из payload — first
        #      attempt: тут лежат свежесгенерированные креды от
        #      server_service. Прочли — копируем в task-local-stash для
        #      будущих retry'ев и сразу DEL'им dispatch-stash, чтобы
        #      plaintext не висел до TTL.
        # Legacy payload-fallback (`password_plaintext`/`ssh_private_key_plaintext`
        # прямо в task-payload) больше не поддерживается. Если ни один
        # источник не дал secrets — fail-fast: на боксе мы ничего полезного
        # сделать не сможем, retry без secrets дал бы silent corruption
        # (useradd без password / без public_key).
        stashed_password, stashed_private_key = await _read_provision_inline(task_id)
        inline_password = stashed_password
        inline_private_key = stashed_private_key
        dispatch_stash_key = payload.get("creds_stash_key")
        if inline_password is None and inline_private_key is None and dispatch_stash_key:
            try:
                _validate_dispatch_creds_key(dispatch_stash_key)
            except ValueError as exc:
                raise CredentialFetchError(
                    error_code="DISPATCH_STASH_INVALID",
                    message=(
                        f"creds_stash_key in payload has unexpected format: "
                        f"{type(dispatch_stash_key).__name__}"
                    ),
                ) from exc
            dispatch_password, dispatch_private_key = await _read_dispatch_creds(
                dispatch_stash_key,
            )
            if dispatch_password is not None or dispatch_private_key is not None:
                inline_password = dispatch_password
                inline_private_key = dispatch_private_key
                # Сохраняем в task-local stash, чтобы retry'и нашли creds
                # даже если dispatch-stash уже DEL'нут (см. ниже).
                await _store_provision_inline(
                    task_id, inline_password, inline_private_key,
                )
                # Plaintext в Redis больше не нужен — task-local stash
                # принял эстафету. TTL подстрахует, но явный DEL сокращает
                # окно жизни plaintext'а в dispatch-namespace.
                await _delete_dispatch_creds(dispatch_stash_key)
        # Legacy payload-fallback (`password_plaintext` / `ssh_private_key_plaintext`
        # прямо в task-payload) удалён: server_service гарантирует, что секреты
        # идут только через Redis-stash под `creds_stash_key`. На смешанных
        # деплоях payload без stash'а упадёт ниже с `DISPATCH_STASH_MISSING`,
        # это правильный фейл-фаст: chpasswd с None ничего не даст.
        inline_public_key = payload.get("ssh_public_key")
        force_replace = bool(payload.get("force_replace"))

        # Если в payload вообще нет ссылки на dispatch-stash и в task-local
        # stash тоже пусто (TTL истёк / Redis-restart) — fail-fast. Иначе
        # chpasswd получил бы None / useradd пошёл бы без публичного ключа,
        # и боксок остался бы с неконсистентным состоянием. Legacy-payload
        # с plaintext-полями больше не поддерживается — server_service кладёт
        # секреты только через Redis-stash.
        if inline_password is None and inline_private_key is None:
            raise CredentialFetchError(
                error_code="DISPATCH_STASH_MISSING",
                message=(
                    f"dispatch creds stash not found in Redis "
                    f"(key={dispatch_stash_key}); TTL expired or already "
                    "consumed, or payload missing creds_stash_key"
                ),
            )

        # Defense-in-depth: даже если кто-то когда-то снова положит
        # `password_plaintext` / `ssh_private_key_plaintext` в payload
        # (мисс-конфиг / регресс), scrub их в `finally` — основной канал
        # теперь Redis-stash, plaintext в JSONB-payload оседать не должен
        # вовсе. `ssh_public_key` — не секрет, оставляем для форенсики;
        # `creds_stash_key` — ссылка, scrub'ать смысла нет (мы DEL'им stash
        # сразу после чтения). Best-effort: если scrub упал, не валим основной поток.
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
            # Defense-in-depth scrub: server_service сейчас кладёт секреты
            # только через Redis-stash (`creds_stash_key`), но если когда-то
            # регресс выкатит plaintext-поля прямо в payload — sentinel
            # подменит их в БД сразу после первой попытки. Другие tasks
            # (update_on_host, deprovision) inline-cred'ов не получают и
            # этот scrub им не нужен.
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
    (usermod groups/sudo/shell) → опционально раскатываем сменившийся
    SSH-публичный ключ в `~/.ssh/authorized_keys` → `submit_provision_status(present=True)`.
    Пароль не меняется, поэтому на управляемом сервере он не запрашивается
    вовсе — `login` берётся из payload.

    `ssh_public_key` едет в payload, когда у аккаунта сменился (или впервые
    появился) ключ — server_service кладёт его в payload так же, как для
    `account.provision`. Поле опционально: пока server_service его не шлёт на
    update, шаг записи ключа пропускается, остальное поведение не меняется.
    Запись идемпотентна и помечает наш ключ managed-маркером — ротация
    заменяет именно его, ручные ключи оператора не трогаются. `force_replace`
    из payload перезаписывает файл целиком (re-provision после переустановки ОС).

    Параметры/payload — как у `account_provision`.

    Возвращает: `{server_id, account_id, operation, present_on_server}`.
    Связано с: `server_account.update_on_host` audit action.
    """
    async def _impl(payload: dict) -> dict:
        _validate_payload_login(payload)
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
        # Смена/первичная установка SSH-ключа на уже заведённом юзере.
        # provision кладёт ключ при useradd; здесь донесём ротацию ключа на
        # хост. Если server_service не положил ключ в payload — пропускаем.
        public_key = payload.get("ssh_public_key")
        if public_key:
            await ssh_client.apply_authorized_key(
                creds, server_id,
                login=creds["login"],
                public_key=public_key,
                force_replace=bool(payload.get("force_replace")),
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
        _validate_payload_login(payload)
        server_id = payload["server_id"]
        account_id = payload["account_id"]
        target_dept = payload.get("target_department_id")
        remove_home = bool(payload.get("remove_home"))

        # Деструктив-гейт: userdel меняет состав входа на боксе и может
        # пересечься с чужой задачей на том же сервере. Откладываем, пока на
        # сервере есть другая running-задача.
        await ensure_no_other_running_on_server(task_id, server_id)

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
