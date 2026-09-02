"""Юнит-тесты: src/services/audit_context.py + audit_service.emit подхватывает контекст."""

import logging

import pytest

from src.services import audit_context, audit_service
from src.services.audit_context import AuditContext


@pytest.fixture(autouse=True)
def reset_audit_context_between_tests():
    """Контекст — process-global contextvar; гарантируем чистый старт для каждого теста."""
    # Сохраняем текущее значение, после теста возвращаем.
    yield
    # Сбрасываем (set None через token-механизм нельзя — ContextVar.reset нужен token).
    # Создаём пустой и кладём — следующий тест начнёт с него.
    audit_context._current.set(None)  # type: ignore[attr-defined]


# ── AuditContext base ─────────────────────────────────────────────────────────


class TestAuditContextBase:
    def test_default_empty_context(self):
        ctx = audit_context.get_context()
        assert ctx.actor_id is None
        assert ctx.username is None
        assert ctx.department_id is None
        assert ctx.request_id is None
        assert ctx.ip_address is None
        assert ctx.user_agent is None

    def test_set_and_get(self):
        token = audit_context.set_context(AuditContext(
            actor_id="usr_1", username="ivanov", department_id="dep_a",
            request_id="req_1", ip_address="1.2.3.4", user_agent="curl/7.81",
        ))
        try:
            ctx = audit_context.get_context()
            assert ctx.actor_id == "usr_1"
            assert ctx.username == "ivanov"
            assert ctx.department_id == "dep_a"
            assert ctx.ip_address == "1.2.3.4"
        finally:
            audit_context.reset_context(token)

    def test_reset_restores_previous(self):
        token = audit_context.set_context(AuditContext(actor_id="usr_outer"))
        try:
            inner = audit_context.set_context(AuditContext(actor_id="usr_inner"))
            assert audit_context.get_context().actor_id == "usr_inner"
            audit_context.reset_context(inner)
            assert audit_context.get_context().actor_id == "usr_outer"
        finally:
            audit_context.reset_context(token)

    def test_update_context_extends_existing(self):
        audit_context.set_context(AuditContext(actor_id="usr_1"))
        audit_context.update_context(username="ivanov", department_id="dep_a")
        ctx = audit_context.get_context()
        assert ctx.actor_id == "usr_1"
        assert ctx.username == "ivanov"
        assert ctx.department_id == "dep_a"

    def test_update_context_ignores_none(self):
        audit_context.set_context(AuditContext(actor_id="usr_1", username="x"))
        audit_context.update_context(username=None)  # не должен затереть
        assert audit_context.get_context().username == "x"

    def test_update_context_creates_when_empty(self):
        audit_context.update_context(actor_id="usr_new")
        assert audit_context.get_context().actor_id == "usr_new"

    def test_update_context_extra_fields(self):
        audit_context.update_context(custom_field="my_value")
        assert audit_context.get_context().extra == {"custom_field": "my_value"}


# ── emit() подхватывает контекст ──────────────────────────────────────────────


def _capture_payload(monkeypatch) -> list[dict]:
    """Перехватывает payload из emit'а в sync-контексте.

    Sync-fallback больше не делает blocking httpx.post (выпиливали из-за DoS
    thread pool под недоступным loging_service). Payload теперь уходит в
    `logger.info('audit_event_fallback %s', payload)`. Перехватываем его
    через monkeypatch на `audit_service.logger.info`.
    """
    captured: list[dict] = []

    original_info = audit_service.logger.info

    def fake_info(msg, *args, **kwargs):
        if isinstance(msg, str) and msg.startswith("audit_event_fallback") and args:
            payload = args[0]
            if isinstance(payload, dict):
                captured.append(payload)
                return
        original_info(msg, *args, **kwargs)

    monkeypatch.setattr(audit_service.logger, "info", fake_info)
    # Заставим emit() пойти по sync-пути: настроим logging_url+api_key, но без event loop
    monkeypatch.setattr("src.services.audit_service.get_settings", lambda: type(
        "S", (), {"logging_service_url": "http://test", "logging_service_api_key": "k"},
    )())
    return captured


class TestEmitPicksUpContext:
    def test_username_from_context(self, monkeypatch):
        captured = _capture_payload(monkeypatch)
        audit_context.set_context(AuditContext(
            actor_id="usr_1", username="ivanov", department_id="dep_a", request_id="req_xyz",
        ))
        audit_service.emit("user.me", status="success")
        assert len(captured) == 1
        assert captured[0]["actor_id"] == "usr_1"
        assert captured[0]["username"] == "ivanov"
        assert captured[0]["department_id"] == "dep_a"
        assert captured[0]["request_id"] == "req_xyz"

    def test_department_name_from_context(self, monkeypatch):
        captured = _capture_payload(monkeypatch)
        audit_context.set_context(AuditContext(
            actor_id="usr_1", username="ivanov",
            department_id="dep_a", department_name="Dept A",
        ))
        audit_service.emit("user.me", status="success")
        assert captured[0]["department_name"] == "Dept A"
        assert captured[0]["department_id"] == "dep_a"

    def test_department_name_none_without_dept(self, monkeypatch):
        captured = _capture_payload(monkeypatch)
        audit_service.emit("service.started", actor_type="service")
        assert captured[0]["department_name"] is None

    def test_explicit_department_name_overrides_context(self, monkeypatch):
        captured = _capture_payload(monkeypatch)
        audit_context.set_context(AuditContext(department_name="Ctx Dept"))
        audit_service.emit("user.create", department_name="Explicit Dept",
                           status="success")
        assert captured[0]["department_name"] == "Explicit Dept"

    def test_explicit_param_overrides_context(self, monkeypatch):
        captured = _capture_payload(monkeypatch)
        audit_context.set_context(AuditContext(
            actor_id="usr_ctx", username="ctx_user",
        ))
        audit_service.emit("user.create", actor_id="usr_explicit",
                           username="explicit_user", status="success")
        assert captured[0]["actor_id"] == "usr_explicit"
        assert captured[0]["username"] == "explicit_user"

    def test_ip_and_user_agent_added_to_details(self, monkeypatch):
        captured = _capture_payload(monkeypatch)
        audit_context.set_context(AuditContext(
            actor_id="usr_1", ip_address="10.0.0.1", user_agent="curl/8",
        ))
        audit_service.emit("user.login", status="success")
        details = captured[0]["details"]
        assert details["ip"] == "10.0.0.1"
        assert details["user_agent"] == "curl/8"

    def test_explicit_details_ip_not_overwritten(self, monkeypatch):
        captured = _capture_payload(monkeypatch)
        audit_context.set_context(AuditContext(ip_address="10.0.0.1"))
        audit_service.emit("user.login", details={"ip": "1.1.1.1"}, status="success")
        assert captured[0]["details"]["ip"] == "1.1.1.1"

    def test_no_context_no_ip_no_ua(self, monkeypatch):
        captured = _capture_payload(monkeypatch)
        audit_service.emit("service.started", actor_type="service")
        details = captured[0]["details"]
        assert "ip" not in details
        assert "user_agent" not in details


# ── emit() применяет redaction ────────────────────────────────────────────────


class TestEmitAppliesRedaction:
    def test_password_in_details_masked(self, monkeypatch):
        captured = _capture_payload(monkeypatch)
        audit_service.emit("user.login", details={"password": "p@ssword"},
                           status="failure", allowed=False)
        assert captured[0]["details"]["password"] == "<PASSWORD>"

    def test_token_in_nested_details_masked(self, monkeypatch):
        captured = _capture_payload(monkeypatch)
        audit_service.emit("bot.token_create",
                           details={"bot": {"id": "b1", "token": "secret-jwt"}})
        assert captured[0]["details"]["bot"]["token"] == "<TOKEN>"
        assert captured[0]["details"]["bot"]["id"] == "b1"

    def test_jwt_shaped_value_masked(self, monkeypatch):
        captured = _capture_payload(monkeypatch)
        audit_service.emit("oauth.code_exchanged",
                           details={"code_metadata": "eyJabcdefgh.eyJijklmnop.signature1234"})
        assert captured[0]["details"]["code_metadata"] == "<TOKEN>"

    def test_clean_details_pass_through(self, monkeypatch):
        captured = _capture_payload(monkeypatch)
        audit_service.emit("user.login",
                           details={"reason": "invalid_credentials", "attempts": 3},
                           status="failure", allowed=False)
        assert captured[0]["details"] == {"reason": "invalid_credentials", "attempts": 3}


# ── Fallback в logger когда url не настроен ──────────────────────────────────


class TestEmitFallback:
    def test_no_url_skips_http_emit(self, monkeypatch):
        # Настройки без URL → no http path. Проверяем что create_task НЕ
        # вызывается и `_EMIT_TASKS` не растёт — это и есть наблюдаемый
        # behavior fallback'а (substring-проверка лог-сообщения была хрупкой
        # к ребрендингу формата).
        monkeypatch.setattr("src.services.audit_service.get_settings", lambda: type(
            "S", (), {"logging_service_url": None, "logging_service_api_key": None},
        )())
        before = len(audit_service._EMIT_TASKS)
        audit_service.emit("user.me", actor_id="u1")
        assert len(audit_service._EMIT_TASKS) == before


# ── extract_client_ip: X-Forwarded-For + allow-list ───────────────────────────


class _FakeClient:
    def __init__(self, host: str | None):
        self.host = host


class _FakeRequest:
    """Минимальный stub: extract_client_ip читает только .client.host и .headers.get()."""

    def __init__(self, client_host: str | None, headers: dict[str, str] | None = None):
        self.client = _FakeClient(client_host) if client_host is not None else None
        self.headers = headers or {}


class TestExtractClientIPAllowList:
    """X-Forwarded-For доверяем ТОЛЬКО если direct client.host ∈ trusted_proxy_ips."""

    def test_no_trusted_proxy_xff_ignored(self):
        """Default (allow-list пустой) → XFF полностью игнорируется."""
        req = _FakeRequest(
            client_host="203.0.113.42",
            headers={"X-Forwarded-For": "1.2.3.4"},
        )
        assert audit_context.extract_client_ip(req, trusted_proxy_ips=[]) == "203.0.113.42"

    def test_untrusted_client_xff_ignored(self):
        """Запрос от НЕ-доверенного IP → XFF игнорируется, даже если allow-list не пуст."""
        req = _FakeRequest(
            client_host="203.0.113.42",  # внешний клиент
            headers={"X-Forwarded-For": "1.2.3.4"},
        )
        result = audit_context.extract_client_ip(req, trusted_proxy_ips=["10.0.0.1"])
        assert result == "203.0.113.42"

    def test_untrusted_client_xreal_ip_ignored(self):
        """X-Real-IP тоже spoofable → игнорируется без trusted proxy."""
        req = _FakeRequest(
            client_host="203.0.113.42",
            headers={"X-Real-IP": "1.2.3.4"},
        )
        result = audit_context.extract_client_ip(req, trusted_proxy_ips=["10.0.0.1"])
        assert result == "203.0.113.42"

    def test_trusted_proxy_xff_used(self):
        """Запрос от proxy из allow-list → XFF парсится."""
        req = _FakeRequest(
            client_host="10.0.0.1",
            headers={"X-Forwarded-For": "203.0.113.42"},
        )
        result = audit_context.extract_client_ip(req, trusted_proxy_ips=["10.0.0.1"])
        assert result == "203.0.113.42"

    def test_trusted_proxy_xreal_ip_fallback(self):
        """Если XFF нет, но есть X-Real-IP → используется он."""
        req = _FakeRequest(
            client_host="10.0.0.1",
            headers={"X-Real-IP": "203.0.113.42"},
        )
        result = audit_context.extract_client_ip(req, trusted_proxy_ips=["10.0.0.1"])
        assert result == "203.0.113.42"

    def test_trusted_proxy_no_headers_returns_direct(self):
        """Доверенный proxy без forwarded-заголовков → его собственный IP."""
        req = _FakeRequest(client_host="10.0.0.1", headers={})
        result = audit_context.extract_client_ip(req, trusted_proxy_ips=["10.0.0.1"])
        assert result == "10.0.0.1"

    def test_xff_leftmost_when_multiple(self):
        """`X-Forwarded-For: client, proxy1, proxy2` → берём самый левый non-trusted."""
        req = _FakeRequest(
            client_host="10.0.0.1",
            headers={"X-Forwarded-For": "203.0.113.42, 10.0.0.2, 10.0.0.1"},
        )
        result = audit_context.extract_client_ip(
            req, trusted_proxy_ips=["10.0.0.1", "10.0.0.2"]
        )
        assert result == "203.0.113.42"

    def test_xff_leftmost_skips_trusted_at_start(self):
        """Если самый левый в XFF — доверенный proxy, берём следующий non-trusted."""
        req = _FakeRequest(
            client_host="10.0.0.1",
            headers={"X-Forwarded-For": "10.0.0.5, 198.51.100.7, 10.0.0.2"},
        )
        result = audit_context.extract_client_ip(
            req, trusted_proxy_ips=["10.0.0.1", "10.0.0.2", "10.0.0.5"]
        )
        assert result == "198.51.100.7"

    def test_xff_all_trusted_returns_direct(self):
        """Если все IP в цепочке — доверенные → возвращаем прямой client.host."""
        req = _FakeRequest(
            client_host="10.0.0.1",
            headers={"X-Forwarded-For": "10.0.0.2, 10.0.0.1"},
        )
        result = audit_context.extract_client_ip(
            req, trusted_proxy_ips=["10.0.0.1", "10.0.0.2"]
        )
        assert result == "10.0.0.1"

    def test_xff_invalid_ips_skipped(self):
        """Невалидные IP в XFF пропускаются."""
        req = _FakeRequest(
            client_host="10.0.0.1",
            headers={"X-Forwarded-For": "not-an-ip, , 203.0.113.42"},
        )
        result = audit_context.extract_client_ip(req, trusted_proxy_ips=["10.0.0.1"])
        assert result == "203.0.113.42"

    def test_cidr_allow_list(self):
        """allow-list поддерживает CIDR."""
        req = _FakeRequest(
            client_host="10.0.0.42",
            headers={"X-Forwarded-For": "203.0.113.42"},
        )
        result = audit_context.extract_client_ip(req, trusted_proxy_ips=["10.0.0.0/8"])
        assert result == "203.0.113.42"

    def test_cidr_allow_list_excludes_outside_network(self):
        """IP вне CIDR — НЕ доверенный → XFF игнорируется."""
        req = _FakeRequest(
            client_host="172.16.0.1",
            headers={"X-Forwarded-For": "203.0.113.42"},
        )
        result = audit_context.extract_client_ip(req, trusted_proxy_ips=["10.0.0.0/8"])
        assert result == "172.16.0.1"

    def test_no_client_returns_none(self):
        """request.client is None и без trusted-логики → None."""
        req = _FakeRequest(client_host=None, headers={"X-Forwarded-For": "1.2.3.4"})
        assert audit_context.extract_client_ip(req, trusted_proxy_ips=["10.0.0.1"]) is None

    def test_settings_default_empty_xff_ignored(self, monkeypatch):
        """Без явного `trusted_proxy_ips` параметра — берётся из Settings; default = [] → XFF ignored."""
        # Подменяем get_settings, чтобы не зависеть от .env / lru_cache
        monkeypatch.setattr(
            "src.core.config.get_settings",
            lambda: type("S", (), {"trusted_proxy_ips": []})(),
        )
        req = _FakeRequest(
            client_host="203.0.113.42",
            headers={"X-Forwarded-For": "1.2.3.4"},
        )
        assert audit_context.extract_client_ip(req) == "203.0.113.42"

    def test_settings_with_trusted_proxy_xff_used(self, monkeypatch):
        """Settings.trusted_proxy_ips заполнен → XFF используется при доверенном source."""
        monkeypatch.setattr(
            "src.core.config.get_settings",
            lambda: type("S", (), {"trusted_proxy_ips": ["10.0.0.1"]})(),
        )
        req = _FakeRequest(
            client_host="10.0.0.1",
            headers={"X-Forwarded-For": "203.0.113.42"},
        )
        assert audit_context.extract_client_ip(req) == "203.0.113.42"


class TestTrustedProxyIPsSetting:
    """`Settings.trusted_proxy_ips` — формат и парсинг.

    Используем alias `TRUSTED_PROXY_IPS` (как в env), потому что
    pydantic-settings в этом проекте не использует populate_by_name.
    """

    def test_default_is_empty_list(self):
        from src.core.config import Settings
        s = Settings()
        assert s.trusted_proxy_ips == []

    def test_accepts_list(self):
        from src.core.config import Settings
        s = Settings(TRUSTED_PROXY_IPS=["10.0.0.1", "192.168.0.0/24"])
        assert s.trusted_proxy_ips == ["10.0.0.1", "192.168.0.0/24"]

    def test_accepts_comma_separated_string(self):
        """Env vars обычно приходят строкой — поддерживаем comma-separated формат."""
        from src.core.config import Settings
        s = Settings(TRUSTED_PROXY_IPS="10.0.0.1, 10.0.0.2 , 192.168.0.0/24")
        assert s.trusted_proxy_ips == ["10.0.0.1", "10.0.0.2", "192.168.0.0/24"]

    def test_accepts_json_string(self):
        from src.core.config import Settings
        s = Settings(TRUSTED_PROXY_IPS='["10.0.0.1","10.0.0.2"]')
        assert s.trusted_proxy_ips == ["10.0.0.1", "10.0.0.2"]

    def test_empty_string_becomes_empty_list(self):
        from src.core.config import Settings
        s = Settings(TRUSTED_PROXY_IPS="")
        assert s.trusted_proxy_ips == []
