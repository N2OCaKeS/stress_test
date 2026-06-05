"""Тесты: pre-check breaker'а перед verify-шагом ipmi_rotate_password.

Сценарий, который чинится: apply на BMC прошёл успешно
(`record_success`), но между apply и verify соседняя replica worker'а
успела сбить breaker для этого host'а в `open` (накатила свой fail-
бюджет на параллельной задаче). Если на верху verify-секции стоит
`_breaker.check(host)` — он бросит `CircuitBreakerOpenError`. Этот
exception НЕ ловится `except (RedfishError, IpmitoolError)`, уходит
наружу мимо `BMC_VERIFY_AFTER_ROTATE_FAILED`-перевода, `submit_rotated_
ipmi_password` не вызывается → BMC стоит c новым паролем, storage
помнит старый, ciphertext'ы расходятся.

Чиним: убираем pre-check перед verify. Сам verify-dispatch всё ещё
прогоняет `record_failure`/`record_success` в обработчике, breaker
видит реальные сбои.
"""

from __future__ import annotations

import pytest

from src.core.constants import TaskStatus
from src.tasks import passwords
from tests._helpers.bmc_mocks import FakeBmc as _FakeBmc


pytestmark = pytest.mark.asyncio


async def test_breaker_check_not_called_before_verify(
    make_task, fetch_task, captured_audit, monkeypatch,
):
    """`_breaker.check` должна звониться один раз — перед apply, не перед verify.

    Между apply (record_success) и verify breaker открыться от чужой реплики
    может — но мы сознательно не дёргаем pre-check, чтобы такой race не
    привёл к storage drift'у. Сам verify-вызов всё ещё гонит
    `record_failure`/`record_success` в except-блоке.
    """
    tid = await make_task(
        task_kind="ipmi.rotate_password",
        target_server_id="srv_brk1",
        payload={"server_id": "srv_brk1"},
    )

    async def fake_fetch(server_id, target_department_id=None):
        return {
            "controller_id": "ipm_brk1",
            "kind": "idrac",
            "endpoint_url": "https://bmc.brk1.test",
            "username": "root",
            "password": "old",
        }

    bmc = _FakeBmc()

    async def _bmc_factory(creds, *, prefer="redfish"):
        return bmc

    submit_calls: list = []

    async def fake_submit(
        controller_id, new_password, rotated_at,
        target_department_id=None, verified_at=None,
    ):
        submit_calls.append({"password": new_password, "rotated_at": rotated_at})
        return {"rotated_at": rotated_at}

    check_calls: list[str] = []
    record_success_calls: list[str] = []
    record_failure_calls: list[str] = []

    async def spy_check(host: str) -> None:
        check_calls.append(host)

    async def spy_record_success(host: str) -> None:
        record_success_calls.append(host)

    async def spy_record_failure(host: str) -> None:
        record_failure_calls.append(host)

    monkeypatch.setattr(
        "src.tasks.passwords.server_service_client.fetch_ipmi_credentials",
        fake_fetch,
    )
    monkeypatch.setattr(
        "src.tasks.passwords.server_service_client.submit_rotated_ipmi_password",
        fake_submit,
    )
    monkeypatch.setattr("src.tasks.passwords._get_bmc", _bmc_factory)
    monkeypatch.setattr("src.tasks.passwords._breaker.check", spy_check)
    monkeypatch.setattr(
        "src.tasks.passwords._breaker.record_success", spy_record_success,
    )
    monkeypatch.setattr(
        "src.tasks.passwords._breaker.record_failure", spy_record_failure,
    )

    await passwords.ipmi_rotate_password.original_func(tid)

    t = await fetch_task(tid)
    assert t.status == TaskStatus.SUCCEEDED

    # check — ровно один раз (apply). Не дважды (apply + verify).
    assert check_calls == ["bmc.brk1.test"], (
        f"`_breaker.check` должна звониться один раз перед apply; "
        f"got {check_calls}"
    )
    # apply прошёл успешно → record_success. verify тоже прошёл — второй
    # record_success. record_failure не звался.
    assert record_success_calls.count("bmc.brk1.test") == 2
    assert record_failure_calls == []
    # submit состоялся — storage и BMC синхронны.
    assert len(submit_calls) == 1


async def test_breaker_open_between_apply_and_verify_does_not_block_submit(
    make_task, fetch_task, captured_audit, monkeypatch,
):
    """Симулируем race: первый `check` (перед apply) даёт closed; если бы
    pre-check перед verify остался — второй вызов открыл бы breaker и
    flow ушёл бы в `BMC_CIRCUIT_OPEN` без submit'а. С фиксом второй check
    не зовётся, verify работает, submit состаивается.
    """
    from src.services.bmc_circuit_breaker import CircuitBreakerOpenError

    tid = await make_task(
        task_kind="ipmi.rotate_password",
        target_server_id="srv_brk2",
        payload={"server_id": "srv_brk2"},
    )

    async def fake_fetch(server_id, target_department_id=None):
        return {
            "controller_id": "ipm_brk2",
            "kind": "idrac",
            "endpoint_url": "https://bmc.brk2.test",
            "username": "root",
            "password": "old",
        }

    bmc = _FakeBmc()

    async def _bmc_factory(creds, *, prefer="redfish"):
        return bmc

    submit_calls: list = []

    async def fake_submit(
        controller_id, new_password, rotated_at,
        target_department_id=None, verified_at=None,
    ):
        submit_calls.append({"password": new_password})
        return {"rotated_at": rotated_at}

    # check: первый вызов (apply) — closed; любой следующий вызов
    # имитирует race и бросает CircuitBreakerOpenError. С фиксом второго
    # вызова просто не будет — submit пройдёт штатно.
    check_call_count = {"n": 0}

    async def flaky_check(host: str) -> None:
        check_call_count["n"] += 1
        if check_call_count["n"] >= 2:
            raise CircuitBreakerOpenError(host, retry_after_seconds=30)

    async def noop_record(host: str) -> None:
        pass

    monkeypatch.setattr(
        "src.tasks.passwords.server_service_client.fetch_ipmi_credentials",
        fake_fetch,
    )
    monkeypatch.setattr(
        "src.tasks.passwords.server_service_client.submit_rotated_ipmi_password",
        fake_submit,
    )
    monkeypatch.setattr("src.tasks.passwords._get_bmc", _bmc_factory)
    monkeypatch.setattr("src.tasks.passwords._breaker.check", flaky_check)
    monkeypatch.setattr("src.tasks.passwords._breaker.record_success", noop_record)
    monkeypatch.setattr("src.tasks.passwords._breaker.record_failure", noop_record)

    await passwords.ipmi_rotate_password.original_func(tid)

    t = await fetch_task(tid)
    assert t.status == TaskStatus.SUCCEEDED, (
        f"flaky breaker между apply и verify не должен валить задачу; "
        f"got status={t.status}, last_error={t.last_error}"
    )
    # check звался ровно один раз (apply). Если бы pre-check перед verify
    # вернулся — был бы второй вызов и CircuitBreakerOpenError.
    assert check_call_count["n"] == 1, (
        f"`_breaker.check` должна звониться один раз; got {check_call_count['n']}"
    )
    assert len(submit_calls) == 1, "submit должен пройти, storage синхронен с BMC"
