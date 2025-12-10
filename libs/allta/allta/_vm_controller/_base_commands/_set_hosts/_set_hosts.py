from ...._decorators.Decorators import BaseDecorators
from ..._libs._ssh_command import _SSH_Command
from concurrent.futures import ThreadPoolExecutor, as_completed
from ...._system_command.SystemCommands import SystemCommands

class _SetHosts:
    """
    Класс для настройки файла /etc/hosts на хосте и виртуальных машинах.

    Основные функции:
    - Генерация содержимого файла /etc/hosts для каждой ВМ.
    - Установка файла /etc/hosts на удалённой машине через SSH.
    - Поддержка многопоточного выполнения для одновременной настройки нескольких ВМ.

    Этот класс используется для автоматизации настройки сетевых параметров на ВМ.
    """
    @staticmethod
    @BaseDecorators.trycorator
    def set_hosts(vms_dates: dict, username: str = "u", password: str = "1", domain: str = None,
                  task_name: str = "Set /etc/hosts") -> dict:
        """
        Настраивает файл /etc/hosts на всех указанных виртуальных машинах.

        Args:
            vm_dates (dict): Полная информация о виртуальных машинах.
                vms_date (list): Полная информация о виртуальных машинах.
                    vm_dates = {'hostname':{
                        'host-port':'*',
                        'ip':'10.0.0.11', #  ip внутренней сети
                        'sshnum':'',
                        'ip_bridge':'*.*.*.*', # ip моста
                        'cpus':'*',
                        'memory':'*', # RAM
                        'disk':'*'}
                        }  
            username (str, optional): Имя пользователя для подключения по SSH. По умолчанию "u".
            password (str, optional): Пароль для подключения по SSH. По умолчанию "1".
            domain (str): Домен для формирования FQDN.
            task_name (str, optional): Имя задачи для логирования.

            
        Returns:
            dict: Результаты выполнения команды для каждой виртуальной машины.
        """
        def process_vm(vm_name: str, vm_info: dict) -> tuple:
            # Генерация содержимого файла /etc/hosts для текущей ВМ
            lines = [
                "127.0.0.1\tlocalhost",
                "127.0.1.1\t$(hostname)",
                "10.177.103.10\tallta.devos.astralinux.ru\tallta",
                "10.177.5.111\tqa111.devos.astralinux.ru\tqa111",
                "10.177.43.1\treleases.devos.astralinux.ru\treleases",
            ]
            

            for key, info in vms_dates.items():
                ip_bridge = info.get("ip_bridge", "")
                if domain is None:
                    lines.append(f"{ip_bridge}\t{key}")   
                else:
                    lines.append(f"{ip_bridge}\t{key}.{domain}\t{key}")   
        
            hosts_content = "\n".join(lines)
            
            # Формирование команды для перезаписи /etc/hosts на удалённой машине
            remote_command = (
                f"sudo sh -c 'cat <<EOF > /etc/hosts\n{hosts_content}\nEOF'"
            )
            
            # Выполнение команды через готовый класс _ssh_command
            result = _SSH_Command.cmd(
                host=vm_name,
                command=remote_command,
                vm_dates=vms_dates,
                username=username,
                password=password,
                task_name=f"{task_name} on {vm_name}"
            )

            # Установка /etc/hosts на хосте
            SystemCommands.cmd_with_returncode(remote_command)
            return vm_name, result

        results = {}
        # Создаем пул потоков, количество которых соответствует количеству ВМ
        with ThreadPoolExecutor(max_workers=len(vms_dates)) as executor:
            future_to_vm = {
                executor.submit(process_vm, vm_name, vm_info): vm_name
                for vm_name, vm_info in vms_dates.items()
            }
            for future in as_completed(future_to_vm):
                vm_name, result = future.result()
                results[vm_name] = result

        return results
