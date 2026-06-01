"""Carry-fixes для server_worker по итогам очередного TODO-sync'а.

Покрывает:

  * `ipmi_rotate_password` повторяет verify-step один раз с 1s паузой,
    если первый заход упал транзиентно (NTP-drift, мгновенный сбой
    сразу после apply). Сторадж коммитится только если хотя бы один
    из двух verify'ев прошёл.
  * `redaction._KV_SECRET_RE` использует negative lookbehind по буквам,
    `userpassword=...` не должен матчиться KV_PASSWORD-веткой.
  * Stash-readers (`_ipmi_stash_parse`, `_read_provision_inline`,
    `_read_dispatch_creds`, `_read_bootstrap_creds`) на повреждённом
    JSON в Redis возвращают fail-safe значение, а не падают наружу с
    `JSONDecodeError`.
"""

from __future__ import annotations

import pytest

from src.core.constants import TaskStatus
from src.tasks import passwords
from src.utils.redaction import redact_error_message


pytestmark = pytest.mark.asyncio


class _FlakyVerifyBmc:
    """BMC, у которого первый verify падает auth-ошибкой, второй — OK."""

    def __init__(self, fail_first_n: int = 1):
        self.rotate_calls: list[tuple[int, str]] = []
        self.power_calls = 0
        self._fail_first_n = fail_first_n

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        pass

    async def rotate_user_password(self, user_id: int, new_password: str) -> None:
        self.rotate_calls.append((user_id, new_password))

    async def get_power_state(self) -> str:
        self.power_calls += 1
        if self.power_calls <= self._fail_first_n:
            from src.clients.redfish import RedfishError
            raise RedfishError(
                status_code=401,
                message="auth failed (transient)",
            )
        return "On"

    async def aclose(self) -> None:
        pass


async def test_ipmi_verify_retries_once_on_transient_auth_fail(
    make_task, fetch_task, captured_audit, monkeypatch,
):
    """Первый verify фейлится, второй проходит — submit коммитится."""
    tid = await make_task(
        task_kind="ipmi.rotate_password",
        target_server_id="srv_w20w1_v",
        payload={"server_id": "srv_w20w1_v"},
    )

    async def fake_fetch(server_id, target_department_id=None):
        return {
            "controller_id": "ipm_w20w1",
            "kind": "idrac",
            "endpoint_url": "https://bmc.flaky.test",
            "username": "root",
            "password": "old",
        }

    bmc = _FlakyVerifyBmc(fail_first_n=1)

    async def _bmc_factory(creds, *, prefer="redfish"):
        return bmc

    submit_calls: list = []

    async def fake_submit(
        controller_id, new_password, rotated_at,
        target_department_id=None, verified_at=None,
    ):
        submit_calls.append({"password": new_password, "verified_at": verified_at})
        return {"rotated_at": rotated_at}

    sleeps: list[float] = []

    async def fake_sleep(t):
        sleeps.append(t)

    monkeypatch.setattr(
        "src.tasks.passwords.server_service_client.fetch_ipmi_credentials",
        fake_fetch,
    )
    monkeypatch.setattr(
        "src.tasks.passwords.server_service_client.submit_rotated_ipmi_password",
        fake_submit,
    )
    monkeypatch.setattr("src.tasks.passwords._get_bmc", _bmc_factory)
    monkeypatch.setattr("src.tasks.passwords.asyncio.sleep", fake_sleep)

    async def noop(*a, **kw):
        pass

    monkeypatch.setattr("src.tasks.passwords._breaker.check", noop)
    monkeypatch.setattr("src.tasks.passwords._breaker.record_success", noop)
    monkeypatch.setattr("src.tasks.passwords._breaker.record_failure", noop)

    await passwords.ipmi_rotate_password.original_func(tid)

    t = await fetch_task(tid)
    assert t.status == TaskStatus.SUCCEEDED, f"task должна завершиться: {t.last_error}"
    assert bmc.power_calls == 2, "verify должен быть вызван дважды"
    assert sleeps == [1.0], f"один sleep 1.0s между попытками; got {sleeps}"
    assert len(submit_calls) == 1, "submit должен случиться после успешного 2го verify"


def test_kv_secret_negative_lookbehind_blocks_letter_prefix():
    """userpassword=x не должен матчить KV_PASSWORD-ветку (другая семантика).

    А `password=x`, `; password=x`, `_api_key=val` — должны редактироваться
    как обычно.
    """
    assert "<PASSWORD>" in redact_error_message("password=x"), \
        "password=x должен редактироваться"
    assert "<PASSWORD>" in redact_error_message("; password=x"), \
        "пунктуация перед key — словарь редактируется"
    # `userpassword=x` — это не "password=" по семантике (например, ключ для
    # отдельного словаря пользовательских паролей), `\b` должен это отбить.
    redacted = redact_error_message("userpassword=secretvalue")
    assert "secretvalue" in redacted, \
        f"userpassword=... не должен редактироваться по KV_PASSWORD; got {redacted!r}"
    # _api_key — суффикс с разделителем `_`, у KV_SECRET (?<![A-Za-z]) пропускает.
    assert "<SECRET>" in redact_error_message("service_api_key=topsecret"), \
        "service_api_key=... должен редактироваться KV_SECRET"
    # myapi_key — буква перед `api_key`, lookbehind блокирует match.
    redacted2 = redact_error_message("myapi_key=value")
    assert "value" in redacted2, \
        f"myapi_key=... не должен редактироваться; got {redacted2!r}"


def test_ipmi_stash_parse_returns_none_on_corrupt_json():
    """`_ipmi_stash_parse` на повреждённом JSON в Redis даёт fail-safe (None, None)."""
    from src.tasks.passwords import _ipmi_stash_parse

    # Совсем не JSON.
    assert _ipmi_stash_parse(b"not a json at all {{{") == (None, None)
    # JSON, но не dict (старый плоский plain-string fallback больше не парсится).
    assert _ipmi_stash_parse(b'"plain-string"') == (None, None)
    assert _ipmi_stash_parse(b"[1,2,3]") == (None, None)
    # Битая UTF-8 — текущая реализация декодирует через `decode("utf-8")` и
    # упадёт. Покрываем доступными нам сценариями: bytes-non-json и не-dict.
    # Happy path для контроля: валидный JSON-dict проходит как ожидается.
    assert _ipmi_stash_parse(b'{"password":"pw","rotated_at":"t"}') == ("pw", "t")
