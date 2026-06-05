"""Carry-фиксы по deferred-bullet'ам loging-triage W45 (read-API tests):

1. `has_more` shape — поле появилось на `RuleListResponse`, `ServiceListResponse`
   и `ServiceEventsResponse`; до этого тестировалась только `EventListResponse`.
   Здесь — serialization-shape тесты на 3 новых модели.

2. Path-validators `rule_id` / `service` — `min/max_length` и `pattern` стоят
   на эндпоинтах, но 422-веток не было в тестах. Параметризованная батарея:
   пустой path-сегмент, длиннее cap'а, недопустимые символы.

3. `ErrorEnvelope` — новая публичная модель в `schemas/common.py` без тестов
   на shape: required-поля, defaults для `details` / `request_id` / `timestamp`,
   обратная совместимость с `ErrorResponse`-алиасом.

4. Body-size middleware request_id correlation — 413 (`PAYLOAD_TOO_LARGE`)
   и 400 (`INVALID_CONTENT_LENGTH`) обязаны нести `request_id` в envelope и
   `X-Request-ID` в response header, иначе SOC не сможет коррелировать DoS-
   spike'и со своими request-id'ами.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.schemas.common import ErrorEnvelope, ErrorResponse
from src.schemas.rules import RuleListResponse
from src.schemas.services import (
    ServiceEventsResponse,
    ServiceInfo,
    ServiceListResponse,
)


EVENTS_URL = "/api/logging/v1/events"
RULES_URL = "/api/logging/v1/rules"
SERVICES_URL = "/api/logging/v1/services"


# ── 1. has_more shape ─────────────────────────────────────────────────────


class TestHasMoreSerializationShape:
    def test_rule_list_response_has_more_default_false(self):
        # Sanity: пустой список — `has_more=False` по default.
        resp = RuleListResponse(items=[], total=0, limit=100, offset=0)
        assert resp.has_more is False
        assert resp.model_dump()["has_more"] is False

    def test_rule_list_response_has_more_explicit_true(self):
        resp = RuleListResponse(items=[], total=999, limit=100, offset=0, has_more=True)
        assert resp.has_more is True
        # Сериализация в JSON через model_dump_json — поле присутствует.
        data = resp.model_dump()
        assert "has_more" in data
        assert "limit" in data and "offset" in data and "total" in data

    def test_service_list_response_has_more_and_nullable_pagination(self):
        # Список заведомо короткий — `has_more=False`, `limit`/`offset` nullable.
        resp = ServiceListResponse(items=[], total=0)
        data = resp.model_dump()
        assert data["has_more"] is False
        assert data["limit"] is None
        assert data["offset"] is None
        assert data["total"] == 0

    def test_service_list_response_with_item(self):
        info = ServiceInfo(
            service="auth_service",
            event_count=42,
            last_event_at=datetime.now(timezone.utc),
        )
        resp = ServiceListResponse(items=[info], total=1)
        data = resp.model_dump()
        assert data["total"] == 1
        assert data["has_more"] is False

    def test_service_events_response_has_more_default_false(self):
        resp = ServiceEventsResponse(
            service="auth_service", items=[], total=0, limit=100, offset=0,
        )
        assert resp.has_more is False
        data = resp.model_dump()
        assert data["has_more"] is False
        assert data["service"] == "auth_service"

    def test_service_events_response_has_more_true(self):
        resp = ServiceEventsResponse(
            service="auth_service",
            items=[],
            total=500,
            limit=100,
            offset=0,
            has_more=True,
        )
        assert resp.has_more is True


# ── 2. Path-validators 422 ────────────────────────────────────────────────


class TestRuleIdPathValidator:
    """`rule_id` в `/rules/{rule_id}`: min_length=1, max_length=48, pattern
    `^[A-Za-z0-9_\\-]{1,48}$` — слишком длинные / с invalid-chars отбиваются
    FastAPI до dependency'и."""

    # CR/LF не проверяем здесь — httpx сам отбивает такие URL'ы до отправки
    # с InvalidURL (это уже client-side гард на response-splitting). Контракт
    # path-validator'а покрывают остальные кейсы.
    @pytest.mark.parametrize("rule_id", [
        # FastAPI ловит too-long до dependency'и; 'a' * 49 — на 1 больше cap'а.
        "a" * 49,
        # Точка не входит в pattern.
        "rl.with.dots",
        # Пробел в pattern'е тоже запрещён.
        "rl-with spaces",
        # Unicode-homoglyph (кириллическая 'а') в opaque-id запрещён.
        "rl_аscii",
        # Символ `$` не входит в [A-Za-z0-9_\\-].
        "rl$bad",
    ])
    def test_invalid_rule_id_returns_422(self, admin_client, rule_id):
        r = admin_client.get(f"{RULES_URL}/{rule_id}")
        assert r.status_code == 422, (
            f"expected 422 for rule_id={rule_id!r}, got {r.status_code}: {r.text}"
        )


class TestServicePathValidator:
    """`service` в `/services/{service}/events`: min_length=1, max_length=128,
    pattern `^[^/\\s]{1,128}$` — slash и любой whitespace запрещены, остальное
    проходит. Тесты — на cap по длине и whitespace."""

    @pytest.mark.parametrize("service", [
        # 129 символов — за cap'ом.
        "s" * 129,
        # Пробел в pattern'е запрещён (любой whitespace отбивается до 422).
        "auth service",
    ])
    def test_invalid_service_returns_422(self, admin_client, service):
        r = admin_client.get(f"{SERVICES_URL}/{service}/events")
        assert r.status_code == 422, (
            f"expected 422 for service={service!r}, got {r.status_code}: {r.text}"
        )


# ── 3. ErrorEnvelope shape ────────────────────────────────────────────────


class TestErrorEnvelopeShape:
    def test_required_fields_only(self):
        env = ErrorEnvelope(
            error="bad_request",
            error_code="VALIDATION_ERROR",
            message="payload invalid",
        )
        data = env.model_dump()
        assert data["error"] == "bad_request"
        assert data["error_code"] == "VALIDATION_ERROR"
        assert data["message"] == "payload invalid"
        # `details` default — пустой dict (НЕ None, иначе клиенту приходится
        # делать `data.get('details') or {}` на каждый ответ).
        assert data["details"] == {}
        # request_id / timestamp — nullable, default None.
        assert data["request_id"] is None
        assert data["timestamp"] is None

    def test_all_fields_present(self):
        ts = datetime(2026, 6, 5, 12, 0, 0, tzinfo=timezone.utc)
        env = ErrorEnvelope(
            error="forbidden",
            error_code="LOGING_ADMIN_REQUIRED",
            message="admin role required",
            details={"hint": "request loging_admin"},
            request_id="req_abc123",
            timestamp=ts,
        )
        data = env.model_dump()
        assert data["request_id"] == "req_abc123"
        assert data["details"] == {"hint": "request loging_admin"}
        # timestamp сериализуется через pydantic-default (datetime).
        assert data["timestamp"] == ts

    def test_error_response_is_alias_of_error_envelope(self):
        # Обратная совместимость: ErrorResponse — тот же класс.
        assert ErrorResponse is ErrorEnvelope


# ── 4. Body-size middleware: request_id correlation ───────────────────────


class TestBodySizeRequestIdCorrelation:
    def test_413_envelope_carries_inbound_request_id(self, client, auth_headers):
        """Атакующий шлёт большой POST с `X-Request-ID: req_attack`. Middleware
        должен сохранить тот же id в envelope + response-header'е, чтобы SOC
        мог связать spike в логах с конкретным request'ом."""
        big = "a" * (2 * 1024 * 1024)
        headers = {**auth_headers, "X-Request-ID": "req_attack"}
        from tests.conftest import make_event
        r = client.post(EVENTS_URL, json=make_event(details={"blob": big}), headers=headers)
        assert r.status_code == 413
        body = r.json()
        assert body["error_code"] == "PAYLOAD_TOO_LARGE"
        # Envelope несёт inbound request_id (санитизированный — для `req_attack`
        # санитизация no-op'ом).
        assert body["request_id"] == "req_attack"
        # Header X-Request-ID тоже выставлен на ответе.
        assert r.headers.get("X-Request-ID") == "req_attack"

    def test_413_without_inbound_id_gets_autogenerated(self, client, auth_headers):
        """Без `X-Request-ID` middleware должен сгенерировать `req_<hex>`."""
        big = "a" * (2 * 1024 * 1024)
        from tests.conftest import make_event
        r = client.post(EVENTS_URL, json=make_event(details={"blob": big}), headers=auth_headers)
        assert r.status_code == 413
        body = r.json()
        gen_id = body["request_id"]
        assert gen_id is not None
        assert gen_id.startswith("req_")
        # Header и envelope согласованы.
        assert r.headers.get("X-Request-ID") == gen_id

    def test_400_invalid_content_length_carries_request_id(self, client, auth_headers):
        """`Content-Length: -1` отбивается с 400 `INVALID_CONTENT_LENGTH`.
        Тот же путь должен нести request_id, как и 413."""
        headers = {
            **auth_headers,
            "Content-Length": "-1",
            "X-Request-ID": "req_neg_cl",
            "Content-Type": "application/json",
        }
        # httpx перевычисляет CL по реальной длине body — используем
        # raw-content + явный Content-Length через `client.build_request`.
        req = client.build_request(
            "POST", EVENTS_URL, headers=headers, content=b'{"x": 1}',
        )
        # Подменяем Content-Length вручную после build_request.
        req.headers["content-length"] = "-1"
        r = client.send(req)
        assert r.status_code == 400
        body = r.json()
        assert body["error_code"] == "INVALID_CONTENT_LENGTH"
        assert body["request_id"] == "req_neg_cl"
        assert r.headers.get("X-Request-ID") == "req_neg_cl"
