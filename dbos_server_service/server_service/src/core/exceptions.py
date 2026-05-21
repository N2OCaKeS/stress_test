"""Иерархия доменных исключений сервиса.

Все они подтягиваются через `app_exception_handler` в main.py и превращаются
в стандартный envelope с `error_code` / `request_id` / `timestamp`.
"""

from dataclasses import dataclass, field


@dataclass
class AppException(Exception):
    """Базовое доменное исключение. error_code — стабильный машинный ключ."""

    error_code: str
    message: str
    details: dict = field(default_factory=dict)
    http_status: int = 500


@dataclass
class AuthenticationError(AppException):
    """401 — нет валидного bearer'а (отсутствие/подделка/expire/ban)."""

    http_status: int = 401


@dataclass
class AuthorizationError(AppException):
    """403 — bearer валидный, но прав не хватает."""

    http_status: int = 403


@dataclass
class NotFoundError(AppException):
    """404 — сущность не существует или скрыта от caller'а (cross-dept)."""

    http_status: int = 404


@dataclass
class ConflictError(AppException):
    """409 — UNIQUE-конфликт, decommissioned-сервер, idempotency-collision и т.п."""

    http_status: int = 409


@dataclass
class DomainValidationError(AppException):
    """422 — невалидное значение поля, неизвестный entity_type/action и т.п."""

    http_status: int = 422


@dataclass
class ServiceUnavailableError(AppException):
    """503 — упал внешний сервис (auth_service / worker-БД / Redis)."""

    http_status: int = 503


class NotImplementedYetError(AppException):
    """501 — endpoint зарегистрирован, но логика пока не написана.

    Используется вместо `HTTPException(501)`, чтобы ответ прошёл через
    `app_exception_handler` и получил стандартный envelope с
    `error_code` / `request_id` / `timestamp`.

    Поле `error` envelope'а проставляется в handler'е из http_status →
    для 501 это `"internal_error"` (fallback `_http_status_to_category`).
    """

    def __init__(self, action: str | None = None, **details) -> None:
        super().__init__(
            error_code="NOT_IMPLEMENTED",
            message=(
                f"endpoint '{action}' is not implemented yet"
                if action
                else "endpoint is not implemented yet"
            ),
            details=dict(details),
            http_status=501,
        )
