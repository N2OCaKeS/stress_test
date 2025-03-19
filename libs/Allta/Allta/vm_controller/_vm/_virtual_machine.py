from .._base_commands._apt._apt_prorocol import _AptManagerProtocol
from .._base_commands._set_hosts._set_hosts_protocol import _HostsManagerProtocol

from abc import ABC, abstractmethod

class _VirtualMashines(ABC):
    """
    Абстрактный конвейер\n
    prepare: Подготовка окружения
    build: Развертывание и общая настройка ВМ
    check: Проверка доступности ВМ
    execute: Запуск точной настройки
    """

    @property
    @abstractmethod
    def apt(self) -> _AptManagerProtocol:
        """Должен возвращать экземпляр менеджера apt."""
        pass

    @property
    @abstractmethod
    def hosts(self) -> _HostsManagerProtocol:
        """Должен возвращать экземпляр менеджера hosts."""
        pass

    @abstractmethod
    def prepare(cls, path_prepare) -> int:
        pass

    @abstractmethod
    def build(cls, box: str, rc: str, vms: list) -> int:
        pass

    @abstractmethod
    def check(cls, vms: list, vm_dates: dict) -> int:
        pass

    @abstractmethod
    def execute(cls, vms: list, commands: list, vm_dates: dict) -> int:
        pass
    
    @abstractmethod
    def scp(cls, vms: list, commands: list, vm_dates: dict) -> int:
        pass
