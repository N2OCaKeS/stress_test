"""Тесты `core/limiter._resolve_client_ip` за k8s-ingress'ом.

Без этого фикса `slowapi.util.get_remote_address` возвращал
`request.client.host` — за ingress'ом всегда IP самого ingress'а, и
per-IP-лимиты на всех клиентах сливались в одну корзину (один HTTP-flood
с любого внешнего клиента выжигал лимит для всех остальных).

Покрываем:
* trusted ingress + XFF cепочка → ключ = настоящий client IP.
* untrusted source + XFF → ключ остаётся direct host (XFF игнорится).
* без XFF — direct host.
* request.client is None — fallback на `get_remote_address` (не падаем).
"""

from __future__ import annotations


class _FakeClient:
    def __init__(self, host: str | None):
        self.host = host


class _FakeRequest:
    """Минимальный stub под slowapi key_func + extract_client_ip."""

    def __init__(self, client_host: str | None, headers: dict[str, str] | None = None):
        self.client = _FakeClient(client_host) if client_host is not None else None
        self.headers = headers or {}


class TestResolveClientIP:
    def test_trusted_ingress_xff_chain(self, monkeypatch):
        """`Client, Proxy1, Proxy2` за trusted ingress → ключ = `Client`, не `Proxy2`."""
        monkeypatch.setattr(
            "src.core.config.get_settings",
            lambda: type("S", (), {"trusted_proxy_ips": ["10.0.0.1", "10.0.0.2"]})(),
        )
        from src.core.limiter import _resolve_client_ip

        req = _FakeRequest(
            client_host="10.0.0.2",
            headers={"X-Forwarded-For": "203.0.113.42, 10.0.0.1, 10.0.0.2"},
        )
        assert _resolve_client_ip(req) == "203.0.113.42"

    def test_untrusted_source_xff_ignored(self, monkeypatch):
        """XFF от не-trusted клиента не должен подменять ключ."""
        monkeypatch.setattr(
            "src.core.config.get_settings",
            lambda: type("S", (), {"trusted_proxy_ips": ["10.0.0.1"]})(),
        )
        from src.core.limiter import _resolve_client_ip

        req = _FakeRequest(
            client_host="203.0.113.42",
            headers={"X-Forwarded-For": "1.2.3.4"},
        )
        assert _resolve_client_ip(req) == "203.0.113.42"

    def test_no_trusted_proxy_setting(self, monkeypatch):
        """trusted_proxy_ips=[] → ключ всегда direct host."""
        monkeypatch.setattr(
            "src.core.config.get_settings",
            lambda: type("S", (), {"trusted_proxy_ips": []})(),
        )
        from src.core.limiter import _resolve_client_ip

        req = _FakeRequest(
            client_host="10.0.0.2",
            headers={"X-Forwarded-For": "203.0.113.42"},
        )
        assert _resolve_client_ip(req) == "10.0.0.2"

    def test_no_headers_returns_direct(self, monkeypatch):
        monkeypatch.setattr(
            "src.core.config.get_settings",
            lambda: type("S", (), {"trusted_proxy_ips": ["10.0.0.1"]})(),
        )
        from src.core.limiter import _resolve_client_ip

        req = _FakeRequest(client_host="10.0.0.1", headers={})
        assert _resolve_client_ip(req) == "10.0.0.1"

    def test_no_client_falls_back_to_get_remote_address(self, monkeypatch):
        """Если у request'а нет `.client` — extract_client_ip даст None;
        ключ-функция должна продолжить работать через slowapi-fallback."""
        monkeypatch.setattr(
            "src.core.config.get_settings",
            lambda: type("S", (), {"trusted_proxy_ips": ["10.0.0.1"]})(),
        )
        from src.core.limiter import _resolve_client_ip

        req = _FakeRequest(client_host=None, headers={})
        # slowapi.get_remote_address вернёт "127.0.0.1" для request без .client.host;
        # главное — что _resolve не падает.
        result = _resolve_client_ip(req)
        assert isinstance(result, str)
        assert result  # непустая строка


class TestLimiterKeyFuncWired:
    """Проверяем, что оба Limiter'а подцепили `_resolve_client_ip`."""

    def test_global_limiter_uses_resolve(self):
        from src.core import limiter as limiter_mod

        assert limiter_mod.limiter._key_func is limiter_mod._resolve_client_ip

    def test_endpoint_limiter_uses_resolve(self):
        from src.core import limiter as limiter_mod

        assert limiter_mod.endpoint_limiter._key_func is limiter_mod._resolve_client_ip
