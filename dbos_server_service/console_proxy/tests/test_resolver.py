from __future__ import annotations

import pytest

from src.resolver import (
    DirectResolver,
    ResolveError,
    SshTunnelResolver,
    _port_from_claims,
    build_resolver,
)
from src.token import ConsoleClaims

from tests.conftest import make_settings


def _claims(**over) -> ConsoleClaims:
    base = dict(
        vm_id="vm_1",
        kind="vnc",
        hub_ip="127.0.0.1",
        hub_server_id="srv_1",
        department_id="dep_1",
        domain=None,
        port=5901,
        display=None,
        ssh_port=22,
        jti="vmc_1",
        exp=0,
    )
    base.update(over)
    return ConsoleClaims(**base)


def test_port_from_claims_explicit():
    assert _port_from_claims(_claims(port=5905)) == 5905


def test_port_from_claims_display():
    assert _port_from_claims(_claims(port=None, display=2)) == 5902


def test_port_from_claims_none():
    assert _port_from_claims(_claims(port=None, display=None)) is None


async def test_direct_resolver_connects_and_bridges(echo_server):
    resolver = DirectResolver(connect_timeout=5)
    target = await resolver.open(_claims(hub_ip="127.0.0.1", port=echo_server))
    try:
        target.writer.write(b"hello")
        await target.writer.drain()
        data = await target.reader.read(64)
        assert data == b"echo:hello"
    finally:
        await target.close()


async def test_direct_resolver_requires_port():
    resolver = DirectResolver()
    with pytest.raises(ResolveError) as e:
        await resolver.open(_claims(port=None, display=None))
    assert e.value.code == "NO_PORT"


async def test_direct_resolver_unreachable():
    resolver = DirectResolver(connect_timeout=1)
    # Порт 1 на localhost почти наверняка закрыт.
    with pytest.raises(ResolveError) as e:
        await resolver.open(_claims(hub_ip="127.0.0.1", port=1))
    assert e.value.code == "TARGET_UNREACHABLE"


def test_build_resolver_direct():
    r = build_resolver(make_settings(target_mode="direct"), internal_client=None)
    assert isinstance(r, DirectResolver)


def test_build_resolver_ssh_default():
    r = build_resolver(make_settings(target_mode="ssh"), internal_client=object())
    assert isinstance(r, SshTunnelResolver)


async def test_ssh_resolver_requires_hub_server_id():
    resolver = SshTunnelResolver(internal_client=None)
    with pytest.raises(ResolveError) as e:
        await resolver.open(_claims(hub_server_id=None))
    assert e.value.code == "NO_HUB_SERVER"
