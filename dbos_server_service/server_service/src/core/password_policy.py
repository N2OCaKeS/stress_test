"""Единая парольная политика для секретов, которые задаются вручную.

Минимум 8 символов, обязательно есть и буква, и цифра. Применяется к ручному
вводу пароля на create/rotate для server-аккаунтов и IPMI-credentials.
Автогенерированные пароли (`secrets.token_urlsafe`) политику проходят сами.

`validate_password` бросает `ValueError` — Pydantic превращает его в 422 на
схеме. `is_compliant` — голая проверка без исключения, удобна для assert'ов.
"""

MIN_PASSWORD_LENGTH = 8

_POLICY_MESSAGE = (
    "Password must be at least 8 characters long and contain "
    "both letters and digits"
)


def is_compliant(password: str) -> bool:
    """True, если пароль удовлетворяет политике."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return False
    has_letter = any(ch.isalpha() for ch in password)
    has_digit = any(ch.isdigit() for ch in password)
    return has_letter and has_digit


def validate_password(password: str) -> str:
    """Проверить пароль по политике и вернуть его же.

    Подходит для использования в pydantic field-валидаторе: на нарушении
    политики поднимает `ValueError`, который схема отдаёт как 422.
    """
    if not is_compliant(password):
        raise ValueError(_POLICY_MESSAGE)
    return password
