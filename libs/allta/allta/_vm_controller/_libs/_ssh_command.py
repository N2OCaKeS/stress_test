from ..._decorators.Decorators import BaseDecorators
from ._signals import _Signals as signals
import paramiko
import time
import shlex
import uuid
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
        nowait_mode: str = "terminate",
    ) -> dict:
        ssh = None
        try:
            if signal_get:
                if isinstance(signal_get, str):
                    signal_get = [signal_get]
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

            try:
                nowait_timeout = int(nowait_timeout)
            except Exception:
                nowait_timeout = 30
            nowait_timeout = max(0, nowait_timeout)

            mode = (nowait_mode or "terminate").strip().lower()
            if mode not in ("terminate", "continue"):
                print(
                    f"[{host}] Неизвестный nowait_mode='{nowait_mode}', использую 'terminate'."
                )
                mode = "terminate"

            if mode == "continue":
                run_id = f"{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
                task_seed = task_name or "task"
                task_tag = "".join(
                    ch if ch.isalnum() else "_" for ch in task_seed
                )[:24] or "task"
                host_tag = "".join(
                    ch if (ch.isalnum() or ch in ("-", "_", ".")) else "_"
                    for ch in host
                )[:24] or "host"
                unit_seed = f"allta_nowait_{host_tag}_{task_tag}_{run_id}"
                log_path = f"/tmp/{unit_seed}.log"
                unit_name = "".join(
                    ch if (ch.isalnum() or ch in ("-", "_", ".")) else "_"
                    for ch in unit_seed
                )[:120]

                cmd_with_log = f"{command} > {shlex.quote(log_path)} 2>&1"
                launch_script = f"""
log_path={shlex.quote(log_path)}
unit_name={shlex.quote(unit_name)}
mode=""
pid=""
unit=""

if command -v systemd-run >/dev/null 2>&1 && command -v sudo >/dev/null 2>&1; then
  if sudo -n true >/dev/null 2>&1; then
    if sudo -n systemd-run --unit "$unit_name" --collect --quiet /bin/bash -lc {shlex.quote(cmd_with_log)} >/dev/null 2>&1; then
      sleep 1
      pid="$(sudo -n systemctl show "$unit_name" -p MainPID --value 2>/dev/null | tr -d '[:space:]')"
      if [ -n "$pid" ] && [ "$pid" != "0" ] && sudo -n kill -0 "$pid" >/dev/null 2>&1; then
        mode="systemd"
        unit="$unit_name"
      fi
    fi
  fi
fi

if [ -z "$mode" ]; then
  if command -v setsid >/dev/null 2>&1; then
    nohup setsid bash -lc {shlex.quote(command)} < /dev/null > "$log_path" 2>&1 &
  else
    nohup bash -lc {shlex.quote(command)} < /dev/null > "$log_path" 2>&1 &
  fi
  pid="$!"
  sleep 1
  if kill -0 "$pid" >/dev/null 2>&1; then
    mode="nohup"
  else
    echo "__ALLTA_ERROR=background_start_failed"
    echo "__ALLTA_LOG=$log_path"
    exit 1
  fi
fi

echo "__ALLTA_MODE=$mode"
echo "__ALLTA_PID=$pid"
echo "__ALLTA_UNIT=$unit"
echo "__ALLTA_LOG=$log_path"
"""
                wrapped = f"bash -lc {shlex.quote(launch_script)}"
                _, stdout, stderr = ssh.exec_command(wrapped)
                out_raw = stdout.read().decode(errors="ignore")
                err_raw = stderr.read().decode(errors="ignore").strip()

                markers = {}
                for line in out_raw.splitlines():
                    if line.startswith("__ALLTA_") and "=" in line:
                        key, value = line.split("=", 1)
                        markers[key.strip()] = value.strip()

                mode_used = markers.get("__ALLTA_MODE", "")
                pid = markers.get("__ALLTA_PID", "")
                unit = markers.get("__ALLTA_UNIT", "")
                log_from_marker = markers.get("__ALLTA_LOG", log_path)
                marker_error = markers.get("__ALLTA_ERROR", "")

                if marker_error or not mode_used or not pid:
                    error_msg = (
                        "Не удалось надёжно запустить фоновый процесс. "
                        f"Проверьте лог: {log_from_marker}"
                    )
                    if marker_error:
                        error_msg = f"{error_msg} (reason={marker_error})"
                    if err_raw:
                        error_msg = f"{error_msg}\n{err_raw}"
                    print(f"[{host}] {error_msg}")
                    return {
                        "host": host,
                        "task_name": task_name or "unknown",
                        "command": command,
                        "output": error_msg,
                        "status": "error",
                    }

                if signal_set:
                    # Ставим сигнал после старта команды (через 10 секунд).
                    signals.set_delayed(host, signal_set, delay_sec=10)

                # Сразу читаем текущий фрагмент VM-лога и передаем его в лог задачи на хосте.
                log_snapshot = ""
                try:
                    snapshot_cmd = (
                        f"if [ -f {shlex.quote(log_from_marker)} ]; then "
                        f"head -c 65536 {shlex.quote(log_from_marker)}; "
                        "fi"
                    )
                    _, snap_stdout, _ = ssh.exec_command(snapshot_cmd)
                    log_snapshot = snap_stdout.read().decode(errors="ignore")
                except Exception as snapshot_error:
                    log_snapshot = (
                        f"Не удалось прочитать snapshot VM-лога: {snapshot_error}"
                    )

                if mode_used == "systemd":
                    print(
                        f"[{host}] Continue-mode: команда запущена через systemd (unit={unit}, PID={pid}, log={log_from_marker})."
                    )
                    output = f"mode=systemd, unit={unit}, PID={pid}, log={log_from_marker}"
                else:
                    print(
                        f"[{host}] Continue-mode: команда отвязана через nohup/setsid (PID={pid}, log={log_from_marker})."
                    )
                    output = f"mode=nohup, PID={pid}, log={log_from_marker}"

                if err_raw:
                    output = f"{output}\n{err_raw}"

                snapshot_text = log_snapshot if log_snapshot else "<empty>"
                output = (
                    f"{output}\nVM_LOG_PATH: {log_from_marker}\n"
                    f"VM_LOG_SNAPSHOT:\n{snapshot_text}"
                )

                return {
                    "host": host,
                    "task_name": task_name or "unknown",
                    "command": command,
                    "output": output,
                    "status": "ok",
                }

            wrapped = (
                f"timeout --signal=TERM --kill-after=5s {nowait_timeout}s "
                f"bash -lc {shlex.quote(command)}"
            )
            _, stdout, stderr = ssh.exec_command(wrapped)

            if signal_set:
                # Ставим сигнал после старта команды (через 10 секунд).
                signals.set_delayed(host, signal_set, delay_sec=10)

            output_stdout = stdout.read().decode(errors="ignore")
            output_stderr = stderr.read().decode(errors="ignore")
            exit_status = stdout.channel.recv_exit_status()
            output = output_stdout + ("\n" + output_stderr if output_stderr else "")

            if exit_status == 0:
                print(f"[{host}] Команда закончила выполнение: {command}")
                return {
                    "host": host,
                    "task_name": task_name or "unknown",
                    "command": command,
                    "output": output,
                    "status": "ok",
                }

            if exit_status in (124, 137):
                timeout_msg = (
                    f"Команда принудительно завершена по nowait_timeout={nowait_timeout}s."
                )
                print(f"[{host}] {timeout_msg}")
                output = f"{output}\n{timeout_msg}".strip()
                return {
                    "host": host,
                    "task_name": task_name or "unknown",
                    "command": command,
                    "output": output,
                    "status": "ok",
                }

            print(
                f"[{host}] Ошибка при выполнении '{command}': ОШИБКА:\n{output_stderr}\n\n\n "
                f"ПОЛНЫЙ ВЫВОД КОМАНДЫ С ОШИБКОЙ\n\n\n{output}\n\n\n (exit status: {exit_status})"
            )
            return {
                "host": host,
                "task_name": task_name or "unknown",
                "command": command,
                "output": (output_stderr, "\n\n\n", output),
                "status": "error",
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
