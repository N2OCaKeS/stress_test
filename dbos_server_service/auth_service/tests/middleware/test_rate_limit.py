"""Тесты per-IP rate-limit middleware для credential-критичных эндпоинтов.

Защищает `/login`, `/refresh`, `/docker/token` от brute-force через ротацию
username'ов / IP-spoofing. Per-user lockout работает по username'у; per-IP
лимит закрывает обход через массовую ротацию.

Проверяем:
* лимит для `/login` (10/minute по умолчанию) → 11-й запрос с одного IP → 429;
* лимит для `/refresh` (30/minute) → 31-й → 429;
* лимит для `/docker/token` (30/minute) → 31-й → 429;
* health-endpoints (`/health`, `/ready`) не лимитируются;
* per-route счётчики независимы — сжигание /login квоты не убивает /refresh.

Чтобы не палить 100+ HTTP-вызовов на запрос, тесты используют override через
`monkeypatch` на `parsed_rate_limits` приложения с маленькими лимитами.
"""

from __future__ import annotations

import base64

import pytest
from limits import parse as parse_rate_limit

from src.main import limiter


LOGIN_URL = "/api/auth/v1/login"
REFRESH_URL = "/api/auth/v1/refresh"
DOCKER_TOKEN_URL = "/api/auth/v1/docker/token"
OAUTH2_TOKEN_URL = "/api/auth/v1/oauth2/token"
HEALTH_URL = "/api/auth/v1/health"


@pytest.fixture
def tight_login_limit(monkeypatch):
    """Подменяет настройку login_rate_limit на жёсткое значение.

    Лимит читается из settings в `create_application`, и кэшируется в
    `parsed_rate_limits` per-app. Чтобы override сработал — нужно либо
    пересобирать application, либо менять settings ДО построения app fixture'а.
    Здесь мы подменяем `LOGIN_RATE_LIMIT` env и сбрасываем `get_settings` cache,
    а потом `client` fixture поднимает свежий app с новым лимитом.
    """
    monkeypatch.setenv("LOGIN_RATE_LIMIT", "3/minute")
    from src.core import config as config_mod
    config_mod.get_settings.cache_clear()
    yield
    config_mod.get_settings.cache_clear()


@pytest.fixture
def tight_refresh_limit(monkeypatch):
    """Override refresh-лимита на 3/minute."""
    monkeypatch.setenv("REFRESH_RATE_LIMIT", "3/minute")
    from src.core import config as config_mod
    config_mod.get_settings.cache_clear()
    yield
    config_mod.get_settings.cache_clear()


@pytest.fixture
def tight_docker_token_limit(monkeypatch):
    """Override docker-token-лимита на 3/minute."""
    monkeypatch.setenv("DOCKER_TOKEN_RATE_LIMIT", "3/minute")
    from src.core import config as config_mod
    config_mod.get_settings.cache_clear()
    yield
    config_mod.get_settings.cache_clear()


# ── /login rate limit ────────────────────────────────────────────────────────


class TestLoginRateLimit:
    async def test_login_default_limit_is_10_per_minute(self):
        """Defaults: settings.login_rate_limit == '10/minute'."""
        from src.core.config import get_settings
        settings = get_settings()
        assert settings.login_rate_limit == "10/minute"

    async def test_login_429_after_exceeding_limit(
        self, tight_login_limit, client, account_admin
    ):
        """С тайтлимитом 3/minute: 4-й login-запрос → 429.

        Login делает Argon2id verify (~100ms CPU), поэтому per-IP лимит
        критичен — без него атакующий выжигает ядра brute-force'ом.
        """
        # Первые 3 проходят (status ∈ {200, 401, 429-never-here}); главное — не 429.
        for i in range(3):
            resp = await client.post(
                LOGIN_URL,
                json={"username": "t_admin", "password": "Admin1234!"},
            )
            assert resp.status_code != 429, f"premature 429 на запросе #{i + 1}: {resp.text}"

        # 4-й — 429
        resp = await client.post(
            LOGIN_URL,
            json={"username": "t_admin", "password": "Admin1234!"},
        )
        assert resp.status_code == 429, f"expected 429, got {resp.status_code}: {resp.text}"

    async def test_login_429_envelope_shape(self, tight_login_limit, client, account_admin):
        """429-ответ должен быть в нашем error-envelope формате."""
        # Сжигаем квоту
        for _ in range(3):
            await client.post(
                LOGIN_URL,
                json={"username": "t_admin", "password": "Admin1234!"},
            )
        resp = await client.post(
            LOGIN_URL,
            json={"username": "t_admin", "password": "Admin1234!"},
        )
        assert resp.status_code == 429

        body = resp.json()
        assert body["error"] == "too_many_requests"
        assert body["error_code"] == "RATE_LIMIT_EXCEEDED"
        assert "Rate limit exceeded" in body["message"]
        assert "details" in body
        assert "limit" in body["details"]
        assert "timestamp" in body
        assert "request_id" in body
        assert resp.headers.get("Retry-After") == "60"

    async def test_login_successful_within_limit_not_throttled(
        self, client, account_admin
    ):
        """5 успешных login'ов под дефолтным лимитом 10/minute → ни одного 429."""
        for _ in range(5):
            resp = await client.post(
                LOGIN_URL,
                json={"username": "t_admin", "password": "Admin1234!"},
            )
            assert resp.status_code == 200, f"unexpected status: {resp.status_code} {resp.text}"


# ── /refresh rate limit ──────────────────────────────────────────────────────


class TestRefreshRateLimit:
    async def test_refresh_default_limit_is_30_per_minute(self):
        from src.core.config import get_settings
        assert get_settings().refresh_rate_limit == "30/minute"

    async def test_refresh_429_after_exceeding_limit(
        self, tight_refresh_limit, client, account_admin
    ):
        """С тайтлимитом 3/minute: 4-й /refresh → 429.

        Используем invalid-токен — endpoint всё равно проходит через limiter
        ДО auth-check'а, главное — что считается per-IP.
        """
        # Сжигаем 3 разрешённых
        for _ in range(3):
            resp = await client.post(
                REFRESH_URL,
                json={"refresh_token": "bogus-refresh-token"},
            )
            assert resp.status_code != 429

        # 4-й — 429
        resp = await client.post(
            REFRESH_URL,
            json={"refresh_token": "bogus-refresh-token"},
        )
        assert resp.status_code == 429


# ── /docker/token rate limit ─────────────────────────────────────────────────


def _basic_auth(username: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()


class TestDockerTokenRateLimit:
    async def test_docker_token_default_limit_is_30_per_minute(self):
        from src.core.config import get_settings
        assert get_settings().docker_token_rate_limit == "30/minute"

    async def test_docker_token_429_after_exceeding_limit(
        self, tight_docker_token_limit, client, account_admin
    ):
        """С тайтлимитом 3/minute: 4-й /docker/token → 429.

        Закрывает lockout-bypass через credential-stuffing с ротацией
        username'ов: per-user lockout не помогает, если атакующий не
        повторяется по username'у.
        """
        # Сжигаем 3
        for _ in range(3):
            resp = await client.get(
                DOCKER_TOKEN_URL,
                headers={"Authorization": _basic_auth("nobody", "wrong")},
            )
            assert resp.status_code != 429, f"premature 429: {resp.status_code} {resp.text}"

        # 4-й — 429
        resp = await client.get(
            DOCKER_TOKEN_URL,
            headers={"Authorization": _basic_auth("nobody", "wrong")},
        )
        assert resp.status_code == 429


# ── /oauth2/token rate limit ─────────────────────────────────────────────────


class TestOAuth2TokenRateLimit:
    async def test_oauth2_token_uses_login_limit(self):
        """oauth2/token делит login_rate_limit — отдельной настройки нет."""
        from src.core.config import get_settings
        assert get_settings().login_rate_limit == "10/minute"

    async def test_oauth2_token_429_after_exceeding_limit(
        self, tight_login_limit, client
    ):
        """С тайтлимитом 3/minute: 4-й /oauth2/token → 429.

        Закрывает brute-force `client_secret` через client_credentials grant:
        compare_digest без per-client lockout, перебор секрета ограничивается
        только этим per-IP лимитом.
        """
        body = {
            "grant_type": "client_credentials",
            "client_id": "cli_nonexistent",
            "client_secret": "wrong",
        }
        for _ in range(3):
            resp = await client.post(OAUTH2_TOKEN_URL, json=body)
            assert resp.status_code != 429, f"premature 429: {resp.status_code} {resp.text}"

        resp = await client.post(OAUTH2_TOKEN_URL, json=body)
        assert resp.status_code == 429, f"expected 429, got {resp.status_code}: {resp.text}"


# ── GET /oauth2/authorize rate limit ─────────────────────────────────────────


class TestOAuth2AuthorizeRateLimit:
    """GET /oauth2/authorize — анонимный endpoint, сканер enumerate'ит client_id
    и redirect_uri-маппинги без ограничений, если не лимитировать. Делит
    `login_rate_limit` (тот же дешёвый бюджет на пробу OAuth-площадки).
    """

    async def test_authorize_429_after_exceeding_limit(
        self, tight_login_limit, client,
    ):
        """С тайтлимитом 3/minute: 4-й GET /authorize → 429.

        Параметры намеренно битые — нам важно, что rate-limit срабатывает ДО
        бизнес-валидации; статус первых трёх ответов нерелевантен, главное
        чтобы НЕ был 429.
        """
        params = {
            "client_id": "cli_nonexistent",
            "redirect_uri": "https://app.example.com/cb",
            "response_type": "code",
            "scope": "svc_a",
        }
        for i in range(3):
            resp = await client.get(
                "/api/auth/v1/oauth2/authorize", params=params,
            )
            assert resp.status_code != 429, (
                f"premature 429 на запросе #{i + 1}: {resp.text}"
            )

        resp = await client.get(
            "/api/auth/v1/oauth2/authorize", params=params,
        )
        assert resp.status_code == 429, (
            f"expected 429, got {resp.status_code}: {resp.text}"
        )


# ── Health не лимитируется ───────────────────────────────────────────────────


class TestHealthNotRateLimited:
    async def test_health_bypasses_limit(self, tight_login_limit, client):
        """20 запросов на /health должны проходить даже с тайтлимитом /login."""
        for _ in range(20):
            resp = await client.get(HEALTH_URL)
            assert resp.status_code == 200
            assert resp.json()["status"] == "ok"


# ── Per-route счётчики независимы ────────────────────────────────────────────


class TestPerRouteIsolation:
    async def test_login_quota_does_not_consume_refresh_quota(
        self, tight_login_limit, tight_refresh_limit, client, account_admin
    ):
        """3 login'а + 3 refresh'а с одного IP не должны давать 429.

        Это значит per-route счётчики хранятся независимо — иначе атакующий
        выжигал бы все routes одной серией.
        """
        for _ in range(3):
            resp = await client.post(
                LOGIN_URL,
                json={"username": "t_admin", "password": "Admin1234!"},
            )
            assert resp.status_code != 429

        # 3 refresh'а — независимая квота
        for _ in range(3):
            resp = await client.post(
                REFRESH_URL,
                json={"refresh_token": "bogus"},
            )
            assert resp.status_code != 429


# ── No audit amplification on 429 ────────────────────────────────────────────


class TestRateLimitNoAuditAmplification:
    """rate-limit middleware outermost → 429 НЕ должен порождать `http.*` audit.

    Симметрия с server_service test_rate_limit.py:
    `rate_limit_middleware` зарегистрирован LAST → outermost (Starlette
    стакает в обратном порядке). 429-ответ возвращается ДО `audit_access`,
    минуя `audit_service.emit("http.client_error", ...)`. Без этого
    slowloris с одного IP заполнял бы audit-канал шумом.
    """

    async def test_429_does_not_emit_http_audit(
        self, tight_login_limit, client, account_admin, monkeypatch
    ):
        """4-й login после 3/minute → 429, но `audit_service.emit("http.*")` не вызвался."""
        captured: list[dict] = []

        def fake_emit(action, actor_id=None, **kwargs):
            captured.append({"action": action, **kwargs})

        import src.services.audit_service as audit_mod
        import src.main as main_mod
        monkeypatch.setattr(audit_mod, "emit", fake_emit)
        monkeypatch.setattr(main_mod.audit_service, "emit", fake_emit)

        # Жжём квоту
        for _ in range(3):
            await client.post(
                LOGIN_URL,
                json={"username": "t_admin", "password": "Admin1234!"},
            )
        captured.clear()

        resp = await client.post(
            LOGIN_URL,
            json={"username": "t_admin", "password": "Admin1234!"},
        )
        assert resp.status_code == 429

        # Никаких `http.*` audit-event'ов от 429-ответа.
        http_emits = [
            e for e in captured
            if e["action"] in (
                "http.access_denied",
                "http.client_error",
                "http.server_error",
            )
        ]
        assert http_emits == [], (
            f"429 спровоцировал audit-emit (amplification): {http_emits}"
        )
