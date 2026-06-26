"""Pin-resolve SSRF-guard в `src/utils/url_security.py`.

Литеральные адреса (loopback / link-local / metadata) отбиваются без DNS.
Для доменов проверяем pin-resolve: резолв один раз, каждый адрес проверяется
по категории, отказ — если домен ведёт в заблокированную сеть; смена резолва
после проверки не обходит guard (caller коннектится к pinned_ip, не резолвит
второй раз).
"""

from __future__ import annotations

import socket

import pytest

from src.utils.url_security import (
    resolve_endpoint_url,
    resolve_hostname,
    validate_safe_endpoint_url,
    validate_safe_hostname,
)


def _fake_getaddrinfo(mapping):
    """getaddrinfo-замена: host → список IP-строк."""

    def _inner(host, *_args, **_kwargs):
        addrs = mapping.get(host)
        if addrs is None:
            raise socket.gaierror(socket.EAI_NONAME, "name resolution failed")
        return [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (a, 0))
            for a in addrs
        ]

    return _inner


def test_literal_metadata_blocked_without_resolve():
    # 169.254.169.254 — link-local (AWS/GCP metadata): отбой даже без резолва.
    with pytest.raises(ValueError, match="link_local_address"):
        validate_safe_endpoint_url("https://169.254.169.254/latest/")


def test_literal_loopback_blocked():
    with pytest.raises(ValueError, match="loopback_address"):
        validate_safe_hostname("127.0.0.1")


def test_literal_allowed_returns_pin():
    # RFC-1918 — BMC живёт там, не блокируем; pin = сам литерал.
    pinned = resolve_endpoint_url("https://10.0.0.5", resolve=True)
    assert pinned.pinned_ip == "10.0.0.5"


def test_domain_skipped_when_resolve_off():
    # resolve=False — DNS не трогаем, домен проходит, pin отсутствует.
    pinned = resolve_endpoint_url("https://idrac.example.com", resolve=False)
    assert pinned.pinned_ip is None


def test_domain_resolving_to_blocked_ip_is_rejected(monkeypatch):
    monkeypatch.setattr(
        socket, "getaddrinfo",
        _fake_getaddrinfo({"evil.example.com": ["169.254.169.254"]}),
    )
    with pytest.raises(ValueError, match="forbidden address: link_local_address"):
        resolve_endpoint_url("https://evil.example.com/redfish", resolve=True)


def test_domain_resolving_to_loopback_rejected(monkeypatch):
    monkeypatch.setattr(
        socket, "getaddrinfo",
        _fake_getaddrinfo({"trap.example.com": ["127.0.0.1"]}),
    )
    with pytest.raises(ValueError, match="forbidden address: loopback_address"):
        resolve_hostname("trap.example.com", resolve=True)


def test_domain_with_one_bad_address_rejected(monkeypatch):
    # Несколько A-записей: одна легитимна (private), вторая — metadata.
    # Guard обязан отбить, а не пройти по «удачному» адресу.
    monkeypatch.setattr(
        socket, "getaddrinfo",
        _fake_getaddrinfo({"mixed.example.com": ["10.0.0.5", "169.254.169.254"]}),
    )
    with pytest.raises(ValueError, match="forbidden address"):
        resolve_endpoint_url("https://mixed.example.com", resolve=True)


def test_good_domain_pins_first_address(monkeypatch):
    monkeypatch.setattr(
        socket, "getaddrinfo",
        _fake_getaddrinfo({"idrac.example.com": ["10.20.30.40"]}),
    )
    pinned = resolve_endpoint_url("https://idrac.example.com/redfish", resolve=True)
    assert pinned.pinned_ip == "10.20.30.40"
    assert pinned.value == "https://idrac.example.com/redfish"


def test_pin_does_not_reresolve_after_check(monkeypatch):
    # TOCTOU: первый резолв безопасен, после проверки DNS «переключают» на
    # metadata. Caller держит pinned_ip из первого резолва и коннектится к
    # нему — повторного резолва нет, guard не обходится.
    calls = {"n": 0}

    def _flipping(host, *_a, **_k):
        calls["n"] += 1
        addr = "10.0.0.7" if calls["n"] == 1 else "169.254.169.254"
        return [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (addr, 0))
        ]

    monkeypatch.setattr(socket, "getaddrinfo", _flipping)
    pinned = resolve_hostname("rebind.example.com", resolve=True)
    assert pinned.pinned_ip == "10.0.0.7"
    # Ровно один резолв на валидацию — pin фиксирует адрес, повторного
    # getaddrinfo в guard'е нет.
    assert calls["n"] == 1


def test_unresolvable_domain_raises_value_error(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo({}))
    with pytest.raises(ValueError, match="could not be resolved"):
        resolve_endpoint_url("https://nope.example.com", resolve=True)


def test_bad_scheme_rejected_before_resolve(monkeypatch):
    # Схема проверяется до резолва — getaddrinfo не должен дёргаться.
    def _boom(*_a, **_k):  # pragma: no cover - не должна вызваться
        raise AssertionError("getaddrinfo must not be called for bad scheme")

    monkeypatch.setattr(socket, "getaddrinfo", _boom)
    with pytest.raises(ValueError, match="http or https scheme"):
        resolve_endpoint_url("ftp://idrac.example.com", resolve=True)
