"""Двунаправленный мост WebSocket ↔ TCP.

Браузер (noVNC/spice-html5) шлёт бинарные WS-кадры — байты RFB/SPICE-протокола.
Мост перекладывает их в TCP-таргет консоли и обратно. Никакой интерпретации
протокола: чистый байтовый прокси. Обе стороны закрываются, как только любая
из них отвалилась, плюс общий cap на длительность сессии.
"""

from __future__ import annotations

import asyncio

from aiohttp import WSMsgType, web

from src.resolver import Target

_CHUNK = 65536


async def pump(ws: web.WebSocketResponse, target: Target, *, idle_timeout: int) -> None:
    """Гонять байты в обе стороны, пока одна из сторон не закроется."""
    ws_to_tcp = asyncio.create_task(_ws_to_tcp(ws, target))
    tcp_to_ws = asyncio.create_task(_tcp_to_ws(ws, target))
    tasks = {ws_to_tcp, tcp_to_ws}
    try:
        done, pending = await asyncio.wait(
            tasks, timeout=idle_timeout, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        # Дать отменённым задачам корректно свернуться.
        await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            exc = task.exception()
            if exc is not None and not isinstance(exc, asyncio.CancelledError):
                raise exc
    finally:
        await target.close()


async def _ws_to_tcp(ws: web.WebSocketResponse, target: Target) -> None:
    async for msg in ws:
        if msg.type == WSMsgType.BINARY:
            target.writer.write(msg.data)
            await target.writer.drain()
        elif msg.type == WSMsgType.TEXT:
            # noVNC/spice шлют бинарь; текст трактуем как байты на всякий случай.
            target.writer.write(msg.data.encode())
            await target.writer.drain()
        elif msg.type in (WSMsgType.CLOSE, WSMsgType.CLOSING, WSMsgType.CLOSED, WSMsgType.ERROR):
            break


async def _tcp_to_ws(ws: web.WebSocketResponse, target: Target) -> None:
    while True:
        data = await target.reader.read(_CHUNK)
        if not data:
            break
        if ws.closed:
            break
        await ws.send_bytes(bytes(data))
