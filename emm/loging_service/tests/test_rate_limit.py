"""Тесты: per-IP rate-limit на ingest POST /api/logging/v1/events.

Фиксит: скомпрометированный shared SERVICE_API_KEY → DB flood. Лимит per-IP
читается из ``settings.ingest_rate_limit`` (default ``120/second``); SlowAPI
держит счётчики в in-memory storage, который мы ресетим перед каждым тестом.

См. ``loging_service/src/main.py::limiter`` и
``loging_service/src/api/v1/endpoints/events.py::create_event``.
"""

import pytest

from tests.conftest import make_event, make_event_def


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Каждый тест стартует с чистым in-memory bucket'ом.

    Без сброса первый тест выжрёт лимит, а второй увидит 429 на первом же
    запросе — TestClient ходит от ``127.0.0.1`` для всех тестов в сессии.
    Reset выполняется и до, и после: до — на случай если предыдущий test_*-файл
    оставил мусор в storage; после — чтобы не утекать ratе-state в test_ingest.
    """
    from src.main import limiter
    limiter.reset()
    yield
    limiter.reset()


class TestRateLimitIngest:
    def test_under_limit_succeeds(self, client, auth_headers):
        """Здоровый трафик в пределах лимита — все 201."""
        for i in range(10):
            resp = client.post(
                "/api/logging/v1/events", json=make_event(), headers=auth_headers
            )
            assert resp.status_code == 201, f"request #{i} got {resp.status_code}: {resp.text}"

    def test_burst_triggers_429(self, client, auth_headers, monkeypatch):
        """Override лимита на компактное значение → 101-й запрос отбивается 429."""
        # Понижаем лимит чтобы тест не делал 100+ полных insert'ов.
        monkeypatch.setenv("INGEST_RATE_LIMIT", "5/minute")
        from src.core.config import get_settings
        get_settings.cache_clear()
        # Reset limiter после изменения настроек — кэш view limits в slowapi
        # держится по route_handler, лимит-строка вычисляется заново при каждом
        # обращении (мы передали callable, не строку).
        from src.main import limiter
        limiter.reset()

        # Первые 5 — 201.
        for i in range(5):
            resp = client.post(
                "/api/logging/v1/events", json=make_event(), headers=auth_headers
            )
            assert resp.status_code == 201, f"request #{i} got {resp.status_code}"

        # 6-й — 429.
        resp = client.post(
            "/api/logging/v1/events", json=make_event(), headers=auth_headers
        )
        assert resp.status_code == 429, f"expected 429, got {resp.status_code}: {resp.text}"

        # Envelope соответствует общему формату ошибок loging_service.
        body = resp.json()
        assert body["error"] == "too_many_requests"
        assert body["error_code"] == "RATE_LIMIT_EXCEEDED"
        assert "request_id" in body
        assert "timestamp" in body
        assert "limit" in body.get("details", {})
        # Retry-After заголовок — для совместимости с RFC 6585.
        assert resp.headers.get("Retry-After") == "60"

    def test_limit_blocks_past_cap(self, client, auth_headers, monkeypatch):
        """Запрос сверх настроенного cap'а с одного IP отбивается 429.

        Лимит понижаем env-override'ом до ``10/minute``, чтобы проверять
        срабатывание cap'а без сотни полных INSERT'ов и без привязки к
        конкретному дефолту.
        """
        monkeypatch.setenv("INGEST_RATE_LIMIT", "10/minute")
        from src.core.config import get_settings
        get_settings.cache_clear()
        from src.main import limiter
        limiter.reset()

        for i in range(10):
            resp = client.post(
                "/api/logging/v1/events", json=make_event(), headers=auth_headers
            )
            assert resp.status_code == 201, f"request #{i} got {resp.status_code}"

        # 11-й — 429.
        resp = client.post(
            "/api/logging/v1/events", json=make_event(), headers=auth_headers
        )
        assert resp.status_code == 429
        assert resp.json()["error_code"] == "RATE_LIMIT_EXCEEDED"


class TestIngestRateLimitPerServiceIdentity:
    """`POST /events` ключует rate-limit по `X-Service-Identity`, не по IP.

    За k8s ingress весь ingest приходит с одного IP — общий per-IP bucket дал бы
    одному флудящему сервису выжать бюджет остальных. Bucket'ы независимы между
    identity'ями, с fallback на IP при отсутствии header'а.
    """

    def test_different_identities_have_independent_buckets(
        self, client, auth_headers, monkeypatch
    ):
        monkeypatch.setenv("INGEST_RATE_LIMIT", "2/minute")
        from src.core.config import get_settings
        get_settings.cache_clear()
        from src.main import limiter
        limiter.reset()

        headers_auth = {**auth_headers, "X-Service-Identity": "auth_service"}
        headers_server = {**auth_headers, "X-Service-Identity": "server_service"}

        # Выжимаем бюджет auth_service.
        for i in range(2):
            r = client.post(
                "/api/logging/v1/events", json=make_event(), headers=headers_auth
            )
            assert r.status_code == 201, f"auth #{i} got {r.status_code}: {r.text}"
        # 3-й от auth_service — 429.
        r = client.post(
            "/api/logging/v1/events", json=make_event(), headers=headers_auth
        )
        assert r.status_code == 429

        # server_service не тронут — его bucket независим.
        for i in range(2):
            r = client.post(
                "/api/logging/v1/events",
                json=make_event(service="server_service"),
                headers=headers_server,
            )
            assert r.status_code == 201, (
                f"server #{i} got {r.status_code}: {r.text}"
            )

    def test_same_identity_shares_bucket(self, client, auth_headers, monkeypatch):
        monkeypatch.setenv("INGEST_RATE_LIMIT", "2/minute")
        from src.core.config import get_settings
        get_settings.cache_clear()
        from src.main import limiter
        limiter.reset()

        headers = {**auth_headers, "X-Service-Identity": "auth_service"}
        for i in range(2):
            r = client.post(
                "/api/logging/v1/events", json=make_event(), headers=headers
            )
            assert r.status_code == 201, f"#{i} got {r.status_code}: {r.text}"
        r = client.post(
            "/api/logging/v1/events", json=make_event(), headers=headers
        )
        assert r.status_code == 429

    def test_normalized_identity_shares_bucket(
        self, client, auth_headers, monkeypatch
    ):
        """`AUTH_SERVICE` и `auth_service` → один bucket (нельзя обойти casing'ом)."""
        monkeypatch.setenv("INGEST_RATE_LIMIT", "2/minute")
        from src.core.config import get_settings
        get_settings.cache_clear()
        from src.main import limiter
        limiter.reset()

        lower = {**auth_headers, "X-Service-Identity": "auth_service"}
        upper = {**auth_headers, "X-Service-Identity": "AUTH_SERVICE"}

        r = client.post("/api/logging/v1/events", json=make_event(), headers=lower)
        assert r.status_code == 201
        r = client.post("/api/logging/v1/events", json=make_event(), headers=upper)
        assert r.status_code == 201
        # 3-й (тот же нормализованный bucket) — 429.
        r = client.post("/api/logging/v1/events", json=make_event(), headers=lower)
        assert r.status_code == 429

    def test_missing_identity_falls_back_to_ip(
        self, client, auth_headers, monkeypatch
    ):
        """Без header'а (legacy single-key) — fallback на per-IP, лимит работает."""
        monkeypatch.setenv("INGEST_RATE_LIMIT", "2/minute")
        from src.core.config import get_settings
        get_settings.cache_clear()
        from src.main import limiter
        limiter.reset()

        for i in range(2):
            r = client.post(
                "/api/logging/v1/events", json=make_event(), headers=auth_headers
            )
            assert r.status_code == 201, f"#{i} got {r.status_code}: {r.text}"
        r = client.post(
            "/api/logging/v1/events", json=make_event(), headers=auth_headers
        )
        assert r.status_code == 429


class TestRateLimitDoesNotAffectHealth:
    """Health probes (`/health`, `/ready`) НЕ должны попадать под лимит.

    Throttling probes → Kubernetes сбросит pod → бесконечный crash loop при
    атаке на ingest. Это основная причина почему key_func возвращает
    уникальный ключ для exempt-путей.
    """

    def test_health_never_rate_limited(self, client, monkeypatch):
        monkeypatch.setenv("INGEST_RATE_LIMIT", "1/minute")
        from src.core.config import get_settings
        get_settings.cache_clear()
        from src.main import limiter
        limiter.reset()

        # 200 health-probes подряд — все 200.
        for i in range(200):
            resp = client.get("/api/logging/v1/health")
            assert resp.status_code == 200, f"probe #{i} got {resp.status_code}"

    def test_ready_never_rate_limited(self, client, monkeypatch):
        monkeypatch.setenv("INGEST_RATE_LIMIT", "1/minute")
        from src.core.config import get_settings
        get_settings.cache_clear()
        from src.main import limiter
        limiter.reset()

        # /ready дёргает БД, делаем меньше итераций — но всё равно намного
        # больше дефолтного лимита.
        for i in range(50):
            resp = client.get("/api/logging/v1/ready")
            assert resp.status_code == 200, f"probe #{i} got {resp.status_code}"

    def test_health_does_not_consume_ingest_budget(self, client, auth_headers, monkeypatch):
        """Probes не вычитаются из ingest-бюджета (отдельные key-buckets)."""
        monkeypatch.setenv("INGEST_RATE_LIMIT", "3/minute")
        from src.core.config import get_settings
        get_settings.cache_clear()
        from src.main import limiter
        limiter.reset()

        # 50 health-probes — бюджет ingest нетронут.
        for _ in range(50):
            assert client.get("/api/logging/v1/health").status_code == 200

        # Все 3 ingest'а проходят.
        for i in range(3):
            resp = client.post(
                "/api/logging/v1/events", json=make_event(), headers=auth_headers
            )
            assert resp.status_code == 201, f"ingest #{i} got {resp.status_code}"

        # 4-й — 429.
        resp = client.post(
            "/api/logging/v1/events", json=make_event(), headers=auth_headers
        )
        assert resp.status_code == 429


class TestRateLimitConfig:
    """Конфигурируемость лимита через ``INGEST_RATE_LIMIT`` env."""

    def test_default_value(self, monkeypatch):
        """Default — ``120/second`` (без env override)."""
        monkeypatch.delenv("INGEST_RATE_LIMIT", raising=False)
        from src.core.config import get_settings
        get_settings.cache_clear()
        settings = get_settings()
        assert settings.ingest_rate_limit == "120/second"

    def test_override_via_env(self, monkeypatch):
        monkeypatch.setenv("INGEST_RATE_LIMIT", "500/hour")
        from src.core.config import get_settings
        get_settings.cache_clear()
        settings = get_settings()
        assert settings.ingest_rate_limit == "500/hour"


# ── POST /services/{service}/events rate-limit ──────────────────────────────


SERVICES_URL = "/api/logging/v1/services"


class TestRegisterEventsRateLimit:
    """Per-service-identity rate-limit на batch ingest канале.

    Фиксит: до этого batch-канал не имел rate-limit — держатель
    ``SERVICE_API_KEY`` мог дудосить registration через
    ``POST /services/{service}/events``, который принимает каталог из 1000
    action'ов одним запросом (× 100 RPS = 100k upsert'ов/s).

    Key — ``X-Service-Identity`` (не IP), потому что внутренний трафик идёт
    через один k8s ingress nginx (одинаковый IP). Bucket'ы независимы между
    identity'ями.
    """

    def test_under_limit_succeeds(self, client, auth_headers):
        """Здоровый трафик в пределах лимита — все 200."""
        headers = {**auth_headers, "X-Service-Identity": "auth_service"}
        for i in range(5):
            resp = client.post(
                f"{SERVICES_URL}/auth_service/events",
                json={"events": [make_event_def(action=f"user.action{i}")]},
                headers=headers,
            )
            assert resp.status_code == 200, (
                f"request #{i} got {resp.status_code}: {resp.text}"
            )

    def test_burst_triggers_429_for_same_identity(
        self, client, auth_headers, monkeypatch
    ):
        """5-й запрос от одной X-Service-Identity → 429 (override на 4/minute)."""
        monkeypatch.setenv("REGISTER_EVENTS_RATE_LIMIT", "4/minute")
        from src.core.config import get_settings
        get_settings.cache_clear()
        from src.main import limiter
        limiter.reset()

        headers = {**auth_headers, "X-Service-Identity": "auth_service"}
        # Первые 4 — 200.
        for i in range(4):
            resp = client.post(
                f"{SERVICES_URL}/auth_service/events",
                json={"events": [make_event_def(action=f"user.event{i}")]},
                headers=headers,
            )
            assert resp.status_code == 200, (
                f"request #{i} got {resp.status_code}: {resp.text}"
            )

        # 5-й — 429.
        resp = client.post(
            f"{SERVICES_URL}/auth_service/events",
            json={"events": [make_event_def(action="user.event_over")]},
            headers=headers,
        )
        assert resp.status_code == 429, (
            f"expected 429, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert body["error"] == "too_many_requests"
        assert body["error_code"] == "RATE_LIMIT_EXCEEDED"
        assert resp.headers.get("Retry-After") == "60"

    def test_different_identities_have_independent_buckets(
        self, client, auth_headers, monkeypatch
    ):
        """auth_service и server_service key'ятся отдельно — burst по одному
        не выжимает бюджет другого.
        """
        monkeypatch.setenv("REGISTER_EVENTS_RATE_LIMIT", "2/minute")
        from src.core.config import get_settings
        get_settings.cache_clear()
        from src.main import limiter
        limiter.reset()

        auth_headers_auth = {
            **auth_headers,
            "X-Service-Identity": "auth_service",
        }
        auth_headers_server = {
            **auth_headers,
            "X-Service-Identity": "server_service",
        }

        # Выжимаем бюджет auth_service.
        for i in range(2):
            r = client.post(
                f"{SERVICES_URL}/auth_service/events",
                json={"events": [make_event_def(action=f"a.{i}")]},
                headers=auth_headers_auth,
            )
            assert r.status_code == 200
        # 3-й от auth_service — 429.
        r = client.post(
            f"{SERVICES_URL}/auth_service/events",
            json={"events": [make_event_def(action="a.over")]},
            headers=auth_headers_auth,
        )
        assert r.status_code == 429

        # server_service должен пройти — его bucket не тронут.
        for i in range(2):
            r = client.post(
                f"{SERVICES_URL}/server_service/events",
                json={"events": [make_event_def(action=f"s.{i}")]},
                headers=auth_headers_server,
            )
            assert r.status_code == 200, (
                f"server_service request #{i} got {r.status_code}: {r.text}"
            )

    def test_normalized_identity_shares_bucket(
        self, client, auth_headers, monkeypatch
    ):
        """``AUTH_SERVICE`` и ``auth_service`` нормализуются к одному key —
        атакующий не может обойти лимит ротацией casing'а.
        """
        monkeypatch.setenv("REGISTER_EVENTS_RATE_LIMIT", "2/minute")
        from src.core.config import get_settings
        get_settings.cache_clear()
        from src.main import limiter
        limiter.reset()

        headers_lower = {**auth_headers, "X-Service-Identity": "auth_service"}
        headers_upper = {**auth_headers, "X-Service-Identity": "AUTH_SERVICE"}

        # Первый запрос — нижний регистр.
        r = client.post(
            f"{SERVICES_URL}/auth_service/events",
            json={"events": [make_event_def(action="a.1")]},
            headers=headers_lower,
        )
        assert r.status_code == 200
        # Второй — верхний регистр (нормализуется в тот же bucket).
        r = client.post(
            f"{SERVICES_URL}/auth_service/events",
            json={"events": [make_event_def(action="a.2")]},
            headers=headers_upper,
        )
        assert r.status_code == 200
        # Третий — снова нижний регистр. После нормализации это 3-й запрос
        # в том же bucket'е → 429.
        r = client.post(
            f"{SERVICES_URL}/auth_service/events",
            json={"events": [make_event_def(action="a.3")]},
            headers=headers_lower,
        )
        assert r.status_code == 429

    def test_missing_identity_falls_back_to_ip(
        self, client, auth_headers, monkeypatch
    ):
        """Без header'а (soft mode legacy single-key) — fallback на per-IP.
        Лимит всё равно работает, но bucket общий для всех caller'ов без
        identity. Reproducibility важна: даже без header нельзя задудосить.
        """
        monkeypatch.setenv("REGISTER_EVENTS_RATE_LIMIT", "3/minute")
        from src.core.config import get_settings
        get_settings.cache_clear()
        from src.main import limiter
        limiter.reset()

        # 3 запроса без X-Service-Identity → 200.
        for i in range(3):
            r = client.post(
                f"{SERVICES_URL}/auth_service/events",
                json={"events": [make_event_def(action=f"a.{i}")]},
                headers=auth_headers,
            )
            assert r.status_code == 200, (
                f"request #{i} got {r.status_code}: {r.text}"
            )
        # 4-й — 429.
        r = client.post(
            f"{SERVICES_URL}/auth_service/events",
            json={"events": [make_event_def(action="a.over")]},
            headers=auth_headers,
        )
        assert r.status_code == 429

    def test_get_endpoints_not_rate_limited(
        self, admin_client, monkeypatch
    ):
        """GET /services и GET /services/{svc}/events НЕ под этим лимитом
        (read-only, key'ятся в общий global-bucket / nicht zugriff).
        Регрессия: лимит распространяется только на POST.
        """
        monkeypatch.setenv("REGISTER_EVENTS_RATE_LIMIT", "1/minute")
        from src.core.config import get_settings
        get_settings.cache_clear()
        from src.main import limiter
        limiter.reset()

        # 20 GET-запросов на /services и /services/{svc}/events — все 200.
        for i in range(20):
            r = admin_client.get(SERVICES_URL)
            assert r.status_code == 200, (
                f"GET /services #{i} got {r.status_code}: {r.text}"
            )
            r = admin_client.get(f"{SERVICES_URL}/auth_service/events")
            assert r.status_code == 200, (
                f"GET /services/auth_service/events #{i} got "
                f"{r.status_code}: {r.text}"
            )


class TestRegisterEventsRateLimitConfig:
    """Конфигурируемость лимита через ``REGISTER_EVENTS_RATE_LIMIT`` env."""

    def test_default_value(self, monkeypatch):
        monkeypatch.delenv("REGISTER_EVENTS_RATE_LIMIT", raising=False)
        from src.core.config import get_settings
        get_settings.cache_clear()
        settings = get_settings()
        assert settings.register_events_rate_limit == "120/second"

    def test_override_via_env(self, monkeypatch):
        monkeypatch.setenv("REGISTER_EVENTS_RATE_LIMIT", "30/minute")
        from src.core.config import get_settings
        get_settings.cache_clear()
        settings = get_settings()
        assert settings.register_events_rate_limit == "30/minute"


# ── headers_enabled=False → no X-RateLimit-* leak ───────────────────────────


_RATE_LIMIT_HEADER_NAMES = (
    "X-RateLimit-Limit",
    "X-RateLimit-Remaining",
    "X-RateLimit-Reset",
)


class TestRateLimitHeadersDisabledByDefault:
    """``Limiter`` ставит ``headers_enabled=False`` по умолчанию.

    До фикса: ``X-RateLimit-Remaining`` утекал на КАЖДОМ 201/200, и
    атакующий с ``SERVICE_API_KEY`` мог adaptive-burst'ить ровно под лимит,
    наблюдая remaining-counter в response. Live feedback своего rate-limit'а.

    После фикса: response не несёт ``X-RateLimit-*`` headers (default
    ``False`` в settings, явный opt-in через ``RATE_LIMIT_HEADERS_ENABLED=true``).
    """

    def test_ingest_response_has_no_rate_limit_headers(
        self, client, auth_headers
    ):
        """``POST /events`` — primary ingest канал — не утекает quota."""
        r = client.post(
            "/api/logging/v1/events", json=make_event(), headers=auth_headers
        )
        assert r.status_code == 201
        for header in _RATE_LIMIT_HEADER_NAMES:
            assert header not in r.headers, (
                f"Response leaked {header}: {r.headers.get(header)!r}"
            )

    def test_register_events_response_has_no_rate_limit_headers(
        self, client, auth_headers
    ):
        """``POST /services/{svc}/events`` — batch канал — тоже не утекает."""
        headers = {**auth_headers, "X-Service-Identity": "auth_service"}
        r = client.post(
            f"{SERVICES_URL}/auth_service/events",
            json={"events": [make_event_def(action="user.login")]},
            headers=headers,
        )
        assert r.status_code == 200
        for header in _RATE_LIMIT_HEADER_NAMES:
            assert header not in r.headers, (
                f"Response leaked {header}: {r.headers.get(header)!r}"
            )

    def test_get_events_has_no_rate_limit_headers(self, admin_client):
        """GET /events — даже когда не под лимитом, headers не должны
        пробрасываться через exempt-path или middleware.
        """
        r = admin_client.get("/api/logging/v1/events")
        assert r.status_code == 200
        for header in _RATE_LIMIT_HEADER_NAMES:
            assert header not in r.headers, (
                f"Response leaked {header}: {r.headers.get(header)!r}"
            )

    def test_429_response_has_no_rate_limit_headers(
        self, client, auth_headers, monkeypatch
    ):
        """Даже на 429 не отдаём X-RateLimit-Remaining (=0) — атакующий
        иначе узнал бы окно cool-down (X-RateLimit-Reset).
        """
        monkeypatch.setenv("INGEST_RATE_LIMIT", "1/minute")
        from src.core.config import get_settings
        get_settings.cache_clear()
        from src.main import limiter
        limiter.reset()

        # Первый запрос — 201.
        client.post(
            "/api/logging/v1/events", json=make_event(), headers=auth_headers
        )
        # Второй — 429.
        r = client.post(
            "/api/logging/v1/events", json=make_event(), headers=auth_headers
        )
        assert r.status_code == 429
        for header in _RATE_LIMIT_HEADER_NAMES:
            assert header not in r.headers, (
                f"429 response leaked {header}: {r.headers.get(header)!r}"
            )


class TestRateLimitHeadersConfig:
    """Конфигурируемость через ``RATE_LIMIT_HEADERS_ENABLED`` env."""

    def test_default_value_is_false(self, monkeypatch):
        monkeypatch.delenv("RATE_LIMIT_HEADERS_ENABLED", raising=False)
        from src.core.config import get_settings
        get_settings.cache_clear()
        settings = get_settings()
        assert settings.rate_limit_headers_enabled is False

    def test_can_be_enabled_via_env(self, monkeypatch):
        monkeypatch.setenv("RATE_LIMIT_HEADERS_ENABLED", "true")
        from src.core.config import get_settings
        get_settings.cache_clear()
        settings = get_settings()
        assert settings.rate_limit_headers_enabled is True
