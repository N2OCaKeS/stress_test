"""Outbound client для lifecycle-уведомлений в secret_service.

auth_service эмитит lifecycle-события (user удалён, отдел удалён, у отдела
отозван access к secret_service) → secret_service делает каскадный
block/revoke на personal-кред, DeptGrant, RoleACL.

Контракт:

* POST `/api/secret/v1/internal/lifecycle/{user-deleted,dept-deleted,
  dept-service-access-revoked}`.
* Bearer = `settings.secret_internal_api_key` (shared service-bearer, тот же
  что в `SERVICE_API_KEYS` на стороне secret_service).
* TLS verify контролируется `settings.secret_service_tls_verify` (для
  dev/test, где сертификат self-signed, выставляем `False` через env).
* Best-effort: любая ошибка → `logger.warning` + audit-emit
  `secret_lifecycle.notify_failed`, наружу не пробрасываем. secret_service
  собирает «осиротевшие» события через Phase 7 sweep'ом, поэтому потеря
  одного callback'а не ломает каскад.
* `secret_service_url` пустой → клиент молча skip'ает emit с WARNING
  (dev/test без secret_service'а, без сообщения операторам).

Пул httpx.AsyncClient инициализируется лениво из main.lifespan через
`init_pool(settings)`, дренируется через `aclose_pool()`. Если пул не
поднят (например, в unit-тесте без lifespan'а) — fallback на per-call
`httpx.AsyncClient`.
"""

from __future__ import annotations

import logging
import time

import httpx

from src.core.config import get_settings
from src.core.http import bearer_header
from src.services import audit_service
from src.services.redaction import redact as _redact

logger = logging.getLogger(__name__)


# Слот под pooled-клиент. None пока lifespan не вызвал `init_pool`.
# Тесты, импортирующие модуль до startup'а, идут по fallback-пути.
_client: httpx.AsyncClient | None = None

_LIFECYCLE_PATH = "/api/secret/v1/internal/lifecycle"
_TIMEOUT_SECONDS = 5.0
_MAX_CONNECTIONS = 10
_MAX_KEEPALIVE = 5

# Identity, который мы шлём в `X-Service-Identity` outbound'ом. secret_service'у
# нужен для (а) lookup'а нашего ключа в его per-caller map'е, (б) rate-limit-
# bucket'инга. Имя должно совпадать с тем, как secret_service нас знает в своих
# `SERVICE_API_KEYS`.
_OUTBOUND_SERVICE_IDENTITY = "auth_service"

# ── In-process circuit breaker ─────────────────────────────────────────────────
# secret_service может быть полностью down (deploy, миграция, network split) —
# каждый последующий emit ждёт TIMEOUT_SECONDS, login-burst упирается в
# httpx-пул. Если 3 fail подряд → open для COOLDOWN; дальше short-circuit'им
# до истечения cooldown'а и audit'им как `circuit_open`. После cooldown'а
# первая попытка снова идёт в сеть (half-open semantically); успех ресетит
# счётчик, провал перевзводит окно.
_BREAKER_FAIL_THRESHOLD = 3
_BREAKER_COOLDOWN_SECONDS = 60.0
_breaker_fail_count: int = 0
_breaker_open_until: float = 0.0


def _breaker_is_open() -> bool:
    """True пока не истёк cooldown после порога fail'ов."""
    return time.monotonic() < _breaker_open_until


def _breaker_record_failure() -> None:
    """Инкрементируем счётчик; на N-м fail подряд open'аем."""
    global _breaker_fail_count, _breaker_open_until
    _breaker_fail_count += 1
    if _breaker_fail_count >= _BREAKER_FAIL_THRESHOLD:
        _breaker_open_until = time.monotonic() + _BREAKER_COOLDOWN_SECONDS


def _breaker_record_success() -> None:
    """Любой успех сбрасывает счётчик и закрывает breaker."""
    global _breaker_fail_count, _breaker_open_until
    _breaker_fail_count = 0
    _breaker_open_until = 0.0


def _breaker_reset() -> None:
    """Тестовый ресет внутреннего состояния breaker'а."""
    global _breaker_fail_count, _breaker_open_until
    _breaker_fail_count = 0
    _breaker_open_until = 0.0


_PII_KEYS_IN_PAYLOAD = ("user_id", "dept_id", "actor_id", "actor_username")


def _mask_id(value: object) -> object:
    """Маскировка ID/username: оставляем тип + хвост (4 символа) для корреляции
    с logs/SIEM, тело прячем. None/пустое пропускаем как есть.
    """
    if value is None:
        return None
    s = str(value)
    if len(s) <= 4:
        return "<REDACTED>"
    return f"<REDACTED:{s[-4:]}>"


def _redact_audit_payload(payload: dict) -> dict:
    """Снимок payload'а с маскированными PII-полями для записи в audit details.

    `payload` мы кладём в `details["payload"]` внутри `notify_failed` — он
    оседает в SIEM. `user_id` / `actor_id` / `actor_username` снаружи плейс-
    холдерами `_classify_key` не покрыты, поэтому маскируем явно.
    """
    if not isinstance(payload, dict):
        return payload
    masked = dict(payload)
    for key in _PII_KEYS_IN_PAYLOAD:
        if key in masked:
            masked[key] = _mask_id(masked[key])
    # Прогоняем через общий redact на случай, если в payload забредёт
    # что-то ещё подозрительное (token-like значение).
    further = _redact(masked)
    return further if isinstance(further, dict) else masked


def init_pool(settings) -> None:
    """Поднять pooled httpx-client под lifecycle-callback'и. Идемпотентно.

    Если `secret_service_url` пуст — клиент не создаётся; notify_*
    обнаружит пустой URL и уйдёт в no-op-with-warning.
    """
    global _client
    url = (getattr(settings, "secret_service_url", "") or "").strip()
    if not url:
        return
    if _client is not None:
        return
    verify = bool(getattr(settings, "secret_service_tls_verify", True))
    _client = httpx.AsyncClient(
        base_url=url.rstrip("/"),
        timeout=httpx.Timeout(_TIMEOUT_SECONDS, connect=2.0, write=2.0, pool=2.0),
        limits=httpx.Limits(
            max_connections=_MAX_CONNECTIONS,
            max_keepalive_connections=_MAX_KEEPALIVE,
        ),
        verify=verify,
    )


async def aclose_pool() -> None:
    """Закрыть pooled-client и обнулить slot. Idempotent."""
    global _client
    client = _client
    _client = None
    if client is not None:
        try:
            await client.aclose()
        except Exception as exc:  # shutdown best-effort
            logger.warning("secret_service_client: failed to close pool: %s", exc)


def reset_for_tests() -> None:
    """Синхронный сброс slot'а без `aclose` — для monkeypatch'а в тестах.

    Заодно гасит circuit-breaker, чтобы прошлый прогон с искусственными
    fail'ами не блокировал следующий.
    """
    global _client
    _client = None
    _breaker_reset()


async def _post(
    suffix: str,
    payload: dict,
    actor_id: str | None,
    actor_username: str | None,
) -> None:
    """POST одного lifecycle-события. Best-effort: ошибки в audit + warning."""
    settings = get_settings()
    url = (getattr(settings, "secret_service_url", "") or "").strip()
    if not url:
        logger.warning(
            "secret_service_client: SECRET_SERVICE_URL is empty, skipping %s",
            suffix,
        )
        return

    # Circuit-breaker: если secret_service лёг и мы уже знаем, что подряд
    # >=N запросов упали — short-circuit'им остальные на COOLDOWN, чтобы
    # login-burst не садился в TIMEOUT_SECONDS × N.
    if _breaker_is_open():
        logger.warning(
            "secret_service_client: %s skipped (circuit-breaker open)", suffix,
        )
        audit_service.emit(
            "secret_lifecycle.notify_failed",
            actor_id=actor_id,
            username=actor_username,
            status="failure",
            allowed=True,
            details={
                "endpoint": suffix,
                "reason": "circuit_open",
                "payload": _redact_audit_payload(payload),
            },
        )
        return

    api_key = getattr(settings, "secret_internal_api_key", "") or ""
    headers = bearer_header(api_key)
    headers["Content-Type"] = "application/json"
    headers["X-Service-Identity"] = _OUTBOUND_SERVICE_IDENTITY

    full_path = f"{_LIFECYCLE_PATH}/{suffix}"

    try:
        if _client is not None:
            response = await _client.post(full_path, json=payload, headers=headers)
        else:
            verify = bool(getattr(settings, "secret_service_tls_verify", True))
            async with httpx.AsyncClient(
                timeout=_TIMEOUT_SECONDS, verify=verify,
            ) as fallback:
                response = await fallback.post(
                    f"{url.rstrip('/')}{full_path}",
                    json=payload,
                    headers=headers,
                )
    except Exception as exc:
        _breaker_record_failure()
        logger.warning(
            "secret_service_client: %s failed (transport): %s", suffix, exc,
        )
        audit_service.emit(
            "secret_lifecycle.notify_failed",
            actor_id=actor_id,
            username=actor_username,
            status="failure",
            allowed=True,
            details={
                "endpoint": suffix,
                "reason": "transport_error",
                "error": str(exc),
                "payload": _redact_audit_payload(payload),
            },
        )
        return

    if response.status_code >= 400:
        _breaker_record_failure()
        logger.warning(
            "secret_service_client: %s returned %d (best-effort, не падаем)",
            suffix,
            response.status_code,
        )
        # Тело может быть бинарным/пустым — берём только safe-срез текста.
        body_preview = ""
        try:
            body_preview = response.text[:500]
        except Exception:
            body_preview = "<unreadable>"
        audit_service.emit(
            "secret_lifecycle.notify_failed",
            actor_id=actor_id,
            username=actor_username,
            status="failure",
            allowed=True,
            details={
                "endpoint": suffix,
                "reason": "http_error",
                "status_code": response.status_code,
                "body_preview": body_preview,
                "payload": _redact_audit_payload(payload),
            },
        )
        return

    # 2xx — закрываем breaker, обнуляем счётчик неудач.
    _breaker_record_success()


async def notify_user_deleted(
    user_id: str,
    actor_id: str | None,
    actor_username: str | None,
) -> None:
    """Уведомить secret_service об удалении юзера. Best-effort."""
    await _post(
        "user-deleted",
        {
            "user_id": user_id,
            "actor_id": actor_id,
            "actor_username": actor_username,
        },
        actor_id,
        actor_username,
    )


async def notify_dept_deleted(
    dept_id: str,
    actor_id: str | None,
    actor_username: str | None,
) -> None:
    """Уведомить secret_service об удалении отдела. Best-effort."""
    await _post(
        "dept-deleted",
        {
            "dept_id": dept_id,
            "actor_id": actor_id,
            "actor_username": actor_username,
        },
        actor_id,
        actor_username,
    )


async def notify_dept_service_access_revoked(
    dept_id: str,
    service: str,
    actor_id: str | None,
    actor_username: str | None,
) -> None:
    """Уведомить secret_service, что отдел потерял access к сервису.

    Функция шлёт callback безусловно. Решение «звать или нет» принимает
    caller: и `department_service.revoke_service_access`, и
    `platform_service_service.delete_service` дёргают её только когда
    `service == "secret_service"` — для отзыва access к docker_registry или
    server_service secret_service'у уведомление не нужно.
    """
    await _post(
        "dept-service-access-revoked",
        {
            "dept_id": dept_id,
            "service": service,
            "actor_id": actor_id,
            "actor_username": actor_username,
        },
        actor_id,
        actor_username,
    )
