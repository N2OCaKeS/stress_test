"""Защита от SSRF на входной валидации URL и hostname-полей.

Worker server_worker делает реальный HTTP/SSH к адресам, которые приходят
из IPMI-карточки и из карточки сервера. Без фильтрации операторский запрос
вида `https://169.254.169.254/latest/meta-data/...` обернётся в обход
сегментации (cloud-metadata, loopback, link-local).

RFC-1918 private (10/8, 172.16/12, 192.168/16) умышленно НЕ блокируем —
BMC живёт ровно там. Блокируем только адреса, которые в нашей сети
никогда не должны быть BMC: loopback, link-local (включая 169.254.169.254
AWS/GCP metadata), CGNAT, multicast, reserved, any-address.

IP-литералы в hostname парсятся через `ipaddress.ip_address()` и проверяются
по категории.

DNS-имена резолвятся ОДИН раз и каждый полученный адрес проверяется по той
же категории, что и литерал. Это закрывает кейс «домен резолвится в
заблокированный IP» и фиксирует адрес, к которому реально пойдёт соединение
(pin-resolve против TOCTOU между проверкой и connect'ом). Резолв опционален:
по умолчанию write-валидация его выполняет, но на средах без DNS или для
полей, где connect делает другой сервис, его можно отключить флагом.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlparse

_ALLOWED_SCHEMES = frozenset({"http", "https"})

# CIDR'ы, которые гарантированно не должны быть BMC или target'ом
# инвентаризационного SSH. `ip_address.is_*` свойства покрывают
# большинство категорий, но 100.64.0.0/10 (CGNAT) приходится держать
# явным CIDR'ом — отдельного флага под него у ipaddress нет.
_CGNAT_V4 = ipaddress.ip_network("100.64.0.0/10")


@dataclass(frozen=True)
class PinnedEndpoint:
    """Результат pin-resolve: проверенный URL/hostname + зафиксированный IP.

    `pinned_ip` — адрес, к которому реально пойдёт соединение. None, если в
    hostname был IP-литерал (пинить нечего — он уже фиксированный) либо резолв
    был отключён. Caller, который сам открывает соединение, обязан коннектиться
    именно к `pinned_ip` (а не резолвить hostname повторно), иначе guard
    обходится сменой DNS-ответа между проверкой и connect'ом.
    """

    value: str
    pinned_ip: str | None


def _is_forbidden_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str | None:
    """Вернуть код-причину блокировки или None, если адрес допустим.

    Коды — короткие машинные строки, идут в текст ValueError'а, чтобы
    422-payload отличал loopback от link-local на стороне SIEM.
    """
    if ip.is_unspecified:
        return "unspecified_address"
    if ip.is_loopback:
        return "loopback_address"
    if ip.is_link_local:
        # AWS/GCP metadata-эндпоинт 169.254.169.254 попадает сюда же.
        return "link_local_address"
    if ip.is_multicast:
        return "multicast_address"
    if ip.is_reserved:
        return "reserved_address"
    if isinstance(ip, ipaddress.IPv4Address) and ip in _CGNAT_V4:
        return "cgnat_address"
    return None


def _try_parse_ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """Распарсить IP-литерал из hostname-компоненты URL.

    IPv6 в URL пишется в квадратных скобках (`https://[::1]:443/...`);
    `urlparse().hostname` уже снимает скобки, так что просто пробуем
    `ip_address()`. На DNS-имени поднимется ValueError → возвращаем None.
    """
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        return None


def _resolve_host(host: str) -> list[str]:
    """Резолвнуть hostname в список IP-строк (один проход getaddrinfo).

    Возвращает все адреса, которые отдал resolver (v4 и v6). Пустой ответ
    невозможен: `getaddrinfo` либо отдаёт хотя бы один адрес, либо кидает
    `socket.gaierror` → транслируем в ValueError, чтобы валидатор отбил 422
    вместо 500.
    """
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ValueError(f"host could not be resolved: {host!r}") from exc
    # Дедупим с сохранением порядка — getaddrinfo часто отдаёт дубли на
    # каждый socktype/proto.
    seen: set[str] = set()
    addrs: list[str] = []
    for info in infos:
        addr = info[4][0]
        if addr not in seen:
            seen.add(addr)
            addrs.append(addr)
    return addrs


def _check_resolved_addrs(host: str, *, field_name: str) -> str:
    """Резолвнуть host и проверить КАЖДЫЙ адрес по категории.

    Возвращает первый адрес (он же pin-адрес). Если хоть один из полученных
    адресов запрещён — отбой: домен с несколькими A-записями, где одна ведёт
    в metadata-сеть, не должен пройти по «удачному» адресу.
    """
    addrs = _resolve_host(host)
    for addr in addrs:
        ip = _try_parse_ip(addr)
        if ip is None:
            # getaddrinfo вернул что-то, что не парсится как IP — параноидальный
            # отбой, нормальный resolver такого не отдаёт.
            raise ValueError(f"{field_name} resolved to non-IP value: {addr!r}")
        reason = _is_forbidden_ip(ip)
        if reason is not None:
            raise ValueError(
                f"{field_name} resolves to forbidden address: {reason}"
            )
    # Первый адрес — pin: к нему пойдёт connect. Список уже дедуплен и проверен.
    return addrs[0]


def validate_safe_endpoint_url(
    value: str,
    *,
    field_name: str = "endpoint_url",
    resolve: bool = False,
) -> str:
    """Проверить, что URL пригоден для исходящего HTTP'а worker'а.

    Допустимые схемы — `http` и `https`. `http` оставлен для legacy ipmitool,
    но HTTPS предпочтительнее (см. obsidian-доку про BMC transport).

    Если hostname — IP-литерал, проверяем по категории. DNS-имя при
    `resolve=False` пропускаем без резолва; при `resolve=True` — резолвим один
    раз и проверяем каждый полученный адрес (см. `resolve_endpoint_url` для
    варианта, возвращающего pin-адрес).
    """
    return resolve_endpoint_url(
        value, field_name=field_name, resolve=resolve,
    ).value


def resolve_endpoint_url(
    value: str,
    *,
    field_name: str = "endpoint_url",
    resolve: bool = True,
) -> PinnedEndpoint:
    """Pin-resolve вариант `validate_safe_endpoint_url`.

    Возвращает `PinnedEndpoint(value, pinned_ip)`. Для IP-литерала `pinned_ip`
    повторяет литерал. Для DNS-имени при `resolve=True` — первый резолвнутый
    адрес (он же проверенный): caller, который сам коннектится, должен идти
    к нему, а не резолвить hostname второй раз.
    """
    parsed = urlparse(value.strip())

    scheme = (parsed.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise ValueError(
            f"{field_name} must use http or https scheme (got {scheme!r})"
        )

    host = parsed.hostname
    if not host:
        raise ValueError(f"{field_name} is missing host component")

    host_lower = host.lower()
    # localhost — отдельная ветка: ip_address() его не распарсит, но это
    # тоже loopback. Закрываем явно, чтобы не зависеть от resolver'а.
    if host_lower == "localhost" or host_lower.endswith(".localhost"):
        raise ValueError(
            f"{field_name} host is forbidden: loopback_hostname"
        )

    ip = _try_parse_ip(host)
    if ip is not None:
        reason = _is_forbidden_ip(ip)
        if reason is not None:
            raise ValueError(
                f"{field_name} host is forbidden: {reason}"
            )
        return PinnedEndpoint(value=value, pinned_ip=str(ip))

    if resolve:
        pinned = _check_resolved_addrs(host, field_name=field_name)
        return PinnedEndpoint(value=value, pinned_ip=pinned)
    return PinnedEndpoint(value=value, pinned_ip=None)


def validate_safe_hostname(
    value: str,
    *,
    field_name: str = "hostname",
    resolve: bool = False,
) -> str:
    """Проверить hostname сервера — FQDN или IP-литерал.

    Сервер обычно адресуется по FQDN, реже — голым IP. Если приехал
    IP-литерал, проверяем те же категории, что и для endpoint_url'а:
    loopback / link-local / multicast и т.п. — это инвентарь машин,
    не точки управления внутри сервиса. DNS-имена при `resolve=False`
    пропускаем без резолва; при `resolve=True` — резолвим и проверяем
    каждый адрес.
    """
    return resolve_hostname(value, field_name=field_name, resolve=resolve).value


def resolve_hostname(
    value: str,
    *,
    field_name: str = "hostname",
    resolve: bool = True,
) -> PinnedEndpoint:
    """Pin-resolve вариант `validate_safe_hostname` (см. `resolve_endpoint_url`)."""
    host = value.strip()
    if not host:
        raise ValueError(f"{field_name} must not be empty")

    host_lower = host.lower()
    if host_lower == "localhost" or host_lower.endswith(".localhost"):
        raise ValueError(
            f"{field_name} is forbidden: loopback_hostname"
        )

    ip = _try_parse_ip(host)
    if ip is not None:
        reason = _is_forbidden_ip(ip)
        if reason is not None:
            raise ValueError(
                f"{field_name} is forbidden: {reason}"
            )
        return PinnedEndpoint(value=value, pinned_ip=str(ip))

    if resolve:
        pinned = _check_resolved_addrs(host, field_name=field_name)
        return PinnedEndpoint(value=value, pinned_ip=pinned)
    return PinnedEndpoint(value=value, pinned_ip=None)
