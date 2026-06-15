"""Хранилище мастер-ключей шифрования с версионированием.

`secrets_service` шифрует AES-256-GCM с версионированием: wire-формат
``v<N>$<nonce>$<ct>``, активная версия пишется в новые токены, старые
расшифровываются по версии из префикса. Раньше материал ключей жил прямо в
env (``SERVER_ENCRYPTION_KEY`` + ``SERVER_ENCRYPTION_KEY__v<N>``), а активная
версия — в ``SERVER_ENCRYPTION_KEY_VERSION``. Это работало для ручной ротации
с рестартом pod'а, но не давало менять ключ в рантайме без простоя.

KeyStore выносит «какой материал у версии N» и «какая версия активна» за
интерфейс с бэкендами:

* :class:`FileKeyStore` — JSON-файл с правами 0600, путь из ``KEYSTORE_PATH``.
  Дефолт для dev/тестов. При отсутствии файла bootstrap'ится из текущих
  env-ключей — обратная совместимость со старым деплоем.
* :class:`K8sSecretKeyStore` — k8s Secret (in-cluster config). Прод = k3s.

Бэкенд выбирается env ``KEYSTORE_BACKEND=file|k8s`` (дефолт ``file``).

Материал — это master-string (то, что раньше лежало в env), а не готовый
AES-ключ: деривацию (HKDF / legacy SHA-256) по-прежнему делает
`secrets_service` по версии из wire-префикса. KeyStore хранит ровно то, что
HKDF съедает на вход — так ротация ключа не трогает KDF-логику и
расшифровку legacy-токенов.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from functools import lru_cache
from typing import Protocol

from src.core.config import get_settings
from src.core.exceptions import AppException

logger = logging.getLogger(__name__)

# Имена env под bootstrap файлового бэкенда — те же, что читал secrets_service
# напрямую до появления KeyStore. Обратная совместимость: подняли сервис на
# старом env, при первом обращении файл засеется из этих значений.
_ACTIVE_KEY_ENV = "SERVER_ENCRYPTION_KEY"
_LEGACY_KEY_ENV_PREFIX = "SERVER_ENCRYPTION_KEY__v"

# Дефолтный путь файла, если KEYSTORE_PATH не задан.
_DEFAULT_KEYSTORE_PATH = "/tmp/dbos_server_keystore.json"


class KeyStore(Protocol):
    """Интерфейс хранилища мастер-ключей.

    Реализации обязаны быть thread-safe для конкурентных read'ов из
    request-handler'ов. Запись (`set_key`/`set_active`) — редкая,
    операторская, идёт под admin-rotate.
    """

    def get_active_version(self) -> int:
        """Версия, под которую шифруются новые токены."""
        ...

    def get_key(self, version: int) -> bytes:
        """Master-материал версии в виде bytes. Нет версии → AppException 500."""
        ...

    def set_key(self, version: int, key_b64: str) -> None:
        """Положить master-материал версии (значение — как пришло, base64-строка)."""
        ...

    def set_active(self, version: int) -> None:
        """Сделать версию активной. Версия обязана уже существовать."""
        ...

    def list_versions(self) -> list[int]:
        """Все известные версии, по возрастанию."""
        ...

    def remove_key(self, version: int) -> None:
        """Убрать версию (retire). Активную убрать нельзя."""
        ...


def _missing_key_error(version: int) -> AppException:
    return AppException(
        http_status=500,
        error_code="ENCRYPTION_KEY_MISSING",
        message=f"No key configured for ciphertext version v{version}",
        details={"version": version},
    )


def _bootstrap_state_from_env() -> dict:
    """Собрать начальное состояние keystore из env-ключей.

    Активная версия + её материал из ``SERVER_ENCRYPTION_KEY`` /
    ``SERVER_ENCRYPTION_KEY_VERSION``; legacy-версии из
    ``SERVER_ENCRYPTION_KEY__v<N>``. Возвращает
    ``{"active_version": int, "keys": {"<N>": "<material>"}}``.
    """
    settings = get_settings()
    active = int(settings.server_encryption_key_version)
    keys: dict[str, str] = {}
    if settings.server_encryption_key:
        keys[str(active)] = settings.server_encryption_key
    for name, value in os.environ.items():
        if not name.startswith(_LEGACY_KEY_ENV_PREFIX):
            continue
        suffix = name[len(_LEGACY_KEY_ENV_PREFIX):]
        try:
            ver = int(suffix)
        except ValueError:
            continue
        if value:
            # Активную не перетираем legacy-env'ом, если совпали версии.
            keys.setdefault(str(ver), value)
    return {"active_version": active, "keys": keys}


class FileKeyStore:
    """JSON-файл (0600) с master-материалом версий.

    Формат файла::

        {"active_version": 2, "keys": {"1": "<material>", "2": "<material>"}}

    При отсутствии файла — bootstrap из env (см. :func:`_bootstrap_state_from_env`)
    и немедленная запись на диск. Всё состояние держится в памяти под
    `threading.Lock`; файл — durable backing store, перечитывается только при
    старте процесса.
    """

    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._state = self._load_or_bootstrap()

    def _load_or_bootstrap(self) -> dict:
        if os.path.exists(self._path):
            with open(self._path, encoding="utf-8") as fh:
                raw = json.load(fh)
            keys = {str(k): str(v) for k, v in (raw.get("keys") or {}).items()}
            active = int(raw.get("active_version"))
            return {"active_version": active, "keys": keys}
        state = _bootstrap_state_from_env()
        self._write(state)
        return state

    def _write(self, state: dict) -> None:
        """Атомарная запись через temp-файл + rename, права 0600."""
        tmp = f"{self._path}.tmp.{os.getpid()}"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(state, fh)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self._path)
            os.chmod(self._path, 0o600)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    def get_active_version(self) -> int:
        with self._lock:
            return int(self._state["active_version"])

    def get_key(self, version: int) -> bytes:
        with self._lock:
            material = self._state["keys"].get(str(version))
        if not material:
            raise _missing_key_error(version)
        return material.encode()

    def set_key(self, version: int, key_b64: str) -> None:
        with self._lock:
            self._state["keys"][str(version)] = key_b64
            self._write(self._state)

    def set_active(self, version: int) -> None:
        with self._lock:
            if str(version) not in self._state["keys"]:
                raise _missing_key_error(version)
            self._state["active_version"] = int(version)
            self._write(self._state)

    def list_versions(self) -> list[int]:
        with self._lock:
            return sorted(int(v) for v in self._state["keys"])

    def remove_key(self, version: int) -> None:
        with self._lock:
            if int(version) == int(self._state["active_version"]):
                raise AppException(
                    http_status=409,
                    error_code="KEYSTORE_CANNOT_RETIRE_ACTIVE",
                    message=f"Cannot retire active key version v{version}",
                    details={"version": version},
                )
            self._state["keys"].pop(str(version), None)
            self._write(self._state)


class K8sSecretKeyStore:
    """Чтение/запись master-материала версий через k8s Secret.

    Secret хранит те же поля, что и :class:`FileKeyStore`-файл, разложенные по
    ключам data-секции: ``active_version`` (число строкой) и
    ``key_v<N>`` для каждой версии. Значения в k8s Secret уже base64-кодируются
    самим API, поэтому материал кладём как есть (raw string).

    `kubernetes` импортируется лениво — file-бэкенд и тесты не должны тянуть
    зависимость. In-cluster config (`load_incluster_config`).
    """

    _ACTIVE_FIELD = "active_version"
    _KEY_FIELD_PREFIX = "key_v"

    def __init__(self, secret_name: str, namespace: str) -> None:
        self._secret_name = secret_name
        self._namespace = namespace
        self._lock = threading.Lock()
        self._api = self._build_api()

    def _build_api(self):
        from kubernetes import client, config  # lazy

        try:
            config.load_incluster_config()
        except Exception:  # noqa: BLE001 — dev-машина вне кластера
            config.load_kube_config()
        return client.CoreV1Api()

    def _read_secret(self) -> dict[str, str]:
        import base64

        with self._lock:
            secret = self._api.read_namespaced_secret(
                self._secret_name, self._namespace
            )
        data = secret.data or {}
        return {
            k: base64.b64decode(v).decode("utf-8") for k, v in data.items()
        }

    def _patch_secret(self, fields: dict[str, str | None]) -> None:
        import base64

        encoded: dict[str, str | None] = {}
        for key, value in fields.items():
            if value is None:
                encoded[key] = None  # k8s strategic-merge удаляет null-поля
            else:
                encoded[key] = base64.b64encode(value.encode()).decode("ascii")
        with self._lock:
            self._api.patch_namespaced_secret(
                self._secret_name, self._namespace, {"data": encoded}
            )

    def get_active_version(self) -> int:
        data = self._read_secret()
        return int(data[self._ACTIVE_FIELD])

    def get_key(self, version: int) -> bytes:
        data = self._read_secret()
        material = data.get(f"{self._KEY_FIELD_PREFIX}{version}")
        if not material:
            raise _missing_key_error(version)
        return material.encode()

    def set_key(self, version: int, key_b64: str) -> None:
        self._patch_secret({f"{self._KEY_FIELD_PREFIX}{version}": key_b64})

    def set_active(self, version: int) -> None:
        data = self._read_secret()
        if f"{self._KEY_FIELD_PREFIX}{version}" not in data:
            raise _missing_key_error(version)
        self._patch_secret({self._ACTIVE_FIELD: str(int(version))})

    def list_versions(self) -> list[int]:
        data = self._read_secret()
        out: list[int] = []
        for key in data:
            if key.startswith(self._KEY_FIELD_PREFIX):
                try:
                    out.append(int(key[len(self._KEY_FIELD_PREFIX):]))
                except ValueError:
                    continue
        return sorted(out)

    def remove_key(self, version: int) -> None:
        active = self.get_active_version()
        if int(version) == int(active):
            raise AppException(
                http_status=409,
                error_code="KEYSTORE_CANNOT_RETIRE_ACTIVE",
                message=f"Cannot retire active key version v{version}",
                details={"version": version},
            )
        self._patch_secret({f"{self._KEY_FIELD_PREFIX}{version}": None})


def _build_keystore() -> KeyStore:
    backend = (os.environ.get("KEYSTORE_BACKEND") or "file").strip().lower()
    if backend == "k8s":
        secret_name = os.environ.get("KEYSTORE_K8S_SECRET", "server-encryption-keys")
        namespace = os.environ.get("KEYSTORE_K8S_NAMESPACE", "default")
        return K8sSecretKeyStore(secret_name, namespace)
    path = os.environ.get("KEYSTORE_PATH") or _DEFAULT_KEYSTORE_PATH
    return FileKeyStore(path)


@lru_cache
def get_keystore() -> KeyStore:
    """Один KeyStore на процесс. Сбрасывается в тестах через cache_clear()."""
    return _build_keystore()
