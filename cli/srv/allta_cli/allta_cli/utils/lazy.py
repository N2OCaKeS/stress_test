from __future__ import annotations

import importlib


class LazyModule:
    """Lazy module proxy: actual import happens on first attribute access."""

    __slots__ = ("_name", "_mod")

    def __init__(self, name: str) -> None:
        object.__setattr__(self, "_name", name)
        object.__setattr__(self, "_mod", None)

    def __getattr__(self, attr: str):
        mod = object.__getattribute__(self, "_mod")
        if mod is None:
            mod = importlib.import_module(object.__getattribute__(self, "_name"))
            object.__setattr__(self, "_mod", mod)
        return getattr(mod, attr)


requests = LazyModule("requests")
