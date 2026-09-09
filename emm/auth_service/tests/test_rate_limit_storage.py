"""Tests for slowapi rate-limit storage backend (`RATE_LIMIT_STORAGE_URI`).

Дефолтный slowapi-backend — in-memory, per-process. В K8s с 2+ репликами это
ломает per-IP лимит: каждый pod держит свой счётчик, brute-force получает
N × лимит попыток. Эти тесты фиксируют контракт:

  - Settings.rate_limit_storage_uri = None → Limiter поднимается с memory://
  - явный redis URI пробрасывается в Limiter._storage_uri
  - production + memory:// → WARNING в лог
  - mask_dsn маскирует пароль в DSN (для безопасного startup-лога)
"""

from __future__ import annotations

import importlib
import logging

import pytest

from src.core.config import Settings
from src.core.security import mask_dsn


# ── mask_dsn ─────────────────────────────────────────────────────────────────


class TestMaskDsn:
    """Маска password'а в DSN-URL — startup-лог не должен утекать секрет."""

    def test_redis_user_password(self):
        assert (
            mask_dsn("redis://user:secret@h:6379/0")
            == "redis://user:***@h:6379/0"
        )

    def test_redis_without_user_with_password(self):
        # `:secret@host` — без username; маска всё равно срабатывает.
        assert mask_dsn("redis://:secret@h:6379/0") == "redis://:***@h:6379/0"

    def test_redis_without_credentials(self):
        assert mask_dsn("redis://h:6379/0") == "redis://h:6379/0"

    def test_memory_uri_passthrough(self):
        assert mask_dsn("memory://") == "memory://"

    def test_redis_unix_socket(self):
        # redis+unix:// без host'а / password'а — отдаём как есть.
        result = mask_dsn("redis+unix:///var/run/redis.sock")
        assert "secret" not in (result or "")
        assert result.startswith("redis+unix://")

    def test_none_passthrough(self):
        assert mask_dsn(None) is None

    def test_empty_passthrough(self):
        assert mask_dsn("") == ""

    def test_query_and_path_preserved(self):
        masked = mask_dsn("redis://u:s@h:6379/2?ssl=true")
        assert masked == "redis://u:***@h:6379/2?ssl=true"


# ── Limiter wiring ───────────────────────────────────────────────────────────


def _fresh_settings(**overrides) -> Settings:
    """Settings без .env-файла, чтобы изоляция тестов не зависела от окружения."""
    base: dict[str, object] = {
        "APP_ENV": "local",
        "DATABASE_URL": "postgresql+psycopg://u:p@db:5432/auth",
        "SECRET_KEY": "x" * 48,
        "SERVICE_API_KEY": "y" * 48,
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


_REDIS_BACKEND_AVAILABLE = False
try:
    import redis  # noqa: F401

    _REDIS_BACKEND_AVAILABLE = True
except ImportError:
    pass


@pytest.mark.skipif(
    not _REDIS_BACKEND_AVAILABLE,
    reason="slowapi redis-backend требует redis-py; не установлен в test-runner",
)
class TestLimiterStorageUri:
    """Limiter в src.main подхватывает storage_uri из Settings."""

    def test_default_memory_when_setting_none(self, monkeypatch):
        """rate_limit_storage_uri=None → Limiter._storage_uri == 'memory://'."""
        from src.core import config as config_module

        s = _fresh_settings(RATE_LIMIT_STORAGE_URI=None)
        assert s.rate_limit_storage_uri is None

        config_module.get_settings.cache_clear()
        monkeypatch.setattr(config_module, "get_settings", lambda: s)

        import src.main as main_module
        importlib.reload(main_module)
        try:
            # slowapi нормализует "memory://" в "memory://" (или похожее).
            # Проверяем, что не уехало в redis://, не None.
            uri = main_module.limiter._storage_uri
            assert uri.startswith("memory")
        finally:
            monkeypatch.undo()
            config_module.get_settings.cache_clear()
            importlib.reload(main_module)

    def test_explicit_redis_uri_propagates(self, monkeypatch):
        """rate_limit_storage_uri='redis://...' → Limiter._storage_uri == тот же."""
        from src.core import config as config_module

        s = _fresh_settings(RATE_LIMIT_STORAGE_URI="redis://test-redis:6379/0")
        assert s.rate_limit_storage_uri == "redis://test-redis:6379/0"

        config_module.get_settings.cache_clear()
        monkeypatch.setattr(config_module, "get_settings", lambda: s)

        import src.main as main_module
        importlib.reload(main_module)
        try:
            assert main_module.limiter._storage_uri == "redis://test-redis:6379/0"
        finally:
            monkeypatch.undo()
            config_module.get_settings.cache_clear()
            importlib.reload(main_module)


# ── Startup log: rate_limit_storage info + prod warning ─────────────────────


class TestStartupLog:
    """`_log_rate_limit_backend` пишет INFO с маскированным DSN, WARNING в prod."""

    def test_info_log_masks_password(self, caplog):
        """Контракт INFO-startup-лога — заматчить password из DSN.

        Функция чисто logging-only (нет return / нет side-effect'а), поэтому
        substring + levelno-фильтр — единственный наблюдаемый канал. Password
        не должен утечь, маскированный DSN — должен присутствовать.
        """
        from src.main import _log_rate_limit_backend

        s = _fresh_settings(RATE_LIMIT_STORAGE_URI="redis://user:topsecret@h:6379/0")
        caplog.set_level(logging.INFO, logger="src.main")
        _log_rate_limit_backend(s)

        info_records = [
            r for r in caplog.records
            if r.levelno == logging.INFO and r.name == "src.main"
        ]
        joined = "\n".join(r.getMessage() for r in info_records)
        assert "rate_limit_storage" in joined
        assert "redis://user:***@h:6379/0" in joined
        # `topsecret` не должен утечь ни в одну запись — даже не-INFO.
        all_joined = "\n".join(r.getMessage() for r in caplog.records)
        assert "topsecret" not in all_joined

    def test_info_log_memory_default(self, caplog):
        from src.main import _log_rate_limit_backend

        s = _fresh_settings(RATE_LIMIT_STORAGE_URI=None)
        caplog.set_level(logging.INFO, logger="src.main")
        _log_rate_limit_backend(s)

        info_records = [
            r for r in caplog.records
            if r.levelno == logging.INFO and r.name == "src.main"
        ]
        joined = "\n".join(r.getMessage() for r in info_records)
        assert "rate_limit_storage" in joined
        assert "memory://" in joined

    def test_production_memory_emits_warning(self, caplog):
        from src.main import _log_rate_limit_backend

        # Не строим полный prod-Settings (тот трогает SECRET_KEY/etc.) —
        # хватает duck-typed объекта с двумя нужными атрибутами.
        class _S:
            rate_limit_storage_uri = None
            app_env = "production"

        caplog.set_level(logging.WARNING, logger="src.main")
        _log_rate_limit_backend(_S())

        warn_msgs = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert any(
            "memory://" in m and "production" in m for m in warn_msgs
        ), f"expected WARNING about memory:// in production, got: {warn_msgs}"

    def test_production_with_redis_no_warning(self, caplog):
        from src.main import _log_rate_limit_backend

        class _S:
            rate_limit_storage_uri = "redis://h:6379/0"
            app_env = "production"

        caplog.set_level(logging.WARNING, logger="src.main")
        _log_rate_limit_backend(_S())

        warn_msgs = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        # Не должно быть WARNING про memory://.
        assert not any("memory://" in m for m in warn_msgs), warn_msgs

    def test_local_env_memory_no_warning(self, caplog):
        """В dev/local env memory:// — норма, WARNING'а быть не должно."""
        from src.main import _log_rate_limit_backend

        s = _fresh_settings(RATE_LIMIT_STORAGE_URI=None, APP_ENV="local")
        caplog.set_level(logging.WARNING, logger="src.main")
        _log_rate_limit_backend(s)

        warn_msgs = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert not any("memory://" in m for m in warn_msgs), warn_msgs


# ── Settings field shape ─────────────────────────────────────────────────────


class TestSettingsField:
    """Поле существует, ENV-alias правильный, default — None."""

    def test_default_is_none(self):
        s = _fresh_settings()
        assert s.rate_limit_storage_uri is None

    def test_env_alias_picked_up(self, monkeypatch):
        monkeypatch.setenv("RATE_LIMIT_STORAGE_URI", "redis://env-host:6379/1")
        # Не передаём kwarg — пусть pydantic-settings прочитает env.
        s = Settings(
            _env_file=None,
            APP_ENV="local",
            DATABASE_URL="postgresql+psycopg://u:p@db:5432/auth",
            SECRET_KEY="x" * 48,
            SERVICE_API_KEY="y" * 48,
        )
        assert s.rate_limit_storage_uri == "redis://env-host:6379/1"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
