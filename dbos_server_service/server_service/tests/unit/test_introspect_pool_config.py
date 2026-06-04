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
    """`http_pool.init_pools` использует settings.introspect_pool_* в httpx.Limits.

    Конструирование клиентов вынесено из `main.py` в `services/http_pool.py`,
    lifespan только дёргает `http_pool.init_pools(settings)`. Проверяем оба
    конца: main вызывает init_pools, init_pools читает settings.introspect_pool_*.
    """
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

    import pathlib
    main_py = pathlib.Path(config.__file__).parent.parent / "main.py"
    main_text = main_py.read_text()
    # Lifespan дёргает init_pools(settings) — единая точка сборки пулов.
    assert "http_pool.init_pools(settings)" in main_text

    http_pool_py = pathlib.Path(config.__file__).parent.parent / "services" / "http_pool.py"
    pool_text = http_pool_py.read_text()
    # init_pools читает обе env-переменные из settings, без hardcode'а.
    assert "settings.introspect_pool_max_connections" in pool_text
    assert "settings.introspect_pool_max_keepalive" in pool_text

    # Удостоверяемся, что в блоке конструирования _introspect_client нет
    # старого hardcoded числа 20.
    import re
    introspect_block = re.search(
        r"auth_deps\._introspect_client = httpx\.AsyncClient\(.*?\)",
        pool_text,
        re.S,
    )
    assert introspect_block is not None
    assert "max_connections=20" not in introspect_block.group(0)

    # Возвращаем lru_cache к исходным значениям, чтобы не сломать соседние тесты.
    config.get_settings.cache_clear()
