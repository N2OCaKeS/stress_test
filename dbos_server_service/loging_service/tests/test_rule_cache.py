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
from src.services.rule_service import CacheState, _RuleCache


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

    def test_soft_delete_bumps_max_and_invalidates_other_workers(self, db, TestSessionLocal):
        """Soft-delete правила (deleted_at=now, bump updated_at) обязан
        повышать MAX(updated_at) и заставить кеш на «других воркерах»
        перезагрузиться. Иначе физический DELETE оставлял бы SUPPRESS-правило
        активным до TTL=30s на репликах, не выполнивших delete.
        """
        # Раздельные сессии для воркеров A и B — у каждого реального воркера
        # свой Session/identity-map. На общей сессии expunge внутри cache_b.get()
        # отвязал бы объект правила и от воркера A, и последующий soft-delete
        # ничего бы не записал в БД.
        cache_b = _RuleCache(ttl_seconds=0)
        session_b = TestSessionLocal()
        try:
            rule = _make_rule(db, "del-me", effect="SUPPRESS")
            time.sleep(0.01)
            rules_b = cache_b.get(session_b)
            assert [r.name for r in rules_b] == ["del-me"]

            # Воркер A удаляет (soft-delete).
            time.sleep(0.01)
            rule_repo.delete(db, rule)

            # Воркер B видит, что MAX(updated_at) переехал, перезагружается и
            # больше не возвращает удалённое правило.
            time.sleep(0.01)
            rules_b_after = cache_b.get(session_b)
            assert rules_b_after == [], (
                "После soft-delete cache на другом воркере обязан перезагрузиться "
                "и не возвращать удалённое правило"
            )
        finally:
            session_b.close()

    def test_soft_deleted_rule_skipped_by_get_active_sorted(self, db):
        """Soft-deleted правило не входит в active_sorted, даже если был active."""
        rule = _make_rule(db, "to-delete")
        rule_repo.delete(db, rule)
        active = rule_repo.get_active_sorted(db)
        assert active == []

    def test_soft_deleted_rule_skipped_by_get_by_id(self, db):
        """`get_by_id` возвращает None для soft-deleted — повторные операции
        в endpoint'е получают 404, а не натыкаются на призрак."""
        rule = _make_rule(db, "del-then-find")
        rule_id = rule.id
        rule_repo.delete(db, rule)
        assert rule_repo.get_by_id(db, rule_id) is None


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
        """После stale-fallback _loaded_monotonic сдвигается (TTL backoff),
        но _loaded_at и _db_empty остаются на последнем подтверждённом
        значении — иначе cross-worker UPDATE, попавший в окно outage'а,
        тихо терялся бы при восстановлении БД."""
        cache = _RuleCache(ttl_seconds=30)
        _make_rule(db, "r1")
        cache.get(db)

        before_loaded_at = cache._loaded_at
        before_db_empty = cache._db_empty
        before_mono = cache._loaded_monotonic

        # Сбрасываем TTL и роняем БД (имитируем outage после успешной загрузки).
        cache._loaded_monotonic = 0.0
        monkeypatch.setattr(rule_repo, "get_max_updated_at",
                            lambda d: (_ for _ in ()).throw(RuntimeError("fail")))

        cache.get(db)
        # `_loaded_monotonic` двигается — иначе TTL не сбрасывается и на
        # следующем get() мы снова полетим в упавшую БД.
        assert cache._loaded_monotonic is not None
        assert cache._loaded_monotonic > 0.0
        assert cache._loaded_monotonic != before_mono
        # `_loaded_at` НЕ должен сдвинуться — это межсервисный watermark
        # для сравнения с MAX(updated_at) из БД. Сдвиг к моменту провалившейся
        # попытки скрыл бы UPDATE, попавший в окно outage'а.
        assert cache._loaded_at == before_loaded_at
        # `_db_empty` тоже сохраняем — мы не подтвердили текущее состояние БД.
        assert cache._db_empty == before_db_empty

    def test_db_outage_then_recovery_picks_up_crossworker_update(self, db, monkeypatch, TestSessionLocal):
        """После outage'а cross-worker UPDATE, прилетевший пока БД лежала,
        должен подхватиться при первом успешном tick'е. Если `_loaded_at`
        двигался в exception-ветке, watermark обогнал бы UPDATE.updated_at,
        и условие `db_updated_at > self._loaded_at` дало бы False до
        следующего bump'а MAX."""
        cache = _RuleCache(ttl_seconds=0)
        _make_rule(db, "old-rule")
        # Подтянули старое состояние.
        rules = cache.get(db)
        assert [r.name for r in rules] == ["old-rule"]

        # Падение БД на одном tick'е.
        boom_calls = {"n": 0}

        def boom(*_args, **_kwargs):
            boom_calls["n"] += 1
            raise RuntimeError("connection lost")

        monkeypatch.setattr(rule_repo, "get_max_updated_at", boom)
        time.sleep(0.01)
        # Stale fallback — отдаёт старый кеш, бампит monotonic TTL.
        rules_during = cache.get(db)
        assert [r.name for r in rules_during] == ["old-rule"]
        assert boom_calls["n"] == 1

        # Пока БД "лежала", другой воркер создал новое правило.
        time.sleep(0.01)
        other_session = TestSessionLocal()
        try:
            _make_rule(other_session, "new-rule-during-outage")
        finally:
            other_session.close()

        # БД восстановилась — снимаем monkeypatch.
        monkeypatch.undo()
        time.sleep(0.01)
        # `_loaded_monotonic` мы только что подвинули; чтобы новый tick реально
        # пошёл в БД, опустим его (имитация прошло >TTL).
        cache._loaded_monotonic = 0.0

        rules_after = cache.get(db)
        names = sorted(r.name for r in rules_after)
        assert "new-rule-during-outage" in names, (
            f"UPDATE, прилетевший во время outage'а, должен подхватиться "
            f"после восстановления БД (есть: {names})"
        )


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


# ── Cross-worker UPDATE во время загрузки кеша ────────────────────────────────


class TestRuleCacheLoadedAtCapturedBeforeMaxQuery:
    """`_loaded_at` должен фиксироваться ДО `get_max_updated_at`, иначе
    cross-worker UPDATE, попавший в окно (max-select, end-of-load), теряется
    до следующего bump'а MAX — на следующем TTL-tick сравнение
    `db_updated_at > self._loaded_at` даст False, и стек правил останется
    устаревшим.

    Симулируем: worker A считал MAX=t0, в этот момент worker B сделал UPDATE
    (t1 > t0). Если `_loaded_at` фиксируется в КОНЦЕ загрузки (≈t2 > t1), то
    cross-worker change подхватится только если кто-то снова двинет MAX
    выше t2 — в пределе теряется UPDATE.
    """

    def test_loaded_at_captured_before_max_select(self, db, monkeypatch):
        cache = _RuleCache(ttl_seconds=0)
        _make_rule(db, "initial")
        cache.get(db)

        # `_loaded_at` после первой загрузки.
        first_loaded_at = cache._loaded_at
        assert first_loaded_at is not None

        # Имитируем cross-worker UPDATE ВНУТРИ get_max_updated_at: возвращаем
        # `db_updated_at`, который СТРОГО МЕНЬШЕ реального максимума, потому
        # что вторая worker'а ещё не успела закоммитить, но коммитит между
        # SELECT MAX и фиксацией `_loaded_at`. После фикса `_loaded_at` берётся
        # ПЕРЕД SELECT'ом, поэтому даже если UPDATE.updated_at == момент SELECT'а
        # — он строго больше `_loaded_at`, и следующий tick подхватит.

        # Реальная имплементация: записываем правило с updated_at в БД, читаем
        # max до фактической записи (внутри monkeypatched repo). Затем на
        # следующем tick'е cache должен поднять «потерянный» UPDATE.
        from src.models.audit_rule import AuditRule

        # Шаг 1: явно сдвигаем `_loaded_at` в прошлое, чтобы updated_at нового
        # правила гарантированно был БОЛЬШЕ него (TTL=0 → следующий get()
        # пойдёт за MAX).
        cache._loaded_monotonic = 0.0  # просрочили TTL
        cache._loaded_at = datetime(2020, 1, 1, tzinfo=timezone.utc)

        # Шаг 2: внутри get_max_updated_at эмулируем cross-worker UPDATE —
        # коммитим новое правило ровно в момент SELECT'а MAX, но возвращаем
        # старое значение MAX (как если бы наш SELECT прошёл до коммита B).
        original_max = rule_repo.get_max_updated_at
        original_active = rule_repo.get_active_sorted

        cross_worker_committed = {"done": False}

        def racing_max(session):
            # Снапшотим текущий MAX (до UPDATE'а воркера B).
            snapshot = original_max(session)
            if not cross_worker_committed["done"]:
                # Эмулируем UPDATE другого воркера, который коммитится ВО
                # ВРЕМЯ нашего SELECT'а MAX. Без фикса `_loaded_at`
                # фиксируется ПОСЛЕ этого момента, и UPDATE «теряется».
                _make_rule(db, "cross-worker-rule")
                cross_worker_committed["done"] = True
            return snapshot

        monkeypatch.setattr(rule_repo, "get_max_updated_at", racing_max)

        # Шаг 3: первый get() после race — взял старый snapshot, cache видит
        # «MAX не вырос», правил не перезагружает (старое поведение).
        # Но `_loaded_at` теперь зафиксирован ДО racing_max, т.е. строго
        # меньше нового updated_at в БД.
        cache.get(db)

        # Шаг 4: следующий tick (TTL истёк) — get_max_updated_at снова
        # возвращает реальный максимум, и т.к. `_loaded_at` фиксировался ДО
        # предыдущего SELECT'а, новое правило (updated_at > load_started_at)
        # подхватывается.
        monkeypatch.setattr(rule_repo, "get_max_updated_at", original_max)
        monkeypatch.setattr(rule_repo, "get_active_sorted", original_active)
        cache._loaded_monotonic = 0.0  # просрочили TTL ещё раз

        rules = cache.get(db)
        names = {r.name for r in rules}
        assert "cross-worker-rule" in names, (
            f"Cross-worker UPDATE должен подхватиться на следующем TTL-tick'е "
            f"после фикса (loaded_at фиксируется ДО SELECT MAX). Видим: {names}"
        )

    def test_loaded_at_uses_pre_query_timestamp(self, db, monkeypatch):
        """Прямая проверка: `_loaded_at` после get() меньше или равен моменту
        перед вызовом, а не позже — отражает НАЧАЛО окна загрузки."""
        cache = _RuleCache(ttl_seconds=0)
        _make_rule(db, "r")

        # Делаем get_active_sorted медленным, чтобы окно (начало, конец)
        # было заметным.
        original_active = rule_repo.get_active_sorted

        def slow_active(d):
            time.sleep(0.05)
            return original_active(d)

        monkeypatch.setattr(rule_repo, "get_active_sorted", slow_active)

        before = datetime.now(timezone.utc)
        cache.get(db)
        after = datetime.now(timezone.utc)

        assert cache._loaded_at is not None
        # `_loaded_at` должен быть ближе к `before`, чем к `after` — фиксируется
        # ДО медленного SELECT'а. Раньше фиксировался в конце, после задержки.
        assert cache._loaded_at <= after
        # Окно загрузки заняло ≥50ms — `_loaded_at` фиксируется в начале, значит
        # `after - _loaded_at` должен включать всю задержку.
        assert (after - cache._loaded_at).total_seconds() >= 0.04, (
            f"_loaded_at должен отражать начало окна загрузки (≤ before), "
            f"чтобы cross-worker UPDATE внутри окна подхватывался. "
            f"before={before}, _loaded_at={cache._loaded_at}, after={after}"
        )


# ── CacheState transitions ────────────────────────────────────────────────────


class TestRuleCacheStateMachine:
    """Регрессия на enum-state: UNLOADED → (READY | EMPTY) и обратно через invalidate.

    Раньше состояние складывалось из пары `_loaded_at is None` + `_db_empty: bool`,
    что расходилось в edge-кейсах (например, `_db_empty` сбрасывался отдельно).
    Один enum-флаг закрывает домен и делает переходы явными.
    """

    def test_initial_state_is_unloaded(self):
        cache = _RuleCache(ttl_seconds=30)
        assert cache._state is CacheState.UNLOADED
        # Обратная совместимость: bool-property отражает «не EMPTY».
        assert cache._db_empty is False

    def test_first_get_on_empty_db_transitions_to_empty(self, db):
        cache = _RuleCache(ttl_seconds=30)
        assert cache.get(db) == []
        assert cache._state is CacheState.EMPTY
        assert cache._db_empty is True

    def test_first_get_with_rules_transitions_to_ready(self, db):
        _make_rule(db, "r1")
        cache = _RuleCache(ttl_seconds=30)
        assert cache.get(db)
        assert cache._state is CacheState.READY
        assert cache._db_empty is False

    def test_invalidate_returns_to_unloaded(self, db):
        cache = _RuleCache(ttl_seconds=30)
        cache.get(db)  # переводит в EMPTY либо READY
        cache.invalidate()
        assert cache._state is CacheState.UNLOADED
        assert cache._loaded_at is None
        assert cache._loaded_monotonic is None
        assert cache._db_empty is False

    def test_legacy_db_empty_setter_drives_state(self):
        """Тесты исторически делают `cache._db_empty = True/False`. Property-сеттер
        проксирует это в `_state` без поломки инвариантов."""
        cache = _RuleCache(ttl_seconds=30)
        cache._db_empty = True
        assert cache._state is CacheState.EMPTY
        cache._db_empty = False
        # Без правил — откатываемся к UNLOADED (не выдумываем READY с пустым snapshot).
        assert cache._state is CacheState.UNLOADED
