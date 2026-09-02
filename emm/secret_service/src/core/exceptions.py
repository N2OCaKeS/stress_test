"""Доменные исключения secret_service.

Минимальная иерархия, симметричная server_service: один базовый
`AppException` плюс несколько типизированных подклассов под HTTP-классы.
HTTP-handler (будет добавлен в `main.py` отдельной фазой) маппит
`error_code` → стабильный JSON-envelope.
"""

from dataclasses import dataclass, field


@dataclass
class AppException(Exception):
    """Базовое доменное исключение. `error_code` — стабильный машинный ключ."""

    error_code: str
    message: str
    details: dict = field(default_factory=dict)
    http_status: int = 500


@dataclass
class BadRequestError(AppException):
    http_status: int = 400


@dataclass
class AuthenticationError(AppException):
    http_status: int = 401


@dataclass
class AuthorizationError(AppException):
    http_status: int = 403


@dataclass
class NotFoundError(AppException):
    http_status: int = 404


@dataclass
class ConflictError(AppException):
    http_status: int = 409


@dataclass
class GoneError(AppException):
    http_status: int = 410


@dataclass
class DomainValidationError(AppException):
    http_status: int = 422


@dataclass
class RateLimitError(AppException):
    http_status: int = 429


@dataclass
class ServiceUnavailableError(AppException):
    """503 — внешняя зависимость (auth_service, loging_service) недоступна."""

    http_status: int = 503
