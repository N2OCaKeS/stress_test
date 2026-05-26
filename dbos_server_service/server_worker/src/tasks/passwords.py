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

from src.clients.ipmitool import IpmitoolError
from src.clients.redfish import RedfishClient, RedfishError
from src.core.config import get_settings
from src.main import broker
from src.services import server_service_client, ssh_client
from src.tasks._bmc_errors import dispatch_rotate_user_password, wrap_bmc_error
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


def _build_redfish_client(creds: dict) -> RedfishClient:
    """Legacy-фабрика чистого `RedfishClient` для тестов.

    Используется только тестовым кодом (`monkeypatch.setattr(..., _build_redfish_client, ...)`).
    Production-путь — `_get_bmc` (probe + fallback на ipmitool).
    """
    settings = get_settings()
    return RedfishClient(
        host=creds["endpoint_url"],
        username=creds["username"],
        password=creds["password"],
        verify_tls=settings.redfish_verify_tls,
        timeout=settings.redfish_timeout_seconds,
    )


@broker.task("account.rotate_password")
async def account_rotate_password(task_id: str) -> None:
    """Ротировать пароль аккаунта на сервере (Linux user).

    Что делает: тянет текущий логин из server_service → генерим новый
    пароль → `ssh_client.set_account_password` (`chpasswd`) → отдаём
    новый пароль обратно в server_service через `submit_rotated_password`,
    тот шифрует и сохраняет.

    Параметры: `task_id`. Payload — `server_id`, `account_id`, опционально
    `target_department_id`.

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

        creds = await server_service_client.fetch_account_password(
            server_id, account_id, target_dept,
        )
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

        # CSPRNG-пароль: 24 байта ≈ 32-символьный URL-safe string. Лимит
        # iDRAC9 — 40 символов, влезает с запасом. token_urlsafe в отличие
        # от _generate_password не гарантирует «все 4 класса», но iDRAC
        # принимает любой ASCII; PAM-чек тут не применим, BMC не часть OS.
        new_password = secrets.token_urlsafe(24)
        rotated_at = datetime.now(timezone.utc).isoformat()

        # ── STORAGE-FIRST: commit plaintext в server_service до BMC ───
        # server_service сам шифрует и сохраняет ciphertext; worker
        # не держит SERVER_ENCRYPTION_KEY.
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
