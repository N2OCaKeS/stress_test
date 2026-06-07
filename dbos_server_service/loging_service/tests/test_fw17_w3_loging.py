"""P4 cleanup из W17 для loging_service.

1) `GET /retention` под `audit_query_rate_limit` (симметрия с остальными
   read-эндпоинтами).
2) `_action_for_path` — segment-based lookup, не подвержен substring-
   коллизиям (`/retention/events_archive`, `/services/{svc}/rules`).
3) `register_events` — `count_for_service` вместо материализации
   `list_for_service(..., limit=1000)`.
4) `_redact_value` — `bytes`/`bytearray` декодируются и попадают в тот же
   classify-pipeline (defence-in-depth, если в `details` случайно
   прорастает bytes-секрет).
"""

from __future__ import annotations


SERVICES_URL = "/api/logging/v1/services"
RETENTION_URL = "/api/logging/v1/retention"


# ── 1: /retention rate-limit ──────────────────────────────────────────────


class TestRetentionGetRateLimit:
    def test_get_retention_burst_429(self, admin_client, monkeypatch):
        """`GET /retention` под `audit_query_rate_limit` — burst даёт 429."""
        monkeypatch.setenv("AUDIT_QUERY_RATE_LIMIT", "3/minute")
        from src.core.config import get_settings
        get_settings.cache_clear()
        from src.main import limiter
        limiter.reset()

        for i in range(3):
            r = admin_client.get(RETENTION_URL)
            assert r.status_code == 200, f"#{i} {r.status_code}: {r.text}"

        r = admin_client.get(RETENTION_URL)
        assert r.status_code == 429, r.text
        assert r.json()["error_code"] == "RATE_LIMIT_EXCEEDED"


# ── 2: _action_for_path коллизии ──────────────────────────────────────────


class TestActionForPathCollisions:
    """Backwards-compat + новые коллизионные пути."""

    def test_existing_rules_path(self):
        from src.main import _action_for_path
        assert _action_for_path("GET", "/api/logging/v1/rules") == "logging.rules_read"
        assert _action_for_path("POST", "/api/logging/v1/rules") == "logging.rules_write"
        assert (
            _action_for_path("PATCH", "/api/logging/v1/rules/rl_123")
            == "logging.rules_write"
        )
        assert (
            _action_for_path("DELETE", "/api/logging/v1/rules/rl_123")
            == "logging.rules_write"
        )

    def test_existing_events_path(self):
        from src.main import _action_for_path
        assert _action_for_path("GET", "/api/logging/v1/events") == "logging.events_queried"

    def test_existing_services_path(self):
        from src.main import _action_for_path
        assert _action_for_path("GET", "/api/logging/v1/services") == "logging.services_read"

    def test_existing_services_events_path(self):
        """Каталог action'ов сервиса под `/services/{svc}/events` —
        `logging.service_events_browsed` (отдельный action; SOC-фильтр по
        `logging.events_queried` ловит только чтения audit-журнала)."""
        from src.main import _action_for_path
        assert (
            _action_for_path("GET", "/api/logging/v1/services/auth_service/events")
            == "logging.service_events_browsed"
        )

    def test_existing_retention_paths(self):
        from src.main import _action_for_path
        assert _action_for_path("GET", "/api/logging/v1/retention") == "logging.retention_read"
        assert _action_for_path("PUT", "/api/logging/v1/retention") == "logging.retention_write"
        assert (
            _action_for_path("DELETE", "/api/logging/v1/retention")
            == "logging.retention_write"
        )

    def test_existing_unknown_path(self):
        from src.main import _action_for_path
        assert _action_for_path("GET", "/api/logging/v1/unknown") == "logging.admin_access"

    # ── новые: коллизионные пути, которые substring-логика ловила неверно ──

    def test_retention_events_archive_attributed_to_retention(self):
        """Гипотетический `/retention/events_archive` — это retention,
        не events_queried. Старая substring-логика отдавала бы
        `logging.events_queried` из-за `/events` в пути."""
        from src.main import _action_for_path
        assert (
            _action_for_path("GET", "/api/logging/v1/retention/events_archive")
            == "logging.retention_read"
        )

    def test_services_rules_attributed_to_services(self):
        """Гипотетический `/services/{svc}/rules` — это сервис-ресурс,
        не rules_read. Старая substring-логика ловила `/rules` первым."""
        from src.main import _action_for_path
        assert (
            _action_for_path("GET", "/api/logging/v1/services/auth_service/rules")
            == "logging.services_read"
        )

    def test_rules_events_export_attributed_to_events(self):
        """`/rules/{id}/events` — финальный сегмент `events`, значит чтение
        events. Сохраняем правило приоритета хвоста."""
        from src.main import _action_for_path
        assert (
            _action_for_path("GET", "/api/logging/v1/rules/rl_123/events")
            == "logging.events_queried"
        )

    def test_trailing_slash_does_not_break_lookup(self):
        from src.main import _action_for_path
        assert _action_for_path("GET", "/api/logging/v1/rules/") == "logging.rules_read"
        assert _action_for_path("GET", "/api/logging/v1/services/") == "logging.services_read"


# ── 3: register_events — count, не materialize ────────────────────────────


class TestRegisterEventsCountInsteadOfList:
    def test_register_uses_count_for_service_source_structure(self, client, db, auth_headers, monkeypatch):
        """`register_events` для self-audit details должен звать count, не
        list_for_service. Контракт фиксируем через source-inspection — иначе
        случайный rollback на `list_for_service(..., limit=1000)` пройдёт молча.
        """
        import inspect

        from src.api.v1.endpoints import services as services_mod

        src = inspect.getsource(services_mod.register_events)
        assert "count_for_service" in src, (
            "register_events must call count_for_service for total counter"
        )
        # Источник может упомянуть list_for_service в комментарии — игнорируем строки-комментарии.
        code_lines = [ln for ln in src.splitlines() if not ln.lstrip().startswith("#")]
        code_only = "\n".join(code_lines)
        assert "list_for_service" not in code_only, (
            "register_events should not materialize list_for_service for total"
        )

        # Smoke: register всё ещё работает end-to-end.
        from src.repositories import service_events as se_repo

        payload = {"events": [{"action": "user.login", "description": "x"}]}
        r = client.post(
            f"{SERVICES_URL}/auth_service/events",
            json=payload,
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text
        assert se_repo.count_for_service(db, "auth_service") == 1

    def test_register_events_with_many_actions_total_accurate(
        self, client, db, auth_headers, monkeypatch
    ):
        """С >1000 зарегистрированных action'ов total в self-audit details
        должен быть точным, а не capped 1000."""
        from src.repositories import service_events as se_repo

        # Засеваем 1100 events напрямую, минуя API (быстрее).
        bulk = [{"action": f"user.evt_{i:04d}"} for i in range(1100)]
        se_repo.upsert_events(db, "auth_service", bulk)
        assert se_repo.count_for_service(db, "auth_service") == 1100

        # Дозаписываем ещё один через API — он попадёт в self-audit details.
        payload = {"events": [{"action": "user.new_one"}]}
        r = client.post(
            f"{SERVICES_URL}/auth_service/events",
            json=payload,
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        # API total корректный (1101).
        assert body["total"] == 1101, body

        # И self-audit row тоже несёт точный total.
        from src.repositories import events as events_repo
        rows, _, _ = events_repo.query(
            db,
            service="loging_service",
            action="logging.service_events_registered",
            include_total=False,
        )
        assert rows, "self-audit row should exist"
        last = rows[0]
        assert last.details["total"] == 1101, last.details


# ── 4: _redact_value bytes ────────────────────────────────────────────────


class TestRedactBytes:
    def test_bytes_under_password_key_redacted(self):
        """bytes-значение под чувствительным ключом маскируется placeholder'ом."""
        from src.utils.redaction import redact

        result = redact({"password": b"super-secret-bytes"})
        assert result == {"password": "<PASSWORD>"}

    def test_bytearray_under_token_key_redacted(self):
        from src.utils.redaction import redact

        result = redact({"access_token": bytearray(b"abc-secret")})
        assert result == {"access_token": "<TOKEN>"}

    def test_bytes_jwt_classified_after_decode(self):
        """bytes-JWT декодируется и попадает в `_classify_value`."""
        from src.utils.redaction import redact

        # JWT-like форма: три blob'а по 8+ символов через точки.
        jwt_bytes = b"abcdefgh.ijklmnop.qrstuvwx"
        result = redact({"some_field": jwt_bytes})
        assert result == {"some_field": "<TOKEN>"}

    def test_bytes_argon2_hash_classified(self):
        from src.utils.redaction import redact

        argon2 = b"$argon2id$v=19$m=65536,t=3,p=4$saltdata$hashdata"
        result = redact({"data": argon2})
        assert result == {"data": "<HASH>"}

    def test_bytes_no_classification_decoded_to_str(self):
        """Обычные bytes без классификации возвращаются как decoded str
        (не как `b'...'`-репрезентация)."""
        from src.utils.redaction import redact

        result = redact({"plain": b"hello world"})
        assert result == {"plain": "hello world"}

    def test_bytes_invalid_utf8_replaced(self):
        """Не-utf-8 bytes не валят redact — `errors='replace'` подменяет
        невалидные байты на U+FFFD."""
        from src.utils.redaction import redact

        result = redact({"raw": b"\xff\xfe-binary"})
        assert isinstance(result["raw"], str)
        # Хвост сохранён, начало заменено placeholder'ом.
        assert "binary" in result["raw"]


# ── Info: _CONTENT_LENGTH_RE и description \x0d — sanity ──────────────────


class TestSchemaSanityNotes:
    """W17 inventory указал на `\\x0d` (CR) и `_CONTENT_LENGTH_RE` без
    якорей. Реально description-regex `[\\x00-\\x08\\x0a-\\x1f\\x7f]` уже
    блокирует CR (0x0d ∈ [0x0a, 0x1f]); `_CONTENT_LENGTH_RE` используется
    через `.fullmatch()` — якоря избыточны. Тесты-инварианты, чтобы
    регрессия не пропустила CR/CRLF/leading-plus."""

    def test_description_rejects_lone_cr(self):
        import pytest
        from pydantic import ValidationError
        from src.schemas.services import EventDefinition

        with pytest.raises(ValidationError):
            EventDefinition(action="user.login", description="line1\rline2")

    def test_content_length_re_fullmatch_strict(self):
        from src.main import _CONTENT_LENGTH_RE

        # Только цифры — match.
        assert _CONTENT_LENGTH_RE.fullmatch("100") is not None
        assert _CONTENT_LENGTH_RE.fullmatch("0") is not None
        # `+`, пробел, underscore, hex — не должны match'иться через fullmatch.
        assert _CONTENT_LENGTH_RE.fullmatch("+100") is None
        assert _CONTENT_LENGTH_RE.fullmatch("100 ") is None
        assert _CONTENT_LENGTH_RE.fullmatch("1_000") is None
        assert _CONTENT_LENGTH_RE.fullmatch("0x10") is None
        assert _CONTENT_LENGTH_RE.fullmatch("") is None
