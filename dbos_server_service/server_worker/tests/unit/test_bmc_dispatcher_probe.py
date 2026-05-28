"""Тесты Redfish-probe в `src/clients/__init__.py`.

Проверяем, что `_probe_redfish` уважает `Settings.redfish_verify_tls`:

* `verify=False` (dev/test) — probe идёт без TLS-валидации и логирует warning;
* `verify=True` (default) — `verify=True` доезжает до `httpx.AsyncClient`;
* SSL-ошибка при `verify=True` — fail-closed: probe возвращает False,
  `get_bmc_client` уходит на ipmitool;
* MITM-сценарий: подменённый эндпоинт отвечает 200 на HEAD, но cert не
  валиден → probe не должен пускать через себя Redfish-транспорт.
"""

from __future__ import annotations

import logging
import ssl
from typing import Any

import httpx
import pytest

from src.clients import (
    IpmitoolClient,
    RedfishClient,
    _is_tls_error,
    _probe_redfish,
    _probe_redfish_cascade,
    get_bmc_client,
)
from src.core.config import get_settings


# ── helpers ─────────────────────────────────────────────────────────────────


def _reset_settings_cache() -> None:
    """`get_settings` кэширован через lru_cache — для перезапуска с
    новым env-var'ом нужен явный cache_clear."""
    get_settings.cache_clear()


def _force_verify_tls(monkeypatch, value: bool) -> None:
    """Принудительно выставить `redfish_verify_tls` в settings.

    Подменяем уже-инстанциированные настройки, чтобы тесты не зависели
    от env-state процесса (conftest выставляет минимальный набор).
    """
    _reset_settings_cache()
    monkeypatch.setenv("REDFISH_VERIFY_TLS", "true" if value else "false")
    settings = get_settings()
    assert settings.redfish_verify_tls is value


def _make_fake_client(
    captured: dict[str, Any],
    *,
    raise_exc: BaseException | None = None,
    status_code: int = 200,
):
    """Фабрика fake `httpx.AsyncClient` для подмены в `_probe_redfish`.

    `captured` собирает kwargs конструктора (чтобы тест мог проверить
    `verify=` и `timeout=`). `raise_exc` — поднять при HEAD-запросе.
    """

    class _FakeResp:
        def __init__(self, code: int) -> None:
            self.status_code = code

    class _FakeAsyncClient:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

        async def __aenter__(self) -> "_FakeAsyncClient":
            return self

        async def __aexit__(self, *exc: Any) -> None:
            return None

        async def head(self, url: str) -> _FakeResp:  # noqa: ARG002
            if raise_exc is not None:
                raise raise_exc
            return _FakeResp(status_code)

    return _FakeAsyncClient


# ── verify=False (dev mode) ─────────────────────────────────────────────────


class TestProbeVerifyFalseDevMode:
    async def test_probe_passes_with_verify_false(self, monkeypatch, caplog):
        """В dev-режиме probe идёт с verify=False, возвращает True на HTTP 200,
        и в логе есть warning о незащищённом probe'е."""
        _force_verify_tls(monkeypatch, False)
        captured: dict[str, Any] = {}
        monkeypatch.setattr(
            "src.clients.httpx.AsyncClient",
            _make_fake_client(captured, status_code=200),
        )

        with caplog.at_level(logging.WARNING, logger="src.clients"):
            ok = await _probe_redfish("bmc.example.com")

        assert ok is True
        assert captured["verify"] is False
        # warning должен явно отметить verify=False (для grep-аудита логов)
        assert any(
            "verify=False" in r.getMessage() and r.levelno == logging.WARNING
            for r in caplog.records
        )

    async def test_probe_passes_with_verify_false_self_signed(self, monkeypatch):
        """Даже если бы реально стоял self-signed cert, verify=False
        не падает по TLS — это и есть смысл dev-режима."""
        _force_verify_tls(monkeypatch, False)
        captured: dict[str, Any] = {}
        # При verify=False httpx self-signed cert не отбрасывает; имитируем
        # успешный 200 (self-signed cert не мешает handshake).
        monkeypatch.setattr(
            "src.clients.httpx.AsyncClient",
            _make_fake_client(captured, status_code=200),
        )

        ok = await _probe_redfish("bmc.example.com")

        assert ok is True
        assert captured["verify"] is False


# ── verify=True (default / production) ──────────────────────────────────────


class TestProbeVerifyTrueDefault:
    async def test_probe_uses_verify_true_by_default(self, monkeypatch):
        """Settings.redfish_verify_tls=True → httpx.AsyncClient(verify=True)."""
        _force_verify_tls(monkeypatch, True)
        captured: dict[str, Any] = {}
        monkeypatch.setattr(
            "src.clients.httpx.AsyncClient",
            _make_fake_client(captured, status_code=200),
        )

        ok = await _probe_redfish("bmc.example.com")

        assert ok is True
        assert captured["verify"] is True

    async def test_probe_fail_closed_on_ssl_cert_error(self, monkeypatch, caplog):
        """verify=True + self-signed (cert-verification ошибка) → fail-closed.

        До правки `_probe_redfish` шёл с verify=False всегда,
        и MITM мог вернуть HTTP 200 → dispatcher уходил в Redfish-транспорт.
        Теперь cert-verify ошибка обрабатывается как «Redfish недоступен»,
        dispatcher падает на ipmitool.
        """
        _force_verify_tls(monkeypatch, True)
        captured: dict[str, Any] = {}
        ssl_err = ssl.SSLCertVerificationError("self-signed certificate")
        # httpx оборачивает в ConnectError с __cause__ = ssl.SSLError
        transport_err = httpx.ConnectError("cert verify failed")
        transport_err.__cause__ = ssl_err
        monkeypatch.setattr(
            "src.clients.httpx.AsyncClient",
            _make_fake_client(captured, raise_exc=transport_err),
        )

        with caplog.at_level(logging.WARNING, logger="src.clients"):
            ok = await _probe_redfish("malicious.example.com")

        assert ok is False
        assert captured["verify"] is True
        # warning должен явно отметить TLS-проблему (для аудита: что-то
        # подозрительное в стенде, не «BMC недоступен»)
        assert any(
            "TLS" in r.getMessage() and r.levelno == logging.WARNING
            for r in caplog.records
        )

    async def test_probe_fail_on_plain_connection_error(self, monkeypatch):
        """Не-TLS transport-ошибка (connect refused) — return False как
        раньше, без warning о TLS."""
        _force_verify_tls(monkeypatch, True)
        captured: dict[str, Any] = {}
        monkeypatch.setattr(
            "src.clients.httpx.AsyncClient",
            _make_fake_client(
                captured, raise_exc=httpx.ConnectError("connection refused")
            ),
        )

        ok = await _probe_redfish("unreachable.example.com")

        assert ok is False

    async def test_probe_returns_false_on_ssl_error_wrapped_deeply(
        self, monkeypatch
    ):
        """`_is_tls_error` должен находить SSLError через __context__,
        не только через __cause__ — httpx иногда выставляет один, иногда
        другой."""
        _force_verify_tls(monkeypatch, True)
        captured: dict[str, Any] = {}
        outer = httpx.ConnectError("ssl-related")
        # __context__ путь (implicit re-raise inside except-block)
        outer.__context__ = ssl.SSLError("handshake fail")
        monkeypatch.setattr(
            "src.clients.httpx.AsyncClient",
            _make_fake_client(captured, raise_exc=outer),
        )

        ok = await _probe_redfish("bmc.example.com")

        assert ok is False


# ── get_bmc_client fallback ─────────────────────────────────────────────────


class TestGetBmcClientFallback:
    async def test_tls_error_falls_back_to_ipmitool(self, monkeypatch):
        """verify=True + cert-verify ошибка на probe → `get_bmc_client`
        возвращает IpmitoolClient, не RedfishClient."""
        _force_verify_tls(monkeypatch, True)
        captured: dict[str, Any] = {}
        transport_err = httpx.ConnectError("cert verify failed")
        transport_err.__cause__ = ssl.SSLCertVerificationError("self-signed")
        monkeypatch.setattr(
            "src.clients.httpx.AsyncClient",
            _make_fake_client(captured, raise_exc=transport_err),
        )

        client = await get_bmc_client(
            host="bmc.example.com",
            username="u",
            password="p",
        )

        assert isinstance(client, IpmitoolClient)
        assert not isinstance(client, RedfishClient)

    async def test_verify_false_probe_returns_true(self, monkeypatch):
        """В dev-режиме probe проходит и возвращает True; dispatcher
        получит RedfishClient. Проверяем именно факт True (не строим
        RedfishClient — он бы дёрнул httpx.AsyncClient с реальными args,
        что вне зоны этого probe-теста)."""
        _force_verify_tls(monkeypatch, False)
        captured: dict[str, Any] = {}
        # patch только в namespace _probe_redfish'а — RedfishClient.__init__
        # лезет в httpx через `src.clients.redfish`, его не трогаем.
        from src.clients import httpx as clients_httpx
        monkeypatch.setattr(
            clients_httpx, "AsyncClient",
            _make_fake_client(captured, status_code=200),
        )

        from src.clients import _probe_redfish
        ok = await _probe_redfish("bmc.example.com")

        assert ok is True
        assert captured["verify"] is False


# ── _probe_redfish_cascade ──────────────────────────────────────────────────


class TestProbeCascade:
    """Каскадный probe: https-verify → https-no-verify → http → ipmitool.

    Покрывает архитектурный инвариант из обсидиана (`bmc-no-cache.md`):
    при отказе верхнего уровня пробуем следующий, без кеширования
    результата (каждый вызов начинает с верхней ступени).
    """

    async def test_cascade_stops_at_first_success(self, monkeypatch):
        """https-verify ответил — http и https-no-verify не дёргаются."""
        _force_verify_tls(monkeypatch, True)
        captured: dict[str, Any] = {}
        monkeypatch.setattr(
            "src.clients.httpx.AsyncClient",
            _make_fake_client(captured, status_code=200),
        )

        ok = await _probe_redfish_cascade("bmc.example.com")

        assert ok is True
        # Первый успешный шаг — verify=True.
        assert captured["verify"] is True

    async def test_cascade_falls_through_to_http_when_tls_fails(self, monkeypatch):
        """https-verify падает TLS-fail'ом, https-no-verify тоже refused,
        plain HTTP отвечает 200 — каскад успешен."""
        _force_verify_tls(monkeypatch, True)

        # Подсчёт схем, которые видел probe — для проверки порядка.
        calls: list[tuple[str, bool]] = []

        class _Resp:
            def __init__(self, code: int) -> None:
                self.status_code = code

        class _FakeAsyncClient:
            def __init__(self, **kwargs: Any) -> None:
                self._verify = kwargs.get("verify")

            async def __aenter__(self) -> "_FakeAsyncClient":
                return self

            async def __aexit__(self, *exc: Any) -> None:
                return None

            async def head(self, url: str) -> _Resp:
                scheme = "https" if url.startswith("https") else "http"
                calls.append((scheme, bool(self._verify)))
                if scheme == "https" and self._verify is True:
                    # https-verify → TLS error.
                    err = httpx.ConnectError("cert fail")
                    err.__cause__ = ssl.SSLCertVerificationError("self-signed")
                    raise err
                if scheme == "https" and self._verify is False:
                    # https-no-verify → connection refused.
                    raise httpx.ConnectError("refused")
                # plain http → 200.
                return _Resp(200)

        monkeypatch.setattr("src.clients.httpx.AsyncClient", _FakeAsyncClient)

        ok = await _probe_redfish_cascade("bmc.example.com")

        assert ok is True
        # Каскад прошёл https-verify → https-no-verify → http.
        assert calls == [
            ("https", True),
            ("https", False),
            ("http", True),  # http-probe ставит verify=True по умолчанию (no-op для http)
        ]

    async def test_cascade_all_fail_returns_false(self, monkeypatch):
        """Все три уровня недоступны — return False, caller уходит на ipmitool."""
        _force_verify_tls(monkeypatch, True)
        captured: dict[str, Any] = {}
        monkeypatch.setattr(
            "src.clients.httpx.AsyncClient",
            _make_fake_client(captured, raise_exc=httpx.ConnectError("down")),
        )

        ok = await _probe_redfish_cascade("dead.example.com")
        assert ok is False

    async def test_cascade_skips_no_verify_when_settings_verify_false(self, monkeypatch):
        """settings.verify=False — первый шаг уже без verify, второй
        (https-no-verify) пропускаем, чтобы не делать лишний HEAD."""
        _force_verify_tls(monkeypatch, False)

        calls: list[bool] = []

        class _Resp:
            def __init__(self, code: int) -> None:
                self.status_code = code

        class _FakeAsyncClient:
            def __init__(self, **kwargs: Any) -> None:
                self._verify = kwargs.get("verify")

            async def __aenter__(self) -> "_FakeAsyncClient":
                return self

            async def __aexit__(self, *exc: Any) -> None:
                return None

            async def head(self, url: str) -> _Resp:
                calls.append(bool(self._verify))
                # Возвращаем 404 — probe считает «недоступен».
                return _Resp(404)

        monkeypatch.setattr("src.clients.httpx.AsyncClient", _FakeAsyncClient)

        ok = await _probe_redfish_cascade("bmc.example.com")
        assert ok is False
        # Должны увидеть: https с verify=False (settings), затем http с verify=True.
        # Никакого «и https-verify, и https-no-verify» — без дубля.
        assert calls == [False, True]


# ── _is_tls_error helper coverage ───────────────────────────────────────────


class TestIsTlsErrorHelper:
    def test_direct_ssl_error(self):
        assert _is_tls_error(ssl.SSLError("x")) is True

    def test_ssl_cert_verification_error_subclass(self):
        assert _is_tls_error(ssl.SSLCertVerificationError("x")) is True

    def test_non_ssl_error(self):
        assert _is_tls_error(httpx.ConnectError("refused")) is False

    def test_chained_via_cause(self):
        outer = httpx.ConnectError("wrap")
        outer.__cause__ = ssl.SSLError("inner")
        assert _is_tls_error(outer) is True

    def test_chained_via_context(self):
        outer = httpx.ConnectError("wrap")
        outer.__context__ = ssl.SSLError("inner")
        assert _is_tls_error(outer) is True

    def test_cycle_safety(self):
        """Защита от циклических __cause__/__context__ — не должен зациклиться."""
        a = httpx.ConnectError("a")
        b = httpx.ConnectError("b")
        a.__cause__ = b
        b.__cause__ = a
        # Никакого SSL — просто отстреливаемся False, без бесконечного цикла.
        assert _is_tls_error(a) is False
