from .._base_commands._apt._apt_prorocol import _AptManagerProtocol

from abc import ABC, abstractmethod

class _VirtualMashines(ABC):
    """
    Абстрактный класс для управления виртуальными машинами.

    Основные этапы работы:
    - prepare: Подготовка окружения.
    - build: Развертывание и общая настройка ВМ.
    - check: Проверка доступности ВМ.
    - execute: Выполнение точной настройки.
    - scp: Копирование файлов между локальной системой и ВМ.

    Этот класс определяет интерфейс, который должен быть реализован в наследниках.
    """

    @property
    @abstractmethod
    def apt(self) -> _AptManagerProtocol:
        """Должен возвращать экземпляр менеджера apt."""
        pass
    


    @abstractmethod
    def prepare(cls, path_prepare) -> int:
        """
        Подготавливает окружение для работы с виртуальными машинами.

        Args:
            path_prepare (str): Путь до скрипта подготовки.

        Returns:
            int: Код завершения выполнения.
        """
        pass

    @abstractmethod
    def build(cls, box: str, rc: str, vms: list) -> int:
        """
        Создаёт и настраивает виртуальные машины.

        Args:
            box (str): Имя образа (бокса).
            rc (str): Версия операционной системы.
            vms (list): Список имён виртуальных машин.

        Returns:
            int: Код завершения выполнения.
        """
        pass

    @abstractmethod
    def check(cls, vms: list, vm_dates: dict) -> int:
        """
        Проверяет доступность виртуальных машин.

        Args:
            vms (list): Список имён виртуальных машин.
            vm_dates (dict): Полная информация о виртуальных машинах.

        Returns:
            int: Код завершения выполнения.
        """
        pass

    @abstractmethod
    def execute(cls, vms: list, commands: list, vm_dates: dict) -> int:
        """
        Выполняет команды на виртуальных машинах.

        Args:
            vms (list): Список имён виртуальных машин.
            commands (list): Список команд для выполнения.
            vm_dates (dict): Полная информация о виртуальных машинах.

        Returns:
            int: Код завершения выполнения.
        """
        pass
    
    @abstractmethod
    def scp(cls, vms: list, commands: list, vm_dates: dict) -> int:
        """
        Выполняет копирование файлов между локальной системой и виртуальными машинами.

        Args:
            vms (list): Список имён виртуальных машин.
            commands (list): Список команд для выполнения.
            vm_dates (dict): Полная информация о виртуальных машинах.

        Returns:
            int: Код завершения выполнения.
        """
        pass

    @abstractmethod
    def set_hosts(cls, domain: str, vms_dates: dict,
            username: str = "u", password: str = "1") -> int:

        pass
    @abstractmethod
    def sed(cls, sed_conf: dict, vm_dates: dict, groups: dict = None,
            username: str = "u", password: str = "1"):
        """Редактирует файлы с помощью утилиты sed

        Args:
            sed_conf (dict): список изменений в файлах
            vm_dates (dict): Полная информация о виртуальных машинах.
            groups (dict, optional): Информация о группах ВМ. Defaults to None.
            username (str, optional): Пользователеь. Defaults to "u".
            password (str, optional): Пароль. Defaults to "1".
        """
        pass

    @abstractmethod
    def freeipa(cls, ):
        pass