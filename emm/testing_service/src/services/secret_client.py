"""Исходящий вызов в secret_service — `POST /credentials/{id}/reveal` (§3.5, §6 плана миграции).

Аутентификация — свой же bot-токен testing_service (`SECRET_SERVICE_API_KEY`,
тот же принцип, что `server_client.SERVER_SERVICE_API_KEY`): secret_service
гейтит reveal обычной identity-based авторизацией (роль/ACL держателя
bearer'а), не shared s2s-секретом — значит нужен bearer, валидный для
auth_service introspect, а не internal-ключ.

Механизм, которым secret_service разрешает боту `testing_service` (живёт в
системном отделе) видеть чужую, department-owned credential
(`department_integration_settings.credential_id` указывает на креду ОТДЕЛА,
не системного отдела), реализован: credential заводится с scope `"service"`
(`secret_service/src/services/access_service.py::_check_service`) — владеет
ей по-прежнему конкретный отдел (обычный CRUD — только через его
dep_admin/admin), но read/reveal получает ЛЮБОЙ бот с флагом
`BotAccount.is_service_bot=True` независимо от отдела бота. Флаг заводится
только bootstrap-кодом auth_service (`bootstrap_service.py`) — у бота
`testing_service` он выставлен. Настройка доступа — дело department_admin'а
отдела-владельца credential'а (завести её со scope=service), не код-уровневый
grant. 403 от secret_service здесь означает конкретно «в этом отделе
`department_integration_settings` ещё не настроен на credential со
scope=service» — не «фичи не существует»; вызывающий код (`services/stp.py`,
`services/stp_status.py`) трактует такой исход как частичный, не фатальный
провал конкретного отдела/стенда (см. их docstring'и).
"""

from __future__ import annotations

import base64
import logging

import httpx

from src.core.config import get_settings
from src.core.constants import SERVICE_NAME
from src.core.exceptions import (
    AuthorizationError,
    NotFoundError,
    ServiceUnavailableError,
)
from src.core.http import bearer_header

logger = logging.getLogger("testing_service.secret_client")

_REVEAL_PATH = "/api/secret/v1/credentials/{cred_id}/reveal"


def build_client(timeout: float) -> httpx.AsyncClient:
    """Клиент под один вызов. Отдельная функция — точка подмены в тестах."""
    return httpx.AsyncClient(timeout=timeout)


def is_configured() -> bool:
    settings = get_settings()
    return bool(settings.secret_service_url and settings.secret_service_api_key)


async def reveal_credential(cred_id: str) -> tuple[str, str]:
    """Раскрыть credential — возвращает `(login, secret)`.

    Поднимает понятные доменные исключения вместо голого 500:

    * `ServiceUnavailableError` (`SECRET_SERVICE_NOT_CONFIGURED`) — канал не
      настроен (нет URL/ключа) в этом окружении;
    * `NotFoundError` (`CREDENTIAL_NOT_FOUND`) — 404 у secret_service (креда
      не существует, либо намеренно замаскирована под 404 для caller'а без
      доступа);
    * `AuthorizationError` (`CREDENTIAL_ACCESS_DENIED`) — 403, доступ не
      настроен (см. module docstring — credential этого отдела ещё не
      заведена со scope=service, либо бот утратил `is_service_bot`);
    * `ServiceUnavailableError` (`SECRET_SERVICE_ERROR`/`*_TIMEOUT`/
      `*_UNREACHABLE`) — сетевой сбой или неожиданный код ответа.

    Секрет в ответе secret_service приходит base64-кодированным
    (`reveal_credential` API-контракт secret_service, см.
    `secret_service/src/api/v1/endpoints/credentials.py::reveal_credential`)
    — декодируется здесь, вызывающий код получает уже сырое значение.
    """
    settings = get_settings()
    base = (settings.secret_service_url or "").rstrip("/")
    api_key = settings.secret_service_api_key
    if not base or not api_key:
        raise ServiceUnavailableError(
            error_code="SECRET_SERVICE_NOT_CONFIGURED",
            message="SECRET_SERVICE_URL/SECRET_SERVICE_API_KEY is not configured",
        )

    headers = {**bearer_header(api_key), "X-Service-Identity": SERVICE_NAME}
    path = _REVEAL_PATH.format(cred_id=cred_id)

    async with build_client(settings.secret_request_timeout_seconds) as client:
        try:
            response = await client.post(f"{base}{path}", headers=headers)
        except httpx.TimeoutException as exc:
            raise ServiceUnavailableError(
                error_code="SECRET_SERVICE_TIMEOUT",
                message="secret_service did not respond in time",
            ) from exc
        except httpx.HTTPError as exc:
            raise ServiceUnavailableError(
                error_code="SECRET_SERVICE_UNREACHABLE",
                message=f"Unable to reach secret_service: {type(exc).__name__}",
            ) from exc

    if response.status_code == 404:
        raise NotFoundError(
            error_code="CREDENTIAL_NOT_FOUND",
            message="Credential not found or not visible to the caller",
            details={"credential_id": cred_id},
        )
    if response.status_code == 403:
        raise AuthorizationError(
            error_code="CREDENTIAL_ACCESS_DENIED",
            message="secret_service denied access to this credential",
            details={"credential_id": cred_id},
        )
    if response.status_code >= 300:
        logger.warning(
            "secret_service ответил %s на reveal %s", response.status_code, cred_id,
        )
        raise ServiceUnavailableError(
            error_code="SECRET_SERVICE_ERROR",
            message=f"secret_service returned {response.status_code}",
        )

    try:
        body = response.json()
    except ValueError as exc:
        raise ServiceUnavailableError(
            error_code="SECRET_SERVICE_ERROR",
            message="secret_service returned a non-JSON body",
        ) from exc

    login = body.get("login") or ""
    secret_b64 = body.get("secret_b64") or ""
    try:
        secret = base64.b64decode(secret_b64).decode("utf-8") if secret_b64 else ""
    except (ValueError, UnicodeDecodeError) as exc:
        raise ServiceUnavailableError(
            error_code="SECRET_SERVICE_ERROR",
            message="secret_service returned a malformed secret payload",
        ) from exc
    return login, secret
