from ..._system_command.SystemCommands import SystemCommands
import threading
from .._decorator._logger import logger
from typing import Optional


class _SCP_Command:
    """
    Класс для копирования файлов между локальной системой и виртуальными машинами с помощью SCP.

    Ожидается, что переменная scp имеет следующую структуру:
        scp = {
            'database3': [
                {
                    'mode': 'pull',
                    'path_host': 'results_balance.txt',
                    'path_vm': '/home/u/results_balance.txt'
                },
                {
                    'mode': 'pull',
                    'path_host': 'available_packages.txt',
                    'path_vm': '/home/u/available_packages.txt'
                }
            ],
            'g_group1': [
                {
                    'mode': 'push',
                    'path_host': '/home/u/test.txt',
                    'path_vm': '/tmp/test.txt'
                }
            ]
        }
    """

    @staticmethod
    @logger
    def _execute_scp(mode: str,
                    host: str,
                    path_host: str,
                    path_vm: str,
                    vms_date: dict,
                    task_name: str = 'SCP',
                    username: str = 'u',
                    password: str = '1',
                    **kwargs) -> dict:
        """
        Выполняет SCP-команду для копирования файлов с детальной обработкой ошибок
        и печатью результата в терминал.
        """
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
                command = (
                    f"sudo sshpass -p {password} scp -r -P {port} "
                    f"-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
                    f"{path_host} {username}@{ip}:{path_vm}"
                )
            else:
                command = (
                    f"sudo sshpass -p {password} scp -r -P {port} "
                    f"-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
                    f"{username}@{ip}:{path_vm} {path_host}"
                )

            exit_status, out = SystemCommands.check_output_command_with_returncode(command)

            if exit_status != 0:
                output_stderr = out
                output_full = out
                print(
                    f"[{host}] Ошибка при выполнении '{command}'"
                    f"\n\n ПОЛНЫЙ ВЫВОД КОМАНДЫ С ОШИБКОЙ\n\n{output_full}"
                )
                return {
                    'host': host,
                    'task_name': task_name or 'unknown',
                    'command': command,
                    'output': (output_stderr, '\n\n\n', output_full),
                    'status': 'error',
                }

            # Успех
            print(f"[{host}] Команда закончила выполнение: {command}")
            return {
                'host': host,
                'task_name': task_name or 'unknown',
                'command': command,
                'output': out,
                'status': 'OK',
            }

        except Exception as e:
            print(
                f"[{host}] Исключение при подготовке/выполнении SCP '{command}': {e}"
            )
            return {
                'host': host,
                'task_name': task_name or 'unknown',
                'command': command,
                'output': f"Ошибка выполнения SCP: {e}",
                'status': 'error'
            }

    @staticmethod
    def execute(
        scp: dict,
        vms_date: dict,
        groups: Optional[dict] = None,
        username: str = "u",
        password: str = "1",
    ) -> dict:
        """
        Выполняет копирование файлов на основе расширенных настроек SCP
        (для каждого хоста или группы — список операций).

        Args:
            scp (dict): Настройки SCP, ключ — имя хоста или группы, значение — список операций.
            vms_date (dict): Словарь с информацией о виртуальных машинах.
            groups (dict, optional): Группы хостов для массового копирования.
            username (str, optional): Имя пользователя для подключения по SSH.
            password (str, optional): Пароль для подключения по SSH.

        Returns:
            dict: Результат выполнения:
                - output: Совокупный вывод всех операций.
                - status: Статус ("OK").
        """
        log_messages = []
        threads = []

        def worker(mode, host, path_host, path_vm, task_name):
            result = _SCP_Command._execute_scp(
                mode, host, path_host, path_vm, vms_date, task_name, username, password
            )
            log_messages.append(result.get("output", ""))

        for target, file_ops in scp.items():
            # Сохраняем обратную совместимость для старого формата (dict вместо list)
            if isinstance(file_ops, dict):
                file_ops = [file_ops]

            # Разворачиваем группу или отдельный хост
            if target.startswith("g_"):
                group_name = target[2:]
                if groups and group_name in groups:
                    hosts = groups[group_name]
                else:
                    message = f"Группа '{group_name}' не найдена в groups."
                    print(message)
                    log_messages.append(message)
                    continue
            else:
                hosts = [target]

            # Для каждого хоста в группе или для одиночного хоста
            for host in hosts:
                if host not in vms_date:
                    message = (
                        f"[{host}] ВМ не найдена в vms_date — пропускаю. "
                        f"Доступные ВМ: {', '.join(vms_date.keys()) or 'нет'}"
                    )
                    print(message)
                    log_messages.append(message)
                    continue                
                for op in file_ops:
                    mode = op.get("mode")
                    path_host = op.get("path_host")
                    path_vm = op.get("path_vm")
                    task_name = f"SCP {mode} {host} {path_vm}"

                    if not mode or not path_host or not path_vm:
                        log_messages.append(
                            f"Не хватает параметров для операции на {host}"
                        )
                        continue

                    thread = threading.Thread(
                        target=worker, args=(mode, host, path_host, path_vm, task_name)
                    )
                    threads.append(thread)
                    thread.start()

        for thread in threads:
            thread.join()

        return {"output": "\n".join(log_messages), "status": "OK"}
