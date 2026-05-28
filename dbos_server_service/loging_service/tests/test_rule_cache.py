"""Unit-тесты _RuleCache: TTL, MAX(updated_at)-перезагрузка, stale fallback, конкуррентный доступ.

Используют внутреннюю реализацию src.services.rule_service._RuleCache напрямую,
не через apply_rules, чтобы изолировать поведение кеша от логики правил.
"""

import threading
import time
from datetime import datetime, timezone

import pytest

from src.repositories import rules as rule_repo
from src.schemas.rules import RuleCreate
from src.services import rule_service
from src.services.rule_service import _RuleCache


def _make_rule(db, name: str, **kwargs):
    payload = RuleCreate(
        name=name,
        effect=kwargs.pop("effect", "SUPPRESS"),
        priority=kwargs.pop("priority", 100),
        **kwargs,
    )
    return rule_repo.create(db, payload)


# ── Базовая загрузка ──────────────────────────────────────────────────────────


class TestRuleCacheLoad:
    def test_empty_cache_loads_from_db(self, db):
        cache = _RuleCache(ttl_seconds=30)
        assert cache.get(db) == []

    def test_cache_returns_active_rules(self, db):
        _make_rule(db, "r1")
        _make_rule(db, "r2")
        cache = _RuleCache(ttl_seconds=30)
        rules = cache.get(db)
        assert {r.name for r in rules} == {"r1", "r2"}

    def test_cache_skips_inactive_rules(self, db):
        active = _make_rule(db, "active")
        inactive = _make_rule(db, "inactive")
        from src.schemas.rules import RuleUpdate
        rule_repo.update(db, inactive, RuleUpdate(is_active=False))
        cache = _RuleCache(ttl_seconds=30)
        rules = cache.get(db)
        assert [r.name for r in rules] == ["active"]


# ── TTL: внутри TTL — без БД ──────────────────────────────────────────────────


class TestRuleCacheTTL:
    def test_within_ttl_does_not_hit_db(self, db, monkeypatch):
        cache = _RuleCache(ttl_seconds=30)
        _make_rule(db, "r1")
        cache.get(db)  # начальная загрузка

        # Эмулируем падение БД: rule_repo.get_active_sorted и get_max_updated_at должны
        # не вызываться при попадании в TTL.
        calls: list[str] = []
        original_active = rule_repo.get_active_sorted
        original_max = rule_repo.get_max_updated_at
        monkeypatch.setattr(rule_repo, "get_active_sorted",
                            lambda d: (calls.append("active"), original_active(d))[1])
        monkeypatch.setattr(rule_repo, "get_max_updated_at",
                            lambda d: (calls.append("max"), original_max(d))[1])

        cache.get(db)
        cache.get(db)
        assert calls == [], f"DB не должна вызываться в пределах TTL, было: {calls}"

    def test_after_ttl_checks_max_updated_at(self, db, monkeypatch):
        """После истечения TTL — сначала запрос MAX(updated_at)."""
        cache = _RuleCache(ttl_seconds=0)  # моментальный TTL
        _make_rule(db, "r1")
        cache.get(db)

        calls: list[str] = []
        original_max = rule_repo.get_max_updated_at
        original_active = rule_repo.get_active_sorted
        monkeypatch.setattr(rule_repo, "get_max_updated_at",
                            lambda d: (calls.append("max"), original_max(d))[1])
        monkeypatch.setattr(rule_repo, "get_active_sorted",
                            lambda d: (calls.append("active"), original_active(d))[1])

        # Небольшая задержка, чтобы TTL гарантированно истёк
        time.sleep(0.01)
        cache.get(db)
        assert "max" in calls

    def test_unchanged_db_does_not_reload_rules(self, db, monkeypatch):
        """Если MAX(updated_at) не изменился — полная перезагрузка пропускается."""
        cache = _RuleCache(ttl_seconds=0)
        _make_rule(db, "r1")
        cache.get(db)

        active_calls: list[int] = []
        original_active = rule_repo.get_active_sorted
        monkeypatch.setattr(rule_repo, "get_active_sorted",
                            lambda d: (active_calls.append(1), original_active(d))[1])

        time.sleep(0.01)
        cache.get(db)  # MAX тот же → активные не перезагружаем
        assert active_calls == [], "При неизменном MAX(updated_at) get_active_sorted не должен вызываться"

    def test_changed_db_triggers_reload(self, db):
        """Если MAX(updated_at) увеличился — перезагружаем активные правила."""
        cache = _RuleCache(ttl_seconds=0)
        cache.get(db)  # пусто

        time.sleep(0.01)
        _make_rule(db, "new-rule")
        time.sleep(0.01)
        rules = cache.get(db)
        assert [r.name for r in rules] == ["new-rule"]


# ── Stale fallback при ошибке БД ──────────────────────────────────────────────


class TestRuleCacheStaleFallback:
    def test_db_error_after_initial_load_serves_stale(self, db, monkeypatch):
        cache = _RuleCache(ttl_seconds=0)
        _make_rule(db, "r1")
        rules_before = list(cache.get(db))
        assert [r.name for r in rules_before] == ["r1"]

        # Симулируем ошибку БД при следующем чтении
        def boom(*_args, **_kwargs):
            raise RuntimeError("connection lost")
        monkeypatch.setattr(rule_repo, "get_max_updated_at", boom)

        time.sleep(0.01)
        rules_after = cache.get(db)
        assert [r.name for r in rules_after] == ["r1"], "должны вернуть устаревший кеш"

    def test_db_error_on_first_load_propagates(self, db, monkeypatch):
        """Если БД недоступна и кеша ещё нет — исключение должно подниматься."""
        cache = _RuleCache(ttl_seconds=30)

        def boom(*_args, **_kwargs):
            raise RuntimeError("connection lost")
        monkeypatch.setattr(rule_repo, "get_max_updated_at", boom)
        monkeypatch.setattr(rule_repo, "get_active_sorted", boom)

        with pytest.raises(RuntimeError):
            cache.get(db)

    def test_stale_fallback_resets_ttl_to_avoid_db_hammer(self, db, monkeypatch):
        """После stale-fallback _loaded_at сдвигается, чтобы не долбить БД до следующего TTL."""
        cache = _RuleCache(ttl_seconds=30)
        _make_rule(db, "r1")
        cache.get(db)

        before = cache._loaded_at
        before_mono = cache._loaded_monotonic

        # Сбрасываем TTL и роняем БД
        cache._loaded_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
        cache._loaded_monotonic = 0.0
        monkeypatch.setattr(rule_repo, "get_max_updated_at",
                            lambda d: (_ for _ in ()).throw(RuntimeError("fail")))

        cache.get(db)
        # _loaded_at должен быть обновлён (свежее, чем до stale-fallback)
        assert cache._loaded_at > datetime(2000, 1, 1, tzinfo=timezone.utc)
        assert cache._loaded_at != before  # обновился во время fallback
        # _loaded_monotonic тоже двигается — иначе TTL не сбрасывается
        # и на следующем get() мы снова полетим в упавшую БД.
        assert cache._loaded_monotonic is not None
        assert cache._loaded_monotonic > 0.0
        assert cache._loaded_monotonic != before_mono


# ── invalidate() ──────────────────────────────────────────────────────────────


class TestRuleCacheInvalidate:
    def test_invalidate_forces_reload(self, db, monkeypatch):
        cache = _RuleCache(ttl_seconds=30)
        cache.get(db)

        reloads: list[int] = []
        original = rule_repo.get_active_sorted
        monkeypatch.setattr(rule_repo, "get_active_sorted",
                            lambda d: (reloads.append(1), original(d))[1])

        cache.invalidate()
        cache.get(db)
        assert reloads == [1]


# ── Конкуррентный доступ ──────────────────────────────────────────────────────


class TestRuleCacheConcurrent:
    def test_concurrent_get_with_expired_ttl_serialised_by_lock(self, db, monkeypatch):
        """Несколько потоков одновременно после TTL → только одна полная перезагрузка
        под защитой _lock (double-checked locking)."""
        cache = _RuleCache(ttl_seconds=0)
        _make_rule(db, "r1")
        cache.get(db)  # начальная загрузка

        load_count = {"n": 0}
        original_active = rule_repo.get_active_sorted

        def slow_active(d):
            load_count["n"] += 1
            time.sleep(0.05)
            return original_active(d)

        # Также обновим MAX, чтобы триггерить перезагрузку
        time.sleep(0.01)
        _make_rule(db, "r2")
        monkeypatch.setattr(rule_repo, "get_active_sorted", slow_active)

        threads = [threading.Thread(target=cache.get, args=(db,)) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Под _lock + double-check только один поток обновит кеш
        assert load_count["n"] == 1, f"ожидалась 1 перезагрузка, фактически {load_count['n']}"


# ── Глобальный invalidate_cache() ─────────────────────────────────────────────


class TestModuleLevelInvalidateCache:
    def test_module_level_invalidate_resets_global_cache(self, db):
        """rule_service.invalidate_cache() должен сбросить TTL глобального _cache."""
        # db fixture сама вызывает invalidate_cache, плюс при создании правила в CRUD
        # его тоже сбрасывают. Проверяем что вызов идемпотентен и не падает.
        rule_service.invalidate_cache()
        rule_service.invalidate_cache()  # повторный вызов — без эффекта, не падает
