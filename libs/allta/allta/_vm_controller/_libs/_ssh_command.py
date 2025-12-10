from ..._decorators.Decorators import BaseDecorators
from ._signals import _Signals as signals
import paramiko
import time
from typing import Optional, List, Union

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
    def cmd(
        host: str,
        command: str,
        vm_dates: dict,
        username: str = "u",
        password: str = "1",
        signal_set: Optional[str] = None,
        signal_get: Optional[Union[str, List[str]]] = None,
        task_name: Optional[str] = None,
        time_out: int = 15,
    ) -> dict:
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
                        "host": host,
                        "task_name": task_name or "unknown",
                        "command": command,
                        "output": error_msg,
                        "status": "error",
                    }

            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(
                hostname=vm_dates[host].get("ip_bridge", ""),
                port=int(vm_dates[host].get("host-port", 22)),
                username=username,
                password=password,
                timeout=10,
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
                print(
                    f"[{host}] Ошибка при выполнении '{command}': ОШИБКА:\n{output_stderr}\n\n\n ПОЛНЫЙ ВЫВОД КОМАНДЫ С ОШИБКОЙ\n\n\n{output}\n\n\n (exit status: {exit_status})"
                )
                return {
                    "host": host,
                    "task_name": task_name or "unknown",
                    "command": command,
                    "output": (output_stderr, "\n\n\n", output),
                    "status": "error",
                }

            print(f"[{host}] Команда закончила выполнение: {command}")

            if signal_set:
                signals.set(host, signal_set)

            return {
                "host": host,
                "task_name": task_name if task_name else "unknown",
                "command": command,
                "output": output,
                "status": "ok",
            }

        except paramiko.AuthenticationException:
            print(f"[{host}] Ошибка аутентификации.")
            return {
                "host": host,
                "task_name": task_name if task_name else "unknown",
                "command": command,
                "output": "Ошибка аутентификации",
                "status": "error",
            }

        except paramiko.SSHException as e:
            print(f"[{host}] Ошибка SSH: {str(e)}")
            return {
                "host": host,
                "task_name": task_name if task_name else "unknown",
                "command": command,
                "output": str(e),
                "status": "error",
            }

        except Exception as e:
            print(f"[{host}] Ошибка: {str(e)}")
            return {
                "host": host,
                "task_name": task_name if task_name else "unknown",
                "command": command,
                "output": str(e),
                "status": "error",
            }

        finally:
            if ssh:
                ssh.close()


    @BaseDecorators.trycorator
    @logger
    @staticmethod
    def cmd_detach(
        host: str,
        command: str,
        vm_dates: dict,
        username: str = "u",
        password: str = "1",
        signal_set: Optional[str] = None,
        signal_get: Optional[Union[str, List[str]]] = None,
        task_name: Optional[str] = None,
        time_out: int = 15,
        nowait_timeout: int = 30,
    ) -> dict:
        ssh = None
        chan = None
        try:
            # --- ждём сигнал, если надо (как в cmd) ---
            if signal_get:
                if len(signal_get) == 1:
                    signal_get = [host, signal_get[0]]
                elif not signal_get[0]:
                    signal_get[0] = host

                if not signals.get(signal_get, timeout_min=time_out):
                    error_msg = f"ОШИБКА СИГНАЛ {signal_get} НЕ НАЙДЕН"
                    print(f"[{host}] {error_msg}")
                    return {
                        "host": host,
                        "task_name": task_name or "unknown",
                        "command": command,
                        "output": error_msg,
                        "status": "error",
                    }

            # --- подключаемся и запускаем команду БЕЗ обёрток ---
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(
                hostname=vm_dates[host].get("ip_bridge", ""),
                port=int(vm_dates[host].get("host-port", 22)),
                username=username,
                password=password,
                timeout=10,
            )

            stdin, stdout, stderr = ssh.exec_command(command)
            chan = stdout.channel
            # делаем чтение неблокирующим
            chan.settimeout(0.0)

            end_ts = time.time() + max(0, int(nowait_timeout))
            out_buf, err_buf = [], []

            def _drain():
                # Читай ИЗ КАНАЛА, не из stdout/stderr-обёрток
                while chan.recv_ready():
                    out_buf.append(chan.recv(4096).decode(errors="ignore"))
                while chan.recv_stderr_ready():
                    err_buf.append(chan.recv_stderr(4096).decode(errors="ignore"))

            # ждём до nowait_timeout
            while time.time() < end_ts:
                _drain()
                if chan.exit_status_ready():
                    _drain()
                    exit_status = chan.recv_exit_status()
                    output_stdout = "".join(out_buf)
                    output_stderr = "".join(err_buf)
                    output = output_stdout + ("\n" + output_stderr if output_stderr else "")

                    if exit_status != 0:
                        print(
                            f"[{host}] Ошибка при выполнении '{command}': ОШИБКА:\n{output_stderr}\n\n\n"
                            f" ПОЛНЫЙ ВЫВОД КОМАНДЫ С ОШИБКОЙ\n\n\n{output}\n\n\n (exit status: {exit_status})"
                        )
                        return {
                            "host": host,
                            "task_name": task_name or "unknown",
                            "command": command,
                            "output": (output_stderr, "\n\n\n", output),
                            "status": "error",
                        }

                    print(f"[{host}] Команда закончила выполнение: {command}")
                    if signal_set:
                        signals.set(host, signal_set)
                    return {
                        "host": host,
                        "task_name": task_name or "unknown",
                        "command": command,
                        "output": output,
                        "status": "ok",
                    }

                time.sleep(0.1)

            # не успела завершиться к таймауту — дочитываем, закрываем SSH, ставим сигнал
            _drain()
            output = "".join(out_buf) + ("\n" + "".join(err_buf) if err_buf else "")

            try:
                if chan and not chan.closed:
                    chan.close()
            except Exception:
                pass
            try:
                ssh.close()
            except Exception:
                pass

            if signal_set:
                signals.set(host, signal_set)

            print(f"[{host}] Детач: ssh закрыт через {nowait_timeout}s, команда продолжит выполняться на хосте.")
            return {
                "host": host,
                "task_name": task_name or "unknown",
                "command": command,
                "output": output,  # может быть пустым — это ок
                "status": "ok",
            }

        except paramiko.AuthenticationException:
            msg = "Ошибка аутентификации"
            print(f"[{host}] {msg}")
            return {"host": host, "task_name": task_name or "unknown", "command": command, "output": msg, "status": "error"}
        except paramiko.SSHException as e:
            print(f"[{host}] Ошибка SSH: {e}")
            return {"host": host, "task_name": task_name or "unknown", "command": command, "output": str(e), "status": "error"}
        except Exception as e:
            print(f"[{host}] Ошибка: {e}")
            return {"host": host, "task_name": task_name or "unknown", "command": command, "output": str(e), "status": "error"}
        finally:
            try:
                if ssh:
                    ssh.close()
            except Exception:
                pass
