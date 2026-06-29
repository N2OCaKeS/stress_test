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

logger = logging.getLogger(__name__)


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


async def _ping_ok(host: str, timeout: float) -> bool:
    """ICMP-ping одним пакетом. True если хост ответил.

    `ping -c 1 -W <sec>` — один эхо-запрос с таймаутом ожидания ответа.
    Отсутствие бинаря (`FileNotFoundError`) трактуем как «ICMP недоступен» и
    возвращаем False — пусть caller полагается на TCP-пробу. Любая другая
    ошибка запуска тоже не должна валить fallback.
    """
    deadline = max(1, int(round(timeout)))
    try:
        proc = await asyncio.create_subprocess_exec(
            "ping", "-c", "1", "-W", str(deadline), host,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except (FileNotFoundError, OSError) as exc:
        logger.debug("ping unavailable for %s: %s", host, exc.__class__.__name__)
        return False
    try:
        rc = await asyncio.wait_for(proc.wait(), timeout=timeout + 1.0)
    except asyncio.TimeoutError:
        # ping завис дольше своего же deadline — прибиваем и считаем неуспехом.
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await proc.wait()
        return False
    return rc == 0


async def _tcp_port_open(host: str, port: int, timeout: float) -> bool:
    """TCP-коннект на `host:port`. True если соединение установилось.

    Открытый SSH-порт (или любой принятый коннект) означает, что у бокса
    поднят сетевой стек и слушает sshd — сервер включён. Коннект сразу
    закрываем, ничего не отправляя.
    """
    try:
        fut = asyncio.open_connection(host, port)
        reader, writer = await asyncio.wait_for(fut, timeout=timeout)
    except (asyncio.TimeoutError, OSError) as exc:
        logger.debug(
            "tcp probe to %s:%s failed: %s", host, port, exc.__class__.__name__,
        )
        return False
    try:
        writer.close()
        await writer.wait_closed()
    except (OSError, asyncio.TimeoutError):
        pass
    return True


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
