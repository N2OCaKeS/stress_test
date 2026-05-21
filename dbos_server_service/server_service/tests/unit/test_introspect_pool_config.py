"""Конфигурируемый pool size для introspect-клиента.

`INTROSPECT_POOL_MAX_CONNECTIONS` / `INTROSPECT_POOL_MAX_KEEPALIVE` env.
Default 20 / 10 — старое hardcoded поведение.
"""

from __future__ import annotations

import os

import pytest

from src.core.config import Settings


def _env(**overrides) -> dict[str, str]:
    base = {
        "DATABASE_URL": "postgresql+psycopg://user:pwd@h/db",
        "AUTH_SERVICE_URL": "http://auth",
        "SERVER_ENCRYPTION_KEY": "x" * 32,
    }
    base.update(overrides)
    return base


def test_default_pool_size(monkeypatch):
    """Без env-переменных — 20/10 (бывшее hardcoded)."""
    for k in ("INTROSPECT_POOL_MAX_CONNECTIONS", "INTROSPECT_POOL_MAX_KEEPALIVE"):
        monkeypatch.delenv(k, raising=False)
    for k, v in _env().items():
        monkeypatch.setenv(k, v)
    s = Settings()
    assert s.introspect_pool_max_connections == 20
    assert s.introspect_pool_max_keepalive == 10


def test_env_override(monkeypatch):
    """`INTROSPECT_POOL_MAX_CONNECTIONS=50 INTROSPECT_POOL_MAX_KEEPALIVE=25` подхватывается."""
    for k, v in _env(
        INTROSPECT_POOL_MAX_CONNECTIONS="50",
        INTROSPECT_POOL_MAX_KEEPALIVE="25",
    ).items():
        monkeypatch.setenv(k, v)
    s = Settings()
    assert s.introspect_pool_max_connections == 50
    assert s.introspect_pool_max_keepalive == 25


def test_keepalive_above_max_rejected(monkeypatch):
    """keepalive > max_connections — невалидно (httpx-инвариант)."""
    for k, v in _env(
        INTROSPECT_POOL_MAX_CONNECTIONS="5",
        INTROSPECT_POOL_MAX_KEEPALIVE="10",
    ).items():
        monkeypatch.setenv(k, v)
    with pytest.raises(ValueError, match="cannot exceed"):
        Settings()


def test_keepalive_zero_allowed(monkeypatch):
    """keepalive=0 отключает keep-alive — валидно."""
    for k, v in _env(INTROSPECT_POOL_MAX_KEEPALIVE="0").items():
        monkeypatch.setenv(k, v)
    s = Settings()
    assert s.introspect_pool_max_keepalive == 0


def test_main_uses_settings_for_pool(monkeypatch):
    """`main.lifespan` использует settings.introspect_pool_max_connections в httpx.Limits."""
    # Импорт после env-выставления гарантирует свежий Settings.
    for k, v in _env(
        INTROSPECT_POOL_MAX_CONNECTIONS="42",
        INTROSPECT_POOL_MAX_KEEPALIVE="7",
    ).items():
        monkeypatch.setenv(k, v)
    # Очищаем lru_cache, чтобы Settings() пересчитался.
    from src.core import config
    config.get_settings.cache_clear()

    # Прямое чтение — гарантирует что lifespan на старте увидит новые значения.
    s = config.get_settings()
    assert s.introspect_pool_max_connections == 42
    assert s.introspect_pool_max_keepalive == 7

    # Грепаем main.py — pool_limits построен из settings, не hardcode'ом.
    import pathlib
    main_py = pathlib.Path(config.__file__).parent.parent / "main.py"
    text = main_py.read_text()
    assert "settings.introspect_pool_max_connections" in text
    assert "settings.introspect_pool_max_keepalive" in text
    # Удостоверяемся что httpx.Limits для introspect-pool'а больше не
    # вызывается с hardcoded числами (могли остаться в _audit_client, но
    # introspect уже на settings).
    import re
    introspect_block = re.search(r"_introspect_client = httpx\.AsyncClient\(.*?\)", text, re.S)
    assert introspect_block is not None
    assert "max_connections=20" not in introspect_block.group(0)

    # Возвращаем lru_cache к исходным значениям, чтобы не сломать соседние тесты.
    config.get_settings.cache_clear()
