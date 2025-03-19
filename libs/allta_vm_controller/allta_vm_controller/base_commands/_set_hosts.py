from astralinux_decorators import ansible_log
from ..libs._system_commands import _system_commands

class _set_hosts:
    @staticmethod
    @ansible_log.ansible
    def _execute_remote_command(remote_command: str, host: str, port: int,
                                username: str, password: str, task_name: str) -> dict:
        """
        Выполняет удалённую команду и возвращает результат для логирования.

        Args:
            remote_command (str): команда, которая будет выполнена на удалённой машине.
            host (str): IP-адрес удалённой машины.
            port (int): SSH-порт удалённой машины.
            username (str): имя пользователя для подключения.
            password (str): пароль для подключения.
            task_name (str): имя задачи для логирования.

        Returns:
            dict: {'command': ..., 'output': ..., 'status': 'OK'/'error', 'host': ..., 'task_name': ...}
        """
        output = _system_commands.check_output_command(remote_command)
        return {
            "command": remote_command,
            "output": output,
            "status": "OK",
            "host": host,
            "task_name": task_name
        }

    @staticmethod
    def set_hosts(vms_date: dict, domen: str, task_name: str = 'Set /etc/hosts',
                  username: str = "u", password: str = "1"):
        """
        Для каждой ВМ из vms_date генерирует содержимое файла /etc/hosts и устанавливает его на удалённой машине.
        
        Формат файла:
            127.0.0.1       localhost
            127.0.0.1       {текущий_хост}.{domen}
            {ip_bridge}     {vm}.{domen}      {vm}
            ... для всех ВМ из vms_date

        Args:
            vms_date (dict): словарь с данными о виртуальных машинах.
                Пример:
                {
                    'suac': {
                        'host-port': '2025',
                        'ip': '192.168.1.101',
                        'sshnum': '',
                        'ip_bridge': '127.0.0.1',
                        'cpus': '4',
                        'memory': '4096'
                    },
                    'susrv': {
                        'host-port': '2023',
                        'ip': '192.168.1.102',
                        'sshnum': '',
                        'ip_bridge': '127.0.0.1',
                        'cpus': '4',
                        'memory': '4096'
                    },
                }
            domen (str): домен для формирования FQDN, например "example.com".
            task_name (str): имя задачи (для логирования).
            username (str): имя пользователя для SSH.
            password (str): пароль для SSH.
        """
        for vm_name, vm_info in vms_date.items():
            port = int(vm_info.get("host-port", 22))
            ip = vm_info.get("ip", "")
            
            # Генерация содержимого /etc/hosts для текущей ВМ:
            # Первая строка: localhost
            # Вторая строка: текущая ВМ (для которой происходит настройка)
            lines = [
                "127.0.0.1       localhost",
                f"127.0.0.1       {vm_name}.{domen}"
            ]
            
            # Далее добавляем список всех ВМ (включая текущую)
            for key, info in vms_date.items():
                ip_bridge = info.get("ip_bridge", "")
                if not ip_bridge:
                    continue
                lines.append(f"{ip_bridge}     {key}.{domen}      {key}")
            
            hosts_content = "\n".join(lines)
            
            remote_command = (
                f"sshpass -p {password} ssh -o StrictHostKeyChecking=no -p {port} {username}@{ip} "
                f"\"sudo sh -c 'cat <<EOF > /etc/hosts\n{hosts_content}\nEOF'\""
            )
            
            result = _set_hosts._execute_remote_command(
                remote_command=remote_command,
                host=ip,
                port=port,
                username=username,
                password=password,
                task_name=f"Set /etc/hosts on {vm_name}"
            )
            print(f"Result for VM {vm_name} ({ip}):\n{result.get('output')}")