from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator


@contextmanager
def system_ld_library_path_scope() -> Iterator[None]:
    """
    Тестовый no-op контекст: CLI больше не управляет LD_LIBRARY_PATH.
    """
    yield
