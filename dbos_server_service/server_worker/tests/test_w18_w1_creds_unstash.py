"""Worker `account.provision` читает inline-creds из dispatch-stash в Redis.

Контракт (W18-W1): server_service кладёт password + ssh_private_key в Redis
под `dbos:dispatch_creds:<id>` с TTL, в payload — только `creds_stash_key`.
Воркер на первой попытке читает stash, копирует creds в task-local stash
(`_PROVISION_INLINE_KEY_PREFIX:<task_id>`) для будущих retry'ев и DEL'ит
dispatch-stash, чтобы plaintext не висел до TTL.

Сценарии:
* первая попытка с валидным dispatch-stash → creds доходят до
  `ssh_client.provision_user`, dispatch-stash удалён, task-local stash
  наполнен;
* dispatch-stash отсутствует (TTL expired / уже прочитан) и task-local
  тоже пуст → fail-fast `DISPATCH_STASH_MISSING`;
* malformed `creds_stash_key` в payload → `DISPATCH_STASH_INVALID`
  (guard от подсовывания `creds_stash_key=":/admin"`).
"""

from __future__ import annotations

import json

import pytest

from src.tasks import users

pytestmark = pytest.mark.asyncio

_ED25519_PUB = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITESTKEY dbos-account"


async def _put_dispatch_stash(stash_key: str, password: str, private_key: str) -> None:
    """Положить creds в Redis под dispatch-stash ключом (через тот же aioredis,
    что использует production-код)."""
    import redis.asyncio as aioredis
    from src.core.config import get_settings
    settings = get_settings()
    client = aioredis.from_url(settings.redis_url)
    try:
        await client.set(
            stash_key,
            json.dumps({
                "password_plaintext": password,
                "ssh_private_key_plaintext": private_key,
            }),
            ex=900,
        )
    finally:
        await client.aclose()


async def _redis_get(key: str) -> bytes | None:
    import redis.asyncio as aioredis
    from src.core.config import get_settings
    settings = get_settings()
    client = aioredis.from_url(settings.redis_url)
    try:
        return await client.get(key)
    finally:
        await client.aclose()


async def test_first_attempt_reads_dispatch_stash_and_deletes_it(
    make_task, monkeypatch,
):
    """Первая попытка: dispatch-stash → ssh_client.provision_user; stash удалён."""
    original_password = "DispatchPwd!42"
    original_private_key = (
        "-----BEGIN OPENSSH PRIVATE KEY-----\nDISPATCH\n"
        "-----END OPENSSH PRIVATE KEY-----\n"
    )
    stash_key = "dbos:dispatch_creds:dcd_w18_first"
    await _put_dispatch_stash(stash_key, original_password, original_private_key)

    tid = await make_task(
        task_kind="account.provision", target_server_id="srv_w18_first",
        payload={
            "server_id": "srv_w18_first", "account_id": "acc_w18_first",
            "login": "ops", "is_managed": True,
            "creds_stash_key": stash_key,
            "ssh_public_key": _ED25519_PUB,
            "force_replace": False,
        },
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
    assert provision_calls[0]["new_password"] == original_password
    assert provision_calls[0]["public_key"] == _ED25519_PUB

    # Dispatch-stash удалён после успешного чтения
    assert await _redis_get(stash_key) is None

    # На успешном submit task-local stash тоже удалён (см. `_delete_provision_inline`)
    p, k = await users._read_provision_inline(tid)
    assert p is None and k is None


async def test_retry_uses_task_local_stash_after_dispatch_stash_deleted(
    make_task, monkeypatch,
):
    """Симулируем retry: dispatch-stash уже удалён, task-local stash жив."""
    original_password = "RetryPwd!9X"
    original_private_key = (
        "-----BEGIN OPENSSH PRIVATE KEY-----\nRETRY\n"
        "-----END OPENSSH PRIVATE KEY-----\n"
    )
    # dispatch-stash отсутствует — его уже подобрала первая попытка
    stash_key = "dbos:dispatch_creds:dcd_w18_retry"
    # task-local stash подложен «как будто первая попытка успела»
    tid = await make_task(
        task_kind="account.provision", target_server_id="srv_w18_retry",
        payload={
            "server_id": "srv_w18_retry", "account_id": "acc_w18_retry",
            "login": "ops", "is_managed": True,
            "creds_stash_key": stash_key,
            "ssh_public_key": _ED25519_PUB,
            "force_replace": False,
        },
    )
    await users._store_provision_inline(tid, original_password, original_private_key)

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
    assert provision_calls[0]["new_password"] == original_password


async def test_missing_stash_fails_fast(make_task, monkeypatch):
    """Dispatch-stash отсутствует, task-local пуст — fail-fast DISPATCH_STASH_MISSING."""
    stash_key = "dbos:dispatch_creds:dcd_w18_missing"
    # Никаких подкладываний — ни dispatch-stash, ни task-local stash
    tid = await make_task(
        task_kind="account.provision", target_server_id="srv_w18_missing",
        payload={
            "server_id": "srv_w18_missing", "account_id": "acc_w18_missing",
            "login": "ops", "is_managed": True,
            "creds_stash_key": stash_key,
            "ssh_public_key": _ED25519_PUB,
            "force_replace": False,
        },
    )

    # ssh_client.provision_user НЕ должен быть вызван
    provision_calls: list[dict] = []

    async def capture_provision(creds, server_id, **kw):
        provision_calls.append(kw)

    monkeypatch.setattr(users.ssh_client, "provision_user", capture_provision)

    async def fake_account_creds(payload, server_id, account_id, target_dept):
        return {"login": "ops", "is_managed": True}

    monkeypatch.setattr(users, "_account_creds", fake_account_creds)

    # run_task ловит исключение и помечает task failed — exception не пробрасывается
    await users.account_provision.original_func(tid)

    # provision не дёрнут
    assert provision_calls == []

    # task в БД — failed (после retry'ев)
    from src.db.session import AsyncSessionLocal
    from src.repositories import task as task_repo
    async with AsyncSessionLocal() as s:
        row = await task_repo.get_by_id(s, tid)
    # либо queued (если retry ещё в очереди), либо failed (исчерпали попытки)
    # Главное — НЕ succeeded
    assert row.status != "succeeded"


async def test_malformed_stash_key_fails_fast(make_task, monkeypatch):
    """`creds_stash_key=":/admin/keys"` отвергается guard'ом."""
    tid = await make_task(
        task_kind="account.provision", target_server_id="srv_w18_bad",
        payload={
            "server_id": "srv_w18_bad", "account_id": "acc_w18_bad",
            "login": "ops", "is_managed": True,
            "creds_stash_key": ":/admin/creds",  # malformed
            "ssh_public_key": _ED25519_PUB,
            "force_replace": False,
        },
    )

    provision_calls: list[dict] = []

    async def capture_provision(creds, server_id, **kw):
        provision_calls.append(kw)

    monkeypatch.setattr(users.ssh_client, "provision_user", capture_provision)

    async def fake_account_creds(payload, server_id, account_id, target_dept):
        return {"login": "ops", "is_managed": True}

    monkeypatch.setattr(users, "_account_creds", fake_account_creds)

    await users.account_provision.original_func(tid)

    assert provision_calls == []


async def test_read_dispatch_creds_invalid_key_raises():
    """`_read_dispatch_creds` guard непосредственно — формат проверяется."""
    with pytest.raises(ValueError):
        await users._read_dispatch_creds(":/admin")
    with pytest.raises(ValueError):
        await users._read_dispatch_creds("dbos:other:foo")
    # Корректный формат — не должен бросать (даже если ключа нет в Redis)
    p, k = await users._read_dispatch_creds("dbos:dispatch_creds:dcd_ok")
    assert p is None and k is None


async def test_delete_dispatch_creds_swallows_malformed_key():
    """Best-effort: malformed key не валит основной поток."""
    # Не должно бросить
    await users._delete_dispatch_creds(":/admin")


async def test_validate_dispatch_creds_key_helper():
    users._validate_dispatch_creds_key("dbos:dispatch_creds:dcd_abc")
    with pytest.raises(ValueError):
        users._validate_dispatch_creds_key("dbos:dispatch_creds:")  # empty id
    with pytest.raises(ValueError):
        users._validate_dispatch_creds_key("dbos:other:foo")
    with pytest.raises(ValueError):
        users._validate_dispatch_creds_key(None)  # type: ignore[arg-type]
