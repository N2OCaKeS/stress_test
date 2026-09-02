"""Lazy re-encrypt при reveal'е: ciphertext, зашифрованный под legacy v2,
должен прозрачно перешиться под новую активную версию v3 при первом чтении.

Сценарии:

* `decrypt_with_metadata` поднимает `needs_reencrypt=True` для ciphertext'а
  под legacy-версией; для актуальной — False.
* `repo.cas_update_secret_encrypted` атомарно меняет blob и возвращает False,
  если ожидаемый blob уже устарел (race).
* `credential_service.reveal` после ротации возвращает корректный plaintext
  и переписывает строку под `v3$`; повторный reveal уже не делает UPDATE.
* Если UPDATE падает (имитируем raise), reveal всё равно возвращает plaintext.
"""

from __future__ import annotations

import base64
import os
from unittest.mock import patch

import pytest

from src.dependencies.auth import Identity
from src.models import Credential
from src.repositories import credentials as repo
from src.services import credential_service, secrets_service


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    from src.core.config import get_settings

    get_settings.cache_clear()  # type: ignore[attr-defined]
    yield
    get_settings.cache_clear()  # type: ignore[attr-defined]


def _identity(user_id: str, dept_id: str = "dep_owner") -> Identity:
    """Identity владельца personal-кред — reveal сразу allowed (owner_match)."""
    return Identity(
        user_id=user_id,
        username=user_id,
        actor_type="user",
        department_id=dept_id,
        allowed_services=["secret_service"],
        service_roles={"secret_service": ["reader"]},
        is_banned=False,
        platform_role=None,
    )


def _bump_to_v3(monkeypatch) -> None:
    """Сменить активную версию ключа на v3, оставив v2-мастер как legacy."""
    from src.core.config import get_settings
    from src.core.keystore import get_keystore

    monkeypatch.setenv(
        "SECRET_ENCRYPTION_KEY__v2",
        os.environ["SECRET_ENCRYPTION_KEY"],
    )
    monkeypatch.setenv("SECRET_ENCRYPTION_KEY_VERSION", "3")
    get_settings.cache_clear()  # type: ignore[attr-defined]
    ks_path = os.environ.get("KEYSTORE_PATH")
    if ks_path and os.path.exists(ks_path):
        os.remove(ks_path)
    get_keystore.cache_clear()  # type: ignore[attr-defined]


# ── decrypt_with_metadata ────────────────────────────────────────────────────


class TestDecryptWithMetadata:
    def test_active_version_no_reencrypt_needed(self):
        aad = secrets_service.aad_for_credential("cred_meta_active")
        token = secrets_service.encrypt("payload", aad=aad)
        result = secrets_service.decrypt_with_metadata(token, aad=aad)
        assert result.plaintext == "payload"
        assert result.source_version == 2
        assert result.needs_reencrypt is False

    def test_legacy_version_flags_reencrypt(self, monkeypatch):
        aad = secrets_service.aad_for_credential("cred_meta_legacy")
        token_v2 = secrets_service.encrypt("legacy-payload", aad=aad)
        assert token_v2.startswith("v2$")

        _bump_to_v3(monkeypatch)

        result = secrets_service.decrypt_with_metadata(token_v2, aad=aad)
        assert result.plaintext == "legacy-payload"
        assert result.source_version == 2
        assert result.needs_reencrypt is True

    def test_decrypt_wrapper_returns_only_plaintext(self):
        aad = secrets_service.aad_for_credential("cred_wrap")
        token = secrets_service.encrypt("hello", aad=aad)
        assert secrets_service.decrypt(token, aad=aad) == "hello"


# ── CAS repo helper ──────────────────────────────────────────────────────────


def _personal_kwargs(cred_id: str, secret_encrypted: str) -> dict:
    return dict(
        id=cred_id,
        name="lazy_jira_" + cred_id[-4:],
        service="jira",
        scope="personal",
        owner_user_id="usr_lazy_owner",
        owner_dept_id=None,
        owner_user_dept_id="dep_owner",
        login="alice",
        secret_encrypted=secret_encrypted,
        status="active",
        created_by="usr_lazy_owner",
    )


@pytest.mark.asyncio
async def test_cas_update_secret_encrypted_swaps_blob(adb):
    aad = secrets_service.aad_for_credential("cred_cas01")
    old = secrets_service.encrypt("orig", aad=aad)
    new = secrets_service.encrypt("orig", aad=aad)
    await repo.create(adb, **_personal_kwargs("cred_cas01", old))
    await adb.commit()

    swapped = await repo.cas_update_secret_encrypted(
        adb,
        cred_id="cred_cas01",
        expected_blob=old,
        new_blob=new,
    )
    assert swapped is True

    fresh = await repo.get_by_id(adb, "cred_cas01")
    assert fresh is not None
    assert fresh.secret_encrypted == new


@pytest.mark.asyncio
async def test_cas_update_returns_false_on_stale_expected(adb):
    aad = secrets_service.aad_for_credential("cred_cas02")
    old = secrets_service.encrypt("orig", aad=aad)
    await repo.create(adb, **_personal_kwargs("cred_cas02", old))
    await adb.commit()

    swapped = await repo.cas_update_secret_encrypted(
        adb,
        cred_id="cred_cas02",
        expected_blob="v2$stale$stale",
        new_blob=secrets_service.encrypt("orig", aad=aad),
    )
    assert swapped is False

    fresh = await repo.get_by_id(adb, "cred_cas02")
    assert fresh is not None
    assert fresh.secret_encrypted == old


# ── reveal lazy re-encrypt end-to-end ────────────────────────────────────────


@pytest.mark.asyncio
async def test_reveal_lazily_reencrypts_under_new_version(adb, monkeypatch):
    """Reveal credential, зашифрованной под v2, при активной v3:

    * возвращает правильный plaintext;
    * заменяет blob в БД на префикс `v3$`;
    * повторный reveal уже не двигает blob (source_version == active).
    """
    aad = secrets_service.aad_for_credential("cred_lazy01")
    token_v2 = secrets_service.encrypt("super-secret", aad=aad)
    assert token_v2.startswith("v2$")
    await repo.create(adb, **_personal_kwargs("cred_lazy01", token_v2))
    await adb.commit()

    _bump_to_v3(monkeypatch)

    login, secret_b64 = await credential_service.reveal(
        adb, _identity("usr_lazy_owner"), "cred_lazy01"
    )
    assert login == "alice"
    assert base64.b64decode(secret_b64).decode("utf-8") == "super-secret"

    fresh = await repo.get_by_id(adb, "cred_lazy01")
    assert fresh is not None
    assert fresh.secret_encrypted.startswith("v3$")
    new_blob = fresh.secret_encrypted

    # Повторный reveal не должен ничего менять — blob уже под v3.
    _, _ = await credential_service.reveal(
        adb, _identity("usr_lazy_owner"), "cred_lazy01"
    )
    fresh2 = await repo.get_by_id(adb, "cred_lazy01")
    assert fresh2 is not None
    assert fresh2.secret_encrypted == new_blob


@pytest.mark.asyncio
async def test_reveal_survives_cas_race(adb, monkeypatch):
    """Если CAS-UPDATE возвращает False (race), reveal всё равно отдаёт plaintext."""
    aad = secrets_service.aad_for_credential("cred_lazy_race")
    token_v2 = secrets_service.encrypt("race-secret", aad=aad)
    await repo.create(adb, **_personal_kwargs("cred_lazy_race", token_v2))
    await adb.commit()

    _bump_to_v3(monkeypatch)

    # Имитируем «кто-то опередил» — CAS возвращает False.
    async def _fake_cas(*args, **kwargs):
        return False

    with patch.object(repo, "cas_update_secret_encrypted", side_effect=_fake_cas):
        login, secret_b64 = await credential_service.reveal(
            adb, _identity("usr_lazy_owner"), "cred_lazy_race"
        )
    assert base64.b64decode(secret_b64).decode("utf-8") == "race-secret"


@pytest.mark.asyncio
async def test_reveal_survives_cas_exception(adb, monkeypatch):
    """Если CAS-UPDATE кидает (БД временно недоступна), reveal не падает."""
    aad = secrets_service.aad_for_credential("cred_lazy_err")
    token_v2 = secrets_service.encrypt("err-secret", aad=aad)
    await repo.create(adb, **_personal_kwargs("cred_lazy_err", token_v2))
    await adb.commit()

    _bump_to_v3(monkeypatch)

    async def _boom(*args, **kwargs):
        raise RuntimeError("db unreachable")

    with patch.object(repo, "cas_update_secret_encrypted", side_effect=_boom):
        login, secret_b64 = await credential_service.reveal(
            adb, _identity("usr_lazy_owner"), "cred_lazy_err"
        )
    assert base64.b64decode(secret_b64).decode("utf-8") == "err-secret"
    # Blob остался под v2 (миграция не прошла), но read-path успешен.
    fresh = await repo.get_by_id(adb, "cred_lazy_err")
    assert fresh is not None
    assert fresh.secret_encrypted.startswith("v2$")
