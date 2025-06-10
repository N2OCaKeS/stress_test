import threading
import time
import paramiko
from ...._decorators.Decorators import BaseDecorators
from ..._libs._ssh_command import _SSH_Command
from ..._libs._signals import _Signals

class _Reboot:
    @staticmethod
    @BaseDecorators.trycorator    
    def reboot_vm(host: str, vm_dates: dict, username: str = "u", password: str = "1",
                  timeout: int = 600, interval: int = 10, signal_get: str = None, ready_signal: str = None) -> bool:
        """
        Перезагружает виртуальную машину и ожидает, пока она не станет доступной по SSH.
        Перед выполнением перезагрузки, если передан signal_get, он передается в _SSH_Command.cmd,
        где реализовано ожидание сигнала.
        Если передан ready_signal, после успешной перезагрузки сигнал устанавливается.
        
        Args:
            host (str): Имя хоста согласно vm_dates.
            vm_dates (dict): Словарь с информацией о виртуальных машинах.
            username (str, optional): Имя пользователя для SSH.
            password (str, optional): Пароль для SSH.
            timeout (int, optional): Максимальное время ожидания перезагрузки (сек).
            interval (int, optional): Интервал между попытками подключения (сек).
            signal_get (str, optional): Имя сигнала, который ожидается перед выполнением команды.
            ready_signal (str, optional): Имя сигнала, который устанавливается при готовности ВМ.
            
        Returns:
            bool: True, если ВМ стала доступной, иначе False.
        """
        reboot_command = "(sleep 2 && sudo shutdown -r now) &"
        result = _SSH_Command.cmd(
            host=host,
            command=reboot_command,
            vm_dates=vm_dates,
            username=username,
            password=password,
            signal_get=signal_get,
            task_name='Reboot'
        )
        if result.get("status") != "ok":
            print(f"[{host}] Ошибка при перезагрузке: {result.get('output')}")
            return False

        print(f"[{host}] Перезагрузка инициирована, ожидаем доступности...")
        time.sleep(60)

        deadline = time.time() + timeout
        while time.time() < deadline:
            ssh = None
            try:
                ssh = paramiko.SSHClient()
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                ssh.connect(
                    hostname=vm_dates[host].get('ip_bridge', ''),
                    port=int(vm_dates[host].get('host-port', 22)),
                    username=username,
                    password=password,
                    timeout=5
                )
                stdin, stdout, stderr = ssh.exec_command("echo 'Connection test'", timeout=5)
                exit_code = stdout.channel.recv_exit_status()
                if exit_code == 0:
                    print(f"[{host}] VM перезагружена и доступна (SSH проверен).")
                    if ready_signal:
                        _Signals.set(host, ready_signal)
                    return True 
                
            except Exception as e:
                print(f"[{host}] Ошибка SSH: {str(e)}")
            finally:
                if ssh: 
                    ssh.close()
    
            time.sleep(interval) 

        print(f"[{host}] Время ожидания перезагрузки истекло.")
        return False

    @classmethod
    def reboot_group(cls, hosts: list, vm_dates: dict, username: str = "u", password: str = "1",
                     timeout: int = 600, interval: int = 10, signal_get: str = None, ready_signal: str = None) -> bool:
        """
        Перезагружает группу виртуальных машин параллельно и ожидает, пока все ВМ не станут доступными.
        Если передан signal_get, он передается для каждой ВМ в _SSH_Command.cmd.
        Если все ВМ готовы и указан ready_signal – сигнал устанавливается.
        
        Args:
            hosts (list): Список имён хостов, которые нужно перезагрузить.
            vm_dates (dict): Словарь с информацией о виртуальных машинах.
            username (str, optional): Имя пользователя для SSH.
            password (str, optional): Пароль для SSH.
            timeout (int, optional): Максимальное время ожидания (сек).
            interval (int, optional): Интервал между проверками (сек).
            signal_get (str, optional): Имя сигнала, который передается для ожидания в _SSH_Command.cmd.
            ready_signal (str, optional): Имя сигнала, который устанавливается после перезагрузки всех ВМ.
            
        Returns:
            bool: True, если все ВМ доступны, иначе False.
        """
        results = {}
        threads = []

        def worker(host):
            res = cls.reboot_vm(host, vm_dates, username, password, timeout, interval, signal_get, None)
            results[host] = res

        for host in hosts:
            t = threading.Thread(target=worker, args=(host,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        all_ready = all(results.get(host, False) for host in hosts)
        if all_ready and ready_signal:
            _Signals.set(host, ready_signal)
        return all_ready
