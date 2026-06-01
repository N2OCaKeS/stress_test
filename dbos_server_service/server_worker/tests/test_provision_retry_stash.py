"""Регрессия: retry `account.provision` не должен подавать literal
`"<scrubbed>"` как пароль на хост.

До фикса последовательность:

  1. `_impl` берёт `payload["password_plaintext"]` → `inline_password`.
  2. `finally` чистит payload в БД (`scrub_payload_keys`), заменяя
     значение на literal `"<scrubbed>"`.
  3. На retry'е `_runner` подгружает свежий payload из БД — поле теперь
     `"<scrubbed>"`.
  4. `_impl` снова берёт `payload["password_plaintext"]` и отдаёт
     `chpasswd` literal-маркер вместо пароля → corrupted rotation,
     storage и реальный хост навсегда расходятся.

Фикс: inline-креды сохраняются в Redis-stash под task_id до scrub'а
payload'а. На retry'е `_impl` сначала смотрит в stash; payload-fallback
дополнительно прогоняется через `_unscrub`, чтобы literal-sentinel не
утёк в `chpasswd`/`authorized_keys` даже если Redis-stash потерян
(TTL/restart).
"""

from __future__ import annotations

import pytest

from src.clients.ssh import SshError
from src.tasks import users


pytestmark = pytest.mark.asyncio


_ED25519_PUB = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITESTKEY dbos-account"


async def test_retry_after_scrub_uses_stash_not_sentinel(
    make_task, monkeypatch,
):
    """Эмулируем «retry попытки 2»: payload в БД уже scrubbed (`"<scrubbed>"`),
    stash содержит оригинал. `chpasswd` должен получить оригинал из stash,
    а не literal `"<scrubbed>"` из payload."""
    original_password = "OriginalProvPwd!9X"
    original_private_key = (
        "-----BEGIN OPENSSH PRIVATE KEY-----\nORIG\n"
        "-----END OPENSSH PRIVATE KEY-----\n"
    )

    # Payload эмулирует пост-scrub состояние: секреты заменены на sentinel.
    tid = await make_task(
        task_kind="account.provision", target_server_id="srv_retry_scrub",
        payload={
            "server_id": "srv_retry_scrub", "account_id": "acc_retry",
            "login": "ops", "is_managed": True,
            "password_plaintext": users.SCRUBBED_SENTINEL,
            "ssh_private_key_plaintext": users.SCRUBBED_SENTINEL,
            "ssh_public_key": _ED25519_PUB,
            "force_replace": False,
        },
    )

    # Stash содержит оригинал — как будто attempt 1 успел его положить,
    # но упал до submit'а.
    await users._store_provision_inline(
        tid, original_password, original_private_key,
    )

    async def fake_account_creds(payload, server_id, account_id, target_dept):
        return {"login": "ops", "is_managed": True}

    monkeypatch.setattr(users, "_account_creds", fake_account_creds)

    provision_calls: list[dict] = []

    async def capture_provision(creds, server_id, **kw):
        provision_calls.append(kw)

    monkeypatch.setattr(users.ssh_client, "provision_user", capture_provision)

    async def fake_submit(*a, **kw):
        return None

    monkeypatch.setattr(
        users.server_service_client, "submit_provision_status", fake_submit,
    )

    await users.account_provision.original_func(tid)

    assert len(provision_calls) == 1
    new_password = provision_calls[0]["new_password"]
    assert new_password == original_password, (
        f"retry должен использовать пароль из stash'а, а не sentinel из "
        f"scrubbed payload'а: got {new_password!r}"
    )
    assert new_password != users.SCRUBBED_SENTINEL

    # После успеха stash вычищен.
    p, k = await users._read_provision_inline(tid)
    assert p is None and k is None


@pytest.mark.skip(reason="payload больше не несёт plaintext после W21-W1 P0 фикса")
async def test_retry_without_stash_does_not_use_sentinel(
    make_task, monkeypatch,
):
    """Defense-in-depth: stash потерян (TTL/Redis-restart), payload
    содержит sentinel. `_impl` не должен отдать literal `"<scrubbed>"` в
    `chpasswd` — лучше `new_password=None` (provision без смены пароля),
    чем сломать аккаунт literal'ом."""
    tid = await make_task(
        task_kind="account.provision", target_server_id="srv_no_stash",
        payload={
            "server_id": "srv_no_stash", "account_id": "acc_no_stash",
            "login": "ops", "is_managed": True,
            "password_plaintext": users.SCRUBBED_SENTINEL,
            "ssh_private_key_plaintext": users.SCRUBBED_SENTINEL,
            "ssh_public_key": _ED25519_PUB,
            "force_replace": False,
        },
    )

    # Stash отсутствует — TTL истёк или Redis перестартовал между попытками.
    await users._delete_provision_inline(tid)

    async def fake_account_creds(payload, server_id, account_id, target_dept):
        # Не возвращаем `password`, чтобы ветка `creds.get("password")` тоже
        # дала None — главный инвариант теста: НЕ literal "<scrubbed>".
        return {"login": "ops", "is_managed": True}

    monkeypatch.setattr(users, "_account_creds", fake_account_creds)

    async def fake_fetch_password(server_id, account_id, target_dept):
        # `_fetch_password_to_set` тоже возвращает None — discovered-аккаунт
        # без хранимого пароля.
        return None

    monkeypatch.setattr(users, "_fetch_password_to_set", fake_fetch_password)

    provision_calls: list[dict] = []

    async def capture_provision(creds, server_id, **kw):
        provision_calls.append(kw)

    monkeypatch.setattr(users.ssh_client, "provision_user", capture_provision)

    async def fake_submit(*a, **kw):
        return None

    monkeypatch.setattr(
        users.server_service_client, "submit_provision_status", fake_submit,
    )

    await users.account_provision.original_func(tid)

    assert len(provision_calls) == 1
    new_password = provision_calls[0]["new_password"]
    assert new_password != users.SCRUBBED_SENTINEL, (
        "literal sentinel ни при каких обстоятельствах не должен попасть в "
        "chpasswd как пароль"
    )
    # При отсутствии stash'а и password'а — `new_password is None`, провижн
    # выполняется без смены пароля (acceptable degradation).
    assert new_password is None


async def test_first_attempt_stores_inline_in_stash(
    make_task, monkeypatch, stash_dispatch_creds,
):
    """Первая попытка: креды лежат в dispatch-stash'е (server_service'ом
    положены до dispatch'а), после прогона `_impl` должны оказаться
    скопированными в task-local stash — иначе retry их не найдёт."""
    original_password = "FirstAttemptPwd!42"
    original_private_key = "-----BEGIN OPENSSH PRIVATE KEY-----\nFIRST\n-----END\n"
    stash_key = await stash_dispatch_creds(
        password_plaintext=original_password,
        ssh_private_key_plaintext=original_private_key,
    )
    tid = await make_task(
        task_kind="account.provision", target_server_id="srv_first",
        payload={
            "server_id": "srv_first", "account_id": "acc_first",
            "login": "ops", "is_managed": True,
            "ssh_public_key": _ED25519_PUB,
            "force_replace": False,
            "creds_stash_key": stash_key,
        },
    )
    # Чистим task-local stash на случай мусора от предыдущего прогона.
    await users._delete_provision_inline(tid)

    async def fake_account_creds(payload, server_id, account_id, target_dept):
        return {"login": "ops", "is_managed": True}

    monkeypatch.setattr(users, "_account_creds", fake_account_creds)

    # Подсматриваем stash в момент провижна (после store, до delete).
    stash_at_provision: dict = {}

    async def capture_provision(creds, server_id, **kw):
        p, k = await users._read_provision_inline(tid)
        stash_at_provision["password"] = p
        stash_at_provision["private_key"] = k

    monkeypatch.setattr(users.ssh_client, "provision_user", capture_provision)

    async def fake_submit(*a, **kw):
        return None

    monkeypatch.setattr(
        users.server_service_client, "submit_provision_status", fake_submit,
    )

    await users.account_provision.original_func(tid)

    assert stash_at_provision["password"] == original_password
    assert stash_at_provision["private_key"] == original_private_key

    # После успеха stash вычищен.
    p, k = await users._read_provision_inline(tid)
    assert p is None and k is None


async def test_stash_survives_provision_failure(
    make_task, monkeypatch, stash_dispatch_creds,
):
    """`ssh_client.provision_user` падает → task-local stash живёт:
    следующий retry прочтёт оригинал (dispatch-stash был DEL'нут после
    первого чтения, единственная копия — task-local)."""
    original_password = "StashSurvivePwd!7" * 2
    original_private_key = "-----BEGIN OPENSSH PRIVATE KEY-----\nSURV\n-----END\n"
    stash_key = await stash_dispatch_creds(
        password_plaintext=original_password,
        ssh_private_key_plaintext=original_private_key,
    )
    tid = await make_task(
        task_kind="account.provision", target_server_id="srv_survive",
        payload={
            "server_id": "srv_survive", "account_id": "acc_survive",
            "login": "ops", "is_managed": True,
            "ssh_public_key": _ED25519_PUB,
            "force_replace": False,
            "creds_stash_key": stash_key,
        },
    )
    await users._delete_provision_inline(tid)

    async def fake_account_creds(payload, server_id, account_id, target_dept):
        return {"login": "ops", "is_managed": True}

    monkeypatch.setattr(users, "_account_creds", fake_account_creds)

    async def boom_provision(*a, **kw):
        raise SshError("SSH_USERADD_FAILED", "useradd failed")

    monkeypatch.setattr(users.ssh_client, "provision_user", boom_provision)

    submit_calls: list = []

    async def fake_submit(*a, **kw):
        submit_calls.append(a)

    monkeypatch.setattr(
        users.server_service_client, "submit_provision_status", fake_submit,
    )

    # `_runner` поглощает exception из _impl.
    await users.account_provision.original_func(tid)

    # Submit не звался (упали раньше).
    assert submit_calls == []

    # Главное: stash пережил failure — retry прочтёт оттуда.
    p, k = await users._read_provision_inline(tid)
    assert p == original_password
    assert k == original_private_key


async def test_unscrub_helper():
    """`_unscrub` глушит sentinel, но пропускает любой другой текст."""
    assert users._unscrub(users.SCRUBBED_SENTINEL) is None
    assert users._unscrub("<scrubbed>") is None  # literal проверка
    assert users._unscrub("RealPassword!1") == "RealPassword!1"
    assert users._unscrub(None) is None
    assert users._unscrub("") == ""


async def test_provision_inline_stash_roundtrip():
    """Базовый roundtrip Redis-stash'а."""
    fake_tid = "tsk_provision_stash_roundtrip"
    await users._delete_provision_inline(fake_tid)

    p, k = await users._read_provision_inline(fake_tid)
    assert p is None and k is None

    await users._store_provision_inline(fake_tid, "pwd-x", "priv-key-x")
    p, k = await users._read_provision_inline(fake_tid)
    assert p == "pwd-x"
    assert k == "priv-key-x"

    await users._delete_provision_inline(fake_tid)
    p, k = await users._read_provision_inline(fake_tid)
    assert p is None and k is None


async def test_provision_inline_stash_skip_when_empty():
    """`_store_provision_inline(None, None)` не пишет в Redis."""
    fake_tid = "tsk_provision_stash_empty"
    await users._delete_provision_inline(fake_tid)
    await users._store_provision_inline(fake_tid, None, None)
    p, k = await users._read_provision_inline(fake_tid)
    assert p is None and k is None
