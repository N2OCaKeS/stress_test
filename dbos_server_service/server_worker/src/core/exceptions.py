"""Иерархия доменных исключений."""

from dataclasses import dataclass, field


@dataclass
class AppException(Exception):
    """Базовый класс для всех доменных ошибок worker'а.

    `error_code` — стабильный машинно-читаемый код для логов и audit
    `details`. `message` — человеко-читаемый текст. `details` — произвольный
    контекст (server_id, status_code, ...).
    """

    error_code: str
    message: str
    details: dict = field(default_factory=dict)

    def __str__(self) -> str:
        # dataclass-based Exception без явного super().__init__() оставляет
        # `self.args` пустым, и `str(exc)` возвращает пустую строку. Это
        # ломает diagnostics в `_runner`, где error_message собирается как
        # `f"{type(exc).__name__}: {exc}"` — без override пользователь
        # увидел бы только `AppException: ` без error_code и message.
        return f"{self.error_code}: {self.message}"


@dataclass
class CredentialFetchError(AppException):
    """Не удалось получить расшифрованные credentials от server_service.

    Бросается из `server_service_client` при transport-error или non-200
    ответе. `error_code` различает причину: `SERVER_SERVICE_UNREACHABLE`,
    `IPMI_CREDENTIALS_UNAVAILABLE`, `ACCOUNT_PASSWORD_UNAVAILABLE`,
    `PASSWORD_ROTATE_REJECTED`.
    """


@dataclass
class DestructiveGateDeferred(AppException):
    """Деструктивную операцию отложили: на сервере есть другая running-задача.

    Бросается гейтом (`tasks._destructive_gate`) ДО любого side-effect'а на
    боксе, когда `count_other_running_on_server` вернул > 0. `_runner`
    распознаёт этот тип и шедулит durable reschedule БЕЗ инкремента
    `attempt` против `max_attempts` — задача ждёт, пока бокс освободится, а
    не сгорает в FAILED по исчерпанию попыток. `details` несёт `server_id` и
    число конкурирующих running-задач для audit/диагностики.
    """
