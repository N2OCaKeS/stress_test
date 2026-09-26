"""Проверка доступности внешних сервисов перед запуском теста на стенде.

Перенос легаси `available_astra_services_checker`: HTTP-пробы на
Jira/Confluence/git/releases и DNS-проверка корпоративных серверов, до
120 минут с ретраями. Нужно потому, что `starter.sh` первым делом клонирует
ветку с git.astralinux.ru, а недоступность git сжигает retry сразу.

Настройки — из `item["preflight"]` claim payload (`department_test_settings.
preflight` отдела стенда, форма `CONTRACTS.md` C3), env `PREFLIGHT_*` — только
фолбэк. `PREFLIGHT_FORCE_DISABLED` — аварийный выключатель на уровне воркера,
сильнее payload.

`ok_status` на HTTP-пробу: `200` — строго `200` с редиректами (паритет с
легаси); `lt500` — любой ответ `< 500` без редиректов, дефолт env-фолбэка.

DNS проверяется TCP-коннектом на `dns_port`, не ICMP: `ping` недоступен без
`NET_RAW` в контейнере.

Проверка живёт в воркере, не в `testing_service`: ждать надо перед
SSH-исполнением, когда item уже выдан.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Literal

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


OkStatus = Literal["200", "lt500"]
_OK_STATUSES: tuple[str, ...] = ("200", "lt500")


@dataclass(frozen=True)
class HttpProbe:
    url: str
    ok_status: OkStatus = "200"


@dataclass(frozen=True)
class PreflightConfig:
    """Эффективные настройки одного ожидания. `source` — `payload` или `env` (для логов)."""

    enabled: bool
    http: tuple[HttpProbe, ...]
    dns_hosts: tuple[str, ...]
    dns_port: int
    poll_interval_seconds: float
    timeout_seconds: float
    probe_timeout_seconds: float
    source: str = "env"


def _split_csv(raw: str) -> list[str]:
    return [chunk.strip() for chunk in (raw or "").split(",") if chunk.strip()]


def _ok_status(raw) -> OkStatus:
    value = str(raw or "").strip()
    if value not in _OK_STATUSES:
        raise ValueError(f"unknown preflight ok_status {raw!r}, expected one of {_OK_STATUSES}")
    return value  # type: ignore[return-value]


def config_from_env(settings) -> PreflightConfig:
    """Фолбэк: настройки из env воркера (`core/config.py`, `PREFLIGHT_*`)."""
    ok_status = _ok_status(settings.preflight_http_ok_status)
    return PreflightConfig(
        enabled=settings.preflight_enabled and not settings.preflight_force_disabled,
        http=tuple(HttpProbe(url, ok_status) for url in _split_csv(settings.preflight_http_urls)),
        dns_hosts=tuple(_split_csv(settings.preflight_dns_hosts)),
        dns_port=settings.preflight_dns_port,
        poll_interval_seconds=settings.preflight_poll_interval_seconds,
        timeout_seconds=settings.preflight_timeout_seconds,
        probe_timeout_seconds=settings.preflight_probe_timeout_seconds,
        source="env",
    )


def resolve_config(payload: dict | None, settings) -> PreflightConfig:
    """Настройки из claim payload (CONTRACTS.md C3), env — только фолбэк.

    `payload is None` — testing_service не прислал поле: целиком env. Иначе
    берётся payload; поле, которого в нём нет (например, необязательный
    `probe_timeout_seconds`), добирается из env. `PREFLIGHT_FORCE_DISABLED`
    выключает проверку в любом случае.
    """
    env = config_from_env(settings)
    if payload is None:
        return env

    http = env.http
    if "http" in payload:
        http = tuple(
            HttpProbe(str(probe["url"]), _ok_status(probe.get("ok_status", "200")))
            for probe in (payload.get("http") or [])
        )
    dns_hosts = env.dns_hosts
    if "dns_hosts" in payload:
        dns_hosts = tuple(str(host).strip() for host in (payload.get("dns_hosts") or []) if str(host).strip())

    def _num(key: str, fallback):
        value = payload.get(key)
        return fallback if value is None else value

    enabled = payload.get("enabled")
    return PreflightConfig(
        enabled=(settings.preflight_enabled if enabled is None else bool(enabled))
        and not settings.preflight_force_disabled,
        http=http,
        dns_hosts=dns_hosts,
        dns_port=int(_num("dns_port", env.dns_port)),
        poll_interval_seconds=float(_num("poll_interval_seconds", env.poll_interval_seconds)),
        timeout_seconds=float(_num("timeout_seconds", env.timeout_seconds)),
        probe_timeout_seconds=float(_num("probe_timeout_seconds", env.probe_timeout_seconds)),
        source="payload",
    )


async def _probe_http(client: httpx.AsyncClient, probe: HttpProbe) -> bool:
    """Один HTTP-запрос. Доступность — по `probe.ok_status` (см. module docstring)."""
    strict = probe.ok_status == "200"
    try:
        response = await client.get(probe.url, follow_redirects=strict)
    except (httpx.HTTPError, OSError):
        return False
    if strict:
        return response.status_code == 200
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


async def check_once(config: PreflightConfig) -> list[str]:
    """Один раунд проб. Возвращает список недоступного (пустой — всё в порядке)."""
    probes = config.http
    dns_hosts = config.dns_hosts
    probe_timeout = config.probe_timeout_seconds

    unavailable: list[str] = []

    if probes:
        async with build_client(probe_timeout) as client:
            results = await asyncio.gather(*(_probe_http(client, probe) for probe in probes))
        unavailable.extend(probe.url for probe, ok in zip(probes, results) if not ok)

    if dns_hosts:
        dns_results = await asyncio.gather(*(
            _probe_tcp(host, config.dns_port, probe_timeout) for host in dns_hosts
        ))
        # Легаси-семантика: достаточно одного живого DNS из списка.
        if not any(dns_results):
            unavailable.append(f"dns({','.join(dns_hosts)})")

    return unavailable


async def wait_for_external_services(
    *,
    preflight: dict | None = None,
    on_wait=None,
    should_abort=None,
) -> PreflightResult:
    """Ждать, пока внешние сервисы не станут доступны. Ограничено по времени.

    `preflight` — поле claim payload (CONTRACTS.md C3), `None` — настройки
    из env (`resolve_config`). `on_wait(unavailable, elapsed)` — коллбэк на
    каждую неудачную попытку. `should_abort()` — опрашивается между
    попытками; непустой результат обрывает ожидание с `aborted` (заявка
    оператора на снятие теста).

    Таймаут — обычный провал item'а, очередь стенда не встаёт из-за
    подвисшей проверки.
    """
    try:
        config = resolve_config(preflight, get_settings())
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        # Payload валидирует testing_service; сюда попадаем только при
        # рассинхроне контракта — провал item'а с понятной причиной лучше,
        # чем тихий откат на env с другими адресами.
        logger.error("preflight: invalid settings in claim payload: %r", exc)
        return PreflightResult(ok=False, error=f"invalid preflight settings in claim payload: {exc}")
    if not config.enabled:
        return PreflightResult(ok=True)
    logger.debug("preflight: using %s settings", config.source)

    deadline = time.monotonic() + config.timeout_seconds
    interval = config.poll_interval_seconds
    attempts = 0
    unavailable: list[str] = []

    while True:
        attempts += 1
        try:
            unavailable = await check_once(config)
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

        elapsed = config.timeout_seconds - remaining
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
