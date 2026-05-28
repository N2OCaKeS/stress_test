"""Ротация паролей.

`account.rotate_password`: генерим новый пароль, применяем на сервере по SSH
(`chpasswd`), и просим server_service пере-зашифровать и сохранить.

`ipmi.rotate_password`: тот же flow, но цель — IPMI-контроллер. Транспорт
выбирает `get_bmc_client` (`src/clients/__init__.py`): probe HEAD `/redfish/v1/`
→ Redfish PATCH `/Accounts/{user_id}` либо fallback `ipmitool user set password`
для legacy BMC без Redfish.

**Storage-first ordering для IPMI.** В отличие от account-ротации, где
ciphertext сохраняется ПОСЛЕ применения на сервере (если applying упадёт,
старый пароль ещё валиден), у IPMI порядок инвертирован:

  1. сгенерить пароль;
  2. отправить **plaintext** в server_service → он шифрует + сохраняет;
  3. ТОЛЬКО ПОСЛЕ успешного storage round-trip'а — отправить на BMC.

Иначе если worker умрёт между «BMC сменил пароль» и «storage сохранил»,
доступ к iDRAC потерян навсегда (нет ни у кого plaintext'а). Storage-first
не идеален (BMC может остаться со старым паролем, но ciphertext указывает
на новый), но это recoverable: оператор видит mismatch в audit и
повторно дёргает rotate. Inverse situation (BMC ушёл, storage нет) —
**не recoverable**.
"""

from __future__ import annotations

import logging
import secrets
import string
from datetime import datetime, timezone

import redis.asyncio as aioredis

from src.clients.ipmitool import IpmitoolError
from src.clients.redfish import RedfishError
from src.core.config import get_settings
from src.main import broker
from src.services import server_service_client, ssh_client
from src.tasks._bmc_errors import dispatch_rotate_user_password, wrap_bmc_error
from src.tasks._bmc_helpers import aclose_bmc as _aclose_bmc
from src.tasks._bmc_helpers import get_bmc as _get_bmc
from src.tasks._runner import run_task

logger = logging.getLogger(__name__)

# Whitelist для audit details.result. Это security-критичный handler:
# result может прийти с plaintext-паролем при ошибке кодинга. Поэтому здесь
# whitelist максимально узкий — только non-secret тайминги/идентификаторы.
#
# Для `account.rotate_password` ключи result: `server_id`, `account_id`,
# `rotated_at` — все безопасны.
# Для `ipmi.rotate_password`: `server_id`, `controller_id`, `user_id`,
# `password_rotated_at`, `controller_rotated`. IP/endpoint_url BMC сюда НЕ
# кладём — он раскрывал бы топологию management-сети в долгоживущих
# audit-логах. Оператор по `controller_id` достанет endpoint из
# server_service, когда реально нужно.
AUDIT_SAFE_FIELDS_ACCOUNT_ROTATE: set[str] = {"server_id", "account_id", "rotated_at"}
AUDIT_SAFE_FIELDS_IPMI_ROTATE: set[str] = {
    "server_id",
    "controller_id",
    "user_id",
    "password_rotated_at",
    "controller_rotated",
}


# Политика пароля под типичные iDRAC / Linux PAM правила сложности:
#   * минимум 16 chars (берём 20),
#   * минимум по одному: lowercase, uppercase, digit, и пунктуация из
#     консервативного набора, на который BMC и PAM согласны.
_PASSWORD_LENGTH = 20
_PASSWORD_PUNCT = "!@#$%^&*"
_PASSWORD_ALPHABET = (
    string.ascii_lowercase + string.ascii_uppercase + string.digits + _PASSWORD_PUNCT
)


# TTL для in-flight IPMI rotate-пароля в Redis. Покрывает суммарное окно
# back-off'а exponential retry'я (`_compute_backoff_delay` capped 300s) с
# запасом на сетевые тормоза. По истечении TTL `mark_failed` уже отработал —
# storage хранит «висящий» пароль, оператор повторяет rotate, который
# сгенерит новый ключ и попадёт в обычный happy-path.
_IPMI_ROTATE_PASSWORD_TTL_SECONDS = 1800

# Префикс ключа задаём явный — отделяет от bootstrap-creds в Redis-namespace.
_IPMI_ROTATE_KEY_PREFIX = "dbos:ipmi_rotate_pw:"


async def _read_ipmi_rotate_password(task_id: str) -> str | None:
    """Достать ранее сгенерированный пароль ротации из Redis.

    Возвращает plaintext или None, если ключа нет (первая попытка либо TTL
    истёк). Фикс P1: при retry'е IPMI rotate `_impl` запускается заново —
    без stash'а каждая попытка генерила бы новый пароль и перезаписывала
    storage, рассинхронизируя storage с реальным BMC при transient-fail'ах.
    """
    settings = get_settings()
    client = aioredis.from_url(settings.redis_url)
    try:
        raw = await client.get(_IPMI_ROTATE_KEY_PREFIX + task_id)
    finally:
        await client.aclose()
    if raw is None:
        return None
    return raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else str(raw)


async def _store_ipmi_rotate_password(task_id: str, password: str) -> None:
    """Сохранить in-flight пароль ротации в Redis с TTL.

    Кладём ПЕРЕД `submit_rotated_ipmi_password`: даже если transient
    network-fail отвалит после POST'а storage, следующий retry достанет
    ТОТ ЖЕ пароль из Redis и подтвердит state. Без stash'а worker
    сгенерил бы новый и перезаписал storage очередным ciphertext'ом,
    оставляя BMC потенциально на старом пароле.
    """
    settings = get_settings()
    client = aioredis.from_url(settings.redis_url)
    try:
        await client.set(
            _IPMI_ROTATE_KEY_PREFIX + task_id,
            password,
            ex=_IPMI_ROTATE_PASSWORD_TTL_SECONDS,
        )
    finally:
        await client.aclose()


async def _delete_ipmi_rotate_password(task_id: str) -> None:
    """Дропнуть in-flight ключ после успешного завершения ротации.

    TTL подстрахует, явный DELETE минимизирует окно жизни plaintext'а в
    Redis. Ошибки глушим — это посмертный cleanup, неуспех не должен
    провалить и без того happy-path задачу.
    """
    settings = get_settings()
    client = aioredis.from_url(settings.redis_url)
    try:
        try:
            await client.delete(_IPMI_ROTATE_KEY_PREFIX + task_id)
        except Exception:  # noqa: BLE001
            logger.debug("failed to delete in-flight ipmi rotate password", exc_info=True)
    finally:
        await client.aclose()


def _generate_password() -> str:
    """Сильный случайный пароль под типичную iDRAC/PAM-политику сложности.

    Гарантии: длина == ``_PASSWORD_LENGTH`` (20); в строке есть минимум по
    одному lowercase, uppercase, digit и символу из ``!@#$%^&*``.

    Все выборы через ``secrets.choice`` — пароль годится для хранения
    credentials (CSPRNG, не ``random``).
    """
    rng = secrets.SystemRandom()
    required = [
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.digits),
        secrets.choice(_PASSWORD_PUNCT),
    ]
    remaining_length = _PASSWORD_LENGTH - len(required)
    body = [secrets.choice(_PASSWORD_ALPHABET) for _ in range(remaining_length)]
    pwd_chars = required + body
    rng.shuffle(pwd_chars)
    return "".join(pwd_chars)


@broker.task("account.rotate_password")
async def account_rotate_password(task_id: str) -> None:
    """Ротировать пароль аккаунта на сервере (Linux user).

    Что делает: определяет логин (из payload на управляемом сервере либо через
    fetch на self-сессии) → генерим новый пароль → `ssh_client.set_account_password`
    (`chpasswd`) → отдаём новый пароль обратно в server_service через
    `submit_rotated_password`, тот шифрует и сохраняет.

    На управляемом сервере смену пароля делаем под управляющим пользователем по
    ключу с sudo; пароль аккаунта для входа не нужен, поэтому если `login` есть в
    payload — fetch не выполняется (это позволяет ротировать и discovered-аккаунт
    без хранимого пароля).

    Параметры: `task_id`. Payload — `server_id`, `account_id`, опционально
    `login`, `target_department_id`, `is_managed`, `management_user`.

    Возвращает: `{server_id, account_id, rotated_at}`. В audit уходит
    только эта тройка (см. AUDIT_SAFE_FIELDS_ACCOUNT_ROTATE) — plaintext
    пароля в audit НЕ попадает.

    Возможные ошибки: `CredentialFetchError` (server_service недоступен
    либо отказал submit), ошибки SSH-клиента.

    Связано с: `server_account.password_rotate` audit action.
    """
    async def _impl(payload: dict) -> dict:
        server_id = payload["server_id"]
        account_id = payload["account_id"]
        # Forwarded из server_service dispatch (X-Target-Department-Id
        # cross-check). См. модуль docstring `server_service.internal_service`.
        target_dept = payload.get("target_department_id")

        # На управляемом сервере вход по ключу под управляющим пользователем,
        # пароль аккаунта для аутентификации не нужен. `login` берём из payload,
        # если server_service его положил, иначе тянем через fetch. У discovered-
        # аккаунта пароля нет (fetch вернул бы 404), но login из payload снимает
        # необходимость в нём. На не управляемом сервере self-сессия требует
        # пароль — fetch обязателен.
        login = payload.get("login")
        if payload.get("is_managed") and login:
            creds: dict = {"login": login}
        else:
            creds = await server_service_client.fetch_account_password(
                server_id, account_id, target_dept,
            )
        ssh_client.apply_session_hints(creds, payload)
        new_password = _generate_password()
        await ssh_client.set_account_password(creds, server_id, creds["login"], new_password)
        confirmation = await server_service_client.submit_rotated_password(
            server_id, account_id, new_password, target_dept,
        )
        return {"server_id": server_id, "account_id": account_id, "rotated_at": confirmation.get("rotated_at")}

    await run_task(
        task_id,
        audit_action="server_account.password_rotate",
        audit_target_type="server_account",
        impl=_impl,
        audit_safe_fields=AUDIT_SAFE_FIELDS_ACCOUNT_ROTATE,
    )


@broker.task("ipmi.rotate_password")
async def ipmi_rotate_password(task_id: str) -> None:
    """Ротировать пароль IPMI-контроллера через Redfish или ipmitool.

    Flow (storage-first ordering, см. module docstring):

      1. `fetch_ipmi_credentials` — для login'а на BMC старым паролем.
      2. `secrets.token_urlsafe(24)` — новый пароль (CSPRNG, ASCII-safe).
      3. `submit_rotated_ipmi_password` → server_service шифрует + хранит.
         Только теперь у нас «commit point»: ciphertext в storage.
      4. `get_bmc_client` → выбор Redfish vs ipmitool → ротация на BMC.
      5. Если шаг 4 упал — handler raise'ит исключение, `_runner` запишет
         failure-audit. У storage уже новый ciphertext, у BMC — старый.
         Recoverable: оператор повторно дёргает rotate.

    Параметры: `task_id`. Payload — `server_id`, опционально
    `target_department_id`, опционально `user_id` (override default из
    `IPMI_USER_ID` env).

    Возвращает: `{server_id, controller_id, user_id, password_rotated_at,
    controller_rotated: True}`. Plaintext-пароль и IP/endpoint BMC НЕ
    возвращаются (топология management-сети наружу не уходит).

    Возможные ошибки: `CredentialFetchError(IPMI_CREDENTIALS_UNAVAILABLE
    | IPMI_ROTATE_REJECTED | SERVER_SERVICE_UNREACHABLE)`,
    `AppException(BMC_*)`.

    Связано с: `ipmi_controller.password_rotate` audit action,
    `server_service` endpoint `POST .../ipmi/credentials_rotated`.
    """
    async def _impl(payload: dict) -> dict:
        server_id = payload["server_id"]
        target_dept = payload.get("target_department_id")
        settings = get_settings()
        user_id = int(payload.get("user_id") or settings.ipmi_user_id)

        creds = await server_service_client.fetch_ipmi_credentials(server_id, target_dept)
        controller_id = creds["controller_id"]

        # Один и тот же пароль на все попытки одной dispatch'и. При retry'е
        # `_impl` запускается заново; без stash'а мы бы каждый раз генерили
        # новый ключ → перезаписывали storage чужим plaintext'ом → при
        # финальном `mark_failed` storage хранит пароль, который НИКОГДА не
        # доехал до BMC. С Redis-stash'ем все retry'и видят тот же пароль:
        # storage синхронен сам с собой, BMC либо принял его, либо нет —
        # повторный rotate генерит новый ключ и заходит в обычный happy-
        # path.
        stashed_password = await _read_ipmi_rotate_password(task_id)
        if stashed_password is None:
            # CSPRNG-пароль: 24 байта ≈ 32-символьный URL-safe string. Лимит
            # iDRAC9 — 40 символов, влезает с запасом. token_urlsafe не
            # гарантирует «все 4 класса», но iDRAC принимает любой ASCII;
            # PAM-чек тут не применим, BMC не часть OS.
            new_password = secrets.token_urlsafe(24)
            # Stash ДО `submit_rotated_ipmi_password`: даже если POST'нём
            # plaintext, а потом упадём transient'ом до того, как retry
            # дойдёт сюда — Redis уже знает «текущий in-flight пароль».
            await _store_ipmi_rotate_password(task_id, new_password)
        else:
            new_password = stashed_password
        rotated_at = datetime.now(timezone.utc).isoformat()

        # ── STORAGE-FIRST: commit plaintext в server_service до BMC ───
        # server_service сам шифрует и сохраняет ciphertext; worker
        # не держит SERVER_ENCRYPTION_KEY. На retry'е этот POST идемпотентен
        # на уровне ciphertext'а — server_service просто перешифровывает
        # тот же plaintext.
        confirmation = await server_service_client.submit_rotated_ipmi_password(
            controller_id, new_password, rotated_at, target_dept,
        )

        # ── BMC apply: Redfish PATCH либо ipmitool user set password ───
        client = await _get_bmc(creds)
        try:
            try:
                await dispatch_rotate_user_password(client, user_id, new_password)
            except (RedfishError, IpmitoolError) as exc:
                raise wrap_bmc_error("ipmi_rotate_password", exc) from exc
        finally:
            await _aclose_bmc(client)

        # Успех — больше не нужен stash. TTL подстрахует, но явный DELETE
        # сокращает окно жизни plaintext'а в Redis.
        await _delete_ipmi_rotate_password(task_id)

        return {
            "server_id": server_id,
            "controller_id": controller_id,
            "user_id": user_id,
            "password_rotated_at": confirmation.get("rotated_at"),
            "controller_rotated": True,
        }

    await run_task(
        task_id,
        audit_action="ipmi_controller.password_rotate",
        audit_target_type="ipmi_controller",
        impl=_impl,
        audit_safe_fields=AUDIT_SAFE_FIELDS_IPMI_ROTATE,
    )
