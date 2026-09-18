"""Тесты `services/preflight.py` — ожидание внешних сервисов перед запуском теста.

Сеть не трогаем: HTTP-часть закрывается `httpx.MockTransport` через подмену
`preflight.build_client`, DNS-часть — подменой `_probe_tcp`. Время виртуальное
(фикстура `no_sleep`): `sleep` сдвигает подменённый `monotonic` на свой
аргумент, иначе тест 120-минутного таймаута реально шёл бы 120 минут.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest

from src.core.config import get_settings
from src.services import preflight

_REAL_SLEEP = asyncio.sleep


def _settings(**overrides) -> SimpleNamespace:
    """Минимальный settings-дубль: только поля, которые читает preflight."""
    base = {
        "preflight_enabled": True,
        "preflight_http_urls": "https://jira.test,https://git.test",
        "preflight_dns_hosts": "10.0.0.1,10.0.0.2",
        "preflight_dns_port": 53,
        "preflight_probe_timeout_seconds": 1.0,
        "preflight_poll_interval_seconds": 180.0,
        "preflight_timeout_seconds": 7200.0,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture
def no_sleep(monkeypatch):
    """Виртуальные часы: `sleep` не спит, а сдвигает `monotonic` на свой аргумент.

    Просто «мгновенного sleep» недостаточно — дедлайн проверки считается по
    `time.monotonic()`, и без сдвига часов цикл честно крутился бы до реального
    истечения двух часов. Возвращает список длительностей всех пауз.
    """
    now = {"t": 1_000.0}
    calls: list[float] = []

    async def fake_sleep(seconds):
        calls.append(seconds)
        now["t"] += seconds
        await _REAL_SLEEP(0)

    monkeypatch.setattr(preflight.asyncio, "sleep", fake_sleep)
    # Подменяем ссылку на модуль в namespace preflight, а не атрибут самого
    # `time` — глобальный monotonic нужен остальному тестовому процессу целым.
    monkeypatch.setattr(preflight, "time", SimpleNamespace(monotonic=lambda: now["t"]))
    return calls


def _mock_http(monkeypatch, handler):
    def build(timeout):
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=timeout)

    monkeypatch.setattr(preflight, "build_client", build)


def _stub_dns(monkeypatch, results: dict[str, bool]):
    async def fake_probe(host, port, timeout):
        return results.get(host, False)

    monkeypatch.setattr(preflight, "_probe_tcp", fake_probe)


class TestCheckOnce:
    async def test_all_available(self, monkeypatch):
        _mock_http(monkeypatch, lambda request: httpx.Response(200))
        _stub_dns(monkeypatch, {"10.0.0.1": True, "10.0.0.2": True})
        assert await preflight.check_once(_settings()) == []

    async def test_one_dns_alive_is_enough(self, monkeypatch):
        """Легаси-семантика `0 in available_dns.values()` — достаточно одного."""
        _mock_http(monkeypatch, lambda request: httpx.Response(200))
        _stub_dns(monkeypatch, {"10.0.0.1": False, "10.0.0.2": True})
        assert await preflight.check_once(_settings()) == []

    async def test_no_dns_alive_is_unavailable(self, monkeypatch):
        _mock_http(monkeypatch, lambda request: httpx.Response(200))
        _stub_dns(monkeypatch, {})
        unavailable = await preflight.check_once(_settings())
        assert unavailable == ["dns(10.0.0.1,10.0.0.2)"]

    async def test_names_every_unreachable_url(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            if "git" in str(request.url):
                raise httpx.ConnectError("down", request=request)
            return httpx.Response(200)

        _mock_http(monkeypatch, handler)
        _stub_dns(monkeypatch, {"10.0.0.1": True})
        assert await preflight.check_once(_settings()) == ["https://git.test"]

    async def test_5xx_counts_as_unavailable(self, monkeypatch):
        _mock_http(monkeypatch, lambda request: httpx.Response(503))
        _stub_dns(monkeypatch, {"10.0.0.1": True})
        unavailable = await preflight.check_once(_settings())
        assert unavailable == ["https://jira.test", "https://git.test"]

    @pytest.mark.parametrize("status", [200, 302, 401, 403, 404])
    async def test_non_5xx_counts_as_available(self, monkeypatch, status):
        """Хост, который ответил, — живой. Легаси требовало ровно 200, но
        редирект на SSO или 401 доказывают доступность так же."""
        _mock_http(monkeypatch, lambda request: httpx.Response(status))
        _stub_dns(monkeypatch, {"10.0.0.1": True})
        assert await preflight.check_once(_settings()) == []

    async def test_empty_config_skips_that_half(self, monkeypatch):
        _stub_dns(monkeypatch, {})
        settings = _settings(preflight_http_urls="", preflight_dns_hosts="")
        assert await preflight.check_once(settings) == []


class TestWaitForExternalServices:
    async def test_disabled_returns_immediately(self, monkeypatch):
        monkeypatch.setattr(preflight, "get_settings", lambda: _settings(preflight_enabled=False))

        async def boom(settings):
            raise AssertionError("check_once must not be called when disabled")

        monkeypatch.setattr(preflight, "check_once", boom)
        result = await preflight.wait_for_external_services()
        assert result.ok is True

    async def test_ok_on_first_round_does_not_sleep(self, monkeypatch, no_sleep):
        monkeypatch.setattr(preflight, "get_settings", lambda: _settings())

        async def all_good(settings):
            return []

        monkeypatch.setattr(preflight, "check_once", all_good)
        result = await preflight.wait_for_external_services()
        assert result.ok is True
        assert result.attempts == 1
        assert no_sleep == []

    async def test_waits_then_succeeds(self, monkeypatch, no_sleep):
        monkeypatch.setattr(preflight, "get_settings", lambda: _settings())
        rounds = {"n": 0}
        notified: list[tuple[list[str], float]] = []

        async def flaky(settings):
            rounds["n"] += 1
            return ["https://git.test"] if rounds["n"] < 3 else []

        async def on_wait(unavailable, elapsed):
            notified.append((unavailable, elapsed))

        monkeypatch.setattr(preflight, "check_once", flaky)
        result = await preflight.wait_for_external_services(on_wait=on_wait)
        assert result.ok is True
        assert result.attempts == 3
        # Два неудачных раунда — две паузы и два уведомления.
        assert no_sleep == [180.0, 180.0]
        assert [item[0] for item in notified] == [["https://git.test"], ["https://git.test"]]

    async def test_bounded_by_timeout(self, monkeypatch, no_sleep):
        """Таймаут — обычный провал item'а, а не вечное ожидание."""
        monkeypatch.setattr(
            preflight, "get_settings",
            lambda: _settings(preflight_timeout_seconds=540.0, preflight_poll_interval_seconds=180.0),
        )

        async def never(settings):
            return ["https://git.test"]

        monkeypatch.setattr(preflight, "check_once", never)
        result = await preflight.wait_for_external_services()
        assert result.ok is False
        assert "https://git.test" in (result.error or "")
        assert result.unavailable == ["https://git.test"]
        # 540 / 180 — три паузы, четвёртый раунд упирается в дедлайн.
        assert no_sleep == [180.0, 180.0, 180.0]
        assert result.attempts == 4

    async def test_legacy_default_cadence_gives_forty_rounds(self, monkeypatch, no_sleep):
        """120 мин / 180 с — 40 попыток, столько же, сколько отсчитывало легаси."""
        monkeypatch.setattr(preflight, "get_settings", lambda: _settings())

        async def never(settings):
            return ["https://git.test"]

        monkeypatch.setattr(preflight, "check_once", never)
        result = await preflight.wait_for_external_services()
        assert result.ok is False
        assert result.attempts == 41
        assert len(no_sleep) == 40

    async def test_defaults_match_legacy_cadence(self):
        """120 минут / 180 секунд — те же числа, что в `available_astra_services_checker`."""
        settings = get_settings()
        assert settings.preflight_timeout_seconds == 120 * 60
        assert settings.preflight_poll_interval_seconds == 180

    async def test_probe_exception_is_treated_as_unavailable(self, monkeypatch, no_sleep):
        monkeypatch.setattr(
            preflight, "get_settings",
            lambda: _settings(preflight_timeout_seconds=1.0),
        )

        async def raises(settings):
            raise RuntimeError("resolver exploded")

        monkeypatch.setattr(preflight, "check_once", raises)
        result = await preflight.wait_for_external_services()
        assert result.ok is False
        assert "probe-error(RuntimeError)" in (result.error or "")

    async def test_abort_stops_waiting(self, monkeypatch, no_sleep):
        monkeypatch.setattr(preflight, "get_settings", lambda: _settings())

        async def never(settings):
            return ["https://git.test"]

        async def abort():
            return "skip"

        monkeypatch.setattr(preflight, "check_once", never)
        result = await preflight.wait_for_external_services(should_abort=abort)
        assert result.ok is False
        assert result.aborted == "skip"
        assert result.error is None

    async def test_abort_failure_does_not_break_waiting(self, monkeypatch, no_sleep):
        monkeypatch.setattr(
            preflight, "get_settings",
            lambda: _settings(preflight_timeout_seconds=1.0),
        )

        async def never(settings):
            return ["https://git.test"]

        async def abort():
            raise RuntimeError("interrupt-check down")

        monkeypatch.setattr(preflight, "check_once", never)
        result = await preflight.wait_for_external_services(should_abort=abort)
        assert result.ok is False
        assert result.aborted is None

    async def test_on_wait_failure_does_not_break_waiting(self, monkeypatch, no_sleep):
        monkeypatch.setattr(preflight, "get_settings", lambda: _settings())
        rounds = {"n": 0}

        async def flaky(settings):
            rounds["n"] += 1
            return ["https://git.test"] if rounds["n"] < 2 else []

        async def on_wait(unavailable, elapsed):
            raise RuntimeError("log-chunk down")

        monkeypatch.setattr(preflight, "check_once", flaky)
        result = await preflight.wait_for_external_services(on_wait=on_wait)
        assert result.ok is True
