"""Живая проверка внешних ASTRA-сервисов для `/host/services`.

Портирует `response_used_astra_services()` из legacy `allta_app`
(ветка `allta_app`, `allta_app/libs/liballta.py:1784`): та же четвёрка
HTTP-сервисов (Jira/Life/Git/Releases) плюс DNS-reachability. Legacy делал
`ping -c 1` по трём IP и считал DNS живым, если ответил хотя бы один —
здесь вместо ICMP (нужны raw sockets/root, ненадёжно в контейнере) —
TCP-connect на порт 53, та же "любой один достаточен" семантика.

up/down/unknown — тот же конвент, что в `web_ui/src/api/health.ts`:
успешный ответ с кодом < 400 → up, любой другой код или сетевой сбой → down,
таймаут (реальное состояние неизвестно) → unknown.

`check_all()` — живая проверка (сеть на каждый вызов), используется напрямую
только фоновым рефрешем и тестами. Эндпоинт `/host/services` ходит через
`check_all_cached()` — TTL-кэш с stale-while-revalidate, см. ниже.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone

import httpx

from src.schemas.host_services import AstraServiceStatus

logger = logging.getLogger("server_service.astra_health")

# (id, label, url) — HTTP-сервисы, проверяются GET-запросом.
ASTRA_HTTP_SERVICES: list[tuple[str, str, str]] = [
    ("jira", "Jira", "https://jira.astralinux.ru"),
    ("life", "Life", "https://life.astralinux.ru"),
    ("git", "Git", "https://git.astralinux.ru"),
    ("releases", "Releases", "https://releases.devos.astralinux.ru"),
]

# DNS-серверы: TCP-connect на порт 53 вместо ICMP-ping. Любой один
# отвечающий — DNS считается живым (1:1 с legacy `if 0 in available_dns.values()`).
ASTRA_DNS_IPS: list[str] = ["10.177.128.198", "10.177.180.246", "10.177.181.142"]

_HTTP_TIMEOUT_SECONDS = 4.0
_DNS_TIMEOUT_SECONDS = 2.0
_DNS_PORT = 53


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _check_http(service_id: str, label: str, url: str) -> AstraServiceStatus:
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECONDS) as client:
            response = await client.get(url)
    except httpx.TimeoutException:
        return AstraServiceStatus(
            id=service_id, label=label, status="unknown", checked_at=_now(),
            latency_ms=None, error="timeout",
        )
    except httpx.HTTPError as exc:
        return AstraServiceStatus(
            id=service_id, label=label, status="down", checked_at=_now(),
            latency_ms=None, error=str(exc) or type(exc).__name__,
        )
    latency_ms = int((time.monotonic() - started) * 1000)
    if response.status_code < 400:
        return AstraServiceStatus(
            id=service_id, label=label, status="up", checked_at=_now(),
            latency_ms=latency_ms, error=None,
        )
    return AstraServiceStatus(
        id=service_id, label=label, status="down", checked_at=_now(),
        latency_ms=latency_ms, error=f"HTTP {response.status_code}",
    )


async def _dns_port_open(ip: str) -> bool:
    try:
        _reader, writer = await asyncio.wait_for(
            asyncio.open_connection(ip, _DNS_PORT), timeout=_DNS_TIMEOUT_SECONDS,
        )
    except Exception:  # noqa: BLE001 — любой сбой connect'а = недоступен
        return False
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:  # noqa: BLE001 — уже получили результат, close — best-effort
        pass
    return True


async def _check_dns() -> AstraServiceStatus:
    started = time.monotonic()
    results = await asyncio.gather(*(_dns_port_open(ip) for ip in ASTRA_DNS_IPS))
    latency_ms = int((time.monotonic() - started) * 1000)
    reachable = any(results)
    return AstraServiceStatus(
        id="dns", label="DNS", status="up" if reachable else "down", checked_at=_now(),
        latency_ms=latency_ms if reachable else None,
        error=None if reachable else "no DNS server reachable on port 53",
    )


async def check_all() -> list[AstraServiceStatus]:
    """4 HTTP-сервиса + агрегированный DNS-статус, все проверки конкурентно."""
    results = await asyncio.gather(
        *(_check_http(service_id, label, url) for service_id, label, url in ASTRA_HTTP_SERVICES),
        _check_dns(),
    )
    return list(results)


# ── Кэш для GET /host/services (stale-while-revalidate) ─────────────────────
#
# Результат глобальный (не зависит от отдела/пользователя), поэтому один
# кэш-слот на процесс. 45 секунд — компромисс между «страница открывается
# мгновенно» и «данные не протухают заметно»: страницу здоровья обычно
# открывают нечасто и смотрят на общую картину (up/down), а не гоняются за
# секундной точностью; при этом 45с достаточно мало, чтобы админ увидел
# восстановление упавшего сервиса в пределах пары обновлений страницы.
_CACHE_TTL_SECONDS = 45.0

_cache: dict[str, object] = {"data": None, "fetched_at": 0.0}
_refreshing = False


def clear_cache() -> None:
    """Сбросить кэш (используется в тестах между прогонами)."""
    _cache["data"] = None
    _cache["fetched_at"] = 0.0


def _cache_is_fresh() -> bool:
    if _cache["data"] is None:
        return False
    age = time.monotonic() - float(_cache["fetched_at"])  # type: ignore[arg-type]
    return age < _CACHE_TTL_SECONDS


def _schedule_background_refresh() -> None:
    global _refreshing
    if _refreshing:
        return
    _refreshing = True

    async def _run() -> None:
        global _refreshing
        try:
            data = await check_all()
            _cache["data"] = data
            _cache["fetched_at"] = time.monotonic()
        except Exception:  # noqa: BLE001 — фоновый рефреш не должен ронять процесс
            logger.exception("background astra_health refresh failed")
        finally:
            _refreshing = False

    asyncio.create_task(_run())


async def check_all_cached() -> list[AstraServiceStatus]:
    """Статус для `/host/services`: мгновенно из кэша, обновление — в фоне.

    Кэша ещё нет (первый запрос после старта процесса) — ждём живую
    проверку один раз. Дальше всегда отдаём последний известный результат
    немедленно; если он протух — параллельно с ответом запускается фоновый
    рефреш (не блокирует текущий запрос), следующий запрос увидит свежие
    данные.
    """
    if _cache["data"] is None:
        data = await check_all()
        _cache["data"] = data
        _cache["fetched_at"] = time.monotonic()
        return data  # type: ignore[return-value]
    if not _cache_is_fresh():
        _schedule_background_refresh()
    return _cache["data"]  # type: ignore[return-value]
