# import paramiko

# class SimpleSSH:
#     def __init__(self, host: str, username: str, password: str, port: int = 22):
#         self.host = host
#         self.username = username
#         self.password = password
#         self.port = port
#         self.client = paramiko.SSHClient()

#     def run_command(self, command: str) -> tuple[str, str, int]:
#         """Подключается к серверу, выполняет команду и возвращает (stdout, stderr, exit_code)."""
#         try:
#             self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
#             self.client.connect(
#                 hostname=self.host,
#                 port=self.port,
#                 username=self.username,
#                 password=self.password
#             )
#             stdin, stdout, stderr = self.client.exec_command(command, get_pty=True)
#             exit_code = stdout.channel.recv_exit_status()
#             return stdout.read().decode().strip(), stderr.read().decode().strip(), exit_code
#         finally:
#             self.client.close()


from __future__ import annotations
import paramiko, time, socket
from typing import Optional, Callable, Dict

LineLogger = Callable[[str], None]

class SimpleSSH:
    def __init__(self, host: str, username: str, password: str, port: int = 22, connect_timeout: int = 15):
        self.host = host
        self.username = username
        self.password = password
        self.port = port
        self.connect_timeout = connect_timeout
        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    def run_command(
        self,
        command: str,
        *,
        timeout: int = 300,
        stream: Optional[LineLogger] = None,
        get_pty: bool = True,
    ) -> Dict[str, object]:
        """
        Выполняет команду и возвращает:
          { "rc": int, "stdout": str, "stderr": str, "duration_s": float }
        Если задан stream(line: str), построчно пишет [out]/[err] строки в него.
        Без sudo, без окружений — максимально просто.
        """
        start = time.time()
        try:
            self.client.connect(
                hostname=self.host,
                port=self.port,
                username=self.username,
                password=self.password,
                timeout=self.connect_timeout,
                allow_agent=False,
                look_for_keys=False,
            )

            stdin, stdout, stderr = self.client.exec_command(command, get_pty=get_pty, timeout=timeout)

            # хотим иметь возможность видеть лог «вживую», но код оставить простым:
            if stream:
                chan = stdout.channel
                chan.settimeout(1.0)

                out_buf, err_buf = [], []
                end_by = start + timeout

                def _drain():
                    flushed = False
                    while stdout.channel.recv_ready():
                        data = stdout.channel.recv(4096).decode(errors="replace")
                        out_buf.append(data)
                        for line in data.splitlines():
                            stream(f"[out] {line}")
                        flushed = True
                    while stderr.channel.recv_stderr_ready():
                        data = stderr.channel.recv_stderr(4096).decode(errors="replace")
                        err_buf.append(data)
                        for line in data.splitlines():
                            stream(f"[err] {line}")
                        flushed = True
                    return flushed

                # основной цикл ожидания завершения
                while True:
                    _drain()
                    if chan.exit_status_ready():
                        break
                    if time.time() > end_by:
                        chan.close()
                        return {
                            "rc": 124,
                            "stdout": "".join(out_buf).strip(),
                            "stderr": "TIMEOUT: command exceeded {}s".format(timeout),
                            "duration_s": time.time() - start,
                        }
                    time.sleep(0.05)

                rc = chan.recv_exit_status()
                # дочистим остатки
                _drain()
                return {
                    "rc": rc,
                    "stdout": "".join(out_buf).strip(),
                    "stderr": "".join(err_buf).strip(),
                    "duration_s": time.time() - start,
                }

            # без стриминга — просто дождёмся завершения и прочитаем целиком
            rc = stdout.channel.recv_exit_status()
            out = stdout.read().decode(errors="replace").strip()
            err = stderr.read().decode(errors="replace").strip()
            return {"rc": rc, "stdout": out, "stderr": err, "duration_s": time.time() - start}

        except (socket.timeout, TimeoutError) as e:
            return {"rc": 124, "stdout": "", "stderr": f"TIMEOUT: {e}", "duration_s": time.time() - start}
        finally:
            try:
                self.client.close()
            except Exception:
                pass
