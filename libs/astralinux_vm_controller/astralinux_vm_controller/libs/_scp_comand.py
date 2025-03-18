from libs._system_commands import _system_commands
import astralinux_decorators 

class _scp_command:
    """
    Класс для копирования файлов между локальной системой и виртуальными машинами с помощью SCP.

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
    @astralinux_decorators.ansible_log.ansible
    def _execute_scp(mode: str, host: str, path_host: str, path_vm: str,
                     vms_date: dict, task_name: str,  username: str = 'u', password: str = '1', **kwargs) -> dict:
        """
        Выполняет команду SCP для копирования файлов и возвращает словарь с полным выводом.

        Args:
            mode (str): 'push' для копирования с локальной системы на VM, 'pull' для копирования с VM на локальную.
            host (str): имя хоста из vms_date.
            path_host (str): путь на локальной системе.
            path_vm (str): путь на виртуальной машине.
            vms_date (dict): словарь с информацией о виртуальных машинах.
            username (str): имя пользователя.
            password (str): пароль.

        Returns:
            dict: {
                'output': полный вывод (команда и результат её выполнения),
                'status': 'OK' или 'error',
                'host': host,
                'task_name': 'SCP ' + mode
            }
        """
        # Устанавливаем значения для логирования
        kwargs.setdefault('task_name', f"SCP {mode}")
        kwargs.setdefault('host', host)

        vm_info = vms_date.get(host, {})
        ip = vm_info.get('ip_bridge', '')
        port = vm_info.get('host-port', 22)

        if mode == 'push':
            command = f"sshpass -p {password} scp -P {port} -o StrictHostKeyChecking=no {path_host} {username}@{ip}:{path_vm}"
        elif mode == 'pull':
            command = f"sshpass -p {password} scp -P {port} -o StrictHostKeyChecking=no {username}@{ip}:{path_vm} {path_host}"
        else:
            output = f"Неизвестный режим копирования: {mode}"
            print(output)
            return {"output": output, "status": "error", "host": host, "task_name": f"SCP {mode}"}

        # Выполняем команду и сохраняем весь вывод
        output = _system_commands.check_output_command(command)
        return {"output": output,
                "status": "OK",
                "host": host,
                "task_name": f"SCP {mode}",
                "command":command}

    @staticmethod
    def execute(scp: dict, vms_date: dict, groups: dict = None,
                username: str = "u", password: str = "1") -> dict:
        """
        Выполняет копирование файлов согласно настройкам из переменной scp, вызывая _execute_scp для каждого элемента.

        Args:
            scp (dict): структура с данными для копирования, включая ключ 'mode'.
            vms_date (dict): словарь с информацией о виртуальных машинах.
            groups (dict, optional): словарь групп хостов.
            username (str): имя пользователя для SSH.
            password (str): пароль для SSH.

        Returns:
            dict: {'output': совокупный вывод, 'status': 'OK'}
        """
        log_messages = []
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
                        result = _scp_command._execute_scp(mode, target, path_host, path_vm, vms_date, task_name, username, password)
                        log_messages.append(result.get("output", ""))
                else:
                    message = f"Группа '{group_name}' не найдена в groups."
                    print(message)
                    log_messages.append(message)
            else:
                result = _scp_command._execute_scp(mode, target, path_host, path_vm, vms_date, task_name, username, password)
                log_messages.append(result.get("output", ""))

        return {"output": "\n".join(log_messages), "status": "OK"}