from ...decorators.decorators import BaseDecorators
from ._signals import _Signals as signals
import paramiko

# Новый декоратор для логирования
from .._decotator._ansible_log import ansible_logger

class _SSH_Command:

    @BaseDecorators.trycorator
    @ansible_logger
    @staticmethod
    def cmd(host: str, command: str, vm_dates: dict, username: str = 'u', password: str = '1',
            signal_set: str = None, signal_get: str = None, task_name: str = None) -> dict:
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
            task_name (str, optional): имя задачи для логирования

        Returns:
            dict: результат выполнения команды с ключами:
                - host: имя хоста,
                - task_name: имя задачи,
                - command: выполненная команда,
                - output: вывод команды,
                - status: статус выполнения.
        """
        ssh = None
        try:
            if signal_get:
                signals.get(signal_get)

            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(
                hostname=vm_dates[host].get('ip_bridge', ''),
                port=int(vm_dates[host].get('host-port', 22)),
                username=username,
                password=password,
                timeout=10
            )

            stdin, stdout, stderr = ssh.exec_command(command)
            output_stdout = stdout.read().decode()
            output_stderr = stderr.read().decode()
            output = output_stdout + output_stderr

            # Если в stderr есть вывод — считаем, что произошла ошибка
            if output_stderr:
                print(f"[{host}] Ошибка при выполнении {command}: {output_stderr}")
                return {
                    'host': host,
                    'task_name': task_name if task_name else 'unknown',
                    'command': command,
                    'output': output_stderr,
                    'status': 'error'
                }

            print(f"[{host}] Команда закончила выполнение: {command}")

            if signal_set:
                signals.set(signal_set)

            return {
                'host': host,
                'task_name': task_name if task_name else 'unknown',
                'command': command,
                'output': output,
                'status': 'ok'
            }

        except paramiko.AuthenticationException:
            print(f"[{host}] Ошибка аутентификации.")
            return {
                'host': host,
                'task_name': task_name if task_name else 'unknown',
                'command': command,
                'output': 'Ошибка аутентификации',
                'status': 'error'
            }

        except paramiko.SSHException as e:
            print(f"[{host}] Ошибка SSH: {str(e)}")
            return {
                'host': host,
                'task_name': task_name if task_name else 'unknown',
                'command': command,
                'output': str(e),
                'status': 'error'
            }

        except Exception as e:
            print(f"[{host}] Ошибка: {str(e)}")
            return {
                'host': host,
                'task_name': task_name if task_name else 'unknown',
                'command': command,
                'output': str(e),
                'status': 'error'
            }

        finally:
            if ssh:
                ssh.close()
