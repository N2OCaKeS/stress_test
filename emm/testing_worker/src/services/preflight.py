"""Проверка доступности внешних сервисов перед запуском теста на стенде.

Перенос легаси `allta_app/libs/liballta.py::available_astra_services_checker`
(+ `response_used_astra_services`), которым `backup_image.py:1045-1050`
гейтил сам запуск:

```python
if available_astra_services_checker():
    remote_test_run()
else:
    logging.error('Testrun aborted, because some services not run')
```

Что проверяло легаси:

* `requests.get('https://<host>')` на четыре адреса из `allta_image_conf.py` —
  `jira.astralinux.ru`, `life.astralinux.ru`, `git.astralinux.ru`,
  `releases.devos.astralinux.ru`; каждый обязан был отдать ровно `200`;
* `ping -c 1 <ip>` на три корпоративных DNS (`ASTRA_DNS`) — достаточно, чтобы
  отозвался ЛЮБОЙ один (`if 0 in available_dns.values()`);
* цикл: пока не всё зелено — `sleep(180)` и заново, суммарно до 120 минут,
  после чего отказ.

Зачем это вообще нужно в новой системе: `starter.sh` первым делом клонирует
ветку с `git.astralinux.ru`, а сами тесты ходят в Jira/Confluence по
`dates.conf`-флагам. Кратковременная недоступность git в новой системе
проваливает item сразу и сжигает единственную попытку retry (`services/
queue.py::_fail_item_and_maybe_retry`), тогда как легаси её просто пережидало.

Два сознательных отличия от легаси:

* «Доступен» — любой HTTP-ответ со статусом < 500, а не строго `200`.
  Проверка отвечает на вопрос «сеть и сервис живы», а не «мне разрешено
  читать корневую страницу»: 301/401/403 доказывают, что хост отвечает,
  тогда как строгое `== 200` заставило бы ждать все два часа впустую из-за
  одного редиректа на SSO.
* DNS проверяется TCP-коннектом на 53-й порт, а не ICMP-пингом: в контейнере
  без `NET_RAW` `ping` недоступен, а интересует именно «DNS-сервер отвечает».

Проверка живёт в воркере, а не в `testing_service`: ждать надо ровно перед
SSH-исполнением, когда item уже выдан. См. подробнее докстринг
`queue_loop._run_one_item`.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

import httpx

from src.core.config import get_settings

logger = logging.getLogger("testing_worker.preflight")


def build_client(timeout: float) -> httpx.AsyncClient:
    """Клиент под один раунд проб. Отдельная функция — точка подмены в тестах."""
    return httpx.AsyncClient(timeout=timeout, follow_redirects=False)


@dataclass
class PreflightResult:
    """Исход ожидания. `ok=False` — item надо завершить провалом, `error` объясняет чем."""

    ok: bool
    error: str | None = None
    attempts: int = 0
    unavailable: list[str] = field(default_factory=list)
    # Заполнено, если ожидание прервал `should_abort` — несёт то, что он вернул
    # (у вызывающего это действие оператора: `skip`/`pause`).
    aborted: str | None = None


def _split_csv(raw: str) -> list[str]:
    return [chunk.strip() for chunk in (raw or "").split(",") if chunk.strip()]


async def _probe_http(client: httpx.AsyncClient, url: str) -> bool:
    """Один HTTP-запрос. Доступность = получили ответ со статусом < 500."""
    try:
        response = await client.get(url)
    except (httpx.HTTPError, OSError):
        return False
    return response.status_code < 500


async def _probe_tcp(host: str, port: int, timeout: float) -> bool:
    """Один TCP-коннект. Доступность = соединение установилось."""
    try:
        _reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout,
        )
    except (OSError, asyncio.TimeoutError):
        return False
    writer.close()
    try:
        await writer.wait_closed()
    except OSError:
        # Сервер мог оборвать соединение первым — нас интересовал сам факт
        # коннекта, он уже состоялся.
        pass
    return True


async def check_once(settings) -> list[str]:
    """Один раунд проб. Возвращает список недоступного (пустой — всё в порядке)."""
    urls = _split_csv(settings.preflight_http_urls)
    dns_hosts = _split_csv(settings.preflight_dns_hosts)
    probe_timeout = settings.preflight_probe_timeout_seconds

    unavailable: list[str] = []

    if urls:
        async with build_client(probe_timeout) as client:
            results = await asyncio.gather(*(_probe_http(client, url) for url in urls))
        unavailable.extend(url for url, ok in zip(urls, results) if not ok)

    if dns_hosts:
        dns_results = await asyncio.gather(*(
            _probe_tcp(host, settings.preflight_dns_port, probe_timeout) for host in dns_hosts
        ))
        # Легаси-семантика: достаточно одного живого DNS из списка.
        if not any(dns_results):
            unavailable.append(f"dns({','.join(dns_hosts)})")

    return unavailable


async def wait_for_external_services(
    *,
    on_wait=None,
    should_abort=None,
) -> PreflightResult:
    """Ждать, пока внешние сервисы не станут доступны. Ограничено по времени.

    `on_wait(unavailable, elapsed)` — необязательный коллбэк, которому отдаётся
    каждая неудачная попытка (вызывающий пишет её в лог теста, чтобы оператор
    видел, чего ждём). Его исключения не срывают ожидание.

    `should_abort()` — необязательная корутина, которую спрашивают между
    попытками. Вернула непустое значение — ожидание прекращается с `ok=False`,
    `error=None` и этим значением в `aborted`: вызывающий сам знает, что с ним
    делать (у него это заявка оператора на снятие теста).

    Возвращает `PreflightResult`. Таймаут — обычный провал item'а, как и
    любая другая ошибка запуска: очередь стенда не должна вставать намертво
    из-за подвисшей проверки.
    """
    settings = get_settings()
    if not settings.preflight_enabled:
        return PreflightResult(ok=True)

    deadline = time.monotonic() + settings.preflight_timeout_seconds
    interval = settings.preflight_poll_interval_seconds
    attempts = 0
    unavailable: list[str] = []

    while True:
        attempts += 1
        try:
            unavailable = await check_once(settings)
        except Exception as exc:  # noqa: BLE001 — сбой самой пробы = «недоступно», не падение воркера
            logger.warning("preflight: probe round raised %r, treating as unavailable", exc)
            unavailable = [f"probe-error({type(exc).__name__})"]

        if not unavailable:
            if attempts > 1:
                logger.info("preflight: all external services are available after %d attempts", attempts)
            return PreflightResult(ok=True, attempts=attempts)

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            logger.error(
                "preflight: giving up after %d attempts, still unavailable: %s",
                attempts, ", ".join(unavailable),
            )
            return PreflightResult(
                ok=False,
                error=f"external services unavailable after {attempts} attempts: {', '.join(unavailable)}",
                attempts=attempts,
                unavailable=unavailable,
            )

        elapsed = settings.preflight_timeout_seconds - remaining
        logger.warning(
            "preflight: attempt %d, unavailable: %s (waiting, %.0fs left)",
            attempts, ", ".join(unavailable), remaining,
        )
        if on_wait is not None:
            try:
                await on_wait(unavailable, elapsed)
            except Exception:  # noqa: BLE001 — уведомление best-effort
                logger.exception("preflight: on_wait callback raised")

        await asyncio.sleep(min(interval, remaining))

        if should_abort is not None:
            try:
                aborted = await should_abort()
            except Exception:  # noqa: BLE001 — сбой опроса не повод бросать ожидание
                logger.exception("preflight: should_abort callback raised")
            else:
                if aborted:
                    return PreflightResult(
                        ok=False, attempts=attempts, unavailable=unavailable, aborted=aborted,
                    )
