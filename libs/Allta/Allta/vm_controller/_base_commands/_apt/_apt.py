from ..._decotator._ansible_log import ansible_logger
from ..._libs._ssh_comand import _ssh_command as ssh_command
import threading



class _apt_manager:  
    @ansible_logger
    @staticmethod
    def install(apt_structure: dict, vm_dates: dict, vms_groups: dict = None, username: str = "u", password: str = "1") -> int:
        """
        Устанавливает указанные пакеты на заданных хостах или группах хостов.
        Выполнение команд происходит блоками: для каждого ключа (хост или группа)
        сначала выполняются команды на всех хостах блока, затем ожидание завершения,
        и только после этого переход к следующему блоку.

        Args:
            apt_structure (dict): Словарь с пакетами для установки, например:
                {
                    'suac': ['freeipa-server', 'postgresql'],
                    'g_databases': ['postgresql', 'aphace']
                }
            vm_dates (dict): Информация о ВМ (хостах).
            vms_groups (dict, optional): Словарь групп хостов, например:
                {
                    'databases': ['dbhost1', 'dbhost2']
                }
            username (str): Имя пользователя для подключения по SSH.
            password (str): Пароль для подключения по SSH.
        """
        def _threaded_install(host: str, packages: list):
            # Формирование команды: обновление репозиториев и установка пакетов
            cmd = f"sudo apt-get update && sudo apt-get install -y {' '.join(packages)}"
            ssh_command.cmd(
                host=host,
                command=cmd,
                username=username,
                password=password,
                vm_dates=vm_dates,
                task_name=f"apt install {packages}"
            )

        # Обрабатываем блок за блоком
        for target, packages in apt_structure.items():
            block_threads = []
            if target.startswith("g_"):
                group_name = target[2:]
                if vms_groups and group_name in vms_groups:
                    for host in vms_groups[group_name]:
                        thread = threading.Thread(target=_threaded_install, args=(host, packages))
                        block_threads.append(thread)
                        thread.start()
                else:
                    print(f"Группа '{group_name}' не найдена в vms_groups.")
            else:
                host = target
                thread = threading.Thread(target=_threaded_install, args=(host, packages))
                block_threads.append(thread)
                thread.start()

            # Ожидание завершения всех потоков текущего блока
            for thread in block_threads:
                thread.join()

        return 0

    @ansible_logger
    @staticmethod
    def remove(apt_structure: dict, vm_dates: dict, vms_groups: dict = None, username: str = "u", password: str = "1") -> int:
        """
        Удаляет указанные пакеты на заданных хостах или группах хостов.
        Выполнение команд происходит блоками: для каждого ключа сначала
        выполняются команды на всех хостах блока, затем ожидание завершения,
        и только после этого переход к следующему блоку.
        """
        def _threaded_remove(host: str, packages: list):
            cmd = f"sudo apt-get remove -y {' '.join(packages)}"
            ssh_command.cmd(
                host=host,
                command=cmd,
                username=username,
                password=password,
                vm_dates=vm_dates,
                task_name=f"apt remove on {host}"
            )

        for target, packages in apt_structure.items():
            block_threads = []
            if target.startswith("g_"):
                group_name = target[2:]
                if vms_groups and group_name in vms_groups:
                    for host in vms_groups[group_name]:
                        thread = threading.Thread(target=_threaded_remove, args=(host, packages))
                        block_threads.append(thread)
                        thread.start()
                else:
                    print(f"Группа '{group_name}' не найдена в vms_groups.")
            else:
                host = target
                thread = threading.Thread(target=_threaded_remove, args=(host, packages))
                block_threads.append(thread)
                thread.start()

            for thread in block_threads:
                thread.join()

        return 0

    @ansible_logger
    @staticmethod
    def reinstall(apt_structure: dict, vm_dates: dict, vms_groups: dict = None, username: str = "u", password: str = "1") -> int:
        """
        Переустанавливает указанные пакеты на заданных хостах или группах хостов.
        Выполнение команд происходит блоками: для каждого ключа сначала
        выполняются команды на всех хостах блока, затем ожидание завершения,
        и только после этого переход к следующему блоку.
        """
        def _threaded_reinstall(host: str, packages: list):
            cmd = f"sudo apt-get install --reinstall -y {' '.join(packages)}"
            ssh_command.cmd(
                host=host,
                command=cmd,
                username=username,
                password=password,
                vm_dates=vm_dates,
                task_name=f"apt reinstall on {host}"
            )

        for target, packages in apt_structure.items():
            block_threads = []
            if target.startswith("g_"):
                group_name = target[2:]
                if vms_groups and group_name in vms_groups:
                    for host in vms_groups[group_name]:
                        thread = threading.Thread(target=_threaded_reinstall, args=(host, packages))
                        block_threads.append(thread)
                        thread.start()
                else:
                    print(f"Группа '{group_name}' не найдена в vms_groups.")
            else:
                host = target
                thread = threading.Thread(target=_threaded_reinstall, args=(host, packages))
                block_threads.append(thread)
                thread.start()

            for thread in block_threads:
                thread.join()

        return 0
