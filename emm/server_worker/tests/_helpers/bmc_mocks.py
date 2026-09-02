"""Fake BMC / SSH connection-stub builders.

`_FakeBmc` имитирует `RedfishClient`-stand-in без транзита через реальный
transport — нужен в нескольких сценариях ротации паролей.
`run_result` / `conn_ok` — shortcuts для тестов, мокающих asyncssh.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import asyncssh


def run_result(stdout: str = "", stderr: str = "", rc: int = 0) -> MagicMock:
    """Один элемент `side_effect` для `conn.run`."""
    res = MagicMock()
    res.stdout = stdout
    res.stderr = stderr
    res.exit_status = rc
    return res


def conn_ok() -> MagicMock:
    """Готовый asyncssh.SSHClientConnection-stub с rc=0 на `run`."""
    conn = MagicMock(spec=asyncssh.SSHClientConnection)
    conn.close = MagicMock()
    conn.wait_closed = AsyncMock()
    conn.run = AsyncMock(return_value=run_result("", "", 0))
    return conn


class FakeBmc:
    """`RedfishClient`-stand-in: __aenter__/aexit, rotate, power, aclose.

    Тесты обычно подменяют `passwords._get_bmc` factory'ёй, возвращающей
    свежий `FakeBmc()`. Счётчики `rotate_calls` / `get_power_state_calls`
    позволяют убедиться, что apply и verify шаги действительно дошли до
    клиента.
    """

    def __init__(self) -> None:
        self.rotate_calls: list[tuple[int, str]] = []
        self.get_power_state_calls = 0

    async def __aenter__(self) -> "FakeBmc":
        return self

    async def __aexit__(self, *_a) -> None:
        return None

    async def rotate_user_password(self, user_id: int, new_password: str) -> None:
        self.rotate_calls.append((user_id, new_password))

    async def get_power_state(self) -> str:
        self.get_power_state_calls += 1
        return "On"

    async def aclose(self) -> None:
        return None


__all__ = ["FakeBmc", "run_result", "conn_ok"]
