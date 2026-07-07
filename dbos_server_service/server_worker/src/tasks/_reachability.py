"""Reachability-проба сервера для fallback'а определения состояния питания.

Когда BMC (Redfish/ipmitool) не отвечает или отдаёт неопределённое состояние,
`power.status` не может сказать «on/off» по управляющему контроллеру. Но если
сама ОС сервера откликается по сети — бокс заведомо включён. Этот модуль даёт
лёгкую сетевую пробу управляющего/основного адреса сервера: ICMP-ping и
TCP-проба SSH-порта.

Зачем две пробы. ICMP в контейнере нередко недоступен (нужен CAP_NET_RAW либо
включённый `net.ipv4.ping_group_range`), поэтому `ping` — best-effort: если
бинарь отсутствует или ICMP запрещён, тихо считаем пробу неуспешной и идём
дальше. TCP-коннект на SSH-порт прав не требует и работает из любого пода —
это основной reachability-метод.

Семантика результата осознанно асимметрична: «порт открыт / ping прошёл» →
сервер включён (`on`), но «не ответил» НЕ означает `off` — это могла быть
сетевая недоступность, firewall или закрытый sshd на работающем боксе.
Поэтому caller на неуспех оставляет `unknown`, а не выдаёт `off`.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time

logger = logging.getLogger(__name__)

# Строка вида `time=0.123 ms` / `time<1 ms` в выводе ping — RTT одного пакета.
_PING_TIME_RE = re.compile(r"time[=<]\s*([\d.]+)\s*ms", re.IGNORECASE)


def _round_ms(value: float) -> float:
    """Округлить latency в миллисекундах до сотых — читаемо и без лишних цифр."""
    return round(value, 2)


def _strip_host(host: str) -> str:
    """Привести host-хинт к голому адресу для ping/connect.

    Снимает scheme (если кто-то прислал `https://...`), квадратные скобки
    IPv6 (`[2001:db8::1]` → `2001:db8::1`) и хвостовой `:port` у IPv4/hostname
    (один `:`). Голый IPv6 без скобок (несколько `:`) отдаём как есть.
    """
    s = host.strip()
    if "://" in s:
        s = s.split("://", 1)[1]
    if "/" in s:
        s = s.split("/", 1)[0]
    if not s:
        return ""
    if s.startswith("["):
        end = s.find("]")
        if end != -1:
            return s[1:end]
        return s
    if s.count(":") == 1:
        return s.split(":", 1)[0]
    return s


def _parse_ping_latency(stdout: bytes) -> float | None:
    """Выдрать RTT (мс) из вывода `ping`. None, если строки `time=` там нет.

    Разные реализации ping печатают `time=0.123 ms` либо `time<1 ms` (округление
    до целого). Берём первое совпадение — при `-c 1` оно единственное.
    """
    text = stdout.decode("utf-8", "replace")
    m = _PING_TIME_RE.search(text)
    if not m:
        return None
    try:
        return _round_ms(float(m.group(1)))
    except ValueError:
        return None


async def _ping_probe(host: str, timeout: float) -> tuple[bool, float | None]:
    """ICMP-ping одним пакетом. `(reachable, latency_ms)`.

    `ping -c 1 -W <sec>` — один эхо-запрос с таймаутом ожидания ответа.
    Latency берём из RTT в выводе (`time=X ms`); если распарсить не удалось —
    fallback на wall-clock вокруг запуска процесса. Отсутствие бинаря
    (`FileNotFoundError`) трактуем как «ICMP недоступен» — `(False, None)`;
    любая другая ошибка запуска тоже не должна валить пробу.
    """
    deadline = max(1, int(round(timeout)))
    started = time.perf_counter()
    try:
        proc = await asyncio.create_subprocess_exec(
            "ping", "-c", "1", "-W", str(deadline), host,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except (FileNotFoundError, OSError) as exc:
        logger.debug("ping unavailable for %s: %s", host, exc.__class__.__name__)
        return False, None
    try:
        stdout, _ = await asyncio.wait_for(
            proc.communicate(), timeout=timeout + 1.0,
        )
    except asyncio.TimeoutError:
        # ping завис дольше своего же deadline — прибиваем и считаем неуспехом.
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await proc.wait()
        return False, None
    if proc.returncode != 0:
        return False, None
    latency = _parse_ping_latency(stdout or b"")
    if latency is None:
        latency = _round_ms((time.perf_counter() - started) * 1000.0)
    return True, latency


async def _tcp_probe(host: str, port: int, timeout: float) -> tuple[bool, float | None]:
    """TCP-коннект на `host:port`. `(reachable, latency_ms)`.

    Открытый SSH-порт (или любой принятый коннект) означает, что у бокса
    поднят сетевой стек и слушает sshd — сервер включён. Latency меряем
    wall-clock'ом вокруг connect/handshake. Коннект сразу закрываем, ничего
    не отправляя; время закрытия в latency не входит.
    """
    started = time.perf_counter()
    try:
        fut = asyncio.open_connection(host, port)
        reader, writer = await asyncio.wait_for(fut, timeout=timeout)
    except (asyncio.TimeoutError, OSError) as exc:
        logger.debug(
            "tcp probe to %s:%s failed: %s", host, port, exc.__class__.__name__,
        )
        return False, None
    latency = _round_ms((time.perf_counter() - started) * 1000.0)
    try:
        writer.close()
        await writer.wait_closed()
    except (OSError, asyncio.TimeoutError):
        pass
    return True, latency


async def _ping_ok(host: str, timeout: float) -> bool:
    """Тонкая обёртка над `_ping_probe`: только флаг достижимости."""
    reachable, _ = await _ping_probe(host, timeout)
    return reachable


async def _tcp_port_open(host: str, port: int, timeout: float) -> bool:
    """Тонкая обёртка над `_tcp_probe`: только флаг достижимости."""
    reachable, _ = await _tcp_probe(host, port, timeout)
    return reachable


async def probe_reachability_signals(
    host: str,
    *,
    ssh_port: int = 22,
    ping_timeout: float = 2.0,
    tcp_timeout: float = 2.0,
) -> dict:
    """Собрать три независимых сетевых сигнала о сервере (ping и ssh с latency).

    В отличие от `probe_power_reachability` (first-wins, отдаёт один метод),
    здесь ping и SSH-порт меряются НЕЗАВИСИМО: обе пробы выполняются всегда,
    даже если первая уже удалась. Возвращает dict:

      * `ping_reachable: bool` / `ping_latency_ms: float | None`
      * `ssh_reachable: bool` / `ssh_latency_ms: float | None`

    Недоступная проба → `reachable=False`, `latency_ms=None`. Пустой/битый host
    → все сигналы недоступны. Latency в миллисекундах, округлён до сотых.
    """
    empty = {
        "ping_reachable": False,
        "ping_latency_ms": None,
        "ssh_reachable": False,
        "ssh_latency_ms": None,
    }
    bare = _strip_host(host)
    if not bare:
        return empty
    ping_reachable, ping_latency = await _ping_probe(bare, ping_timeout)
    ssh_reachable, ssh_latency = await _tcp_probe(bare, ssh_port, tcp_timeout)
    return {
        "ping_reachable": ping_reachable,
        "ping_latency_ms": ping_latency,
        "ssh_reachable": ssh_reachable,
        "ssh_latency_ms": ssh_latency,
    }


async def probe_power_reachability(
    host: str,
    *,
    ssh_port: int = 22,
    ping_timeout: float = 2.0,
    tcp_timeout: float = 2.0,
) -> str | None:
    """Определить, доступен ли сервер по сети. Вернуть метод успеха либо None.

    Порядок: сначала ICMP-ping (дешёвый, не зависит от конкретного сервиса),
    затем TCP-проба SSH-порта (работает в контейнере без привилегий). Первая
    успешная проба и есть источник эвристики:

      * `"ping"` — хост ответил на ICMP;
      * `"ssh"` — открыт SSH-порт;
      * `None` — ни одна проба не прошла (хост недоступен либо firewall'ом
        закрыт; это НЕ доказывает, что бокс выключен).
    """
    bare = _strip_host(host)
    if not bare:
        return None
    if await _ping_ok(bare, ping_timeout):
        return "ping"
    if await _tcp_port_open(bare, ssh_port, tcp_timeout):
        return "ssh"
    return None
