from ..._decorators.Decorators import BaseDecorators
from ._signals import _Signals as signals
import paramiko

# Новый декоратор для логирования
from .._decorator._logger import logger

class _SSH_Command:
    """
    Класс для выполнения SSH-команд на удалённых хостах.

    Основные функции:
    - Выполнение команды на одном хосте с обработкой ошибок.
    - Поддержка сигналов для синхронизации выполнения задач.

    Этот класс использует библиотеку `paramiko` для выполнения SSH-команд.
    """

    @BaseDecorators.trycorator
    @logger
    @staticmethod
    def cmd(host: str, command: str, vm_dates: dict, username: str = 'u', password: str = '1',
            signal_set: str = None, signal_get: list = None, task_name: str = None, time_out: int = 15) -> dict:
        """
        Выполняет SSH-команду на удалённом хосте с обработкой ошибок.

        Args:
            host (str): Имя хоста, на котором выполняется команда.
            command (str): Команда для выполнения.
            vm_dates (dict): Словарь с информацией о виртуальных машинах.
            username (str, optional): Имя пользователя для подключения по SSH. По умолчанию "u".
            password (str, optional): Пароль для подключения по SSH. По умолчанию "1".
            signal_set (str, optional): Сигнал для установки после выполнения команды.
            signal_get (list, optional): Сигнал для ожидания перед выполнением команды.
            task_name (str, optional): Имя задачи для логирования.
            time_out (int, optional): timeout для ожидания сигнала 15 мин по умолчанию

        Returns:
            dict: Результат выполнения команды с ключами:
                - host: Имя хоста.
                - task_name: Имя задачи.
                - command: Выполненная команда.
                - output: Вывод команды.
                - status: Статус выполнения ("ok" или "error").
        """
        ssh = None
        try:
            if signal_get:
                # Если сигнал задан в виде ['signal'], подставляем host как первый элемент
                if len(signal_get) == 1:
                    signal_get = [host, signal_get[0]]
                elif not signal_get[0]:
                    signal_get[0] = host

                if not signals.get(signal_get, timeout_min=time_out):
                    error_msg = f"ОШИБКА СИГНАЛ {signal_get} НЕ НАЙДЕН"
                    print(f"[{host}] {error_msg}")
                    return {
                        'host': host,
                        'task_name': task_name or 'unknown',
                        'command': command,
                        'output': error_msg,
                        'status': 'error'
                    }

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

            # # Если в stderr есть вывод — считаем, что произошла ошибка
            # if output_stderr:
            #     print(f"[{host}] Ошибка при выполнении {command}: {output_stderr}")
            #     return {
            #         'host': host,
            #         'task_name': task_name if task_name else 'unknown',
            #         'command': command,
            #         'output': output_stderr,
            #         'status': 'error'
            #     }
            exit_status = stdout.channel.recv_exit_status()
            output = output_stdout + ("\n" + output_stderr if output_stderr else "")

            if exit_status != 0:
                print(f"[{host}] Ошибка при выполнении '{command}': ОШИБКА:\n{output_stderr}\n\n\n ПОЛНЫЙ ВЫВОД КОМАНДЫ С ОШИБКОЙ\n\n\n{output}\n\n\n (exit status: {exit_status})")
                return {
                    'host': host,
                    'task_name': task_name or 'unknown',
                    'command': command,
                    'output': (output_stderr,f'\n\n\n', output),
                    'status': 'error'
                }


            print(f"[{host}] Команда закончила выполнение: {command}")

            if signal_set:
                signals.set(host, signal_set)

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
