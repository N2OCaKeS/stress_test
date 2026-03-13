from __future__ import annotations

import os

from cryptography.fernet import Fernet

from app.utils.config import settings


class Crypto:
    def __init__(self) -> None:
        self.key_file_path = settings.TOKEN_KEY_FILE_PATH
        key_dir = os.path.dirname(self.key_file_path)
        if key_dir:
            os.makedirs(key_dir, exist_ok=True)

        if os.path.exists(self.key_file_path):
            with open(self.key_file_path, "rb") as key_file:
                key = key_file.read()
        else:
            key = Fernet.generate_key()
            with open(self.key_file_path, "wb") as key_file:
                key_file.write(key)

        self.fernet = Fernet(key)

    def encrypt(self, secret: str) -> str:
        return self.fernet.encrypt(secret.encode()).decode()

    def decrypt(self, token: str) -> str:
        return self.fernet.decrypt(token.encode()).decode()
