"""Живые CPU/RAM стенда — прямой HTTP-скрейп node_exporter'а.

Ни `server_service`, ни `testing_service` сегодня не хранят и не публикуют
телеметрию нагрузки стенда — только идентичность и `busy_state` (см.
`services/pool_overview.py`). У каждого стенда уже есть node_exporter
(`server_service/src/services/vm.py` — `installNodeExporter`), но готового
Prometheus/агрегирующего сервиса перед ним нет, а заводить его ради одной
карточки — overkill. Поэтому здесь самый простой рабочий вариант: сходить
напрямую в `http://<ip стенда>:9100/metrics` с сервера (не с браузера — там
это либо CORS, либо сеть, до которой у клиента вообще нет маршрута) и
распарсить текстовый экспозиционный формат руками, без клиента Prometheus.

IP резолвится тем же каналом, что и SSH-диспатч теста воркеру —
`server_client.get_connection_info` (`services/queue.py` использует его для
той же цели). Ограничения, которые стоит держать в голове:

* Порт 9100 должен быть физически достижим из пода `testing_service` до
  стенда — то же самое сетевое условие, что и у SSH-диспатча воркера,
  подтверждено рабочим сценарием, отдельно не проверялось для 9100.
* CPU% считается по двум снятиям счётчика с паузой (rate между t0/t1) — один
  снимок `node_cpu_seconds_total` сам по себе бесполезен, это монотонный
  счётчик с момента загрузки. Два HTTP-похода на стенд вместо одного — цена
  корректного процента, а не рефреш чаще, чем раз в TTL кэша.
* Температура/диски намеренно не реализованы: `node_hwmon_temp_celsius`
  зависит от конкретного железа/чипсета и не гарантирован на стенде,
  сопоставление namespace'а диска с "NVMe"/"SDA" из демо-заглушки на живом
  стенде ничем не обосновано. Даже если данных нет — 0, не фантазия.
* Любая ошибка (сеть, таймаут, нет `node_exporter`, стенд не резолвится через
  `connection-info`) на отдельном стенде превращается в `{0, 0}` для него —
  не роняет остальной обзор и не всплывает как 500.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass

import httpx

from src.core.config import get_settings
from src.models import TestStand
from src.services import server_client

logger = logging.getLogger("testing_service.stand_metrics")

_CPU_LINE_RE = re.compile(r'^node_cpu_seconds_total\{cpu="[^"]*",mode="([^"]+)"\}\s+([0-9.eE+-]+)', re.MULTILINE)
_MEM_AVAILABLE_RE = re.compile(r'^node_memory_MemAvailable_bytes\s+([0-9.eE+-]+)', re.MULTILINE)
_MEM_TOTAL_RE = re.compile(r'^node_memory_MemTotal_bytes\s+([0-9.eE+-]+)', re.MULTILINE)

# Между двумя снятиями CPU-счётчика — минимальная пауза, чтобы rate не делился
# на почти-ноль секунд и не улетал в шум.
_SAMPLE_GAP_SECONDS = 0.2


@dataclass(frozen=True)
class StandMetrics:
    cpu_percent: float
    ram_percent: float


_ZERO = StandMetrics(cpu_percent=0.0, ram_percent=0.0)

# In-process TTL-кэш по server_id — несколько открытых вкладок/пользователей
# за короткое окно не должны каждый гонять свой скрейп на один и тот же стенд.
_cache: dict[str, tuple[float, StandMetrics]] = {}


def _clamp_pct(value: float) -> float:
    return max(0.0, min(100.0, value))


def _parse_cpu_seconds(text: str) -> tuple[float, float] | None:
    """(сумма всех mode-счётчиков, сумма только `idle`) по всем cpu, либо `None`, если строк не нашлось."""
    total = 0.0
    idle = 0.0
    found = False
    for match in _CPU_LINE_RE.finditer(text):
        found = True
        value = float(match.group(2))
        total += value
        if match.group(1) == "idle":
            idle += value
    return (total, idle) if found else None


def _parse_ram_percent(text: str) -> float | None:
    available = _MEM_AVAILABLE_RE.search(text)
    total = _MEM_TOTAL_RE.search(text)
    if not available or not total:
        return None
    total_bytes = float(total.group(1))
    if total_bytes <= 0:
        return None
    used_ratio = 1.0 - (float(available.group(1)) / total_bytes)
    return round(_clamp_pct(used_ratio * 100.0), 1)


async def _scrape(client: httpx.AsyncClient, ip: str, port: int, timeout: float) -> str:
    response = await client.get(f"http://{ip}:{port}/metrics", timeout=timeout)
    response.raise_for_status()
    return response.text


async def _measure(ip: str) -> StandMetrics:
    settings = get_settings()
    try:
        async with httpx.AsyncClient() as client:
            first = await _scrape(client, ip, settings.stand_metrics_port, settings.stand_metrics_scrape_timeout_seconds)
            await asyncio.sleep(_SAMPLE_GAP_SECONDS)
            second = await _scrape(client, ip, settings.stand_metrics_port, settings.stand_metrics_scrape_timeout_seconds)
    except (httpx.HTTPError, OSError) as exc:
        logger.debug("node_exporter недоступен на %s: %s", ip, exc)
        return _ZERO

    cpu_percent = 0.0
    cpu_t0 = _parse_cpu_seconds(first)
    cpu_t1 = _parse_cpu_seconds(second)
    if cpu_t0 and cpu_t1:
        total_delta = cpu_t1[0] - cpu_t0[0]
        idle_delta = cpu_t1[1] - cpu_t0[1]
        if total_delta > 0:
            cpu_percent = round(_clamp_pct((1.0 - idle_delta / total_delta) * 100.0), 1)

    ram_percent = _parse_ram_percent(second) or 0.0
    return StandMetrics(cpu_percent=cpu_percent, ram_percent=ram_percent)


async def _resolve_and_measure(stand: TestStand) -> StandMetrics:
    try:
        info = await server_client.get_connection_info(stand.server_id)
        ip = info.get("host")
    except Exception as exc:  # noqa: BLE001 — любой сбой резолва IP не должен ронять обзор пула
        logger.debug("connection-info не отдал IP для %s: %s", stand.server_id, exc)
        return _ZERO
    if not ip:
        return _ZERO
    return await _measure(ip)


async def get_pool_metrics(stands: list[TestStand]) -> dict[str, StandMetrics]:
    """`test_stand.id` → `{cpu_percent, ram_percent}` для переданных стендов.

    Кэш ключуется по `server_id` (не по `test_stand.id`) — IP/железо привязаны
    к серверу, не к надстройке `test_stand`. Каждый непопавший в кэш стенд
    скрейпится параллельно, а не по очереди — иначе N стендов × 2 похода ×
    таймаут легко перевалит за разумное время ответа одной карточки.
    """
    settings = get_settings()
    now = time.monotonic()
    result: dict[str, StandMetrics] = {}
    to_fetch: list[TestStand] = []
    for stand in stands:
        cached = _cache.get(stand.server_id)
        if cached is not None and (now - cached[0]) < settings.stand_metrics_cache_seconds:
            result[stand.id] = cached[1]
        else:
            to_fetch.append(stand)

    if not to_fetch:
        return result

    measured = await asyncio.gather(*[_resolve_and_measure(stand) for stand in to_fetch])
    fetched_at = time.monotonic()
    for stand, metrics in zip(to_fetch, measured):
        _cache[stand.server_id] = (fetched_at, metrics)
        result[stand.id] = metrics
    return result
