"""Stress / edge-кейсы для `_RuleCache`.

Покрывает пропуски из TEST_COVERAGE.md:
* TTL=1 сек поведение,
* 100+ параллельных `get()` с одним «холодным» кешем — ровно одна перезагрузка,
* `get()` параллельно с `invalidate()` — не зависает,
* memory leak: повторная загрузка не накапливает _rules,
* поведение `get_max_updated_at` на пустой БД (None) — кеш просто продлевает TTL.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest

from src.repositories import rules as rule_repo
from src.schemas.rules import RuleCreate
from src.services.rule_service import _RuleCache


def _seed(db, name: str):
    rule_repo.create(db, RuleCreate(name=name, effect="SUPPRESS", priority=100))


# ── TTL короче секунды ───────────────────────────────────────────────────────

class TestShortTTL:
    def test_one_second_ttl_reloads_after_sleep(self, db, monkeypatch):
        cache = _RuleCache(ttl_seconds=1)
        _seed(db, "first")
        first_call = cache.get(db)
        assert {r.name for r in first_call} == {"first"}

        # Подсчитываем количество запросов к БД
        calls = {"max_updated": 0, "active_sorted": 0}
        orig_max = rule_repo.get_max_updated_at
        orig_sorted = rule_repo.get_active_sorted

        def count_max(*a, **kw):
            calls["max_updated"] += 1
            return orig_max(*a, **kw)

        def count_sorted(*a, **kw):
            calls["active_sorted"] += 1
            return orig_sorted(*a, **kw)

        monkeypatch.setattr(rule_repo, "get_max_updated_at", count_max)
        monkeypatch.setattr(rule_repo, "get_active_sorted", count_sorted)

        time.sleep(1.1)
        _seed(db, "second")
        second_call = cache.get(db)
        assert {r.name for r in second_call} == {"first", "second"}
        assert calls["max_updated"] == 1
        assert calls["active_sorted"] == 1


# ── Параллельный get на холодном кеше ────────────────────────────────────────

class TestConcurrentGet:
    def test_100_concurrent_gets_reload_db_at_most_once(self, db, monkeypatch):
        """100 потоков одновременно зовут get() — благодаря лок'у БД
        перезагружается единожды."""
        for i in range(3):
            _seed(db, f"r{i}")

        cache = _RuleCache(ttl_seconds=30)
        load_count = {"n": 0}
        orig = rule_repo.get_active_sorted

        def counted(*a, **kw):
            load_count["n"] += 1
            time.sleep(0.05)  # удерживаем лок дольше, чтобы конкуренция была реальной
            return orig(*a, **kw)

        monkeypatch.setattr(rule_repo, "get_active_sorted", counted)

        from sqlalchemy.orm import sessionmaker
        ThreadSession = sessionmaker(bind=db.bind, autocommit=False, autoflush=False)

        def worker():
            with ThreadSession() as session:
                return cache.get(session)

        with ThreadPoolExecutor(max_workers=20) as ex:
            futures = [ex.submit(worker) for _ in range(100)]
            results = [f.result() for f in as_completed(futures)]

        # Все 100 вызовов получили одинаковый набор правил
        names = [{r.name for r in res} for res in results]
        assert all(n == {"r0", "r1", "r2"} for n in names)
        # БД дёрнули ровно один раз благодаря лок'у
        assert load_count["n"] == 1

    def test_invalidate_while_get_in_progress_does_not_deadlock(self, db, monkeypatch):
        """Параллельно: get() удерживает лок → invalidate() ждёт → отрабатывает."""
        _seed(db, "r1")
        cache = _RuleCache(ttl_seconds=30)

        block = threading.Event()
        orig = rule_repo.get_active_sorted

        def slow(*a, **kw):
            block.wait(timeout=2.0)
            return orig(*a, **kw)

        monkeypatch.setattr(rule_repo, "get_active_sorted", slow)

        from sqlalchemy.orm import sessionmaker
        ThreadSession = sessionmaker(bind=db.bind, autocommit=False, autoflush=False)

        with ThreadSession() as inv_session:
            getter_done = threading.Event()

            def getter():
                with ThreadSession() as s:
                    cache.get(s)
                getter_done.set()

            t = threading.Thread(target=getter)
            t.start()
            time.sleep(0.1)
            # Освобождаем «медленную» загрузку и ждём, что getter завершится.
            block.set()
            t.join(timeout=3.0)
            assert getter_done.is_set(), "getter must finish — no deadlock"
            # invalidate теперь должен пройти моментально (без БД)
            start = time.perf_counter()
            cache.invalidate()
            assert time.perf_counter() - start < 0.5


# ── Memory leak / re-load ────────────────────────────────────────────────────

class TestNoMemoryLeak:
    def test_repeated_invalidate_get_does_not_grow_internal_list(self, db, monkeypatch):
        _seed(db, "r1")
        _seed(db, "r2")
        cache = _RuleCache(ttl_seconds=30)
        for _ in range(20):
            cache.invalidate()
            cache.get(db)
        assert len(cache._rules) == 2


# ── get_max_updated_at(None) ─────────────────────────────────────────────────

class TestEmptyDb:
    def test_first_load_on_empty_db_sets_loaded_at(self, db):
        cache = _RuleCache(ttl_seconds=30)
        before = cache._loaded_at
        assert before is None
        assert cache.get(db) == []
        assert cache._loaded_at is not None

    def test_no_change_in_db_keeps_existing_rules(self, db, monkeypatch):
        """Если MAX(updated_at) не изменился — `_rules` не перезагружаются."""
        _seed(db, "r1")
        cache = _RuleCache(ttl_seconds=0)  # сразу истёкший TTL
        first = cache.get(db)
        first_obj = first[0]

        load_count = {"n": 0}
        orig = rule_repo.get_active_sorted

        def counted(*a, **kw):
            load_count["n"] += 1
            return orig(*a, **kw)

        monkeypatch.setattr(rule_repo, "get_active_sorted", counted)

        # повторный get — БД не изменилась с предыдущей загрузки → перезагрузки нет
        time.sleep(0.01)
        second = cache.get(db)
        assert second[0] is first_obj
        assert load_count["n"] == 0


# ── Stale fallback (DB-сбой → старый снепшот) ────────────────────────────────

class TestStaleFallback:
    def test_db_failure_after_first_load_serves_stale(self, db, monkeypatch, caplog):
        _seed(db, "r1")
        cache = _RuleCache(ttl_seconds=0)
        cache.get(db)  # первая успешная загрузка

        def boom(*a, **kw):
            raise RuntimeError("DB down")

        monkeypatch.setattr(rule_repo, "get_max_updated_at", boom)

        result = cache.get(db)
        # старый снепшот
        assert [r.name for r in result] == ["r1"]

    def test_db_failure_on_first_load_raises(self, db, monkeypatch):
        """Если кеш ещё ни разу не успел загрузиться — exception пробрасывается."""
        cache = _RuleCache(ttl_seconds=30)

        def boom(*a, **kw):
            raise RuntimeError("DB down")

        monkeypatch.setattr(rule_repo, "get_max_updated_at", boom)
        with pytest.raises(RuntimeError):
            cache.get(db)
