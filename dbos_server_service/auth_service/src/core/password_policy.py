"""Единая парольная политика для пользовательских паролей auth_service.

Минимум 12 символов, обязательно есть и буква, и цифра. Применяется к
`UserCreate.password` и `ResetPasswordRequest.new_password` (одно и то же
поле в семантическом смысле — secret юзера). Тот же контракт, что и
`server_service/src/core/password_policy.py` для серверных секретов: лимит
длины фиксированный, чтобы политика была одинаково читаемой во всех
сервисах.

Единое значение 12 синхронизировано с production-guard'ом для
`INITIAL_ADMIN_PASSWORD` (`core/config.py::_validate_production_secrets`).
Раньше user-facing валидатор требовал 8, prod-guard — 12; оператор мог
выставить юзеру 10-символьный пароль, validator его принимал, а bootstrap
admin отбивался — асимметрия порождала «у соседа работает, у меня нет».

`validate_password` бросает `ValueError` — Pydantic превращает его в 422.
"""

MIN_PASSWORD_LENGTH = 12

_POLICY_MESSAGE = (
    "Password must be at least 12 characters long and contain "
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

    Удобен в field-валидаторе: при нарушении политики поднимает
    `ValueError`, который Pydantic отдаёт как 422.
    """
    if not is_compliant(password):
        raise ValueError(_POLICY_MESSAGE)
    return password
