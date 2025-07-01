import paramiko
from scp import SCPClient
from typing import Optional

class SCP:
    """Класс для копирования файлов через SCP (автономный, с параметрами подключения)"""
    
    def __init__(
        self,
        hostname: str,
        port: int,
        username: str,
        password: str,
        remote_path: str,
        local_path: str,
        timeout: int = 10
    ):
        self.hostname = hostname
        self.port = port
        self.username = username
        self.password = password
        self.remote_path = remote_path
        self.local_path = local_path
        self.timeout = timeout
        self.ssh: Optional[paramiko.SSHClient] = None
        self.scp: Optional[SCPClient] = None

    def connect(self) -> None:
        """Устанавливает SSH-соединение и создаёт SCP-клиент"""
        self.ssh = paramiko.SSHClient()
        self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.ssh.connect(
            hostname=self.hostname,
            port=self.port,
            username=self.username,
            password=self.password,
            timeout=self.timeout
        )
        self.scp = SCPClient(self.ssh.get_transport())

    def upload(self) -> None:
        """Загружает файл на удалённый сервер"""
        if not self.scp:
            self.connect()
        self.scp.put(self.local_path, self.remote_path)

    def download(self) -> None:
        """Скачивает файл с удалённого сервера"""
        if not self.scp:
            self.connect()
        self.scp.get(self.remote_path, self.local_path)

    def close(self) -> None:
        """Закрывает соединения"""
        if self.scp:
            self.scp.close()
        if self.ssh:
            self.ssh.close()

    def __enter__(self):
        """Поддержка контекстного менеджера (`with`)"""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Автоматическое закрытие соединения"""
        self.close()