"""Иерархия доменных исключений. Каждый — стабильный HTTP status + error_code."""

from dataclasses import dataclass, field


@dataclass
class AppException(Exception):
    """Базовое доменное исключение. Все наследники задают свой http_status."""
    error_code: str
    message: str
    details: dict = field(default_factory=dict)
    http_status: int = 500


@dataclass
class AuthenticationError(AppException):
    """401 — invalid credentials, expired token и т.п."""
    http_status: int = 401


@dataclass
class AuthorizationError(AppException):
    """403 — есть identity, но нет прав на действие."""
    http_status: int = 403


@dataclass
class NotFoundError(AppException):
    """404 — ресурс не найден."""
    http_status: int = 404


@dataclass
class ConflictError(AppException):
    """409 — уникальность, идемпотентность, конфликт состояний."""
    http_status: int = 409


@dataclass
class DomainValidationError(AppException):
    """422 — невалидные доменные параметры (Pydantic-валидация даёт свои 422 сама)."""
    http_status: int = 422


@dataclass
class RateLimitError(AppException):
    """429 — превышен лимит / залочен после неудачных попыток."""
    http_status: int = 429


@dataclass
class ServiceUnavailableError(AppException):
    """503 — внешняя зависимость (loging_service / DB) недоступна."""
    http_status: int = 503
