from importlib import import_module
from typing import TYPE_CHECKING, Any

from ._math_models.math_model import MathModel
from ._math_models.old_math_model import OldMathModel

if TYPE_CHECKING:
    from ._decorators.Decorators import BaseDecorators
    from ._get_env.GetEnv import GetEnv
    from ._jira_confluence_reporter.confluence_publisher import ConfluencePublisher
    from ._jira_confluence_reporter.page_builder import PageBuilder
    from ._system_command.SystemCommands import SystemCommands
    from ._timer.Timer import Timer
    from ._vm_controller.Libvit import Libvirt
    from ._vm_controller.VBox import VBox
    from ._vm_controller._vm.LibvirtManager import LibvirtManager
    from ._vm_controller._vm.VBoxManager import VBoxManager
    from ._zefir.zefir import UploaderZC

__all__ = [
    "BaseDecorators",
    "ConfluencePublisher",
    "GetEnv",
    "Libvirt",
    "LibvirtManager",
    "MathModel",
    "OldMathModel",
    "PageBuilder",
    "SystemCommands",
    "Timer",
    "UploaderZC",
    "VBox",
    "VBoxManager",
]

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "BaseDecorators": ("allta._decorators.Decorators", "BaseDecorators"),
    "ConfluencePublisher": (
        "allta._jira_confluence_reporter.confluence_publisher",
        "ConfluencePublisher",
    ),
    "GetEnv": ("allta._get_env.GetEnv", "GetEnv"),
    "Libvirt": ("allta._vm_controller.Libvit", "Libvirt"),
    "LibvirtManager": ("allta._vm_controller._vm.LibvirtManager", "LibvirtManager"),
    "PageBuilder": ("allta._jira_confluence_reporter.page_builder", "PageBuilder"),
    "SystemCommands": ("allta._system_command.SystemCommands", "SystemCommands"),
    "Timer": ("allta._timer.Timer", "Timer"),
    "UploaderZC": ("allta._zefir.zefir", "UploaderZC"),
    "VBox": ("allta._vm_controller.VBox", "VBox"),
    "VBoxManager": ("allta._vm_controller._vm.VBoxManager", "VBoxManager"),
}


def __getattr__(name: str) -> Any:
    if name in _LAZY_EXPORTS:
        module_name, attribute_name = _LAZY_EXPORTS[name]
        module = import_module(module_name)
        value = getattr(module, attribute_name)
        globals()[name] = value
        return value
    raise AttributeError(f"module 'allta' has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
