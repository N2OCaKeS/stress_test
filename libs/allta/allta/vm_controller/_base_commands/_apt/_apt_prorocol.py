# В начале файла VBox.py (или в отдельном модуле для типизации)
from typing import Protocol

class _AptManagerProtocol(Protocol):
    def install(self, apt_structure: dict, vm_dates: dict, vms_groups: dict = None, 
                username: str = "u", password: str = "1") -> int: 
        """Устанавливает пакеты на хостах или группах хостов."""
        ...

    def remove(self, apt_structure: dict, vm_dates: dict, vms_groups: dict = None, 
               username: str = "u", password: str = "1") -> int:
        """Удаляет пакеты на хостах или группах хостов."""
        ...

    def reinstall(self, apt_structure: dict, vm_dates: dict, vms_groups: dict = None, 
                  username: str = "u", password: str = "1") -> int:
        """Переустанавливает пакеты на хостах или группах хостов."""
        ...
