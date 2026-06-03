"""Ротация паролей.

`account.rotate_password`: генерим новый пароль, применяем на сервере по SSH
(`chpasswd`), и просим server_service пере-зашифровать и сохранить.

`ipmi.rotate_password`: тот же flow, но цель — IPMI-контроллер. Транспорт
выбирает `get_bmc_client` (`src/clients/__init__.py`): probe HEAD `/redfish/v1/`
→ Redfish PATCH `/Accounts/{user_id}` либо fallback `ipmitool user set password`
для legacy BMC без Redfish.

**Verify-then-submit для IPMI.** В отличие от account-ротации, где
ciphertext сохраняется ПОСЛЕ применения на сервере, у IPMI поток такой:

  1. сгенерить пароль и положить в Redis-stash под task_id (TTL);
  2. отправить на BMC (Redfish PATCH или ipmitool user set password);
  3. сделать read-only вызов BMC с НОВЫМ паролем (`get_power_state`) —
     это доказывает, что BMC реально принял пароль (не отверг тихо по
     policy сложности и не вернулся к старому);
  4. ТОЛЬКО при успешном verify — отправить plaintext в server_service
     вместе с `verified_at`. Сервер шифрует и сохраняет.

Без verify storage мог бы хранить пароль, который BMC отверг — следующая
ротация попыталась бы зайти ciphertext'ом, который никогда не работал, и
out-of-band доступ к iDRAC пропал бы без шумного сигнала. Если verify
упал — submit НЕ зовём, задача FAILED с reason `verify_after_rotate_failed`;
stash в Redis с in-flight паролем живёт до TTL, оператор может разобраться
вручную или дождаться следующего retry.

**Account-stash (симметрично IPMI).** Account-ротация тоже держит in-flight
пароль в Redis под `dbos:account_rotate_pw:<task_id>` с тем же TTL. При
retry `_impl` запускается заново; без stash'а каждая попытка генерила бы
новый `_generate_password()` и `chpasswd` перезаписывал бы аккаунт другим
секретом, а submit ушёл бы с третьим — storage хранил бы пароль, отличный
от того, что реально стоит на хосте. Self-сессии (`is_managed=False`),
завязанные на пароль из storage, ловили бы вечный auth-fail после первого
transient-fail'а submit'а. Stash хранит plaintext-пароль; для IPMI рядом с
паролем кладётся `rotated_at`, чтобы при retry submit'а timestamp не
дрейфил относительно фактического момента смены пароля на BMC. Удаляем
stash явно после успешного submit'а; TTL подстрахует на случай аварии
worker'а между apply и delete.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import string
from datetime import datetime, timezone

import redis.asyncio as aioredis

from src.clients.ipmitool import IpmitoolError
from src.clients.redfish import RedfishError
from src.core.config import get_settings
from src.core.constants import STASH_TTL_SECONDS
from src.core.identifiers import validate_task_id
from src.main import broker
from src.services import server_service_client, ssh_client
from src.tasks._bmc_errors import (
    dispatch_get_power_state,
    dispatch_rotate_user_password,
    wrap_bmc_error,
)
from src.services import bmc_circuit_breaker as _breaker
from src.tasks._bmc_helpers import aclose_bmc as _aclose_bmc
from src.tasks._bmc_helpers import extract_bmc_host as _extract_bmc_host
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


# Префикс ключа задаём явный — отделяет от bootstrap-creds в Redis-namespace.
_IPMI_ROTATE_KEY_PREFIX = "dbos:ipmi_rotate_pw:"

# Ключ для in-flight account-rotate. Симметричен `_IPMI_ROTATE_KEY_PREFIX`.
# Зачем: при retry'е `account.rotate_password._impl` запускается заново;
# без stash'а каждая попытка генерила бы новый пароль через
# `_generate_password()`, `chpasswd` перезаписывал бы аккаунт другим
# секретом, и self-сессии (`is_managed=False`), завязанные на пароль из
# storage, ломались бы при первом transient-fail'е submit'а — storage
# хранит один пароль, на хосте стоит другой.
_ACCOUNT_ROTATE_KEY_PREFIX = "dbos:account_rotate_pw:"


def _ipmi_stash_value(password: str, rotated_at: str | None) -> str:
    """Сериализовать (password, rotated_at) в JSON для Redis-stash'а."""
    return json.dumps({"password": password, "rotated_at": rotated_at})


def _ipmi_stash_parse(raw: bytes | bytearray | str) -> tuple[str | None, str | None]:
    """Разобрать stash из Redis.

    Writer (`_store_ipmi_rotate_password`) всегда пишет JSON-dict, поэтому
    plain-string fallback больше не нужен. Если в Redis вдруг лежит чужой
    или старый формат (не валидный JSON либо не dict) — возвращаем
    `(None, None)`: caller увидит «как будто stash пустой», сгенерит
    новый пароль через `_generate_password` и пойдёт штатным путём
    (storage перезапишет ciphertext). Старый format сам выпадет по TTL.
    """
    if isinstance(raw, (bytes, bytearray)):
        text = raw.decode("utf-8")
    else:
        text = str(raw)
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None, None
    if not isinstance(data, dict):
        return None, None
    return data.get("password"), data.get("rotated_at")


def _account_stash_value(
    password: str, login: str | None, rotated_at: str | None,
) -> str:
    """Сериализовать (password, login, rotated_at) в JSON для account-stash'а.

    Симметрично `_ipmi_stash_value`. `login` нужен на retry'е, если мы
    впервые попали сюда после chpasswd (логин уже выбран — из payload или
    fetch'а) и хотим избежать второго fetch'а, который на discovered-
    аккаунте всё равно вернёт 404. `rotated_at` хранится для симметрии
    с IPMI: server_service возвращает свой timestamp на submit, но если
    submit повторится из-за transient'а, мы переиспользуем тот же.
    """
    return json.dumps(
        {"password": password, "login": login, "rotated_at": rotated_at}
    )


def _account_stash_parse(
    raw: bytes | bytearray | str,
) -> tuple[str | None, str | None, str | None]:
    """Разобрать account-stash из Redis.

    Возвращает `(password, login, rotated_at)`. Если в Redis лежит чужой
    или старый формат (не валидный JSON, не dict) — возвращаем тройку
    `None`'ов: caller увидит «как будто stash пустой», сгенерит новый
    пароль и пойдёт штатным путём. Старый формат сам выпадет по TTL.
    """
    if isinstance(raw, (bytes, bytearray)):
        text = raw.decode("utf-8")
    else:
        text = str(raw)
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None, None, None
    if not isinstance(data, dict):
        return None, None, None
    return data.get("password"), data.get("login"), data.get("rotated_at")


async def _read_ipmi_rotate_state(task_id: str) -> tuple[str | None, str | None]:
    """Достать (password, rotated_at) из stash'а.

    rotated_at сохраняется вместе с паролем, чтобы при retry'е submit'а
    storage получал тот же timestamp, что и при первой успешной попытке —
    без этого rotated_at дрейфил бы между BMC apply и записью в storage
    на каждой повторной submit-попытке (drift до десятков секунд).
    """
    validate_task_id(task_id)
    settings = get_settings()
    client = aioredis.from_url(settings.redis_url)
    try:
        raw = await client.get(_IPMI_ROTATE_KEY_PREFIX + task_id)
    finally:
        await client.aclose()
    if raw is None:
        return None, None
    return _ipmi_stash_parse(raw)


async def _store_ipmi_rotate_password(
    task_id: str, password: str, rotated_at: str | None = None,
) -> None:
    """Сохранить in-flight пароль ротации (и опционально rotated_at) с TTL.

    Кладём ПЕРЕД `submit_rotated_ipmi_password`: даже если transient
    network-fail отвалит после POST'а storage, следующий retry достанет
    ТОТ ЖЕ пароль и timestamp из Redis и подтвердит state. Без stash'а
    worker сгенерил бы новый и перезаписал storage очередным
    ciphertext'ом, оставляя BMC потенциально на старом пароле.

    `rotated_at` опционален для обратной совместимости с тестами,
    которые предзаполняют stash перед запуском handler'а.
    """
    validate_task_id(task_id)
    settings = get_settings()
    client = aioredis.from_url(settings.redis_url)
    try:
        await client.set(
            _IPMI_ROTATE_KEY_PREFIX + task_id,
            _ipmi_stash_value(password, rotated_at),
            ex=STASH_TTL_SECONDS,
        )
    finally:
        await client.aclose()


async def _delete_ipmi_rotate_password(task_id: str) -> None:
    """Дропнуть in-flight ключ после успешного завершения ротации.

    TTL подстрахует, явный DELETE минимизирует окно жизни plaintext'а в
    Redis. Ошибки глушим — это посмертный cleanup, неуспех не должен
    провалить и без того happy-path задачу.
    """
    validate_task_id(task_id)
    settings = get_settings()
    client = aioredis.from_url(settings.redis_url)
    try:
        try:
            await client.delete(_IPMI_ROTATE_KEY_PREFIX + task_id)
        except Exception:  # noqa: BLE001
            logger.debug("failed to delete in-flight ipmi rotate password", exc_info=True)
    finally:
        await client.aclose()


async def _read_account_rotate_state(
    task_id: str,
) -> tuple[str | None, str | None, str | None]:
    """Достать (password, login, rotated_at) account-stash'а из Redis.

    Симметрично `_read_ipmi_rotate_state`. `login` нужен для retry'я
    после первого chpasswd (избежать повторного fetch'а у server_service);
    `rotated_at` хранится опционально (server_service возвращает свой
    timestamp на submit, но переиспользуется на retry'ях submit'а).
    """
    validate_task_id(task_id)
    settings = get_settings()
    client = aioredis.from_url(settings.redis_url)
    try:
        raw = await client.get(_ACCOUNT_ROTATE_KEY_PREFIX + task_id)
    finally:
        await client.aclose()
    if raw is None:
        return None, None, None
    return _account_stash_parse(raw)


async def _read_account_rotate_password(task_id: str) -> str | None:
    """Достать только password из account-stash'а.

    Тонкая обёртка над `_read_account_rotate_state` — оставлена для
    call-site'ов и тестов, которым нужен только пароль.
    """
    password, _login, _rotated_at = await _read_account_rotate_state(task_id)
    return password


async def _store_account_rotate_password(
    task_id: str,
    password: str,
    login: str | None = None,
    rotated_at: str | None = None,
) -> None:
    """Сохранить in-flight account-пароль ротации в Redis с TTL.

    Кладём ПЕРЕД `chpasswd`: если retry повторит _impl, мы достанем тот
    же пароль и не сгенерим новый. Без этого каждый retry перезаписывал
    бы пароль на хосте новым случайным секретом, ломая self-сессии и
    рассинхронизируя storage с реальностью на хосте.

    Формат — JSON `{password, login, rotated_at}` симметрично IPMI-stash'у.
    `login` и `rotated_at` опциональны: на первом заходе известен только
    `password` (login резолвится из payload/fetch чуть позже), оба слота
    дополнятся при следующих обновлениях stash'а.
    """
    validate_task_id(task_id)
    settings = get_settings()
    client = aioredis.from_url(settings.redis_url)
    try:
        await client.set(
            _ACCOUNT_ROTATE_KEY_PREFIX + task_id,
            _account_stash_value(password, login, rotated_at),
            ex=STASH_TTL_SECONDS,
        )
    finally:
        await client.aclose()


async def _delete_account_rotate_password(task_id: str) -> None:
    """Дропнуть in-flight account-ключ после успешного submit'а."""
    validate_task_id(task_id)
    settings = get_settings()
    client = aioredis.from_url(settings.redis_url)
    try:
        try:
            await client.delete(_ACCOUNT_ROTATE_KEY_PREFIX + task_id)
        except Exception:  # noqa: BLE001
            logger.debug(
                "failed to delete in-flight account rotate password",
                exc_info=True,
            )
    finally:
        await client.aclose()


def _generate_password() -> str:
    """Сильный случайный пароль под типичную iDRAC/PAM-политику сложности.

    Гарантии: длина == ``_PASSWORD_LENGTH`` (20); в строке есть минимум по
    одному lowercase, uppercase, digit и символу из ``!@#$%^&*``.

    Все источники случайности — CSPRNG. ``secrets.choice`` берёт энтропию
    из ``os.urandom``; перемешивание делает ``secrets.SystemRandom().shuffle``,
    у которого тот же источник (``random.SystemRandom`` биндится на
    ``os.urandom``, не на mt19937). Отдельный ``random.shuffle`` тут
    нельзя — он сидится из времени и снизит стойкость.
    """
    required = [
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.digits),
        secrets.choice(_PASSWORD_PUNCT),
    ]
    remaining_length = _PASSWORD_LENGTH - len(required)
    body = [secrets.choice(_PASSWORD_ALPHABET) for _ in range(remaining_length)]
    pwd_chars = required + body
    secrets.SystemRandom().shuffle(pwd_chars)
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

        # Один и тот же пароль на все попытки одной dispatch'и (симметрично
        # ipmi.rotate_password). Без stash'а retry _impl запускался бы с
        # новым `_generate_password()`, chpasswd перезаписывал бы аккаунт
        # другим секретом, а submit ушёл бы с третьим — storage хранил бы
        # пароль, отличный от того, что реально стоит на хосте. Для
        # self-сессий (`is_managed=False`), которые логинятся паролем из
        # storage, это означало бы постоянный auth-fail после первого же
        # transient-fail'а submit'а.
        stashed_password, _stashed_login, _stashed_rotated_at = (
            await _read_account_rotate_state(task_id)
        )
        if stashed_password is None:
            new_password = _generate_password()
            # login на этом этапе уже известен (из payload либо fetch'а
            # выше) — кладём его в stash сразу, чтобы retry мог обойтись
            # без второго fetch'а к server_service.
            await _store_account_rotate_password(
                task_id, new_password, login=creds.get("login"),
            )
        else:
            new_password = stashed_password
        await ssh_client.set_account_password(creds, server_id, creds["login"], new_password)
        confirmation = await server_service_client.submit_rotated_password(
            server_id, account_id, new_password, target_dept,
        )

        # Успех — больше не нужен stash. TTL подстрахует, явный DELETE
        # сокращает окно жизни plaintext'а в Redis.
        await _delete_account_rotate_password(task_id)

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

    Flow (verify-then-submit):

      1. `fetch_ipmi_credentials` — login на BMC старым паролем.
      2. `secrets.token_urlsafe(24)` — новый пароль (CSPRNG, ASCII-safe).
         Stash в Redis под `_IPMI_ROTATE_KEY_PREFIX` с TTL — единый пароль
         на все попытки одной dispatch'и (см. комментарий ниже).
      3. `dispatch_rotate_user_password` → BMC apply (Redfish PATCH либо
         ipmitool user set password).
      4. `dispatch_get_power_state` под НОВЫМ паролем — read-only verify,
         доказательство что BMC действительно принял пароль (не отверг
         тихо по policy, не вернулся к старому). Verify пробуем до двух
         раз с 1s паузой: первая попытка может упасть на лёгком NTP-drift
         либо мгновенном transient-сбое сразу после apply. Не verify
         прошёл оба раза → не коммитим storage; задача FAILED.
      5. `submit_rotated_ipmi_password` с `verified_at` → server_service
         шифрует + хранит. server_service отказывает без `verified_at`.
      6. DELETE stash.

    Если verify (шаг 4) упал — submit НЕ зовём, поднимаем исключение
    `BMC_VERIFY_AFTER_ROTATE_FAILED`. Storage остаётся со старым ciphertext,
    BMC — с новым паролем (если apply прошёл) или со старым (если apply
    откатил). Stash в Redis с in-flight паролем доживёт до TTL, оператор
    видит mismatch в audit и решает: подождать ещё один retry или
    разбираться вручную.

    Параметры: `task_id`. Payload — `server_id`, опционально
    `target_department_id`, опционально `user_id` (override default из
    `IPMI_USER_ID` env).

    Возвращает: `{server_id, controller_id, user_id, password_rotated_at,
    controller_rotated: True}`. Plaintext-пароль и IP/endpoint BMC НЕ
    возвращаются (топология management-сети наружу не уходит).

    Возможные ошибки: `CredentialFetchError(IPMI_CREDENTIALS_UNAVAILABLE
    | IPMI_ROTATE_REJECTED | SERVER_SERVICE_UNREACHABLE)`,
    `AppException(BMC_*)`, в т.ч. `BMC_VERIFY_AFTER_ROTATE_FAILED`.

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
        # новый ключ. Тогда apply на BMC выставил бы один пароль, а на
        # следующем retry'е submit ушёл бы с другим — storage разъехался бы
        # с BMC. С Redis-stash'ем все retry'и видят тот же пароль и
        # сходятся к одному и тому же ciphertext'у.
        stashed_password, stashed_rotated_at = await _read_ipmi_rotate_state(task_id)
        if stashed_password is None:
            # 20-символьный CSPRNG-пароль с гарантией lower/upper/digit/punct
            # (см. `_generate_password`). Лимит iDRAC9 — 40 символов, влезает
            # с запасом. Все 4 класса symbol'ов нужны для совместимости с
            # iDRAC-policy'ями, где админ включил complexity check.
            new_password = _generate_password()
            await _store_ipmi_rotate_password(task_id, new_password)
        else:
            new_password = stashed_password

        # ── BMC apply: Redfish PATCH либо ipmitool user set password ───
        host = _extract_bmc_host(creds["endpoint_url"])
        await _breaker.check(host)
        client = await _get_bmc(creds)
        try:
            try:
                await dispatch_rotate_user_password(client, user_id, new_password)
            except (RedfishError, IpmitoolError, ValueError, RuntimeError) as exc:
                await _breaker.record_failure(host)
                raise wrap_bmc_error("ipmi_rotate_password", exc) from exc
            await _breaker.record_success(host)
        finally:
            await _aclose_bmc(client)

        # rotated_at фиксируем ПОСЛЕ успешного apply на BMC. Если apply
        # упал — исключение пробрасывается выше, в storage rotated_at не
        # уходит, и retry-цикл считает новый timestamp на следующей
        # попытке. Без этого порядка storage помечал бы ротацию моментом
        # старта, что расходится с реальным временем смены пароля на BMC.
        #
        # Если в stash'е уже лежит rotated_at от предыдущей успешной apply-
        # попытки (retry упёрся в submit, не в apply) — переиспользуем его,
        # иначе новое значение ушло бы в storage и timestamp разъехался бы
        # с фактическим моментом смены пароля на BMC.
        #
        # rotated_at берётся из worker-clock (`datetime.now(UTC)`), а не из
        # BMC. На стенде с NTP это безопасно (расхождение worker↔BMC обычно
        # <1s), но если worker уехал по часам на минуты — `verify_in_future`
        # на server_service поймает разъезд (±60s окно). В отчётах ротации
        # ориентироваться на server_service.account.rotated_at — это
        # источник истины с точки зрения платформы.
        if stashed_rotated_at:
            rotated_at = stashed_rotated_at
        else:
            rotated_at = datetime.now(timezone.utc).isoformat()
            await _store_ipmi_rotate_password(task_id, new_password, rotated_at)

        # ── BMC verify: read-only call с НОВЫМ паролем ──────────────────
        # Доказательство, что BMC действительно сохранил новый пароль —
        # пере-аутентифицируемся новым ключом и опрашиваем power state
        # (легчайший read-op в Redfish и ipmitool). Если этот шаг падает с
        # auth — BMC отверг пароль (например, policy сложности), и в
        # storage его класть НЕЛЬЗЯ, иначе следующая ротация попробует
        # зайти ciphertext'ом, который никогда не работал.
        verify_creds = dict(creds)
        verify_creds["password"] = new_password
        # Pre-check breaker'а здесь сознательно не зовём. Apply только что
        # отстрелял `record_success` — breaker для этого host'а закрыт. Если
        # между apply и verify соседняя replica успеет сбить breaker в open,
        # `_breaker.check` бросит `CircuitBreakerOpenError` мимо
        # `except (RedfishError, IpmitoolError)` — apply прошёл, BMC уже c
        # новым паролем, а submit в storage не уйдёт. Storage разъедется с
        # BMC из-за чужого circuit-state. Сам verify-вызов всё ещё гоняет
        # `record_failure/success` в except'е — реальные network/auth
        # сбои останутся видимы breaker'у.
        #
        # Verify пытаемся максимум дважды с задержкой 1s между попытками.
        # Зачем: на стенде с лёгким NTP-drift'ом (worker↔BMC расходятся на
        # секунды) BMC может первой попыткой отбить auth с новым паролем —
        # часть моделей не сразу применяет PATCH к internal-clock'у, плюс
        # есть транзиентные сетевые сбои сразу после apply. Один retry с
        # 1s паузой даёт BMC шанс «настояться» без раскачки цикла. Больше
        # одного retry'я не делаем: каждый дополнительный заход — это ещё
        # одна возможность storage разъехаться с BMC через timeout/race и
        # лишний таймаут на и без того долгом durable-retry-цикле.
        #
        # Trade-off: если NTP-drift между worker и BMC реально превышает
        # ±60s (broken time-source), оба захода упадут и `verify_in_future`
        # на server_service всё равно отобьёт callback — это правильная
        # реакция: storage не примет пароль с заведомо неверным timestamp'ом,
        # оператор увидит явный `BMC_VERIFY_AFTER_ROTATE_FAILED` + audit с
        # деталями skew'а и пойдёт чинить NTP.
        last_exc: Exception | None = None
        last_attempt: int = 0
        try:
            for attempt in range(2):
                verify_client = await _get_bmc(verify_creds)
                try:
                    try:
                        await dispatch_get_power_state(verify_client)
                        await _breaker.record_success(host)
                        last_exc = None
                        break
                    except (RedfishError, IpmitoolError, ValueError, RuntimeError) as exc:
                        await _breaker.record_failure(host)
                        last_exc = exc
                        last_attempt = attempt
                finally:
                    await _aclose_bmc(verify_client)
                if attempt == 0:
                    await asyncio.sleep(1.0)
        finally:
            # Defense-in-depth: новый пароль остаётся в `verify_creds["password"]`
            # после успешной верификации и держится во frame'е до конца task'а
            # (submit, audit, return). Затираем сразу, дальше он не нужен —
            # plaintext продолжает жить только в `new_password` локально и в
            # Redis-stash под TTL до явного DELETE ниже.
            try:
                verify_creds["password"] = ""
            except Exception:  # noqa: BLE001
                pass
            verify_creds = None  # noqa: F841
        if last_exc is not None:
            wrapped = wrap_bmc_error("ipmi_rotate_password", last_exc)
            # error_code намеренно перетираем: оператору важна фаза
            # (apply прошёл, упал verify), а transport-уровень
            # (BMC_AUTH_FAILED / BMC_UNREACHABLE / ...) поднимаем
            # выше через `__cause__` (`raise ... from exc`).
            # В details кладём фазу verify (первая попытка или retry) —
            # чтобы по audit'у было видно, упал ли verify сразу после apply
            # или уже на повторе после 1s паузы.
            wrapped.error_code = "BMC_VERIFY_AFTER_ROTATE_FAILED"
            wrapped.details["phase"] = (
                "verify_first_attempt" if last_attempt == 0 else "verify_retry"
            )
            raise wrapped from last_exc

        verified_at = datetime.now(timezone.utc).isoformat()

        # ── Storage commit: только после verify ─────────────────────────
        # server_service шифрует и сохраняет ciphertext; worker не держит
        # `SERVER_ENCRYPTION_KEY`. Без `verified_at` server_service
        # отвергает запрос.
        confirmation = await server_service_client.submit_rotated_ipmi_password(
            controller_id, new_password, rotated_at, target_dept,
            verified_at=verified_at,
        )

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
