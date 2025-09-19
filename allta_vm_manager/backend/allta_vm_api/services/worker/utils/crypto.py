import os
from cryptography.fernet import Fernet
from utils.config import settings

class Crypto:
    """
    Обёртка вокруг Fernet: шифрует и расшифровывает строки.
    Ключ хранится в переменной окружения ENCRYPTION_KEY.
    """
    def __init__(self):
        self.key_file_path = settings.KEY_FILE_PATH
        if os.path.exists(self.key_file_path):
            with open(self.key_file_path, "rb") as key_file:
                key = key_file.read()
        else:
            key = Fernet.generate_key()
            with open(self.key_file_path, "wb") as key_file:
                key_file.write(key)
            print(f"[+] Ключ сгенерирован и сохранён в {self.key_file_path}")
        self.fernet = Fernet(key)

    def encrypt(self, secret: str) -> str:
        return self.fernet.encrypt(secret.encode()).decode()

    def decrypt(self, token: str) -> str:
        return self.fernet.decrypt(token.encode()).decode()


