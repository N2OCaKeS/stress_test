"""Парольные политики для секретов, которые задаются вручную.

Базовая политика — минимум 8 символов, обязательно буква и цифра. Применяется
к ручному вводу пароля на create/rotate для server-аккаунтов и IPMI-credentials.
Автогенерированные пароли (`secrets.token_urlsafe`) политику проходят сами.

Усиленная политика — минимум 16 символов плюс буква, цифра и нелитерально-
цифровой символ. Применяется к bootstrap-кредам на `server.prepare`: входная
точка доступа к свежей коробке, держать здесь слабые пароли нельзя.

`validate_password` / `validate_strong_password` бросают на схеме исключение
с конкретным error-code (`WEAK_PASSWORD`) — Pydantic поднимает 422, FastAPI
кладёт type в `details.errors[].type`. `is_compliant` / `is_strong` — голые
проверки без исключения, удобны для assert'ов.
"""

from pydantic_core import PydanticCustomError

MIN_PASSWORD_LENGTH = 8
MIN_STRONG_PASSWORD_LENGTH = 16

_POLICY_MESSAGE = (
    "Password must be at least 8 characters long and contain "
    "both letters and digits"
)
_STRONG_POLICY_MESSAGE = (
    "Password must be at least 16 characters long and contain "
    "a letter, a digit and a symbol"
)


def is_compliant(password: str) -> bool:
    """True, если пароль удовлетворяет базовой политике."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return False
    has_letter = any(ch.isalpha() for ch in password)
    has_digit = any(ch.isdigit() for ch in password)
    return has_letter and has_digit


def is_strong(password: str) -> bool:
    """True, если пароль удовлетворяет усиленной политике."""
    if len(password) < MIN_STRONG_PASSWORD_LENGTH:
        return False
    has_letter = any(ch.isalpha() for ch in password)
    has_digit = any(ch.isdigit() for ch in password)
    has_symbol = any(not ch.isalnum() for ch in password)
    return has_letter and has_digit and has_symbol


def validate_password(password: str) -> str:
    """Проверить пароль по базовой политике и вернуть его же.

    Подходит для использования в pydantic field-валидаторе: на нарушении
    политики поднимает `ValueError`, который схема отдаёт как 422.
    """
    if not is_compliant(password):
        raise ValueError(_POLICY_MESSAGE)
    return password


def validate_strong_password(password: str) -> str:
    """Проверить пароль по усиленной политике и вернуть его же.

    На нарушении бросает `PydanticCustomError` с типом `WEAK_PASSWORD` — он
    проедет наружу в `details.errors[].type` 422-envelope'а, машинно
    различимый код.
    """
    if not is_strong(password):
        raise PydanticCustomError("WEAK_PASSWORD", _STRONG_POLICY_MESSAGE)
    return password
