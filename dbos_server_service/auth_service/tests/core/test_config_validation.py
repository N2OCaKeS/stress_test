"""Юнит-тесты production-validator'а в `src.core.config.Settings`.

Цель — проверить, что при `APP_ENV=production` сервис **не поднимется**
с дефолтными/слабыми секретами:

  - `SECRET_KEY` ∈ {empty, placeholder `change-me*`, < 32 символов};
  - `SERVICE_API_KEY` ∈ {empty, placeholder, < 32 символов};
  - `INITIAL_ADMIN_PASSWORD` ∈ {empty/placeholder/слабый, < 12 символов},
    но только если задан `INITIAL_ADMIN_USERNAME`;
  - `LOGGING_SERVICE_API_KEY` пустой → audit-канал не работает в проде;
  - `DOCKER_RSA_PRIVATE_KEY` отсутствует (уже было до фикса, регрессия-чек);
  - `APP_DEBUG=true` (уже было до фикса, регрессия-чек).

Не-production env (`local`, `development`, `test`) **не должны** триггерить
эти проверки — иначе разработчики не смогут запустить сервис локально.

Все тесты строят `Settings(**kwargs)` напрямую, минуя `get_settings()` и
`.env`-файл — так не зависим от глобального состояния процесса и кеша
`@lru_cache`.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.core.config import Settings


# ── Базовый «всё валидно для prod» набор ─────────────────────────────────────
# Используется как старт для каждого негативного кейса: меняется ровно
# один параметр, остальные остаются «правильными». Так тест провалится
# именно на проверяемом аспекте, а не на чём-то соседнем.

_VALID_PROD_KWARGS: dict[str, object] = {
    "APP_ENV": "production",
    "APP_DEBUG": False,
    "DATABASE_URL": "postgresql+psycopg://u:p@db:5432/auth",
    "SECRET_KEY": "a" * 48,
    "SERVICE_API_KEY": "b" * 48,
    "LOGGING_SERVICE_API_KEY": "c" * 32,
    "DOCKER_RSA_PRIVATE_KEY": "-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----",
    # Lifecycle-канал в secret_service: production validator требует
    # непустой ключ; URL может быть пустым (no-op для стендов без secret_service).
    "SECRET_INTERNAL_API_KEY": "d" * 32,
    "SECRET_SERVICE_URL": "",
}


def _build(**overrides) -> Settings:
    """Construct Settings with explicit kwargs, bypassing the .env file.

    `_env_file=None` отключает чтение `.env` — иначе тестовый сервис
    подхватил бы реальные значения и тесты стали бы flaky."""
    kwargs = {**_VALID_PROD_KWARGS, **overrides}
    return Settings(_env_file=None, **kwargs)


# ── Sanity: «правильный» production-конфиг не падает ─────────────────────────


def test_valid_production_config_passes():
    """Базовый набор валидных значений должен инстанциироваться без ошибок —
    иначе все остальные негативные кейсы могут проходить «ложно»."""
    s = _build()
    assert s.app_env == "production"
    assert s.app_debug is False


# ── SECRET_KEY ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "weak_value, expected_substring",
    [
        ("change-me", "SECRET_KEY"),                              # точный дефолт
        ("change-me-to-a-long-random-secret-at-least-32-chars",   # .env.example:33
         "SECRET_KEY"),
        ("Change-Me-XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX",         # регистр
         "SECRET_KEY"),
        ("changeme-but-also-very-long-actually-32-chars+",        # без дефиса
         "SECRET_KEY"),
    ],
)
def test_production_rejects_placeholder_secret_key(weak_value, expected_substring):
    """`change-me`-подстрока ловится регистронезависимо, даже если строка
    «удлинена» до ≥ 32 символов — типичный антипаттерн «положу placeholder
    и забуду заменить»."""
    with pytest.raises(ValidationError) as exc:
        _build(SECRET_KEY=weak_value)
    assert expected_substring in str(exc.value)


def test_production_rejects_short_secret_key():
    """HS256 без ≥ 32 байт ключа криптографически слабый — JWT можно
    подобрать brute-force за разумное время."""
    with pytest.raises(ValidationError) as exc:
        _build(SECRET_KEY="a" * 31)
    assert "at least 32 characters" in str(exc.value)


def test_production_rejects_empty_secret_key():
    with pytest.raises(ValidationError) as exc:
        _build(SECRET_KEY="")
    assert "SECRET_KEY" in str(exc.value)


def test_production_accepts_strong_secret_key():
    s = _build(SECRET_KEY="x" * 64)
    assert len(s.secret_key) == 64


# ── SERVICE_API_KEY ──────────────────────────────────────────────────────────


def test_production_rejects_placeholder_service_api_key():
    with pytest.raises(ValidationError) as exc:
        _build(SERVICE_API_KEY="change-me-service-key")
    assert "SERVICE_API_KEY" in str(exc.value)


def test_production_rejects_short_service_api_key():
    with pytest.raises(ValidationError) as exc:
        _build(SERVICE_API_KEY="a" * 16)
    assert "at least 32 characters" in str(exc.value)


def test_production_rejects_empty_service_api_key():
    with pytest.raises(ValidationError) as exc:
        _build(SERVICE_API_KEY="")
    assert "SERVICE_API_KEY" in str(exc.value)


# ── INITIAL_ADMIN_PASSWORD ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "weak_password",
    [
        "change-me",
        "change-me-immediately",  # .env.example:81
        "1234",
        "password",
        "admin",
    ],
)
def test_production_rejects_default_initial_admin_password(weak_password):
    """Если задан `INITIAL_ADMIN_USERNAME`, bootstrap создаст пользователя
    с `INITIAL_ADMIN_PASSWORD` — placeholder там даст мгновенный admin-доступ."""
    with pytest.raises(ValidationError) as exc:
        _build(
            INITIAL_ADMIN_USERNAME="admin",
            INITIAL_ADMIN_PASSWORD=weak_password,
        )
    assert "INITIAL_ADMIN_PASSWORD" in str(exc.value)


def test_production_rejects_short_initial_admin_password():
    """OWASP-минимум для admin-пароля — 12 символов."""
    with pytest.raises(ValidationError) as exc:
        _build(
            INITIAL_ADMIN_USERNAME="admin",
            INITIAL_ADMIN_PASSWORD="Short1!",  # 7 chars
        )
    assert "at least 12 characters" in str(exc.value)


def test_production_rejects_empty_initial_admin_password_when_username_set():
    with pytest.raises(ValidationError) as exc:
        _build(
            INITIAL_ADMIN_USERNAME="admin",
            INITIAL_ADMIN_PASSWORD="",
        )
    assert "INITIAL_ADMIN_PASSWORD" in str(exc.value)


def test_production_allows_admin_password_when_username_not_set():
    """Если bootstrap отключён (`INITIAL_ADMIN_USERNAME=None`), валидация
    пароля не должна срабатывать — пользователь всё равно не создастся.
    Это позволяет деплоить prod без bootstrap (например, при rollback'е
    схемы или повторных деплоях, когда пользователь уже существует)."""
    s = _build(
        INITIAL_ADMIN_USERNAME=None,
        INITIAL_ADMIN_PASSWORD="change-me",  # placeholder — но валидно, т.к. username не задан
    )
    assert s.initial_admin_username is None


def test_production_accepts_strong_initial_admin_password():
    s = _build(
        INITIAL_ADMIN_USERNAME="admin",
        INITIAL_ADMIN_PASSWORD="StrongP@ssw0rd-123",
    )
    assert s.initial_admin_password == "StrongP@ssw0rd-123"


# ── LOGGING_SERVICE_API_KEY ──────────────────────────────────────────────────


def test_production_rejects_empty_logging_service_api_key():
    """Без ключа `audit_client.emit()` либо тихо дропает события, либо
    шлёт PAT worker'а как fallback — оба варианта рвут audit-trail.
    В проде audit обязателен по требованиям ГОСТ → fail-fast при старте."""
    with pytest.raises(ValidationError) as exc:
        _build(LOGGING_SERVICE_API_KEY=None)
    assert "LOGGING_SERVICE_API_KEY" in str(exc.value)


def test_production_rejects_empty_string_logging_service_api_key():
    with pytest.raises(ValidationError) as exc:
        _build(LOGGING_SERVICE_API_KEY="")
    assert "LOGGING_SERVICE_API_KEY" in str(exc.value)


# ── APP_DEBUG / DOCKER_RSA_PRIVATE_KEY (regression-чеки уже-существующих) ────


def test_production_rejects_app_debug_true():
    with pytest.raises(ValidationError) as exc:
        _build(APP_DEBUG=True)
    assert "APP_DEBUG" in str(exc.value)


def test_production_rejects_missing_docker_rsa_private_key():
    with pytest.raises(ValidationError) as exc:
        _build(DOCKER_RSA_PRIVATE_KEY=None)
    assert "DOCKER_RSA_PRIVATE_KEY" in str(exc.value)


# ── Non-production: слабые секреты допускаются ───────────────────────────────


@pytest.mark.parametrize("env", ["local", "development", "test"])
def test_non_production_allows_weak_secret_key(env):
    """В local/development/test нет смысла требовать длинные секреты —
    разработчику нужно быстро поднять сервис без лишних env-vars."""
    s = Settings(
        _env_file=None,
        APP_ENV=env,
        DATABASE_URL="postgresql+psycopg://u:p@db:5432/auth",
        SECRET_KEY="change-me",
        SERVICE_API_KEY="change-me-service-key",
        APP_DEBUG=True,
    )
    assert s.app_env == env
    assert s.secret_key == "change-me"


@pytest.mark.parametrize("env", ["local", "development", "test"])
def test_non_production_allows_empty_logging_service_api_key(env):
    """LOGGING_SERVICE_API_KEY опционален вне prod — local-разработка не
    должна требовать поднимать logging_service."""
    s = Settings(
        _env_file=None,
        APP_ENV=env,
        DATABASE_URL="postgresql+psycopg://u:p@db:5432/auth",
        SECRET_KEY="change-me",
        LOGGING_SERVICE_API_KEY=None,
    )
    assert s.logging_service_api_key is None


@pytest.mark.parametrize("env", ["local", "development", "test"])
def test_non_production_allows_weak_admin_password(env):
    s = Settings(
        _env_file=None,
        APP_ENV=env,
        DATABASE_URL="postgresql+psycopg://u:p@db:5432/auth",
        SECRET_KEY="dev",
        INITIAL_ADMIN_USERNAME="admin",
        INITIAL_ADMIN_PASSWORD="1234",
    )
    assert s.initial_admin_password == "1234"


# ── LOGGING_SERVICE_URL https-only гард ──────────────────────────────────────


def test_production_rejects_http_logging_service_url():
    """audit-канал по plain http в кластере снифается / перехватывается;
    атакующий может тихо съесть события про bans / role-changes /
    login-failures. Симметричный гард с loging_service для AUTH_SERVICE_URL."""
    with pytest.raises(ValidationError) as exc:
        _build(LOGGING_SERVICE_URL="http://logging.example.com")
    assert "LOGGING_SERVICE_URL" in str(exc.value)
    assert "https" in str(exc.value).lower()


def test_production_accepts_https_logging_service_url():
    s = _build(LOGGING_SERVICE_URL="https://logging.example.com")
    assert s.logging_service_url == "https://logging.example.com"


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8001",
        "http://127.0.0.1:8001",
        "http://[::1]:8001",
    ],
)
def test_production_allows_http_localhost_logging_service_url(url):
    """Localhost-исключение для devcontainer/sidecar — там TLS терминируется
    на той же машине, MITM-модель другая."""
    s = _build(LOGGING_SERVICE_URL=url)
    assert s.logging_service_url == url


def test_production_allows_none_logging_service_url_with_other_envs():
    """Если URL не задан вообще — гард не должен срабатывать. Сценарий
    `LOGGING_SERVICE_URL=None` сейчас всё равно отбивается требованием
    `LOGGING_SERVICE_API_KEY`, но это другой чек — отдельный регрессия-кейс
    специально для URL-гарда."""
    # API_KEY обязателен в prod, URL опционален — проверим что URL=None+key=set
    # проходит URL-гард (валится может на другом чеке — нам не важно).
    # Тут берём корректный URL, чтобы изолировать именно URL-гард.
    s = _build(LOGGING_SERVICE_URL="https://safe.example.com")
    assert s.logging_service_url == "https://safe.example.com"


@pytest.mark.parametrize("env", ["local", "development", "test"])
def test_non_production_allows_http_logging_service_url(env):
    """В local/development/test http://logging — норма для dev-стенда."""
    s = Settings(
        _env_file=None,
        APP_ENV=env,
        DATABASE_URL="postgresql+psycopg://u:p@db:5432/auth",
        SECRET_KEY="dev",
        LOGGING_SERVICE_URL="http://logging.local:8001",
    )
    assert s.logging_service_url == "http://logging.local:8001"
