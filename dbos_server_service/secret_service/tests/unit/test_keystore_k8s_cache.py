"""TTL-кэш K8sSecretKeyStore.

Синхронный k8s-API round-trip на каждый крипто-вызов (2-6 на reveal) блокировал
event loop. Кэш схлопывает чтения в TTL-окно; мутации (rotate/retire) кэш
сбрасывают сразу.
"""

from __future__ import annotations

import base64

from src.core.keystore import K8sSecretKeyStore


class _FakeSecret:
    def __init__(self, data: dict[str, str]) -> None:
        self.data = data


class _FakeApi:
    """Мок CoreV1Api: считает read/patch и держит data как base64-мапу."""

    def __init__(self, data: dict[str, str]) -> None:
        self._data = dict(data)
        self.reads = 0
        self.patches = 0

    def read_namespaced_secret(self, name, namespace):
        self.reads += 1
        return _FakeSecret(dict(self._data))

    def patch_namespaced_secret(self, name, namespace, body):
        self.patches += 1
        for key, value in body["data"].items():
            if value is None:
                self._data.pop(key, None)
            else:
                self._data[key] = value


def _b64(value: str) -> str:
    return base64.b64encode(value.encode()).decode("ascii")


def _make_ks(monkeypatch, data):
    fake = _FakeApi(data)
    monkeypatch.setattr(K8sSecretKeyStore, "_build_api", lambda self: fake)
    ks = K8sSecretKeyStore("secret-encryption-keys", "default")
    return ks, fake


def test_cache_collapses_multiple_reads(monkeypatch):
    ks, fake = _make_ks(
        monkeypatch,
        {"active_version": _b64("2"), "key_v2": _b64("material-2")},
    )
    assert ks.get_active_version() == 2
    assert ks.get_key(2) == b"material-2"
    assert ks.list_versions() == [2]
    # Три обращения — один реальный round-trip в пределах TTL.
    assert fake.reads == 1


def test_mutation_invalidates_cache(monkeypatch):
    ks, fake = _make_ks(
        monkeypatch,
        {"active_version": _b64("2"), "key_v2": _b64("material-2")},
    )
    assert ks.get_active_version() == 2  # прогрели кэш

    ks.set_key(3, "material-3")
    ks.set_active(3)

    # Кэш сброшен мутацией — новая активная версия видна сразу.
    assert ks.get_active_version() == 3
    assert 3 in ks.list_versions()


def test_remove_key_invalidates_cache(monkeypatch):
    ks, fake = _make_ks(
        monkeypatch,
        {
            "active_version": _b64("3"),
            "key_v2": _b64("material-2"),
            "key_v3": _b64("material-3"),
        },
    )
    assert ks.list_versions() == [2, 3]  # прогрели кэш

    ks.remove_key(2)

    # Свежий list не видит выведенную версию (кэш сброшен).
    assert ks.list_versions() == [3]
