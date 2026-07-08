from __future__ import annotations

import time

from src import token as tok
from src.app import create_app

from tests.conftest import SECRET, make_settings


def _token(port: int, *, kind="vnc", vm_id="vm_1", exp_delta=120, hub_ip="127.0.0.1"):
    return tok.sign(
        {
            "vm_id": vm_id,
            "kind": kind,
            "hub_ip": hub_ip,
            "hub_server_id": "srv_1",
            "port": port,
            "exp": int(time.time()) + exp_delta,
            "jti": "vmc_1",
        },
        SECRET,
        issuer="dbos-server-service",
    )


async def test_healthz(aiohttp_client):
    client = await aiohttp_client(create_app(make_settings()))
    resp = await client.get("/healthz")
    assert resp.status == 200
    assert (await resp.json())["status"] == "ok"


async def test_readyz_ok(aiohttp_client):
    client = await aiohttp_client(create_app(make_settings()))
    resp = await client.get("/readyz")
    assert resp.status == 200


async def test_readyz_unconfigured(aiohttp_client):
    client = await aiohttp_client(create_app(make_settings(console_token_secret="")))
    resp = await client.get("/readyz")
    assert resp.status == 503


async def test_page_served_for_plain_get(aiohttp_client):
    client = await aiohttp_client(create_app(make_settings()))
    resp = await client.get("/vm-console/vnc/vm_1")
    assert resp.status == 200
    body = await resp.text()
    assert "vm_1" in body
    assert "Content-Security-Policy" in resp.headers
    # CDN запрещён — connect-src только self.
    assert "connect-src 'self'" in resp.headers["Content-Security-Policy"]


async def test_ws_bridge_end_to_end(aiohttp_client, echo_server):
    client = await aiohttp_client(create_app(make_settings()))
    token = _token(echo_server)
    ws = await client.ws_connect(f"/vm-console/vnc/vm_1?token={token}", protocols=["binary"])
    await ws.send_bytes(b"\x03\x00world")
    msg = await ws.receive()
    assert msg.data == b"echo:\x03\x00world"
    await ws.close()


async def test_ws_bridge_token_via_subprotocol(aiohttp_client, echo_server):
    client = await aiohttp_client(create_app(make_settings()))
    token = _token(echo_server)
    ws = await client.ws_connect(
        "/vm-console/vnc/vm_1", protocols=["binary", f"bearer.{token}"]
    )
    await ws.send_bytes(b"ping")
    msg = await ws.receive()
    assert msg.data == b"echo:ping"
    await ws.close()


async def test_ws_rejects_bad_token(aiohttp_client, echo_server):
    client = await aiohttp_client(create_app(make_settings()))
    ws = await client.ws_connect("/vm-console/vnc/vm_1?token=garbage", protocols=["binary"])
    msg = await ws.receive()
    # Соединение закрывается с 4401-семейством.
    assert msg.type.name in ("CLOSE", "CLOSING", "CLOSED")
    assert ws.close_code == 4401


async def test_ws_rejects_expired_token(aiohttp_client, echo_server):
    client = await aiohttp_client(create_app(make_settings()))
    token = _token(echo_server, exp_delta=-300)
    ws = await client.ws_connect(f"/vm-console/vnc/vm_1?token={token}", protocols=["binary"])
    msg = await ws.receive()
    assert msg.type.name in ("CLOSE", "CLOSING", "CLOSED")
    assert ws.close_code == 4401


async def test_ws_rejects_kind_mismatch(aiohttp_client, echo_server):
    client = await aiohttp_client(create_app(make_settings()))
    # Токен на vnc, а путь запрашивает spice.
    token = _token(echo_server, kind="vnc")
    ws = await client.ws_connect(f"/vm-console/spice/vm_1?token={token}", protocols=["binary"])
    msg = await ws.receive()
    assert msg.type.name in ("CLOSE", "CLOSING", "CLOSED")
    assert ws.close_code == 4403


async def test_ws_rejects_vm_mismatch(aiohttp_client, echo_server):
    client = await aiohttp_client(create_app(make_settings()))
    token = _token(echo_server, vm_id="vm_1")
    ws = await client.ws_connect(f"/vm-console/vnc/vm_OTHER?token={token}", protocols=["binary"])
    msg = await ws.receive()
    assert msg.type.name in ("CLOSE", "CLOSING", "CLOSED")
    assert ws.close_code == 4403


async def test_unknown_kind_404(aiohttp_client):
    client = await aiohttp_client(create_app(make_settings()))
    resp = await client.get("/vm-console/telnet/vm_1")
    assert resp.status == 404
