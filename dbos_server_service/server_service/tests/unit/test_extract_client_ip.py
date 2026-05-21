"""Тесты `audit_context.extract_client_ip` + `Settings.trusted_proxy_ips`.

Парный тест к `auth_service/tests/core/test_audit_context.py:TestExtractClientIPAllowList`
— закрывает дыру «`_extract_client_ip` без allow-list».

Что закрываем:
* без `trusted_proxy_ips` → `X-Forwarded-For` / `X-Real-IP` полностью игнорируются
  (любой запрос с подложным `X-Forwarded-For: 1.2.3.4` НЕ подделает actor IP в audit-trail);
* только запрос от IP, входящего в allow-list, доверяет forwarded-заголовкам;
* CIDR-нотация (`10.0.0.0/8`) поддерживается;
* `Settings.trusted_proxy_ips` парсит env-string в трёх форматах
  (native list / comma-separated / JSON-string).
"""

from __future__ import annotations

from src.services import audit_context


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
        """Запрос от НЕ-доверенного IP → XFF игнорируется, даже при непустом allow-list."""
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

    Используем alias `TRUSTED_PROXY_IPS` (env-style), потому что
    pydantic-settings в этом проекте не использует `populate_by_name`.
    """

    def test_default_is_empty_list(self):
        from src.core.config import Settings
        # Нужны обязательные поля для server_service Settings — auth_service_url
        # и database_url + server_encryption_key.
        s = Settings(
            DATABASE_URL="postgresql+psycopg://x:y@h/db",
            AUTH_SERVICE_URL="http://auth",
            SERVER_ENCRYPTION_KEY="x" * 32,
        )
        assert s.trusted_proxy_ips == []

    def test_accepts_list(self):
        from src.core.config import Settings
        s = Settings(
            DATABASE_URL="postgresql+psycopg://x:y@h/db",
            AUTH_SERVICE_URL="http://auth",
            SERVER_ENCRYPTION_KEY="x" * 32,
            TRUSTED_PROXY_IPS=["10.0.0.1", "192.168.0.0/24"],
        )
        assert s.trusted_proxy_ips == ["10.0.0.1", "192.168.0.0/24"]

    def test_accepts_comma_separated_string(self):
        """Env vars обычно приходят строкой — поддерживаем comma-separated формат."""
        from src.core.config import Settings
        s = Settings(
            DATABASE_URL="postgresql+psycopg://x:y@h/db",
            AUTH_SERVICE_URL="http://auth",
            SERVER_ENCRYPTION_KEY="x" * 32,
            TRUSTED_PROXY_IPS="10.0.0.1, 10.0.0.2 , 192.168.0.0/24",
        )
        assert s.trusted_proxy_ips == ["10.0.0.1", "10.0.0.2", "192.168.0.0/24"]

    def test_accepts_json_string(self):
        from src.core.config import Settings
        s = Settings(
            DATABASE_URL="postgresql+psycopg://x:y@h/db",
            AUTH_SERVICE_URL="http://auth",
            SERVER_ENCRYPTION_KEY="x" * 32,
            TRUSTED_PROXY_IPS='["10.0.0.1","10.0.0.2"]',
        )
        assert s.trusted_proxy_ips == ["10.0.0.1", "10.0.0.2"]

    def test_empty_string_becomes_empty_list(self):
        from src.core.config import Settings
        s = Settings(
            DATABASE_URL="postgresql+psycopg://x:y@h/db",
            AUTH_SERVICE_URL="http://auth",
            SERVER_ENCRYPTION_KEY="x" * 32,
            TRUSTED_PROXY_IPS="",
        )
        assert s.trusted_proxy_ips == []
