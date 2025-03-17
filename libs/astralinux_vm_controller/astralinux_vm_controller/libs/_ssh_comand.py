import astralinux_decorators
from libs._signals import _signals as signals
import paramiko

class _ssh_command:

    @astralinux_decorators.trycorator.trycorator
    @astralinux_decorators.ansible_log.log_task
    @staticmethod
    def _cmd(host: str, command: str, username: str, password: str, vm_dates: dict, signal_set: str = None, signal_get: str = None, task_name: str = None) -> dict:
        """
        Выполнение команды на одном хосте с обработкой ошибок.

        Args:
            host (str): имя хоста
            command (str): команда для выполнения
            username (str): имя пользователя для подключения к ВМ
            password (str): пароль для подключения к ВМ
            vm_dates (dict): полная информация о ВМ
            signal_set (str, optional): сигнал для установки
            signal_get (str, optional): сигнал для получения

        Returns:
            dict: результат выполнения команды
        """
        ssh = None
        try:
            if signal_get:
                signals.get(signal_get)

            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(
                hostname=vm_dates[host].get('ip', ''),
                port=int(vm_dates[host].get('host-port', 22)),
                username=username,
                password=password,
                timeout=10
            )

            stdin, stdout, stderr = ssh.exec_command(command)
            output = stdout.read().decode() + stderr.read().decode()
            error = stderr.read().decode()

            if error:
                print(f"[{host}] Ошибка при выполнении {command}: {error}")
                return {'output': error, 'status': 'error'}
            
            print(f"[{host}] Команда успешно выполнена: {command}")
            
            if signal_set:
                signals.set(signal_set)

            return {'output': output, 'status': 'success'}

        except paramiko.AuthenticationException:
            print(f"[{host}] Ошибка аутентификации.")
            return {'output': 'Ошибка аутентификации', 'status': 'error'}

        except paramiko.SSHException as e:
            print(f"[{host}] Ошибка SSH: {str(e)}")
            return {'output': str(e), 'status': 'error'}

        except Exception as e:
            print(f"[{host}] Ошибка: {str(e)}")
            return {'output': str(e), 'status': 'error'}

        finally:
            if ssh:
                ssh.close()
