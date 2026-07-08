from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from src.config import Settings

SECRET = "test-console-secret-at-least-32-chars-long"
STATIC_DIR = str(Path(__file__).resolve().parent.parent / "static")


def make_settings(**overrides) -> Settings:
    """Собрать Settings для теста без чтения окружения."""
    base = {
        "console_token_secret": SECRET,
        "console_token_issuer": "dbos-server-service",
        "target_mode": "direct",
        "static_dir": STATIC_DIR,
        "path_prefix": "/vm-console",
        "idle_timeout_seconds": 30,
    }
    base.update(overrides)
    # populate_by_name недоступен, поэтому строим через _env-less конструктор:
    return Settings.model_construct(**{**Settings().model_dump(), **base})


@pytest.fixture
async def echo_server():
    """Мок TCP-таргета: эхо-сервер, имитирующий VNC/SPICE-сокет на хабе."""

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    break
                writer.write(b"echo:" + data)
                await writer.drain()
        except (ConnectionResetError, BrokenPipeError):
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        await server.start_serving()
        yield port
