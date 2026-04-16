from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator


def _embedded_lib_dir() -> str | None:
    return os.environ.get("ALLTA_EMBEDDED_LIB_DIR")


@contextmanager
def system_ld_library_path_scope() -> Iterator[None]:
    """
    Временно убирает embedded runtime libs из LD_LIBRARY_PATH для системных команд.
    """
    original = os.environ.get("LD_LIBRARY_PATH")
    embedded_lib_dir = _embedded_lib_dir()

    if not original or not embedded_lib_dir:
        yield
        return

    parts = [part for part in original.split(":") if part and part != embedded_lib_dir]

    try:
        if parts:
            os.environ["LD_LIBRARY_PATH"] = ":".join(parts)
        else:
            os.environ.pop("LD_LIBRARY_PATH", None)
        yield
    finally:
        os.environ["LD_LIBRARY_PATH"] = original
