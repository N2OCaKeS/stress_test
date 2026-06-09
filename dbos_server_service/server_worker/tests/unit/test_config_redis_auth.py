"""Sanity-тесты для фикса «Redis без AUTH → privilege escalation через RPUSH».

Проверяем, что `Settings.redis_url` корректно принимает DSN с password
(`redis://:<password>@host:port/db`) и что production-validator отвергает
DSN без auth-сегмента.

Не требует живого Redis — пересоздаём `Settings()` напрямую с подменой env
через `monkeypatch.setenv`. `get_settings()` мы НЕ используем, чтобы обойти
`lru_cache`.
"""

from __future__ import annotations

import pytest


def _make_settings(monkeypatch: pytest.MonkeyPatch, **overrides: str):
    """Создать свежий Settings с минимальным окружением + перекрытиями.

    Все required-поля (DATABASE_URL, *_SERVICE_URL, WORKER_BOT_TOKEN) уже
    проставлены conftest'ом через `os.environ.setdefault`, но повторим
    `monkeypatch.setenv` для надёжности — если кто-то поменяет conftest,
    тесты не превратятся в molchun.
    """
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://app_user:app_password@postgres:5432/server_worker_db_test",
    )
    # https://-схема, чтобы не триггерить `_require_https_outbound_in_prod`
    # на тех тестах, где APP_ENV=production/staging.
    monkeypatch.setenv("SERVER_SERVICE_URL", "https://not-used")
    monkeypatch.setenv("AUTH_SERVICE_URL", "https://not-used")
    monkeypatch.setenv("LOGGING_SERVICE_URL", "https://not-used")
    monkeypatch.setenv("WORKER_BOT_TOKEN", "dummy-test-token")
    # Production-guard `_require_logging_api_key_in_prod` требует непустого
    # ключа. Тесты, которые специально проверяют этот guard, перекрывают
    # значение через `overrides`.
    monkeypatch.setenv("LOGGING_SERVICE_API_KEY", "dummy-test-key")
    for key, value in overrides.items():
        monkeypatch.setenv(key, value)

    from src.core.config import Settings

    return Settings()


class TestRedisUrlPasswordParsing:
    """Sanity: REDIS_URL с password парсится 1:1 без модификации."""

    def test_url_with_password_kept_verbatim(self, monkeypatch: pytest.MonkeyPatch) -> None:
        url = "redis://:dev-redis-password@redis:6379/0"
        s = _make_settings(monkeypatch, APP_ENV="local", REDIS_URL=url)
        assert s.redis_url == url

    def test_url_with_user_and_password_kept_verbatim(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # ACL-режим (Redis 6+): `redis://user:pass@host`.
        url = "redis://worker:s3cret@redis:6379/0"
        s = _make_settings(monkeypatch, APP_ENV="local", REDIS_URL=url)
        assert s.redis_url == url

    def test_url_without_password_accepted_in_local(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # В local-окружении (`APP_ENV=local`) anonymous Redis допустим
        # — иначе CI/dev-стек без secret-rotation не поднимется.
        url = "redis://redis:6379/0"
        s = _make_settings(monkeypatch, APP_ENV="local", REDIS_URL=url)
        assert s.redis_url == url

    def test_default_redis_url_is_anonymous(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Дефолт остаётся anonymous (dev-фикстура), но `.env.example`
        и compose-файл явно прописывают password-формат.

        Этот тест защищает от silent-замены дефолта на password-форму
        без обновления документации/compose.
        """
        monkeypatch.delenv("REDIS_URL", raising=False)
        s = _make_settings(monkeypatch, APP_ENV="local")
        assert s.redis_url == "redis://redis:6379/0"


class TestRedisAuthRequiredInProduction:
    """Production validator: REDIS_URL обязан содержать password."""

    def test_production_requires_password(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with pytest.raises(ValueError, match="REDIS_URL must contain a password"):
            _make_settings(
                monkeypatch,
                APP_ENV="production",
                REDIS_URL="redis://redis:6379/0",
            )

    def test_production_accepts_password_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        url = "redis://:strong-prod-password@redis.prod.svc:6379/0"
        s = _make_settings(monkeypatch, APP_ENV="production", REDIS_URL=url)
        assert s.redis_url == url

    def test_production_case_insensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # `APP_ENV=PRODUCTION` тоже должен триггерить guard.
        with pytest.raises(ValueError, match="REDIS_URL must contain a password"):
            _make_settings(
                monkeypatch,
                APP_ENV="PRODUCTION",
                REDIS_URL="redis://redis:6379/0",
            )

    @pytest.mark.parametrize("env", ["local", "dev", "test", "staging"])
    def test_non_production_envs_skip_check(
        self, monkeypatch: pytest.MonkeyPatch, env: str
    ) -> None:
        # Все non-prod окружения пропускают anonymous-DSN.
        s = _make_settings(monkeypatch, APP_ENV=env, REDIS_URL="redis://redis:6379/0")
        assert s.redis_url == "redis://redis:6379/0"


class TestHttpsOutboundRequiredInProd:
    """Гард `_require_https_outbound_in_prod`: LOGGING_SERVICE_URL и
    SERVER_SERVICE_URL обязаны быть https:// в production/staging."""

    @pytest.mark.parametrize("env", ["production", "staging"])
    def test_logging_service_url_http_rejected(
        self, monkeypatch: pytest.MonkeyPatch, env: str
    ) -> None:
        with pytest.raises(ValueError, match="LOGGING_SERVICE_URL must use https"):
            _make_settings(
                monkeypatch,
                APP_ENV=env,
                REDIS_URL="redis://:pw@redis:6379/0",
                LOGGING_SERVICE_URL="http://loging.example.com:8000",  # external FQDN; short names = intra-cluster post-W1

            )

    @pytest.mark.parametrize("env", ["production", "staging"])
    def test_server_service_url_http_rejected(
        self, monkeypatch: pytest.MonkeyPatch, env: str
    ) -> None:
        with pytest.raises(ValueError, match="SERVER_SERVICE_URL must use https"):
            _make_settings(
                monkeypatch,
                APP_ENV=env,
                REDIS_URL="redis://:pw@redis:6379/0",
                SERVER_SERVICE_URL="http://server.example.com:8000",

            )

    @pytest.mark.parametrize("env", ["production", "staging"])
    def test_auth_service_url_http_rejected(
        self, monkeypatch: pytest.MonkeyPatch, env: str
    ) -> None:
        with pytest.raises(ValueError, match="AUTH_SERVICE_URL must use https"):
            _make_settings(
                monkeypatch,
                APP_ENV=env,
                REDIS_URL="redis://:pw@redis:6379/0",
                AUTH_SERVICE_URL="http://auth.example.com:8000",

            )

    @pytest.mark.parametrize("env", ["production", "staging"])
    def test_https_urls_accepted(
        self, monkeypatch: pytest.MonkeyPatch, env: str
    ) -> None:
        s = _make_settings(
            monkeypatch,
            APP_ENV=env,
            REDIS_URL="redis://:pw@redis:6379/0",
            LOGGING_SERVICE_URL="https://logging.prod.svc:8443",
            SERVER_SERVICE_URL="https://server.prod.svc:8443",
        )
        assert s.logging_service_url.startswith("https://")
        assert s.server_service_url.startswith("https://")

    @pytest.mark.parametrize(
        "host",
        ["http://localhost:8000", "http://127.0.0.1:8000", "http://[::1]:8000"],
    )
    def test_localhost_http_allowed_in_prod(
        self, monkeypatch: pytest.MonkeyPatch, host: str
    ) -> None:
        # Localhost-исключение для devcontainer / port-forward в prod-режиме.
        s = _make_settings(
            monkeypatch,
            APP_ENV="production",
            REDIS_URL="redis://:pw@redis:6379/0",
            LOGGING_SERVICE_URL=host,
            SERVER_SERVICE_URL=host,
        )
        assert s.logging_service_url == host

    @pytest.mark.parametrize("env", ["local", "dev", "test"])
    def test_non_prod_envs_allow_http(
        self, monkeypatch: pytest.MonkeyPatch, env: str
    ) -> None:
        # В local/dev/test plain http проходит без вопросов.
        s = _make_settings(
            monkeypatch,
            APP_ENV=env,
            LOGGING_SERVICE_URL="http://loging_service:8000",
            SERVER_SERVICE_URL="http://server_service:8000",
        )
        assert s.logging_service_url.startswith("http://")


class TestLoggingApiKeyRequiredInProduction:
    """Production-guard: пустой `LOGGING_SERVICE_API_KEY` дисэйблит audit-emit
    тихо (только ERROR-лог в `audit_client.emit`). В production это
    материальная дыра — внешнее SIEM не увидит ни одного события. Старт
    воркера должен фейлиться явно, чтобы оператор сразу заметил.
    """

    def test_production_requires_api_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with pytest.raises(ValueError, match="LOGGING_SERVICE_API_KEY"):
            _make_settings(
                monkeypatch,
                APP_ENV="production",
                REDIS_URL="redis://:pw@redis:6379/0",
                LOGGING_SERVICE_API_KEY="",
            )

    def test_production_accepts_non_empty_api_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        s = _make_settings(
            monkeypatch,
            APP_ENV="production",
            REDIS_URL="redis://:pw@redis:6379/0",
            LOGGING_SERVICE_API_KEY="prod-shared-key",
        )
        assert s.logging_service_api_key == "prod-shared-key"

    @pytest.mark.parametrize("env", ["local", "dev", "test", "staging"])
    def test_non_production_allows_empty_api_key(
        self, monkeypatch: pytest.MonkeyPatch, env: str
    ) -> None:
        # CI/dev-стек поднимается без секрета. `staging` тоже мягкий —
        # на разных стендах политика разная.
        s = _make_settings(
            monkeypatch,
            APP_ENV=env,
            LOGGING_SERVICE_API_KEY="",
        )
        assert s.logging_service_api_key == ""


class TestSshHostKeyNotVerified:
    """server-SSH не проверяет host-key: флот часто переустанавливается,
    host-key меняется → known_hosts/strict непрактичны. Настройка и
    prod-guard убраны; никакой режим больше не требует strict-host-key."""

    def test_no_strict_setting_on_config(self) -> None:
        from src.core.config import Settings
        assert "ssh_strict_host_key_checking" not in Settings.model_fields

    def test_production_starts_without_strict_setting(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Прод поднимается без всякого упоминания strict-host-key — guard'а нет.
        s = _make_settings(
            monkeypatch,
            APP_ENV="production",
            REDIS_URL="redis://:pw@redis:6379/0",
        )
        assert s.app_env == "production"
