"""Иерархия доменных исключений сервиса."""

from dataclasses import dataclass, field


@dataclass
class AppException(Exception):
    error_code: str
    message: str
    details: dict = field(default_factory=dict)
    http_status: int = 500


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
class DomainValidationError(AppException):
    http_status: int = 422


@dataclass
class ServiceUnavailableError(AppException):
    http_status: int = 503
