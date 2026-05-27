"""Identity-кэш ограничен по размеру (maxsize) — не растёт неограниченно.

При burst'е уникальных коротко-живущих токенов (PAT/bot per-request) кэш
без cap'а рос бы до OOM. Проверяем, что превышение `_IDENTITY_CACHE_MAXSIZE`
вытесняет старые записи, число entries не превышает потолок, а самые
недавно прочитанные записи переживают eviction (LRU-recency).
"""

import pytest

from src.dependencies import auth as auth_dep
from src.schemas.auth import IdentityContext


def _identity(uid: str) -> IdentityContext:
    return IdentityContext(
        user_id=uid,
        username=uid,
        department_id="dep_x",
        allowed_services=[],
        service_roles={},
        platform_role=None,
        subject_type="user",
    )


@pytest.fixture()
def small_cache(monkeypatch):
    """TTL>0 + маленький maxsize, чистый кэш до/после теста."""
    monkeypatch.setattr(auth_dep, "_IDENTITY_CACHE_TTL_SECONDS", 60.0)
    monkeypatch.setattr(auth_dep, "_IDENTITY_CACHE_MAXSIZE", 5)
    auth_dep._identity_cache_clear()
    yield
    auth_dep._identity_cache_clear()


class TestIdentityCacheBounded:
    def test_cache_never_exceeds_maxsize(self, small_cache):
        """Кладём 1000 уникальных токенов → кэш держит ровно maxsize."""
        for i in range(1000):
            auth_dep._identity_cache_put(f"token-{i}", _identity(f"usr_{i}"))
        assert len(auth_dep._identity_cache) == auth_dep._IDENTITY_CACHE_MAXSIZE

    def test_oldest_entries_evicted_first(self, small_cache):
        """FIFO-eviction: самые ранние токены вытесняются, последние остаются."""
        for i in range(8):  # maxsize=5 → 0..2 должны выпасть
            auth_dep._identity_cache_put(f"tok-{i}", _identity(f"usr_{i}"))
        assert auth_dep._identity_cache_get("tok-0") is None
        assert auth_dep._identity_cache_get("tok-2") is None
        # Последние 5 (3..7) должны быть живы.
        for i in range(3, 8):
            cached = auth_dep._identity_cache_get(f"tok-{i}")
            assert cached is not None
            assert cached.user_id == f"usr_{i}"

    def test_recent_read_survives_eviction(self, small_cache):
        """Cache hit двигает запись в конец → она переживает последующий рост."""
        for i in range(5):  # заполнили под завязку (maxsize=5)
            auth_dep._identity_cache_put(f"k-{i}", _identity(f"usr_{i}"))
        # Читаем самую старую — она «освежается».
        assert auth_dep._identity_cache_get("k-0") is not None
        # Добавляем новый токен — eviction должен выбросить k-1 (теперь самый
        # старый), а не недавно прочитанный k-0.
        auth_dep._identity_cache_put("k-new", _identity("usr_new"))
        assert auth_dep._identity_cache_get("k-0") is not None
        assert auth_dep._identity_cache_get("k-1") is None

    def test_disabled_cache_stays_empty(self, monkeypatch):
        """TTL<=0 → put это no-op, кэш не растёт вовсе."""
        monkeypatch.setattr(auth_dep, "_IDENTITY_CACHE_TTL_SECONDS", 0.0)
        auth_dep._identity_cache_clear()
        for i in range(100):
            auth_dep._identity_cache_put(f"t-{i}", _identity(f"usr_{i}"))
        assert len(auth_dep._identity_cache) == 0
        auth_dep._identity_cache_clear()
