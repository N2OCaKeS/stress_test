"""Production-validator на `SERVER_WORKER_REDIS_URL`.

Без AUTH'а Redis-broker в проде = privilege escalation: любой контейнер
в одной k8s/Docker сети может `RPUSH` payload в taskiq-очередь и
инициировать power-cycle / password-rotation от имени worker'а. Зеркало
аналогичного фикса в server_worker.

Контракт:
* `APP_ENV=production|staging` + `SERVER_WORKER_REDIS_URL=redis://redis:6379/0`
  (без password) → `ValueError`.
* `APP_ENV=local|dev|test` → anonymous Redis допустим (dev/CI без secret-rotation).
* `SERVER_WORKER_REDIS_URL=""` (пусто) — dispatch отключён, validator не дёргается.
* `redis://:pwd@redis:6379/0` + любой env → ок.
"""

from __future__ import annotations

import pytest


def _make_settings(monkeypatch: pytest.MonkeyPatch, **overrides: str):
    """Создать свежий Settings с минимальным окружением + перекрытиями.

    Все required-поля заданы. `lru_cache` обходим через прямой `Settings()`.
    """
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://app_user:app_password@postgres:5432/server_db_test",
    )
    monkeypatch.setenv("AUTH_SERVICE_URL", "http://not-used")
    monkeypatch.setenv(
        "SERVER_ENCRYPTION_KEY",
        "test-server-encryption-key-do-not-use-anywhere-else",
    )
    for key, value in overrides.items():
        monkeypatch.setenv(key, value)

    from src.core.config import Settings

    return Settings()


class TestProductionRequiresRedisAuth:
    """В production/staging `SERVER_WORKER_REDIS_URL` обязан содержать password."""

    def test_production_rejects_anonymous_redis(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with pytest.raises(ValueError, match="SERVER_WORKER_REDIS_URL must contain a password"):
            _make_settings(
                monkeypatch,
                APP_ENV="production",
                SERVER_WORKER_REDIS_URL="redis://redis:6379/0",
            )

    def test_staging_rejects_anonymous_redis(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with pytest.raises(ValueError, match="SERVER_WORKER_REDIS_URL must contain a password"):
            _make_settings(
                monkeypatch,
                APP_ENV="staging",
                SERVER_WORKER_REDIS_URL="redis://redis:6379/0",
            )

    def test_production_case_insensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`APP_ENV=PRODUCTION` тоже должен триггерить guard."""
        with pytest.raises(ValueError, match="SERVER_WORKER_REDIS_URL must contain a password"):
            _make_settings(
                monkeypatch,
                APP_ENV="PRODUCTION",
                SERVER_WORKER_REDIS_URL="redis://redis:6379/0",
            )

    def test_production_accepts_password_redis(self, monkeypatch: pytest.MonkeyPatch) -> None:
        url = "redis://:strong-prod-password@redis.prod.svc:6379/0"
        s = _make_settings(
            monkeypatch,
            APP_ENV="production",
            SERVER_WORKER_REDIS_URL=url,
            AUTH_SERVICE_URL="https://auth.prod.svc:8000",
        )
        assert s.server_worker_redis_url == url

    def test_production_accepts_user_password_acl_format(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Redis 6+ ACL: `redis://user:pass@host` — тоже валидно."""
        url = "redis://worker:s3cret@redis:6379/0"
        s = _make_settings(
            monkeypatch,
            APP_ENV="production",
            SERVER_WORKER_REDIS_URL=url,
            AUTH_SERVICE_URL="https://auth.prod.svc:8000",
        )
        assert s.server_worker_redis_url == url

    def test_production_empty_url_skips_check(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Пустой `SERVER_WORKER_REDIS_URL` (dispatch отключён) — ок в любом env."""
        s = _make_settings(
            monkeypatch,
            APP_ENV="production",
            SERVER_WORKER_REDIS_URL="",
            AUTH_SERVICE_URL="https://auth.prod.svc:8000",
        )
        assert s.server_worker_redis_url == ""


class TestNonProductionEnvsAllowAnonymousRedis:
    """В local/dev/test anonymous Redis допустим — иначе CI/dev не поднимется."""

    @pytest.mark.parametrize("env", ["local", "dev", "test"])
    def test_dev_envs_accept_anonymous_redis(
        self, monkeypatch: pytest.MonkeyPatch, env: str
    ) -> None:
        s = _make_settings(
            monkeypatch,
            APP_ENV=env,
            SERVER_WORKER_REDIS_URL="redis://redis:6379/0",
        )
        assert s.server_worker_redis_url == "redis://redis:6379/0"

    def test_default_app_env_local_accepts_anonymous(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Если `APP_ENV` не задан → default 'local' → guard не работает."""
        monkeypatch.delenv("APP_ENV", raising=False)
        s = _make_settings(monkeypatch, SERVER_WORKER_REDIS_URL="redis://redis:6379/0")
        assert s.app_env == "local"
        assert s.server_worker_redis_url == "redis://redis:6379/0"


class TestProductionRequiresHttpsAuthUrl:
    """В production/staging `AUTH_SERVICE_URL` обязан быть https:// (не localhost).

    Симметрия с `loging_service`. На plain http в кластере любой sniff/MITM
    в network namespace перехватит introspect-bearer'ы пользователей.
    """

    def test_production_rejects_http_auth_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with pytest.raises(ValueError, match="AUTH_SERVICE_URL must use https"):
            _make_settings(
                monkeypatch,
                APP_ENV="production",
                AUTH_SERVICE_URL="http://auth-service.cluster.svc:8000",
            )

    def test_production_accepts_https_auth_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        url = "https://auth-service.cluster.svc:8000"
        s = _make_settings(monkeypatch, APP_ENV="production", AUTH_SERVICE_URL=url)
        assert s.auth_service_url == url
