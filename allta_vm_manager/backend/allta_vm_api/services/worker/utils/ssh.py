import paramiko

class SimpleSSH:
    def __init__(self, host: str, username: str, password: str, port: int = 22):
        self.host = host
        self.username = username
        self.password = password
        self.port = port
        self.client = paramiko.SSHClient()

    def run_command(self, command: str) -> tuple[str, str, int]:
        """Подключается к серверу, выполняет команду и возвращает (stdout, stderr, exit_code)."""
        try:
            self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            self.client.connect(
                hostname=self.host,
                port=self.port,
                username=self.username,
                password=self.password
            )
            stdin, stdout, stderr = self.client.exec_command(command)
            exit_code = stdout.channel.recv_exit_status()
            return stdout.read().decode().strip(), stderr.read().decode().strip(), exit_code
        finally:
            self.client.close()
