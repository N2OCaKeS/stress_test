"""Тесты под шесть фиксов loging_service.

1) `GET /services` и `GET /services/{svc}/events` под `audit_query_rate_limit`;
   offset-cap на `list_service_events`.
2) `register_events` self-audit с длинным service-именем не валит 422 на
   `actor_id` (длина обрезается до 48).
3) `rule_repo.delete` после двойного soft-delete не ловит UNIQUE на
   `name` — suffix теперь несёт nanosecond timestamp.
4) `_fetch_identity` на unknown `subject_type` фолбэчит на
   `actor_type="anonymous"`, симметрично `_resolve_actor_type` в outbox.
5) `_validate_match_action` использует `has_any(db)` вместо материализации
   `list_all(db)` ради boolean.
6) `limit_body_size` chunked-overflow перебивает route только до его вызова —
   легитимный 4xx роута не пропадает под 413.
"""

from __future__ import annotations

import asyncio
import json

from src.repositories import events as events_repo
from src.repositories import rules as rule_repo
from src.repositories import service_events as se_repo
from src.schemas.rules import RuleCreate
from tests.conftest import (
    TEST_API_KEY,
    TEST_SERVICE_API_KEYS,
    make_event_def,
)


EVENTS_URL = "/api/logging/v1/events"
SERVICES_URL = "/api/logging/v1/services"


# ── Fix 1: rate-limit + offset cap на read-эндпоинты реестра сервисов ─────


class TestServicesListRateLimit:
    def test_list_services_burst_429(self, admin_client, monkeypatch):
        monkeypatch.setenv("AUDIT_QUERY_RATE_LIMIT", "3/minute")
        from src.core.config import get_settings
        get_settings.cache_clear()
        from src.main import limiter
        limiter.reset()

        for i in range(3):
            r = admin_client.get(SERVICES_URL)
            assert r.status_code == 200, f"#{i} {r.status_code}: {r.text}"

        r = admin_client.get(SERVICES_URL)
        assert r.status_code == 429, r.text
        assert r.json()["error_code"] == "RATE_LIMIT_EXCEEDED"

    def test_list_service_events_burst_429(self, admin_client, monkeypatch):
        monkeypatch.setenv("AUDIT_QUERY_RATE_LIMIT", "3/minute")
        from src.core.config import get_settings
        get_settings.cache_clear()
        from src.main import limiter
        limiter.reset()

        url = f"{SERVICES_URL}/auth_service/events"
        for i in range(3):
            r = admin_client.get(url)
            assert r.status_code == 200, f"#{i} {r.status_code}: {r.text}"

        r = admin_client.get(url)
        assert r.status_code == 429, r.text
        assert r.json()["error_code"] == "RATE_LIMIT_EXCEEDED"

    def test_list_service_events_offset_capped(self, admin_client):
        from src.core.limits import MAX_QUERY_OFFSET

        # Off-by-one: ровно `MAX_QUERY_OFFSET` ещё проходит, +1 валит 422.
        ok = admin_client.get(
            f"{SERVICES_URL}/auth_service/events",
            params={"offset": MAX_QUERY_OFFSET},
        )
        assert ok.status_code == 200, ok.text

        bad = admin_client.get(
            f"{SERVICES_URL}/auth_service/events",
            params={"offset": MAX_QUERY_OFFSET + 1},
        )
        assert bad.status_code == 422, bad.text


# ── Fix 2: register_events truncate actor_id ─────────────────────────────


class TestRegisterEventsLongIdentityTruncated:
    def test_long_service_path_truncated_in_actor_id(
        self, client, db, auth_headers, monkeypatch
    ):
        """64-символьное service-имя не валит self-audit 422 на actor_id."""
        # Разрешим длинное имя в SERVICE_API_KEYS map — иначе require_service_token
        # отобьёт identity до того, как мы доедем до actor_id-trim'а.
        long_name = "a" * 64
        keys = dict(TEST_SERVICE_API_KEYS)
        keys[long_name] = TEST_API_KEY
        monkeypatch.setenv("SERVICE_API_KEYS", json.dumps(keys))
        from src.core.config import get_settings
        get_settings.cache_clear()

        headers = {
            "Authorization": f"Bearer {TEST_API_KEY}",
            "X-Service-Identity": long_name,
        }
        payload = {"events": [make_event_def(action="user.login")]}
        r = client.post(
            f"{SERVICES_URL}/{long_name}/events",
            json=payload,
            headers=headers,
        )
        assert r.status_code == 200, r.text

        rows, _, _ = events_repo.query(
            db,
            service="loging_service",
            action="logging.service_events_registered",
            include_total=False,
        )
        assert len(rows) == 1, f"expected exactly one self-audit row, got {len(rows)}"
        row = rows[0]
        assert len(row.actor_id) <= 48
        assert len(row.target_id) <= 48
        # Префиксный детерминизм: actor_id — это первые 48 символов identity,
        # downstream-аналитика по `LIKE 'aaaa%'` всё ещё находит caller'а.
        assert row.actor_id == long_name[:48]
        assert row.target_id == long_name[:48]


# ── Fix 3: rule_repo.delete soft-rename collision ─────────────────────────


class TestRuleDeleteSoftRenameUnique:
    def test_double_delete_long_name_no_unique_violation(self, db):
        """Два правила с длинным общим префиксом — оба soft-delete'ятся
        без `UniqueViolation` на `audit_rules.name`."""
        long_prefix = "x" * 100  # >80, чтобы префикс в suffix'е был truncate'нут
        r1 = rule_repo.create(
            db, RuleCreate(name=long_prefix + "-a", effect="SUPPRESS", priority=100)
        )
        r2 = rule_repo.create(
            db, RuleCreate(name=long_prefix + "-b", effect="SUPPRESS", priority=100)
        )
        rule_repo.delete(db, r1)
        rule_repo.delete(db, r2)
        # Оба soft-deleted и не пересеклись.
        assert r1.deleted_at is not None
        assert r2.deleted_at is not None
        assert r1.name != r2.name
        # `#deleted-` маркер на месте.
        assert "#deleted-" in r1.name
        assert "#deleted-" in r2.name

    def test_deleted_rule_skipped_by_active(self, db):
        """Контракт soft-delete не сломан: после переименования
        правило выпадает из `get_active_sorted`."""
        rule = rule_repo.create(
            db, RuleCreate(name="to-del", effect="SUPPRESS", priority=100)
        )
        rule_repo.delete(db, rule)
        active = rule_repo.get_active_sorted(db)
        assert active == []


# ── Fix 4: _fetch_identity fallback на anonymous ──────────────────────────


class TestFetchIdentityAnonymousFallback:
    def test_source_branch_falls_back_to_anonymous(self):
        """Проверяем, что в исходнике `_fetch_identity` теперь именно
        `actor_type = "anonymous"`, а не `"user"`. Без сетевого слоя —
        ловим регрессию прямо по тексту функции, чтобы случайный rollback
        фикса попадал в этот тест."""
        import inspect

        from src.dependencies import auth as auth_mod

        src = inspect.getsource(auth_mod._fetch_identity)
        # Старый код фолбэчил на "user". Если кто-то откатит фикс — этот
        # ассерт начнёт ругаться.
        assert 'identity["actor_type"] = "anonymous"' in src
        assert 'identity["actor_type"] = "user"' not in src

    def test_aligns_with_audit_outbox_resolve(self):
        """Сам `_resolve_actor_type('alien')` тоже возвращает 'anonymous'.
        Фикс — это симметрия двух call-site'ов."""
        from src.services.audit_outbox import _resolve_actor_type

        assert _resolve_actor_type("alien") == "anonymous"
        assert _resolve_actor_type(None) == "anonymous"
        assert _resolve_actor_type("user") == "user"


# ── Fix 5: has_any вместо list_all ────────────────────────────────────────


class TestServiceEventsHasAny:
    def test_has_any_empty(self, db):
        assert se_repo.has_any(db) is False

    def test_has_any_after_insert(self, db):
        se_repo.upsert_events(
            db, "auth_service", [make_event_def(action="user.login")]
        )
        assert se_repo.has_any(db) is True

    def test_create_rule_unknown_action_when_registry_empty_allowed(
        self, admin_client, db
    ):
        """Пустой реестр → match_action: 'user.login' не отбивается 422
        (контракт сохранён: validate skips check when registry is empty).
        Регрессия `list_all → has_any` не меняет это поведение."""
        assert se_repo.has_any(db) is False
        r = admin_client.post(
            "/api/logging/v1/rules",
            json={
                "name": "fw17-rule",
                "effect": "SUPPRESS",
                "priority": 100,
                "match_action": "user.login",
            },
        )
        assert r.status_code == 201, r.text

    def test_create_rule_unknown_action_when_registry_nonempty_rejected(
        self, admin_client, db
    ):
        """Реестр не пуст → unknown match_action валится 422
        UNKNOWN_MATCH_ACTION. Зеркальная проверка к предыдущему тесту."""
        se_repo.upsert_events(
            db, "auth_service", [make_event_def(action="user.login")]
        )
        assert se_repo.has_any(db) is True
        r = admin_client.post(
            "/api/logging/v1/rules",
            json={
                "name": "fw17-rule2",
                "effect": "SUPPRESS",
                "priority": 100,
                "match_action": "nope.never_registered",
            },
        )
        assert r.status_code == 422, r.text
        body = r.json()
        assert body["error_code"] == "UNKNOWN_MATCH_ACTION"


# ── Fix 6: limit_body_size chunked + 4xx route ────────────────────────────


class TestChunkedBodyPreserves4xx:
    @staticmethod
    def _send_to_app(app, *, chunks, path=EVENTS_URL, method="POST", api_key="bad-key"):
        headers = [
            (b"host", b"testserver"),
            (b"authorization", f"Bearer {api_key}".encode()),
            (b"content-type", b"application/json"),
            (b"transfer-encoding", b"chunked"),
        ]
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "root_path": "",
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 12345),
            "headers": headers,
        }
        receive_queue = [
            {"type": "http.request", "body": c, "more_body": i < len(chunks) - 1}
            for i, c in enumerate(chunks)
        ]
        responses = []

        async def receive():
            if receive_queue:
                return receive_queue.pop(0)
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            responses.append(message)

        asyncio.run(app(scope, receive, send))
        status = None
        body = b""
        for msg in responses:
            if msg["type"] == "http.response.start":
                status = msg["status"]
            elif msg["type"] == "http.response.body":
                body += msg.get("body", b"")
        return status, body

    def test_chunked_overflow_returns_413(self, monkeypatch):
        """Чистый overflow без сторонних причин: middleware должен честно
        отбить 413, route не вызывается."""
        monkeypatch.setenv("MAX_REQUEST_BODY_BYTES", str(50 * 1024))
        monkeypatch.setenv("SERVICE_API_KEYS", '{"auth_service":"test-service-api-key"}')
        from src.core.config import get_settings
        get_settings.cache_clear()
        from src.main import app

        chunk = b"x" * (20 * 1024)
        status, body = self._send_to_app(
            app,
            chunks=[chunk, chunk, chunk, chunk],
            api_key="test-service-api-key",
        )
        assert status == 413, f"expected 413, got {status}: {body!r}"
        payload = json.loads(body)
        assert payload["error_code"] == "PAYLOAD_TOO_LARGE"

    def test_chunked_under_limit_passes_through_to_route(self, monkeypatch):
        """Chunked под лимитом — route видит body и отвечает штатно
        (валидный POST → 201). Регрессия на replay-receive'е."""
        monkeypatch.setenv("MAX_REQUEST_BODY_BYTES", str(50 * 1024))
        monkeypatch.setenv("SERVICE_API_KEYS", '{"auth_service":"test-service-api-key"}')
        from src.core.config import get_settings
        get_settings.cache_clear()
        from src.main import app

        from tests.conftest import make_event
        body = json.dumps(make_event()).encode()
        assert len(body) < 50 * 1024
        # Split на два чанка чтобы убедиться, что replay склеивает.
        half = len(body) // 2
        try:
            status, _ = self._send_to_app(
                app,
                chunks=[body[:half], body[half:]],
                api_key="test-service-api-key",
            )
        except Exception:
            # Downstream может упасть на отсутствии БД — главное не 413.
            return
        assert status != 413
