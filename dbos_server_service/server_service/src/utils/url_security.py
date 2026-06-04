"""Защита от SSRF на входной валидации URL и hostname-полей.

Worker server_worker делает реальный HTTP/SSH к адресам, которые приходят
из IPMI-карточки и из карточки сервера. Без фильтрации операторский запрос
вида `https://169.254.169.254/latest/meta-data/...` обернётся в обход
сегментации (cloud-metadata, loopback, link-local).

RFC-1918 private (10/8, 172.16/12, 192.168/16) умышленно НЕ блокируем —
BMC живёт ровно там. Блокируем только адреса, которые в нашей сети
никогда не должны быть BMC: loopback, link-local (включая 169.254.169.254
AWS/GCP metadata), CGNAT, multicast, reserved, any-address.

DNS-имена не резолвим (резолв в validator'е даёт TOCTOU, расходится с
тем, что увидит worker, и зависит от DNS infra). IP-литералы в hostname
парсятся через `ipaddress.ip_address()` и проверяются по категории.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

_ALLOWED_SCHEMES = frozenset({"http", "https"})

# CIDR'ы, которые гарантированно не должны быть BMC или target'ом
# инвентаризационного SSH. `ip_address.is_*` свойства покрывают
# большинство категорий, но 100.64.0.0/10 (CGNAT) приходится держать
# явным CIDR'ом — отдельного флага под него у ipaddress нет.
_CGNAT_V4 = ipaddress.ip_network("100.64.0.0/10")


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


def validate_safe_endpoint_url(value: str, *, field_name: str = "endpoint_url") -> str:
    """Проверить, что URL пригоден для исходящего HTTP'а worker'а.

    Допустимые схемы — `http` и `https`. `http` оставлен для legacy ipmitool,
    но HTTPS предпочтительнее (см. obsidian-доку про BMC transport).

    Если hostname — IP-литерал, проверяем по категории. DNS-имя пропускаем
    без резолва (резолв в validator'е TOCTOU'ит относительно worker'а).
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

    return value


def validate_safe_hostname(value: str, *, field_name: str = "hostname") -> str:
    """Проверить hostname сервера — FQDN или IP-литерал.

    Сервер обычно адресуется по FQDN, реже — голым IP. Если приехал
    IP-литерал, проверяем те же категории, что и для endpoint_url'а:
    loopback / link-local / multicast и т.п. — это инвентарь машин,
    не точки управления внутри сервиса. DNS-имена пропускаем без резолва.
    """
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

    return value
