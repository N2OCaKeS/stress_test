from ..._decorator._logger import logger
import paramiko
import time
from .._reboot._reboot import _Reboot
from ..._libs._signals import _Signals

class _Freeipa():
    """
    Базовый класс для настройки контроллера домена и клиентов домена.
    """

    @staticmethod
    def freeipa(domain, vm_dates, vms_groups, ssh_user="u", ssh_password='1'):
        """
        Основной метод для настройки домена.

        Параметры:
          domain: словарь с настройками домена, пример:
            {
                'settings': {
                    'domain': 'example.com',
                    'admin_password': 'secret'
                },
                'domain': {
                    'host': 'domain'
                },
                'client': {
                    'host': 'database'  # если значение начинается с "g_", то это группа хостов
                }
            }
          vm_dates: словарь с данными виртуальных машин, например:
            {
                'domain': {
                    'host-port': '22',
                    'ip': '10.0.0.11',
                    'sshnum': '',
                    'ip_bridge': '192.168.1.10',
                    'cpus': '4',
                    'memory': '8192',
                    'disk': '100'
                },
                'db1': { ... },
                'db2': { ... },
            }
          vms_groups: словарь групп, например:
            {
                'databases': ['db1', 'db2'],
            }
          ssh_user: имя пользователя для SSH-подключения (по умолчанию "u")
          ssh_password: пароль для SSH-подключения (по умолчанию "1")
        """
        # 1. Настройка контроллера домена (однопоточно)
        controller_host = domain['domain']['host']
        if controller_host not in vm_dates:
            raise Exception(f"Информация о VM для контроллера {controller_host} не найдена")
        controller_vm = vm_dates[controller_host]
        domain_name = domain['settings']['domain']
        admin_password = domain['settings']['admin_password']

        print(f"Настройка контроллера домена на {controller_host}")
        server_cmd = f"sudo DEBIAN_FRONTEND=noninteractive astra-freeipa-server -d {domain_name} -p {admin_password} -o -y"
        _Freeipa._execute_command(controller_vm, server_cmd, task_name="FreeIPA Server Setup",
                                    username=ssh_user, password=ssh_password, vm_name=controller_host)

        # 1.2) Перезагрузка контроллера домена
        print(f"Перезагрузка контроллера домена {controller_host}")
        reboot_success = _Reboot.reboot_vm(controller_host, vm_dates,
                                           username=ssh_user, password=ssh_password)
        if not reboot_success:
            print(f"Перезагрузка контроллера {controller_host} не удалась.")
            return

        # 1.3) Ждём 20 секунд для полной готовности, затем устанавливаем сигнал
        time.sleep(20)
        _Signals.set(controller_host, set_signal="domain_ready")
        print("Контроллер домена готов к работе (сигнал domain_ready установлен)")

        # 2. Настройка клиентов домена по очереди
        client_host_key = domain['client']['host']
        if client_host_key.startswith("g_"):
            group_name = client_host_key[2:]
            if group_name in vms_groups:
                client_hosts = vms_groups[group_name]
            else:
                raise Exception(f"Группа {group_name} не найдена в vms_groups")
        else:
            client_hosts = [client_host_key]

        # Отфильтруем тех, для кого нет данных в vm_dates
        hosts_to_configure = []
        for host in client_hosts:
            if host in vm_dates:
                hosts_to_configure.append(host)
            else:
                print(f"Информация о VM для клиента {host} не найдена, пропуск...")

        # Последовательная настройка каждого клиента
        for host in hosts_to_configure:
            try:
                _Freeipa._configure_client(
                    host,
                    vm_dates[host],
                    domain_name,
                    admin_password,
                    controller_host,
                    ssh_user,
                    ssh_password
                )
                print(f"Клиент {host} успешно настроен")
            except Exception as e:
                print(f"Ошибка при настройке клиента {host}: {e}")

        print("Настройка клиентов домена завершена")
        _Signals.remove_all()

    @staticmethod
    @logger
    def _execute_command(vm_info, command, task_name="Command Execution",
                         username="root", password=None, vm_name=None):
        """
        Выполняет указанную команду на удалённой машине через SSH.
        """
        host_ip = vm_info.get('ip_bridge')
        port = int(vm_info.get('host-port')) if vm_info.get('host-port', '22') != '*' else 22

        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            ssh.connect(host_ip, port=port, username=username, password=password, timeout=10)
            stdin, stdout, stderr = ssh.exec_command(command)
            output = stdout.read().decode('utf-8') + stderr.read().decode('utf-8')
            ssh.close()
            result = {
                'host': vm_name or host_ip,
                'task_name': task_name,
                'output': output,
                'status': 'success' if "error" not in output.lower() else 'error',
                'command': command
            }
            return result
        except Exception as e:
            return {
                'host': vm_name or host_ip,
                'task_name': task_name,
                'output': str(e),
                'status': 'error',
                'command': command
            }

    @staticmethod
    def _configure_client(host, vm_info, domain_name, admin_password,
                          controller_host, username="root", password=None):
        """
        Настраивает клиента домена.

        Шаги:
          1. Ожидание сигнала готовности домена (domain_ready).
          2. Установка клиента через sudo astra-freeipa-client.
          3. Перезагрузка клиента.
        """
        print(f"Клиент {host}: ожидание сигнала domain_ready...")
        sig = [controller_host, 'domain_ready']
        if not _Signals.get(sig):
            print(f"Клиент {host}: сигнал domain_ready не получен, прерывание настройки.")
            return
        
        print(f"Настройка клиента домена на {host}")
        client_cmd = f"sudo astra-freeipa-client -d {domain_name} -p {admin_password} -y"
        client_cmd = f'sudo DEBIAN_FRONTEND=noninteractive astra-freeipa-client -d {domain_name} -p {admin_password} -y --par "--domain={domain_name} --server={controller_host}.{domain_name} --realm={domain_name.upper()}"'
        
        _Freeipa._execute_command(
            vm_info,
            client_cmd,
            task_name="FreeIPA Client Setup",
            username=username,
            password=password,
            vm_name=host
        )

        # Перезагрузка клиента
        reboot_success = _Reboot.reboot_vm(
            host,
            {host: vm_info},
            username=username,
            password=password
        )
        if not reboot_success:
            print(f"Клиент {host}: перезагрузка не удалась.")
        else:
            print(f"Клиент {host}: успешно перезагружен.")

