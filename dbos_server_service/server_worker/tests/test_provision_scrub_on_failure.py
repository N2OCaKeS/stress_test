"""Регрессия: scrub inline-секретов на retry-path.

Раньше `scrub_payload_keys` стоял после `submit_provision_status` —
если SSH или callback ломались, plaintext password / private SSH key
оставались в `tasks.payload` до 3 × retry × минуты и больше (до
retention cleanup'а). Оператор с SELECT'ом на worker.tasks мог
выгрести их в окне retry.

После фикса scrub переведён в `finally` блок внутри `_impl`. Этот
тест воспроизводит: provision-handler падает в `ssh_client.provision_user`,
exception всплывает (как ожидает `_runner`), но к этому моменту scrub
уже отработал — `password_plaintext` и `ssh_private_key_plaintext`
замаскированы.
"""

from __future__ import annotations

import pytest

from src.clients.ssh import SshError
from src.db.session import AsyncSessionLocal
from src.repositories import task as task_repo
from src.tasks import users


_ED25519_PUB = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITESTKEY dbos-account"


@pytest.mark.skip(reason="payload больше не несёт plaintext после W21-W1 P0 фикса")
async def test_inline_secrets_scrubbed_when_provision_user_raises(
    make_task, monkeypatch,
):
    """`ssh_client.provision_user` падает → scrub всё равно стирает секреты."""
    original_password = "GenStrongPwd!9X" * 2
    original_private_key = (
        "-----BEGIN OPENSSH PRIVATE KEY-----\nfake\n"
        "-----END OPENSSH PRIVATE KEY-----\n"
    )
    tid = await make_task(
        task_kind="account.provision", target_server_id="srv_fail",
        payload={
            "server_id": "srv_fail", "account_id": "acc_fail",
            "login": "ops",
            "password_plaintext": original_password,
            "ssh_public_key": _ED25519_PUB,
            "ssh_private_key_plaintext": original_private_key,
            "force_replace": False,
        },
    )

    async def fake_fetch(server_id, account_id, target_department_id=None):
        return {"login": "ops", "password": "sess-pwd"}

    monkeypatch.setattr(
        "src.tasks.users.server_service_client.fetch_account_password",
        fake_fetch,
    )

    async def boom_provision(*a, **kw):
        raise SshError("SSH_USERADD_FAILED", "useradd failed")

    monkeypatch.setattr(
        "src.tasks.users.ssh_client.provision_user", boom_provision,
    )

    submit_calls: list = []

    async def fake_submit(*a, **kw):
        submit_calls.append(a)

    monkeypatch.setattr(
        "src.tasks.users.server_service_client.submit_provision_status",
        fake_submit,
    )

    # `_runner` поглощает exception из _impl (mark_pending_for_retry или
    # mark_failed), нам важно состояние payload ПОСЛЕ — scrub в `finally`
    # внутри _impl должен сработать до того, как exception уйдёт в _runner.
    await users.account_provision.original_func(tid)

    # Submit НЕ вызывался — мы упали раньше.
    assert submit_calls == []

    # Главная проверка: секреты в payload замаскированы, даже несмотря
    # на failure в середине _impl.
    async with AsyncSessionLocal() as session:
        t = await task_repo.get_by_id(session, tid)
    assert t is not None
    assert t.payload.get("password_plaintext") == "<scrubbed>"
    assert t.payload.get("ssh_private_key_plaintext") == "<scrubbed>"
    # Не-секретные поля остались.
    assert t.payload.get("ssh_public_key") == _ED25519_PUB
    assert t.payload.get("login") == "ops"


@pytest.mark.skip(reason="payload больше не несёт plaintext после W21-W1 P0 фикса")
async def test_inline_secrets_scrubbed_when_submit_raises(
    make_task, monkeypatch,
):
    """`submit_provision_status` падает → scrub всё равно стирает секреты."""
    original_password = "ProvCredS3cr3t!" * 2
    tid = await make_task(
        task_kind="account.provision", target_server_id="srv_submit_fail",
        payload={
            "server_id": "srv_submit_fail", "account_id": "acc_submit_fail",
            "login": "ops",
            "password_plaintext": original_password,
            "ssh_public_key": _ED25519_PUB,
            "ssh_private_key_plaintext": "PRIVKEY-XXX",
            "force_replace": False,
        },
    )

    async def fake_fetch(server_id, account_id, target_department_id=None):
        return {"login": "ops", "password": "sess-pwd"}

    monkeypatch.setattr(
        "src.tasks.users.server_service_client.fetch_account_password",
        fake_fetch,
    )

    async def ok_provision(*a, **kw):
        return None

    monkeypatch.setattr(
        "src.tasks.users.ssh_client.provision_user", ok_provision,
    )

    async def boom_submit(*a, **kw):
        raise RuntimeError("server_service callback 5xx")

    monkeypatch.setattr(
        "src.tasks.users.server_service_client.submit_provision_status",
        boom_submit,
    )

    await users.account_provision.original_func(tid)

    async with AsyncSessionLocal() as session:
        t = await task_repo.get_by_id(session, tid)
    assert t is not None
    # Failure на submit'е — но scrub отработал.
    assert t.payload.get("password_plaintext") == "<scrubbed>"
    assert t.payload.get("ssh_private_key_plaintext") == "<scrubbed>"
    assert t.payload.get("ssh_public_key") == _ED25519_PUB
