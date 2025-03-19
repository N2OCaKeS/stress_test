from ....decorators.trycorator import trycorator
from ..._decotator._ansible_log import ansible_logger
from ..._libs._ssh_comand import _ssh_command
from concurrent.futures import ThreadPoolExecutor, as_completed

class _set_hosts:
    @staticmethod
    @trycorator
    @ansible_logger
    def set_hosts(domain: str, vms_dates: dict, username: str = "u", password: str = "1",
                  task_name: str = "Set /etc/hosts") -> dict:
        """
        Для каждой виртуальной машины из словаря vms_dates генерирует содержимое файла /etc/hosts и
        устанавливает его на удалённой машине через SSH с использованием класса _ssh_command.
        Выполнение производится в многопоточном режиме.

        Формат файла /etc/hosts:
            127.0.0.1       localhost
            127.0.0.1       {текущий_хост}.{domain}
            {ip_bridge}     {vm}.{domain}      {vm}

        Args:
            domain (str): домен для формирования FQDN, например "example.com".
            vms_dates (dict): словарь с данными о виртуальных машинах.
                Пример:
                {
                    'database1': {
                        "host-port": "22",
                        "ip_bridge": "10.177.103.111",
                        "cpus": "4",
                        "memory": "32768",
                        "disk": "40960"
                    },
                    'database2': { ... },
                    ...
                }
            username (str): имя пользователя для подключения по SSH.
            password (str): пароль для подключения по SSH.
            task_name (str): имя задачи для логирования.

        Returns:
            dict: Результаты выполнения команды для каждой ВМ.
        """
        def process_vm(vm_name: str, vm_info: dict) -> tuple:
            # Генерация содержимого файла /etc/hosts для текущей ВМ
            lines = [
                "127.0.0.1       localhost",
                f"127.0.0.1       {vm_name}.{domain}"
            ]
            
            # Добавляем строки для всех ВМ из словаря (включая текущую)
            for key, info in vms_dates.items():
                ip_bridge = info.get("ip_bridge", "")
                if ip_bridge:
                    lines.append(f"{ip_bridge}     {key}.{domain}      {key}")
            
            hosts_content = "\n".join(lines)
            
            # Формирование команды для перезаписи /etc/hosts на удалённой машине
            remote_command = (
                f"sudo sh -c 'cat <<EOF > /etc/hosts\n{hosts_content}\nEOF'"
            )
            
            # Выполнение команды через готовый класс _ssh_command
            result = _ssh_command.cmd(
                host=vm_name,
                command=remote_command,
                vm_dates=vms_dates,
                username=username,
                password=password,
                task_name=f"{task_name} on {vm_name}"
            )
            print(f"Result for VM {vm_name}: {result.get('output')}")
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
