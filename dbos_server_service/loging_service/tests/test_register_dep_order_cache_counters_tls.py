"""Anti-drift фиксы loging: dependency-order
для register_events rate-limit + observability-counters для `_RuleCache`
+ TLS CA-bundle Settings-поле.

Контракты:

1. **Dependency order для `_register_events_rate_limit_key`**. Ключ-функция
   читает `request.state.service_identity`, который выставляет
   `require_service_token`. FastAPI выполняет route-level `dependencies=[]`
   ДО body-execution, поэтому декоратор `@limiter.limit(..., key_func=...)`
   получает state уже инициализированным. Контракт неявный — если кто-то
   уберёт `Depends(require_service_token)` из `dependencies=[]` (например,
   переедет в параметр функции), rate-limit-key схлопнется до IP-fallback
   и bucket'ы всех internal caller'ов сольются в один.

2. **`_RuleCache` counters**. `get_cache_counters()` возвращает snapshot
   `{hits, misses, stale_serves}`. Hot-path TTL-hit инкрементит hits,
   slow-path с DB-touch'ем — misses, DB-fail на slow-path — stale_serves.

3. **`introspect_tls_ca_bundle` Settings-поле**. None (default) — bundle
   не подменяет httpx-verify; путь к PEM — переопределяет `verify=True`
   на путь к bundle. При `introspect_tls_verify=False` bundle игнорируется
   (verify полностью off — поведение httpx).
"""

from __future__ import annotations

from fastapi.routing import APIRoute

from src.api.v1.endpoints.services import router as services_router
from src.core.config import Settings
from src.dependencies.auth import require_service_token
from src.services import rule_service


# ── 1. register_events: dependency order ─────────────────────────────────────


class TestRegisterEventsDependencyOrder:
    """Если `require_service_token` уедет из route-level `dependencies=[]`,
    ключ-функция rate-limit'а схлопнется на IP-fallback (см.
    `endpoints/services.py::_register_events_rate_limit_key`)."""

    def _register_route(self) -> APIRoute:
        for route in services_router.routes:
            if (
                isinstance(route, APIRoute)
                and route.path == "/{service}/events"
                and "POST" in route.methods
            ):
                return route
        raise AssertionError("POST /{service}/events route not found on services_router")

    def test_require_service_token_in_route_dependencies(self) -> None:
        route = self._register_route()
        # `route.dependencies` хранит `Depends(callable)`; разворачиваем
        # `.dependency` и сравниваем по callable identity.
        callables = [d.dependency for d in route.dependencies]
        assert require_service_token in callables, (
            "require_service_token должен быть в route-level dependencies, "
            "иначе rate-limit key_func сольётся на IP-fallback "
            "(см. _register_events_rate_limit_key)"
        )

    def test_route_dependency_runs_before_endpoint(self) -> None:
        """Route-level `dependencies=[]` — единственная гарантия, что
        `require_service_token` отработает до того, как `@limiter.limit`
        прочитает `request.state.service_identity`. Sanity: список не пустой
        и его первый/единственный элемент — наш guard."""
        route = self._register_route()
        assert len(route.dependencies) >= 1
        first = route.dependencies[0].dependency
        assert first is require_service_token


# ── 2. _RuleCache observability counters ─────────────────────────────────────


class TestRuleCacheCounters:
    def setup_method(self) -> None:
        # Сбрасываем счётчики и кеш на новый экземпляр для каждого теста.
        rule_service._cache.invalidate()
        rule_service._cache._hits = 0
        rule_service._cache._misses = 0
        rule_service._cache._stale_serves = 0

    def test_get_cache_counters_returns_all_three_fields(self) -> None:
        snap = rule_service.get_cache_counters()
        assert set(snap) == {"hits", "misses", "stale_serves"}
        assert all(isinstance(v, int) for v in snap.values())

    def test_ttl_hit_increments_hits(self, db) -> None:
        # Первый вызов — slow-path (UNLOADED → load → READY).
        rule_service._cache.get(db)
        before = rule_service.get_cache_counters()
        # Второй сразу — TTL fresh, fast-path.
        rule_service._cache.get(db)
        after = rule_service.get_cache_counters()
        assert after["hits"] == before["hits"] + 1
        assert after["misses"] == before["misses"]

    def test_invalidate_then_get_increments_misses(self, db) -> None:
        rule_service._cache.get(db)
        rule_service._cache.invalidate()
        before = rule_service.get_cache_counters()
        rule_service._cache.get(db)
        after = rule_service.get_cache_counters()
        assert after["misses"] == before["misses"] + 1


# ── 3. introspect_tls_ca_bundle Settings-поле ────────────────────────────────


class TestIntrospectTlsCaBundleField:
    def test_default_is_none(self, monkeypatch) -> None:
        monkeypatch.delenv("INTROSPECT_TLS_CA_BUNDLE", raising=False)
        s = Settings(_env_file=None)
        assert s.introspect_tls_ca_bundle is None

    def test_accepts_path_string(self, monkeypatch) -> None:
        monkeypatch.setenv(
            "INTROSPECT_TLS_CA_BUNDLE", "/etc/ssl/corp-ca.pem"
        )
        s = Settings(_env_file=None)
        assert s.introspect_tls_ca_bundle == "/etc/ssl/corp-ca.pem"
