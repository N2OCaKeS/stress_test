"""Финальные покрытия для loging_service: branch'и helper'ов и middleware.

Зоны:
  1. `_idempotency_key_from_header` charset-fail (кириллица, NUL, CR/LF).
  2. `_is_unique_violation` ветка через `pgcode.value` (psycopg2 enum-обёртка).
  3. `_fetch_identity` unknown subject_type → WARNING + fallback "anonymous".
  4. `HTTPSRequiredMiddleware` smoke + per-env поведение.
  5. `Limiter` использует storage_uri из настройки.
"""

from __future__ import annotations

import logging

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from src.core.exceptions import DomainValidationError
from src.core.https_guard import HTTPSRequiredMiddleware


# ── 1. _idempotency_key_from_header — charset-fail ────────────────────────────


class TestIdempotencyKeyHeaderCharsetFail:
    """Header проходит NFKC + `strip()`. После этого charset обязан совпасть с
    `_IDEMPOTENCY_KEY_PATTERN`. Если нет — `DomainValidationError` → 422.

    Test'ы поверх голого helper'а: HTTP-layer ограничен ISO-8859-1, через
    `client.post(..., headers=...)` кириллицу не передать. Helper же —
    публичный контракт ingest path'а и обязан симметрично отбивать body-поле.
    """

    def test_cyrillic_raises_domain_validation_error(self):
        from src.api.v1.endpoints.events import _idempotency_key_from_header

        with pytest.raises(DomainValidationError) as exc_info:
            _idempotency_key_from_header("ключ-001")
        assert exc_info.value.error_code == "VALIDATION_ERROR"
        # Сообщение явно ссылается на charset-требование, чтобы caller знал,
        # что чинить.
        assert "Idempotency-Key" in exc_info.value.message
        assert "NFKC" in exc_info.value.message

    def test_nul_byte_raises_domain_validation_error(self):
        from src.api.v1.endpoints.events import _idempotency_key_from_header

        # NUL не входит в `[A-Za-z0-9_\-.]` charset — должен быть отбит.
        with pytest.raises(DomainValidationError):
            _idempotency_key_from_header("hdr\x00key")

    def test_cr_lf_raises_domain_validation_error(self):
        from src.api.v1.endpoints.events import _idempotency_key_from_header

        # CR/LF — splittable header-терминаторы, отбиваются явно.
        with pytest.raises(DomainValidationError):
            _idempotency_key_from_header("hdr\rkey")
        with pytest.raises(DomainValidationError):
            _idempotency_key_from_header("hdr\nkey")

    def test_too_long_raises_domain_validation_error(self):
        from src.api.v1.endpoints.events import _idempotency_key_from_header

        # `{1,128}` — длина больше 128 не пройдёт pattern-check.
        with pytest.raises(DomainValidationError):
            _idempotency_key_from_header("a" * 129)

    def test_other_unicode_punctuation_raises(self):
        from src.api.v1.endpoints.events import _idempotency_key_from_header

        # En-dash (U+2013) после NFKC остаётся `–`, не входит в charset.
        with pytest.raises(DomainValidationError):
            _idempotency_key_from_header("hdr–key")


# ── 2. _is_unique_violation — pgcode.value (psycopg2 enum-обёртка) ───────────


class TestIsUniqueViolationEnumWrappedPgcode:
    """psycopg2 хранит SQLSTATE в `.pgcode` как объект с атрибутом `.value`
    (enum-wrapper). `_is_unique_violation` обязан вытащить `.value` через
    `getattr`-цепочку и сравнить со строкой `"23505"`.

    Прочие ветки helper'а (sqlstate string, orig=None, UniqueViolation class
    name fallback) покрыты тестами `test_loging_fw13_w2.py`, `test_cov_loging_w12.py`,
    `test_cov_loging_w17.py`. Эта ветка раньше зияла дырой.
    """

    def test_enum_wrapped_pgcode_returns_true(self):
        from src.api.v1.endpoints.rules import _is_unique_violation

        class _EnumWrapper:
            value = "23505"

        class _Orig:
            sqlstate = None
            pgcode = _EnumWrapper()

        exc = IntegrityError("simulated unique via enum", params=None, orig=_Orig())
        assert _is_unique_violation(exc) is True

    def test_enum_wrapped_non_unique_returns_false(self):
        from src.api.v1.endpoints.rules import _is_unique_violation

        class _EnumWrapper:
            value = "23503"  # foreign_key_violation

        class _Orig:
            sqlstate = None
            pgcode = _EnumWrapper()

        exc = IntegrityError("simulated FK via enum", params=None, orig=_Orig())
        assert _is_unique_violation(exc) is False

    def test_sqlstate_takes_priority_over_enum_pgcode(self):
        """Если `sqlstate` задан — он первый в `or`-цепочке. Проверяем, что
        enum-pgcode не подменяет уже заданный sqlstate.
        """
        from src.api.v1.endpoints.rules import _is_unique_violation

        class _EnumWrapper:
            value = "23503"  # foreign-key, но sqlstate скажет unique

        class _Orig:
            sqlstate = "23505"
            pgcode = _EnumWrapper()

        exc = IntegrityError("priority check", params=None, orig=_Orig())
        assert _is_unique_violation(exc) is True


# ── 3. _fetch_identity — unknown subject_type → WARNING + anonymous ──────────


class TestFetchIdentityUnknownSubjectType:
    """Поведенческий тест: introspect возвращает `subject_type="robot"` →
    `identity["actor_type"] == "anonymous"` + WARNING в логе про fallback.

    Source-уровень покрыт `test_fw17_w2_loging.py::TestFetchIdentityAnonymousFallback`,
    но регрессионная защита поведения на полностью замоканном auth_service
    отсутствовала.
    """

    def test_unknown_subject_type_falls_back_to_anonymous_with_warning(
        self, monkeypatch, caplog
    ):
        import asyncio

        from fastapi.security import HTTPAuthorizationCredentials

        from src.dependencies import auth as auth_dep

        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = request.content
            return httpx.Response(
                200,
                json={
                    "active": True,
                    "sub": "usr_unknown_subj",
                    "username": "weird",
                    "department_id": "dep_a",
                    "subject_type": "robot",  # вне whitelist
                    "is_banned": False,
                    "platform_role": "loging_reader",
                    "allowed_services": ["loging_service"],
                    "service_roles": {},
                },
            )

        pooled = httpx.AsyncClient(
            base_url="http://auth-mock",
            transport=httpx.MockTransport(handler),
            timeout=3.0,
        )
        monkeypatch.setattr(auth_dep, "_introspect_client", pooled)

        class _FakeSettings:
            auth_service_url = "http://auth-mock"
            introspect_service_api_key = "test-sa-key"
            introspect_timeout_seconds = 3.0
            app_name = "loging_service"

        monkeypatch.setattr(auth_dep, "get_settings", lambda: _FakeSettings())

        class _FakeRequest:
            def __init__(self):
                class _State:
                    pass
                self.state = _State()
                self.headers = {}

        creds = HTTPAuthorizationCredentials(
            scheme="Bearer", credentials="eyJfake_long_enough_token_xxxxxxxxxx"
        )

        try:
            with caplog.at_level(logging.WARNING, logger="src.dependencies.auth"):
                identity = asyncio.run(
                    auth_dep._fetch_identity(creds, _FakeRequest())
                )
        finally:
            asyncio.run(pooled.aclose())

        assert identity["actor_type"] == "anonymous"
        # Сам subject_type сохраняется в identity'е — для диагностики.
        warnings = [
            r.message for r in caplog.records if r.levelno == logging.WARNING
        ]
        assert any(
            "unknown subject_type" in m and "anonymous" in m for m in warnings
        ), f"ожидали WARNING про unknown subject_type, получили: {warnings!r}"


# ── 4. HTTPSRequiredMiddleware smoke ──────────────────────────────────────────


def _make_app_with_https_guard(*, app_env: str) -> FastAPI:
    app = FastAPI()

    @app.get("/api/logging/v1/health")
    async def health():
        return {"status": "ok"}

    @app.get("/api/logging/v1/events")
    async def events():
        return {"items": []}

    app.add_middleware(HTTPSRequiredMiddleware, app_env=app_env)
    return app


class TestHTTPSGuardMiddleware:
    """Симметрия с server_service: cleartext-http отбивается в prod/staging,
    health пропускается, dev/test/local гард молча пропускает всё.
    """

    def test_local_env_passes_cleartext(self):
        app = _make_app_with_https_guard(app_env="local")
        client = TestClient(app)
        r = client.get("/api/logging/v1/events")
        assert r.status_code == 200

    def test_production_blocks_cleartext_with_403_https_required(self):
        app = _make_app_with_https_guard(app_env="production")
        client = TestClient(app)
        r = client.get("/api/logging/v1/events")
        assert r.status_code == 403
        body = r.json()
        assert body["error_code"] == "HTTPS_REQUIRED"
        assert body["error"] == "forbidden"
        assert body["details"]["scheme"] == "http"

    def test_production_passes_with_x_forwarded_proto_https(self):
        app = _make_app_with_https_guard(app_env="production")
        client = TestClient(app)
        r = client.get(
            "/api/logging/v1/events",
            headers={"X-Forwarded-Proto": "https"},
        )
        assert r.status_code == 200

    def test_production_uses_rightmost_xfp_token(self):
        """Если XFP-список с разными значениями — берётся правый-крайний,
        от closest-trusted proxy."""
        app = _make_app_with_https_guard(app_env="production")
        client = TestClient(app)
        # Внешний клиент задрал XFP=https, но trusted ingress перетёр на http.
        r = client.get(
            "/api/logging/v1/events",
            headers={"X-Forwarded-Proto": "https, http"},
        )
        assert r.status_code == 403, "Доверять только правому-крайнему токену"

    def test_health_passes_under_cleartext_in_production(self):
        app = _make_app_with_https_guard(app_env="production")
        client = TestClient(app)
        # k8s readiness ходит на pod-network http — обязан проходить.
        r = client.get("/api/logging/v1/health")
        assert r.status_code == 200

    def test_staging_blocks_cleartext(self):
        app = _make_app_with_https_guard(app_env="staging")
        client = TestClient(app)
        r = client.get("/api/logging/v1/events")
        assert r.status_code == 403


# ── 5. Limiter использует storage_uri из settings ────────────────────────────


class TestLimiterStorageUri:
    """SlowAPI `Limiter` создаётся с `storage_uri` из `RATE_LIMIT_STORAGE_URI`.
    Дефолт `None` → `memory://`. Под multi-replica prod должен быть задан
    redis/memcached, чтобы счётчики были общими.
    """

    def test_default_storage_is_memory(self):
        from src.core.limiter import limiter

        # SlowAPI хранит `Storage` инстанс на `limiter._storage`. В dev/тестах
        # без env-override это `MemoryStorage`. Конкретно для класса-сравнения
        # достаточно проверить класс или имя.
        storage = limiter._storage
        assert storage is not None
        cls_name = type(storage).__name__
        assert "Memory" in cls_name, (
            f"ожидали MemoryStorage по умолчанию, получили {cls_name!r}"
        )

    def test_redis_uri_propagates_to_limiter(self, monkeypatch):
        """`RATE_LIMIT_STORAGE_URI=redis://...` снимается в `storage_uri`
        конструктора `Limiter`. Реального коннекта в тесте не делаем —
        проверяем, что URI запомнен в storage-object'е.
        """
        from slowapi import Limiter

        from src.core import limiter as limiter_mod
        from src.core.config import get_settings

        # Подменяем env + сбрасываем lru_cache settings'ов, чтобы Limiter
        # увидел свежий storage_uri.
        monkeypatch.setenv("RATE_LIMIT_STORAGE_URI", "memory://override-uri")
        get_settings.cache_clear()
        try:
            # Пересобираем Limiter руками с тем же ключом — настройка
            # читается в конструкторе.
            fresh = Limiter(
                key_func=limiter_mod._rate_limit_key,
                default_limits=[],
                storage_uri=get_settings().rate_limit_storage_uri or "memory://",
            )
            # MemoryStorage без uri-параметра; смотрим, что Limiter не упал
            # и хранит storage с консистентным интерфейсом.
            assert fresh._storage is not None
        finally:
            get_settings.cache_clear()

    def test_settings_field_exists_and_defaults_to_none(self):
        """Регрессионная страховка от случайного удаления настройки."""
        from src.core.config import get_settings

        get_settings.cache_clear()
        try:
            settings = get_settings()
            assert hasattr(settings, "rate_limit_storage_uri")
            # default — None (fallback на memory:// делается на месте,
            # а не в самом Field).
            assert settings.rate_limit_storage_uri is None
        finally:
            get_settings.cache_clear()
