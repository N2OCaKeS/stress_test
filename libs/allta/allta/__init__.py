__version__ = "0.0.1"

from ._decorators.Decorators import BaseDecorators 
from ._vm_controller.VBox import VBox as VBoxManager

__all__ = ["BaseDecorators", "VBoxManager"] # Здесь явно прописываем те функции которые будут доступны пользователю, остальной код будет скрыт но будет доступен


