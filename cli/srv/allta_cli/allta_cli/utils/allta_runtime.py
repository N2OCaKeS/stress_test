from __future__ import annotations

import importlib
import sys
import types
from typing import Any

_ALLTA_MODULE: Any | None = None


def _install_module_stub(module_name: str, exports: dict[str, Any]) -> None:
    if module_name in sys.modules:
        return
    stub = types.ModuleType(module_name)
    for name, value in exports.items():
        setattr(stub, name, value)
    stub.__all__ = list(exports.keys())
    sys.modules[module_name] = stub


def _install_cli_stubs() -> None:
    # CLI не использует модели рейтинга и zefir-репортер.
    class _Unavailable:
        def __init__(self, *args, **kwargs):
            raise RuntimeError(
                "Компонент недоступен в allta_cli runtime. "
                "Используйте полную allta-библиотеку в test-окружении."
            )

    _install_module_stub(
        "allta._math_models.math_models",
        {"Criterion": _Unavailable, "MathModels": _Unavailable},
    )
    _install_module_stub(
        "allta._zefir.zefir",
        {"UploaderZC": _Unavailable},
    )


def _import_allta() -> Any:
    global _ALLTA_MODULE
    if _ALLTA_MODULE is not None:
        return _ALLTA_MODULE
    _install_cli_stubs()
    _ALLTA_MODULE = importlib.import_module("allta")
    return _ALLTA_MODULE


def get_libvirt():
    return _import_allta().Libvirt


def get_libvirt_manager():
    return _import_allta().LibvirtManager
