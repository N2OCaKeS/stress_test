"""Настраиваемая парольная политика пользовательских паролей auth_service.

Политика (минимальная длина + обязательность буквы/цифры) применяется к
`UserCreate.password` и `ResetPasswordRequest.new_password` (secret юзера).
Базовое значение — 12 символов, буква и цифра — историческое поведение;
живёт в `_DEFAULT_POLICY`, чтобы до загрузки строки из БД валидатор вёл себя
как раньше.

Настройка хранится singleton-строкой в БД и правится платформенным
`account_admin` через `/admin/password-policy`. На старте lifespan сидирует
активную политику: если строки в БД ещё нет (первый запуск) — пишет её из env
(`AUTH_PASSWORD_POLICY_*`) и грузит в кэш; иначе берёт из БД, минуя env.
Синхронные `is_compliant`/`validate_password` читают процессный кэш `_ACTIVE`.

`INITIAL_ADMIN_PASSWORD` этой политике НЕ подчиняется — его проверяет отдельный
жёсткий guard на 12 (`core/config.py::_validate_production_secrets`), чтобы
бутстрап-пароль владельца нельзя было ослабить.

`validate_password` бросает `ValueError` — Pydantic превращает его в 422.
"""

# Дефолтная (историческая) минимальная длина. Дефолт env-поля и жёсткий guard
# для INITIAL_ADMIN_PASSWORD завязаны на это значение.
MIN_PASSWORD_LENGTH = 12

# Границы настраиваемой минимальной длины. Ниже 1 смысла нет, выше 128 — за
# гранью разумного для пароля.
MIN_CONFIGURABLE_LENGTH = 1
MAX_CONFIGURABLE_LENGTH = 128

# Дефолт политики — до загрузки строки из БД (или на свежей БД без миграции).
_DEFAULT_POLICY: dict = {
    "min_length": MIN_PASSWORD_LENGTH,
    "require_letter": True,
    "require_digit": True,
}


def _build_policy_message(policy: dict) -> str:
    """Человекочитаемое сообщение об ошибке под конкретную политику."""
    parts = [f"Password must be at least {policy['min_length']} characters long"]
    require_letter = policy["require_letter"]
    require_digit = policy["require_digit"]
    if require_letter and require_digit:
        parts.append("contain both letters and digits")
    elif require_letter:
        parts.append("contain a letter")
    elif require_digit:
        parts.append("contain a digit")
    return " and ".join(parts)


# Процессный кэш активной политики. Обновляется через `apply_policy`.
_ACTIVE: dict = dict(_DEFAULT_POLICY)


def apply_policy(policy: dict) -> None:
    """Обновить процессный кэш активной политики (читает ровно три поля)."""
    _ACTIVE["min_length"] = int(policy["min_length"])
    _ACTIVE["require_letter"] = bool(policy["require_letter"])
    _ACTIVE["require_digit"] = bool(policy["require_digit"])


def current_policy() -> dict:
    """Копия активной политики (min_length / require_letter / require_digit)."""
    return dict(_ACTIVE)


def is_compliant(password: str) -> bool:
    """True, если пароль удовлетворяет активной политике."""
    if len(password) < _ACTIVE["min_length"]:
        return False
    if _ACTIVE["require_letter"] and not any(ch.isalpha() for ch in password):
        return False
    if _ACTIVE["require_digit"] and not any(ch.isdigit() for ch in password):
        return False
    return True


def validate_password(password: str) -> str:
    """Проверить пароль по активной политике и вернуть его же.

    Удобен в field-валидаторе: при нарушении поднимает `ValueError`, который
    Pydantic отдаёт как 422. Сообщение собирается под активную политику, чтобы
    клиент видел актуальные требования.
    """
    if not is_compliant(password):
        raise ValueError(_build_policy_message(_ACTIVE))
    return password
