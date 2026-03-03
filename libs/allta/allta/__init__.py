from ._jira_confluence_reporter.page_builder import PageBuilder
from ._decorators.Decorators import BaseDecorators
from ._get_env.GetEnv import GetEnv
from ._math_models.math_models import Criterion, MathModels
from ._system_command.SystemCommands import SystemCommands
from ._vm_controller.Libvit import Libvirt
from ._vm_controller.VBox import VBox
from ._vm_controller._vm.LibvirtManager import LibvirtManager
from ._vm_controller._vm.VBoxManager import VBoxManager
from ._zefir.zefir import UploaderZC

__all__ = [
    "BaseDecorators",
    "ConfluencePublisher",
    "Criterion",
    "GetEnv",
    "Libvirt",
    "LibvirtManager",
    "MathModels",
    "PageBuilder",
    "SystemCommands",
    "UploaderZC",
    "VBox",
    "VBoxManager",
] # Здесь явно прописываем те функции которые будут доступны пользователю, остальной код будет скрыт но будет доступен внутри пакета

try:
    from ._jira_confluence_reporter.confluence_publisher import ConfluencePublisher
except ModuleNotFoundError as exc:
    if exc.name != "atlassian":
        raise

    class ConfluencePublisher:  # type: ignore[no-redef]
        def __init__(self, *args, **kwargs):
            raise ModuleNotFoundError(
                "ConfluencePublisher requires the 'atlassian-python-api' package"
            ) from exc
