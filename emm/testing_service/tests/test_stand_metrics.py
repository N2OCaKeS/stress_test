"""Тесты живых CPU/RAM стенда (`GET /api/testing/v1/test-stands/metrics`, доработка 2026-09-23).

Три уровня:

* Чистый парсинг экспозиционного формата node_exporter'а (`_parse_cpu_seconds`/
  `_parse_ram_percent`) — без сети и без БД.
* `_measure`/`_resolve_and_measure`/`get_pool_metrics` — с замоканным
  HTTP-скрейпом (`_scrape`) и `server_client.get_connection_info`, проверяют
  расчёт rate между двумя снятиями, нулевой фолбэк на любую ошибку и TTL-кэш.
* Эндпоинт — `stand_metrics.get_pool_metrics` замокан целиком, проверяет
  только wiring (свой отдел, форма ответа, пустой пул — пустой список).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

from src.services import stand_metrics
from tests.conftest import auth_hdr
from tests.test_queue import _create_stand, mock_server_service, recorded_calls  # noqa: F401 — фикстуры переиспользуются pytest'ом по имени

BASE = "/api/testing/v1/test-stands/metrics"

CPU_TEXT_T0 = (
    'node_cpu_seconds_total{cpu="0",mode="idle"} 1000.0\n'
    'node_cpu_seconds_total{cpu="0",mode="user"} 100.0\n'
    "node_memory_MemAvailable_bytes 4000000000\n"
    "node_memory_MemTotal_bytes 16000000000\n"
)
# idle +45 из +100 общего прироста за интервал -> занятость 55%.
CPU_TEXT_T1 = (
    'node_cpu_seconds_total{cpu="0",mode="idle"} 1045.0\n'
    'node_cpu_seconds_total{cpu="0",mode="user"} 155.0\n'
    "node_memory_MemAvailable_bytes 2000000000\n"
    "node_memory_MemTotal_bytes 16000000000\n"
)


@pytest.fixture(autouse=True)
def _clear_metrics_cache():
    """In-process TTL-кэш переживает между тестами (module-level dict) — сбрасываем перед каждым."""
    stand_metrics._cache.clear()
    yield
    stand_metrics._cache.clear()


class TestParsing:
    def test_parses_cpu_totals_across_modes_and_cpus(self):
        text = (
            'node_cpu_seconds_total{cpu="0",mode="idle"} 10.0\n'
            'node_cpu_seconds_total{cpu="0",mode="user"} 2.0\n'
            'node_cpu_seconds_total{cpu="1",mode="idle"} 8.0\n'
            'node_cpu_seconds_total{cpu="1",mode="system"} 1.0\n'
        )
        total, idle = stand_metrics._parse_cpu_seconds(text)
        assert total == pytest.approx(21.0)
        assert idle == pytest.approx(18.0)

    def test_returns_none_when_no_cpu_lines(self):
        assert stand_metrics._parse_cpu_seconds("# HELP something\n") is None

    def test_ram_percent_from_available_and_total(self):
        text = "node_memory_MemAvailable_bytes 4000000000\nnode_memory_MemTotal_bytes 16000000000\n"
        assert stand_metrics._parse_ram_percent(text) == pytest.approx(75.0)

    def test_ram_percent_none_when_metrics_missing(self):
        assert stand_metrics._parse_ram_percent("no memory metrics here\n") is None


class TestMeasure:
    async def test_computes_cpu_and_ram_from_two_samples(self, monkeypatch):
        calls = iter([CPU_TEXT_T0, CPU_TEXT_T1])
        monkeypatch.setattr(stand_metrics, "_scrape", AsyncMock(side_effect=lambda *a, **k: next(calls)))
        result = await stand_metrics._measure("10.0.0.9")
        assert result.cpu_percent == pytest.approx(55.0)
        assert result.ram_percent == pytest.approx(87.5)  # 1 - 2e9/16e9

    async def test_network_failure_yields_zero_not_exception(self, monkeypatch):
        monkeypatch.setattr(stand_metrics, "_scrape", AsyncMock(side_effect=httpx.ConnectError("refused")))
        result = await stand_metrics._measure("10.0.0.9")
        assert result.cpu_percent == 0.0
        assert result.ram_percent == 0.0

    async def test_zero_total_delta_does_not_divide_by_zero(self, monkeypatch):
        calls = iter([CPU_TEXT_T0, CPU_TEXT_T0])  # тот же снимок дважды -> delta=0
        monkeypatch.setattr(stand_metrics, "_scrape", AsyncMock(side_effect=lambda *a, **k: next(calls)))
        result = await stand_metrics._measure("10.0.0.9")
        assert result.cpu_percent == 0.0


class TestResolveAndMeasure:
    async def test_zero_when_connection_info_fails(self, monkeypatch):
        from src.core.exceptions import NotFoundError
        from src.services import server_client

        monkeypatch.setattr(server_client, "get_connection_info", AsyncMock(side_effect=NotFoundError(
            error_code="SERVER_NOT_FOUND", message="not found",
        )))
        stand = type("S", (), {"server_id": "srv_missing", "id": "stand_1"})()
        result = await stand_metrics._resolve_and_measure(stand)
        assert result.cpu_percent == 0.0
        assert result.ram_percent == 0.0

    async def test_zero_when_connection_info_has_no_host(self, monkeypatch):
        from src.services import server_client

        monkeypatch.setattr(server_client, "get_connection_info", AsyncMock(return_value={"server_id": "srv_x"}))
        stand = type("S", (), {"server_id": "srv_x", "id": "stand_1"})()
        result = await stand_metrics._resolve_and_measure(stand)
        assert result.cpu_percent == 0.0
        assert result.ram_percent == 0.0


class TestPoolMetricsCache:
    async def test_second_call_within_ttl_hits_cache_not_network(self, monkeypatch):
        measure_mock = AsyncMock(return_value=stand_metrics.StandMetrics(cpu_percent=42.0, ram_percent=33.0))
        monkeypatch.setattr(stand_metrics, "_resolve_and_measure", measure_mock)
        stand = type("S", (), {"server_id": "srv_cached", "id": "stand_1"})()

        first = await stand_metrics.get_pool_metrics([stand])
        second = await stand_metrics.get_pool_metrics([stand])

        assert first["stand_1"].cpu_percent == 42.0
        assert second["stand_1"].cpu_percent == 42.0
        measure_mock.assert_called_once()

    async def test_empty_stand_list_short_circuits(self):
        assert await stand_metrics.get_pool_metrics([]) == {}


class TestEndpoint:
    async def test_requires_auth(self, client):
        assert (await client.get(BASE)).status_code == 401

    async def test_empty_pool_returns_empty_items(self, client, admin_token, mock_server_service):
        mock_server_service()
        resp = await client.get(BASE, headers=auth_hdr(admin_token))
        assert resp.status_code == 200, resp.text
        assert resp.json()["items"] == []

    async def test_returns_live_metrics_per_stand(self, client, admin_token, mock_server_service, monkeypatch):
        mock_server_service()
        stand_id, _server_id = await _create_stand(client, admin_token)
        monkeypatch.setattr(stand_metrics, "get_pool_metrics", AsyncMock(return_value={
            stand_id: stand_metrics.StandMetrics(cpu_percent=63.0, ram_percent=41.0),
        }))
        resp = await client.get(BASE, headers=auth_hdr(admin_token))
        assert resp.status_code == 200, resp.text
        items = resp.json()["items"]
        assert items == [{"stand_id": stand_id, "cpu_percent": 63.0, "ram_percent": 41.0}]

    async def test_unreachable_stand_reports_zero_not_error(self, client, admin_token, mock_server_service, monkeypatch):
        """Стенд без node_exporter'а/сети — 0/0 в ответе, не 500 и не пропуск из списка."""
        mock_server_service()
        stand_id, _server_id = await _create_stand(client, admin_token)
        monkeypatch.setattr(
            stand_metrics, "_resolve_and_measure",
            AsyncMock(return_value=stand_metrics.StandMetrics(cpu_percent=0.0, ram_percent=0.0)),
        )
        resp = await client.get(BASE, headers=auth_hdr(admin_token))
        assert resp.status_code == 200, resp.text
        items = resp.json()["items"]
        assert items == [{"stand_id": stand_id, "cpu_percent": 0.0, "ram_percent": 0.0}]
