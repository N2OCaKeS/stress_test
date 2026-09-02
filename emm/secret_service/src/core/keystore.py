"""Хранилище мастер-ключей шифрования secret_service с версионированием.

Зеркало `server_service/src/core/keystore.py`, но с собственным master-key
(крипто двух сервисов раздельно — компрометация ключа одного не открывает
секреты другого). `secrets_service` шифрует AES-256-GCM, wire-формат
``v<N>$<nonce>$<ct>``; активная версия пишется в новые токены, старые
расшифровываются по версии из префикса.

Раньше материал ключей жил в env (``SECRET_ENCRYPTION_KEY`` +
``SECRET_ENCRYPTION_KEY__v<N>``), активная версия — в
``SECRET_ENCRYPTION_KEY_VERSION``. KeyStore выносит это за интерфейс с
бэкендами:

* :class:`FileKeyStore` — JSON-файл 0600, путь из ``KEYSTORE_PATH``. Дефолт
  для dev/тестов; при отсутствии файла bootstrap из env (обратная
  совместимость).
* :class:`K8sSecretKeyStore` — k8s Secret (in-cluster config). Прод = k3s.

Бэкенд выбирается env ``KEYSTORE_BACKEND=file|k8s`` (дефолт ``file``).

Хранится master-материал (вход HKDF), не готовый AES-ключ: деривация
остаётся в `secrets_service` и выбирается по версии из wire-префикса.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from functools import lru_cache
from typing import Protocol

from src.core.config import get_settings
from src.core.exceptions import AppException, ConflictError

logger = logging.getLogger(__name__)

_ACTIVE_KEY_ENV = "SECRET_ENCRYPTION_KEY"
_LEGACY_KEY_ENV_PREFIX = "SECRET_ENCRYPTION_KEY__v"
_DEFAULT_KEYSTORE_PATH = "/tmp/dbos_secret_keystore.json"

# Короткий TTL кэша материала k8s-Secret'а: reveal + lazy-reencrypt делает
# 2-6 обращений к keystore, каждое — синхронный round-trip к API-серверу,
# который блокирует event loop. Кэш схлопывает их в одно чтение на TTL-окно;
# мутации (rotate/retire) кэш сбрасывают сразу, так что свежая версия видна
# без задержки.
_K8S_CACHE_TTL_SECONDS = float(os.environ.get("KEYSTORE_K8S_CACHE_TTL_SECONDS", "5"))


class KeyStore(Protocol):
    """Интерфейс хранилища мастер-ключей (см. модуль-docstring)."""

    def get_active_version(self) -> int: ...
    def get_key(self, version: int) -> bytes: ...
    def set_key(self, version: int, key_b64: str) -> None: ...
    def set_active(self, version: int) -> None: ...
    def list_versions(self) -> list[int]: ...
    def remove_key(self, version: int) -> None: ...


def _missing_key_error(version: int) -> AppException:
    return AppException(
        error_code="ENCRYPTION_KEY_MISSING",
        message=f"No key configured for ciphertext version v{version}",
        details={"version": version},
        http_status=500,
    )


def _bootstrap_state_from_env() -> dict:
    """Начальное состояние из env: активная версия + материал + legacy-версии."""
    settings = get_settings()
    active = int(settings.secret_encryption_key_version)
    keys: dict[str, str] = {}
    if settings.secret_encryption_key:
        keys[str(active)] = settings.secret_encryption_key
    for name, value in os.environ.items():
        if not name.startswith(_LEGACY_KEY_ENV_PREFIX):
            continue
        suffix = name[len(_LEGACY_KEY_ENV_PREFIX):]
        try:
            ver = int(suffix)
        except ValueError:
            continue
        if value:
            keys.setdefault(str(ver), value)
    return {"active_version": active, "keys": keys}


class FileKeyStore:
    """JSON-файл (0600) с master-материалом версий. См. server_service-зеркало."""

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
                raise ConflictError(
                    error_code="KEYSTORE_CANNOT_RETIRE_ACTIVE",
                    message=f"Cannot retire active key version v{version}",
                    details={"version": version},
                )
            self._state["keys"].pop(str(version), None)
            self._write(self._state)


class K8sSecretKeyStore:
    """k8s Secret backend. `kubernetes` импортируется лениво. См. server-зеркало."""

    _ACTIVE_FIELD = "active_version"
    _KEY_FIELD_PREFIX = "key_v"

    def __init__(self, secret_name: str, namespace: str) -> None:
        self._secret_name = secret_name
        self._namespace = namespace
        self._lock = threading.Lock()
        self._api = self._build_api()
        # Кэш декодированного Secret'а под тем же lock'ом. None = пусто/сброшено.
        self._cache: dict[str, str] | None = None
        self._cache_ts = 0.0
        self._cache_ttl = _K8S_CACHE_TTL_SECONDS

    def _build_api(self):
        from kubernetes import client, config  # lazy

        try:
            config.load_incluster_config()
        except Exception:  # noqa: BLE001
            config.load_kube_config()
        return client.CoreV1Api()

    def _read_secret(self) -> dict[str, str]:
        import base64

        now = time.monotonic()
        with self._lock:
            if (
                self._cache is not None
                and now - self._cache_ts < self._cache_ttl
            ):
                return self._cache
            secret = self._api.read_namespaced_secret(
                self._secret_name, self._namespace
            )
            data = secret.data or {}
            decoded = {
                k: base64.b64decode(v).decode("utf-8") for k, v in data.items()
            }
            self._cache = decoded
            self._cache_ts = now
            return decoded

    def _patch_secret(self, fields: dict[str, str | None]) -> None:
        import base64

        encoded: dict[str, str | None] = {}
        for key, value in fields.items():
            if value is None:
                encoded[key] = None
            else:
                encoded[key] = base64.b64encode(value.encode()).decode("ascii")
        with self._lock:
            self._api.patch_namespaced_secret(
                self._secret_name, self._namespace, {"data": encoded}
            )
            # Мутация меняет содержимое Secret'а — сбрасываем кэш, чтобы
            # следующий read увидел новую активную версию/материал сразу.
            self._cache = None

    def get_active_version(self) -> int:
        return int(self._read_secret()[self._ACTIVE_FIELD])

    def get_key(self, version: int) -> bytes:
        material = self._read_secret().get(f"{self._KEY_FIELD_PREFIX}{version}")
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
        out: list[int] = []
        for key in self._read_secret():
            if key.startswith(self._KEY_FIELD_PREFIX):
                try:
                    out.append(int(key[len(self._KEY_FIELD_PREFIX):]))
                except ValueError:
                    continue
        return sorted(out)

    def remove_key(self, version: int) -> None:
        if int(version) == int(self.get_active_version()):
            raise ConflictError(
                error_code="KEYSTORE_CANNOT_RETIRE_ACTIVE",
                message=f"Cannot retire active key version v{version}",
                details={"version": version},
            )
        self._patch_secret({f"{self._KEY_FIELD_PREFIX}{version}": None})


def _build_keystore() -> KeyStore:
    backend = (os.environ.get("KEYSTORE_BACKEND") or "file").strip().lower()
    if backend == "k8s":
        secret_name = os.environ.get("KEYSTORE_K8S_SECRET", "secret-encryption-keys")
        namespace = os.environ.get("KEYSTORE_K8S_NAMESPACE", "default")
        return K8sSecretKeyStore(secret_name, namespace)
    path = os.environ.get("KEYSTORE_PATH") or _DEFAULT_KEYSTORE_PATH
    return FileKeyStore(path)


@lru_cache
def get_keystore() -> KeyStore:
    """Один KeyStore на процесс. Сбрасывается в тестах через cache_clear()."""
    return _build_keystore()
