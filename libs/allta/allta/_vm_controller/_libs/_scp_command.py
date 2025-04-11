from ..._system_command.SystemCommands import SystemCommands
import threading
from .._decorator._logger import logger

class _SCP_Command:
    """
    Класс для копирования файлов между локальной системой и виртуальными машинами с помощью SCP.

    Основные функции:
    - Копирование файлов на ВМ (режим push).
    - Копирование файлов с ВМ (режим pull).
    - Поддержка многопоточного выполнения для одновременного копирования на несколько хостов.

    Этот класс использует утилиту `sshpass` для выполнения SCP-команд.

    Ожидается, что переменная scp имеет следующую структуру:
        scp = {
            'suac': {
                'mode': 'push' или 'pull',  # push – копирование с локальной системы на VM, pull – наоборот.
                'path_host': '/home/n2ocake/test',  # путь на локальной системе
                'path_vm': '/home/u/'                # путь на виртуальной машине
            },
            ...
        }

    Словарь vms_date содержит информацию о виртуальных машинах, например:
        vms_date = {
            'suac': {
                'host-port': '2025',
                'ip_bridge': '*.*.*.*',
                ...
            },
            ...
        }
    """

    @staticmethod
    @logger
    def _execute_scp(mode: str, host: str, path_host: str, path_vm: str,
                     vms_date: dict, task_name: str = 'SCP', username: str = 'u', password: str = '1', **kwargs) -> dict:
        """
        Выполняет SCP-команду для копирования файлов.

        Args:
            mode (str): Режим копирования ('push' или 'pull').
            host (str): Имя хоста, на котором выполняется команда.
            path_host (str): Путь на локальной системе.
            path_vm (str): Путь на виртуальной машине.
            vms_date (dict): Словарь с информацией о виртуальных машинах.
            task_name (str): Имя задачи для логирования.
            username (str, optional): Имя пользователя для подключения по SSH. По умолчанию "u".
            password (str, optional): Пароль для подключения по SSH. По умолчанию "1".

        Returns:
            dict: Результат выполнения команды с ключами:
                - output: Полный вывод команды.
                - status: Статус выполнения ("OK" или "error").
                - host: Имя хоста.
                - task_name: Имя задачи.
                - command: Выполненная команда.
        """
        # Устанавливаем значения для логирования
        kwargs.setdefault('task_name', f"SCP {mode}")
        kwargs.setdefault('host', host)

        command = ""
        try:
            if mode not in ('push', 'pull'):
                raise ValueError(f"Неизвестный режим копирования: {mode}")
            vm_info = vms_date.get(host, {})
            ip = vm_info.get('ip_bridge', '')
            port = vm_info.get('host-port', 22)

            if not ip:
                raise ValueError(f"Не указан ip для хоста: {host}")

            if mode == 'push':
                command = f"sudo sshpass -p {password} scp -P {port} -o StrictHostKeyChecking=no {path_host} {username}@{ip}:{path_vm}"
            else:  # mode == 'pull'
                command = f"sudo sshpass -p {password} scp -P {port} -o StrictHostKeyChecking=no {username}@{ip}:{path_vm} {path_host}"

            output = SystemCommands.check_output_command(command)
            return {
                "output": output,
                "status": "OK",
                "host": host,
                "task_name": f"SCP {mode}",
                "command": command
            }
        except Exception as e:
            # В случае ошибки формируем корректный результат для логирования
            return {
                "output": f"Ошибка выполнения SCP: {str(e)}",
                "status": "error",
                "host": host,
                "task_name": f"SCP {mode}",
                "command": command
            }

    @staticmethod
    def execute(scp: dict, vms_date: dict, groups: dict = None,
                username: str = "u", password: str = "1") -> dict:
        """
        Выполняет копирование файлов на основе настроек SCP.

        Args:
            scp (dict): Настройки SCP, включая режим, пути и хосты.
            vms_date (dict): Словарь с информацией о виртуальных машинах.
            groups (dict, optional): Группы хостов для массового копирования.
            username (str, optional): Имя пользователя для подключения по SSH. По умолчанию "u".
            password (str, optional): Пароль для подключения по SSH. По умолчанию "1".

        Returns:
            dict: Результат выполнения с ключами:
                - output: Совокупный вывод всех операций.
                - status: Статус выполнения ("OK").
        """
        log_messages = []
        threads = []

        def worker(mode, host, path_host, path_vm, task_name):
            result = _SCP_Command._execute_scp(mode, host, path_host, path_vm,
                                                vms_date, task_name, username, password)
            log_messages.append(result.get("output", ""))

        for target, paths in scp.items():
            mode = paths.get('mode')
            path_host = paths.get('path_host')
            path_vm = paths.get('path_vm')

            if not mode:
                message = f"Режим копирования не указан для {target}."
                print(message)
                log_messages.append(message)
                continue

            task_name = f"SCP {mode}"

            if target.startswith("g_"):
                group_name = target[2:]
                if groups and group_name in groups:
                    for host_item in groups[group_name]:
                        thread = threading.Thread(target=worker,
                                                  args=(mode, host_item, path_host, path_vm, task_name))
                        threads.append(thread)
                        thread.start()
                else:
                    message = f"Группа '{group_name}' не найдена в groups."
                    print(message)
                    log_messages.append(message)
            else:
                thread = threading.Thread(target=worker,
                                          args=(mode, target, path_host, path_vm, task_name))
                threads.append(thread)
                thread.start()

        for thread in threads:
            thread.join()

        return {"output": "\n".join(log_messages), "status": "OK"}
