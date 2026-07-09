"""Резолв TCP-таргета консоли ВМ на хабе.

Две стратегии (см. `CONSOLE_TARGET_MODE`):

* **direct** — libvirt/qemu настроены слушать VNC/SPICE на mgmt-LAN хаба, порт
  известен из токена. Прокси просто открывает `asyncio.open_connection(hub_ip, port)`.
  Достижимость: под console_proxy должен иметь сетевой путь до `hub_ip:port`
  (NetworkPolicy egress + LAN-маршрут). Небезопасный дефолт qemu — bind на
  0.0.0.0 — здесь обязателен, поэтому режим годится только для доверенного
  сегмента.

* **ssh** (дефолт) — qemu слушает на 127.0.0.1 хаба (безопасный дефолт). Прокси
  под управляющей учёткой хаба открывает SSH-туннель и внутри него direct-tcpip
  до `127.0.0.1:port`. Порт при отсутствии в токене резолвится через
  `virsh domdisplay <domain>` на самом хабе. Управляющие креды хаба тянутся из
  server_service (`InternalClient`).

Обе стратегии возвращают `Target` — унифицированную пару stream'ов (reader/writer
у asyncio и asyncssh совместимы по API) плюс `close()`, которую дёргает мост.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any, Protocol

from src.internal_client import HubCredentials, InternalClient
from src.token import ConsoleClaims


class ResolveError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class Target:
    reader: Any
    writer: Any
    _closers: list[Any]

    async def close(self) -> None:
        try:
            self.writer.close()
        except Exception:
            pass
        for closer in reversed(self._closers):
            try:
                res = closer()
                if asyncio.iscoroutine(res):
                    await res
            except Exception:
                pass


class Resolver(Protocol):
    async def open(self, claims: ConsoleClaims) -> Target: ...


class DirectResolver:
    """Прямой TCP на hub_ip:port (порт обязателен в токене)."""

    def __init__(self, *, connect_timeout: int = 10) -> None:
        self._timeout = connect_timeout

    async def open(self, claims: ConsoleClaims) -> Target:
        port = _port_from_claims(claims)
        if port is None:
            raise ResolveError(
                "NO_PORT", "direct mode requires an explicit port in the token"
            )
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(claims.hub_ip, port),
                timeout=self._timeout,
            )
        except (OSError, asyncio.TimeoutError) as exc:
            raise ResolveError(
                "TARGET_UNREACHABLE",
                f"cannot reach {claims.hub_ip}:{port}: {exc}",
            ) from exc
        return Target(reader=reader, writer=writer, _closers=[])


_VNCDISPLAY_RE = re.compile(r":(\d+)")
_DOMDISPLAY_RE = re.compile(r"(?:vnc|spice)://[^:]+:(\d+)")


class SshTunnelResolver:
    """SSH-туннель на хаб + резолв дисплея через virsh."""

    def __init__(
        self,
        *,
        internal_client: InternalClient,
        connect_timeout: int = 15,
        credentials_provider=None,
    ) -> None:
        self._internal = internal_client
        self._timeout = connect_timeout
        # Позволяет тестам подсунуть креды без реального server_service.
        self._credentials_provider = credentials_provider

    async def _creds(self, claims: ConsoleClaims) -> HubCredentials:
        if self._credentials_provider is not None:
            return await self._credentials_provider(claims)
        if not claims.hub_server_id:
            raise ResolveError(
                "NO_HUB_SERVER", "hub_server_id is required for ssh mode"
            )
        return await self._internal.fetch_hub_credentials(
            claims.hub_server_id, target_department_id=claims.department_id
        )

    async def open(self, claims: ConsoleClaims) -> Target:
        creds = await self._creds(claims)

        import asyncssh  # локальный импорт — тяжёлая зависимость, нужна только тут

        connect_kwargs: dict[str, Any] = {
            "host": claims.hub_ip,
            "port": claims.ssh_port,
            "username": creds.management_user,
            "known_hosts": None,
            "connect_timeout": self._timeout,
        }
        if creds.ssh_private_key:
            connect_kwargs["client_keys"] = [
                asyncssh.import_private_key(creds.ssh_private_key)
            ]
        if creds.password:
            connect_kwargs["password"] = creds.password

        try:
            conn = await asyncio.wait_for(
                asyncssh.connect(**connect_kwargs), timeout=self._timeout
            )
        except Exception as exc:  # asyncssh поднимает разнородные ошибки
            raise ResolveError(
                "SSH_CONNECT_FAILED", f"ssh to hub {claims.hub_ip} failed: {exc}"
            ) from exc

        try:
            port = _port_from_claims(claims)
            if port is None:
                port = await self._resolve_via_virsh(conn, claims)
            reader, writer = await conn.open_connection("127.0.0.1", port)
        except ResolveError:
            conn.close()
            raise
        except Exception as exc:
            conn.close()
            raise ResolveError(
                "TUNNEL_FAILED",
                f"cannot tunnel to 127.0.0.1:{port} on hub: {exc}",
            ) from exc

        return Target(reader=reader, writer=writer, _closers=[conn.close, conn.wait_closed])

    async def _resolve_via_virsh(self, conn, claims: ConsoleClaims) -> int:
        if not claims.domain:
            raise ResolveError(
                "NO_DOMAIN",
                "cannot resolve display without domain name or explicit port",
            )
        domain = claims.domain
        if claims.kind == "vnc":
            # vncdisplay отдаёт `:N` (номер дисплея) → TCP 5900+N. Надёжнее
            # domdisplay, который для vnc возвращает номер дисплея, а не порт.
            out = await self._virsh(conn, f"vncdisplay {_sh_quote(domain)}")
            m = _VNCDISPLAY_RE.search(out)
            if m:
                return 5900 + int(m.group(1))
        else:
            # spice: domdisplay отдаёт spice://host:PORT (реальный TCP-порт).
            out = await self._virsh(conn, f"domdisplay {_sh_quote(domain)}")
            m = _DOMDISPLAY_RE.search(out)
            if m:
                return int(m.group(1))
        raise ResolveError(
            "DISPLAY_NOT_FOUND",
            f"virsh did not report a {claims.kind} display for {domain}",
        )

    async def _virsh(self, conn, args: str) -> str:
        """virsh с fallback: сначала user (qemu:///session, без sudo), затем
        sudo (qemu:///system). ВМ в пользовательском libvirt резолвятся без
        привилегий; ВМ в системном — через sudo (NOPASSWD с prepare)."""
        result = await conn.run(f"virsh {args}", check=False)
        out = (result.stdout or "").strip()
        if result.exit_status == 0 and out:
            return out
        result = await conn.run(f"sudo virsh {args}", check=False)
        return (result.stdout or "").strip()


def _port_from_claims(claims: ConsoleClaims) -> int | None:
    if claims.port is not None:
        return claims.port
    if claims.display is not None:
        # По конвенции VNC-дисплей N → TCP 5900+N.
        return 5900 + claims.display
    return None


def _sh_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def build_resolver(settings, internal_client: InternalClient) -> Resolver:
    if settings.target_mode == "direct":
        return DirectResolver(connect_timeout=settings.tcp_connect_timeout)
    return SshTunnelResolver(
        internal_client=internal_client,
        connect_timeout=settings.ssh_connect_timeout,
    )
