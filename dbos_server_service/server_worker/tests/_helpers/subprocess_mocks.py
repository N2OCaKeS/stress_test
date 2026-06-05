"""Subprocess-mock builders для ipmitool тестов.

`make_fake_process` — конструктор MagicMock'а, имитирующего
`asyncio.subprocess.Process` (returncode, communicate, wait, terminate, kill).
`patch_subprocess` — подмена `asyncio.create_subprocess_exec` с сохранением
истории вызовов для последующих ассертов про argv.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock


def make_fake_process(
    returncode: int = 0,
    stdout: bytes = b"",
    stderr: bytes = b"",
    *,
    communicate_raises: BaseException | None = None,
) -> MagicMock:
    """Сконструировать MagicMock, имитирующий asyncio.subprocess.Process."""
    proc = MagicMock()
    proc.returncode = returncode
    if communicate_raises is not None:
        proc.communicate = AsyncMock(side_effect=communicate_raises)
    else:
        proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.wait = AsyncMock(return_value=returncode)
    proc.terminate = MagicMock()
    proc.kill = MagicMock()
    return proc


def patch_subprocess(monkeypatch, proc_or_factory: Any) -> list[tuple[tuple, dict]]:
    """Подменить `asyncio.create_subprocess_exec` и собрать вызовы.

    `proc_or_factory` — либо готовый MagicMock-Process, либо обычная
    функция-фабрика (для тестов, где результат зависит от команды).
    """
    calls: list[tuple[tuple, dict]] = []

    async def fake_exec(*args, **kwargs):
        calls.append((args, kwargs))
        if not isinstance(proc_or_factory, MagicMock) and callable(proc_or_factory):
            return proc_or_factory(args, kwargs)
        return proc_or_factory

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    return calls


__all__ = ["make_fake_process", "patch_subprocess"]
