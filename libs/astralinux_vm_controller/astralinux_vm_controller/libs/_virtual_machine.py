from abc import ABC, abstractmethod

class _VirtualMashines(ABC):
    """
    Абстрактный конвейер\n
    prepare: Подготовка окружения
    build: Развертывание и общая настройка ВМ
    check: Проверка доступности ВМ
    execute: Запуск точной настройки
    """
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
    def apt(cls, vms: list, commands: list, vm_dates: dict) -> int:
        pass