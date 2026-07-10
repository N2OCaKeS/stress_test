"""Парольные политики для секретов, которые задаются вручную.

Базовая политика настраивается в рантайме платформенным админом
(`account_admin`) через `/admin/password-policy` и хранится singleton-строкой в
БД. Здесь живёт её процессный кэш (`_ACTIVE`), из которого читают синхронные
`is_compliant` / `validate_password` — их зовут Pydantic-валидаторы на ручном
вводе пароля в create/rotate для server-аккаунтов и IPMI-credentials.
Автогенерированные пароли (`secrets.token_urlsafe`) политику проходят сами.

Дефолты кэша (`_DEFAULT_POLICY`) совпадают с историческим поведением — 8
символов, обязательны буква и цифра — чтобы до загрузки строки из БД (или на
свежей БД без миграции) валидация вела себя как раньше. На старте сервиса
lifespan читает singleton и зовёт `apply_policy(...)`; PUT-эндпоинт делает то
же для процесса, который обработал запрос.

Усиленная политика — минимум 16 символов плюс буква, цифра и нелитерально-
цифровой символ — остаётся захардкоженной. Она применяется к bootstrap-кредам
на `server.prepare`: входная точка доступа к свежей коробке, слабые пароли
здесь недопустимы, настраивать её нельзя.

Обе `validate_*` функции бросают `PydanticCustomError` с типом `WEAK_PASSWORD`
— клиент видит одинаковый машинно-различимый код в `details.errors[].type` и
для базовой, и для усиленной политики. `PydanticCustomError` — подкласс
`ValueError`, поэтому Pydantic поднимает его как 422, а старые тесты с
`pytest.raises(ValueError)` остаются валидными. `is_compliant` / `is_strong`
— голые проверки без исключения, удобны для assert'ов.
"""

from pydantic_core import PydanticCustomError

MIN_PASSWORD_LENGTH = 8
MIN_STRONG_PASSWORD_LENGTH = 16

# Границы настраиваемой минимальной длины базовой политики. Ниже 1 смысла нет,
# выше 128 — уже за гранью разумного для пароля учётки.
MIN_CONFIGURABLE_LENGTH = 1
MAX_CONFIGURABLE_LENGTH = 128

# Дефолт базовой политики — историческое поведение. До загрузки строки из БД
# (или на свежей БД без миграции) валидатор ведёт себя как раньше.
_DEFAULT_POLICY: dict = {
    "min_length": MIN_PASSWORD_LENGTH,
    "require_letter": True,
    "require_digit": True,
}


def _build_policy_message(policy: dict) -> str:
    """Собрать человекочитаемое сообщение об ошибке под конкретную политику."""
    min_length = policy["min_length"]
    require_letter = policy["require_letter"]
    require_digit = policy["require_digit"]
    parts = [f"Password must be at least {min_length} characters long"]
    if require_letter and require_digit:
        parts.append("contain both letters and digits")
    elif require_letter:
        parts.append("contain a letter")
    elif require_digit:
        parts.append("contain a digit")
    return " and ".join(parts)


# Процессный кэш активной базовой политики. Копия дефолта — mutable-состояние на
# модуль, обновляется через `apply_policy`.
_ACTIVE: dict = dict(_DEFAULT_POLICY)

# Сообщение для дефолтной политики. Существующие регрессионные тесты сверяют,
# что оно содержит `MIN_PASSWORD_LENGTH` и не содержит strong-длину.
_POLICY_MESSAGE = _build_policy_message(_DEFAULT_POLICY)
_STRONG_POLICY_MESSAGE = (
    f"Password must be at least {MIN_STRONG_PASSWORD_LENGTH} characters long and contain "
    "a letter, a digit and a symbol"
)


def apply_policy(policy: dict) -> None:
    """Обновить процессный кэш активной базовой политики.

    Зовётся из lifespan на старте (после чтения singleton из БД) и из
    `password_policy_service.update_settings` после успешного апдейта строки.
    Читаем ровно три поля; неизвестные ключи игнорируются.
    """
    _ACTIVE["min_length"] = int(policy["min_length"])
    _ACTIVE["require_letter"] = bool(policy["require_letter"])
    _ACTIVE["require_digit"] = bool(policy["require_digit"])


def current_policy() -> dict:
    """Копия активной базовой политики (min_length / require_letter / require_digit)."""
    return dict(_ACTIVE)


def is_compliant(password: str) -> bool:
    """True, если пароль удовлетворяет активной базовой политике."""
    if len(password) < _ACTIVE["min_length"]:
        return False
    if _ACTIVE["require_letter"] and not any(ch.isalpha() for ch in password):
        return False
    if _ACTIVE["require_digit"] and not any(ch.isdigit() for ch in password):
        return False
    return True


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

    На нарушении бросает `PydanticCustomError` с типом `WEAK_PASSWORD` —
    тот же код, что и `validate_strong_password`, чтобы клиент по
    `details.errors[].type` различал нарушение политики единообразно. По
    длине сообщения видно, какая именно из двух политик не прошла.
    `PydanticCustomError` — подкласс `ValueError`, поэтому Pydantic
    нормально складывает его в 422-envelope. Сообщение собирается под активную
    политику, чтобы клиент видел актуальные требования (длину и наборы).
    """
    if not is_compliant(password):
        raise PydanticCustomError("WEAK_PASSWORD", _build_policy_message(_ACTIVE))
    return password


def validate_strong_password(password: str) -> str:
    """Проверить пароль по усиленной политике и вернуть его же.

    На нарушении бросает `PydanticCustomError` с типом `WEAK_PASSWORD` —
    тот же код, что и `validate_password`. Сообщение содержит strong-
    требования (16+ символов, три класса), по нему различимо, какая из
    политик сработала.
    """
    if not is_strong(password):
        raise PydanticCustomError("WEAK_PASSWORD", _STRONG_POLICY_MESSAGE)
    return password
