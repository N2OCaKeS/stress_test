"""IP источника запроса для контроля доступа по подсетям.

`sdk/extract_client_ip.py` (и его копии в auth/server_service) берёт из
`X-Forwarded-For` самый ЛЕВЫЙ недоверенный адрес — для аудита этого хватает,
но для контроля доступа нет: клиент сам пишет в заголовок
`X-Forwarded-For: 10.177.103.201`, прокси дописывает настоящий адрес справа,
и левый элемент оказывается подделкой. Здесь цепочка читается справа
налево: пропускаем доверенные прокси (`TRUSTED_PROXY_IPS`), первый
недоверенный адрес — тот, кого видел наш крайний прокси.

Что приходит в `request.client.host`, зависит от деплоя:

* docker-compose: uvicorn с `--proxy-headers --forwarded-allow-ips=*` уже
  подменил его первым элементом `X-Forwarded-For`, а nginx в
  `location /rest/api/` этот заголовок ПЕРЕЗАПИСЫВАЕТ адресом клиента
  (`docker/compose-proxy/nginx.conf`) — подделать нечего;
* k8s: то же самое делает traefik (заголовки от недоверенных клиентов он
  отбрасывает); `TRUSTED_PROXY_IPS` (pod-CIDR traefik'а) нужен, если uvicorn
  запущен без разбора заголовков и видит адрес пода traefik.
"""

from __future__ import annotations

import ipaddress
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import Request


def _parse_ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        ip = ipaddress.ip_address(value.strip())
    except ValueError:
        return None
    # IPv4-mapped IPv6 (`::ffff:10.177.103.5`) — dual-stack сокет; сравниваем как IPv4.
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def _is_trusted(ip: ipaddress.IPv4Address | ipaddress.IPv6Address, trusted: list[str]) -> bool:
    for entry in trusted:
        entry = (entry or "").strip()
        if not entry:
            continue
        try:
            if ip in ipaddress.ip_network(entry, strict=False):
                return True
        except ValueError:
            continue
    return False


def normalize_ip(value: str | None) -> str | None:
    """Строковая форма адреса (`::ffff:a.b.c.d` → `a.b.c.d`); мусор → None."""
    if not value:
        return None
    ip = _parse_ip(value)
    return str(ip) if ip is not None else None


def source_ip(request: "Request", trusted_proxy_ips: list[str] | None = None) -> str | None:
    """IP источника с учётом доверенных прокси (правый недоверенный из XFF)."""
    if trusted_proxy_ips is None:
        from src.core.config import get_settings

        trusted_proxy_ips = list(get_settings().trusted_proxy_ips or [])

    direct = _parse_ip(request.client.host) if request.client and request.client.host else None
    if direct is None:
        return None
    if not trusted_proxy_ips or not _is_trusted(direct, trusted_proxy_ips):
        return str(direct)

    xff = request.headers.get("X-Forwarded-For") or ""
    for raw in reversed(xff.split(",")):
        ip = _parse_ip(raw)
        if ip is None:
            # Мусор в цепочке — дальше влево доверять нельзя.
            break
        if not _is_trusted(ip, trusted_proxy_ips):
            return str(ip)
    real_ip = _parse_ip(request.headers.get("X-Real-IP") or "")
    if real_ip is not None and not _is_trusted(real_ip, trusted_proxy_ips):
        return str(real_ip)
    return str(direct)
