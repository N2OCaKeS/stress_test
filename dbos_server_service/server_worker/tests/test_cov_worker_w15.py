"""Coverage — пять точечных пробелов.

GAP-1  _FORBIDDEN_HOMES: Python-guard (target_home=) через _write_authorized_key
        и bootstrap; подтверждение, что ssh_public_key НЕ в scrub-листе provision.
GAP-2  backoff cap: граничный случай attempts == _BACKOFF_EXPONENT_CAP (=16),
        attempts == cap-1, cap+1; поведение `_compute_backoff_delay` вблизи _RETRY_MAX.
GAP-3  audit_client DLQ: double-prefix guard в `_send_to_dlq` — повторный вызов
        на строке с уже существующим [DLQ:...] prefix'ом не добавляет второй;
        truncate at LAST_ERROR_MAX_LEN с prefix'ом.
GAP-4  login guard: `_account_creds` already covered; добавляем missed branch —
        managed-ветка с is_managed=True, login задан, но apply_session_hints
        пробрасывает host из payload.
GAP-5  ssh_private_key stash: структурная проверка scrub-листа в account_provision
        (contains both keys, NOT ssh_public_key).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.clients.ssh import SshClient, SshError, _FORBIDDEN_HOMES
from tests._ssh_mock_helpers import make_conn, run_result


# ══════════════════════════════════════════════════════════════════════════════
# GAP-1  _FORBIDDEN_HOMES: python-level guard через _write_authorized_key и
#        bootstrap; ssh_public_key вне scrub-листа
# ══════════════════════════════════════════════════════════════════════════════

_PUBKEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIW15Key w15@test"


def _client_with_conn(run_results):
    ssh = SshClient(host="10.0.0.9", username="dbos", password="pwd")
    ssh._conn = make_conn(run_results)
    return ssh


class TestForbiddenHomesViaWriteAuthorizedKey:
    """_write_authorized_key → _install_authorized_key с явным target_home."""

    @pytest.mark.parametrize(
        "forbidden_home",
        sorted(_FORBIDDEN_HOMES),
    )
    async def test_write_authorized_key_rejects_forbidden_home(self, forbidden_home):
        """_write_authorized_key с явным target_home из _FORBIDDEN_HOMES → SSH_INVALID_HOME.

        Guard активен в production: `tasks/users.py::account_provision`
        прокидывает `home_dir=payload.get("home_dir")` через `provision_user` в
        `create_user`/`_write_authorized_key`, и оператор/server_service может
        прислать в payload системный путь. Этот тест фиксирует поведение, чтобы
        ослабление guard'а на refactor'е не прошло тихо.
        """
        ssh = _client_with_conn([])
        with pytest.raises(SshError) as exc:
            await ssh._install_authorized_key(
                target_user="nobody",
                public_key=_PUBKEY,
                truncate=False,
                error_code="SSH_AUTHORIZED_KEYS_FAILED",
                target_home=forbidden_home,
            )
        assert exc.value.error_code == "SSH_INVALID_HOME", (
            f"ожидали SSH_INVALID_HOME для home={forbidden_home!r}, "
            f"получили {exc.value.error_code!r}"
        )
        ssh._conn.run.assert_not_awaited()

    async def test_unknown_home_passes_python_guard(self):
        """/home/realuser не в _FORBIDDEN_HOMES → Python-guard молчит."""
        ssh = _client_with_conn([run_result("", "", 0)])
        await ssh._install_authorized_key(
            target_user="realuser",
            public_key=_PUBKEY,
            truncate=False,
            error_code="SSH_AUTHORIZED_KEYS_FAILED",
            target_home="/home/realuser",
        )
        ssh._conn.run.assert_awaited_once()

    async def test_none_target_home_skips_python_guard(self):
        """Без target_home Python-guard не активируется — проверка на bash-стороне."""
        ssh = _client_with_conn([run_result("", "", 0)])
        await ssh._install_authorized_key(
            target_user="dbos",
            public_key=_PUBKEY,
            truncate=False,
            error_code="SSH_AUTHORIZED_KEYS_FAILED",
            target_home=None,
        )
        ssh._conn.run.assert_awaited_once()

    def test_forbidden_homes_set_non_empty(self):
        """Константа не пуста — удаление из _FORBIDDEN_HOMES не прошло тихо."""
        assert len(_FORBIDDEN_HOMES) >= 6, (
            "_FORBIDDEN_HOMES должна содержать минимум 6 системных путей"
        )


class TestScrubListDoesNotScrubPublicKey:
    """ssh_public_key НЕ попадает в scrub-лист account_provision.

    По комментарию в коде: public_key — не секрет, оставляем для форенсики.
    Если кто-то ошибочно добавит "ssh_public_key" в список scrub — этот тест
    упадёт, и намеренное решение станет явным.
    """

    def test_scrub_list_contains_private_not_public(self):
        """Scrub-лист provision включает private_key и пароль, но не public_key."""
        import ast
        import inspect
        from src.tasks import users

        source = inspect.getsource(users.account_provision)
        tree = ast.parse(source)
        scrub_keys: list[str] = []
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "scrub_payload_keys"
            ):
                # Третий позиционный аргумент — список ключей (session, task_id, [...]).
                for arg in node.args:
                    if isinstance(arg, ast.List):
                        for elt in arg.elts:
                            if isinstance(elt, ast.Constant):
                                scrub_keys.append(elt.value)

        assert scrub_keys, "scrub_payload_keys с листом ключей не найден в account_provision"
        assert "ssh_private_key_plaintext" in scrub_keys, (
            f"private key должен быть в scrub-листе, нашли: {scrub_keys}"
        )
        assert "password_plaintext" in scrub_keys, (
            f"password должен быть в scrub-листе, нашли: {scrub_keys}"
        )
        assert "ssh_public_key" not in scrub_keys, (
            "public_key НЕ должен быть в scrub-листе — он нужен для форенсики"
        )


# ══════════════════════════════════════════════════════════════════════════════
# GAP-2  backoff cap: граничные значения _BACKOFF_EXPONENT_CAP
# ══════════════════════════════════════════════════════════════════════════════


class TestBackoffExponentCapBoundary:
    """Граничные значения attempts вокруг _BACKOFF_EXPONENT_CAP."""

    def _backoff_seconds(self, attempts: int) -> float:
        from src.models import AuditOutbox
        from src.services.audit_outbox_publisher import _apply_backoff

        row = AuditOutbox(task_id=None, payload={"action": "x"})
        row.attempts = attempts
        _apply_backoff(row)
        assert row.next_retry_at is not None
        return (row.next_retry_at - datetime.now(timezone.utc)).total_seconds()

    def test_attempts_at_exact_exponent_cap(self):
        """attempts == _BACKOFF_EXPONENT_CAP → delay == _BACKOFF_MAX_SECONDS (300s)."""
        from src.services.audit_outbox_publisher import (
            _BACKOFF_EXPONENT_CAP,
            _BACKOFF_MAX_SECONDS,
        )
        delta = self._backoff_seconds(_BACKOFF_EXPONENT_CAP)
        # 2^16=65536 → обрезается до 300s.
        assert abs(delta - _BACKOFF_MAX_SECONDS) <= 5, (
            f"при attempts={_BACKOFF_EXPONENT_CAP} ожидали ~{_BACKOFF_MAX_SECONDS}s, "
            f"получили {delta:.1f}s"
        )

    def test_attempts_just_below_exponent_cap(self):
        """attempts == _BACKOFF_EXPONENT_CAP - 1 → raw delay < _BACKOFF_MAX_SECONDS."""
        from src.services.audit_outbox_publisher import (
            _BACKOFF_EXPONENT_CAP,
            _BACKOFF_MAX_SECONDS,
        )
        below = _BACKOFF_EXPONENT_CAP - 1
        delta = self._backoff_seconds(below)
        # 2^15=32768 > 300 → всё равно capped в 300s (бизнес-cap).
        assert abs(delta - _BACKOFF_MAX_SECONDS) <= 5, (
            f"при attempts={below} ожидали ~{_BACKOFF_MAX_SECONDS}s, "
            f"получили {delta:.1f}s"
        )

    def test_attempts_just_above_exponent_cap(self):
        """attempts == _BACKOFF_EXPONENT_CAP + 1 → delay не превышает _BACKOFF_MAX_SECONDS."""
        from src.services.audit_outbox_publisher import (
            _BACKOFF_EXPONENT_CAP,
            _BACKOFF_MAX_SECONDS,
        )
        above = _BACKOFF_EXPONENT_CAP + 1
        delta = self._backoff_seconds(above)
        assert delta <= _BACKOFF_MAX_SECONDS + 5, (
            f"при attempts={above} delay вышел за _BACKOFF_MAX_SECONDS: {delta:.1f}s"
        )

    def test_small_attempts_grow_exponentially_before_cap(self):
        """Первые attempts действительно растут по 2^n — cap не режет ранние значения."""
        from src.services.audit_outbox_publisher import _BACKOFF_MAX_SECONDS

        prev = None
        for att in range(1, 9):
            delta = self._backoff_seconds(att)
            if prev is not None and delta < _BACKOFF_MAX_SECONDS - 5:
                assert delta > prev * 1.5, (
                    f"delta должна расти экспоненциально: attempts={att}, "
                    f"prev={prev:.1f}s, cur={delta:.1f}s"
                )
            prev = delta


class TestComputeBackoffDelay:
    """_compute_backoff_delay (task retry backoff) — граничные случаи."""

    def test_cap_exact_value(self):
        """10 * 2^9 = 5120 → обрезается до 300s."""
        from src.tasks._runner import _compute_backoff_delay
        assert _compute_backoff_delay(10) == 300.0

    def test_first_attempt_base_delay(self):
        """attempt=1 → 10 * 2^0 = 10s."""
        from src.tasks._runner import _compute_backoff_delay
        assert _compute_backoff_delay(1) == 10.0

    def test_second_attempt(self):
        """attempt=2 → 10 * 2^1 = 20s."""
        from src.tasks._runner import _compute_backoff_delay
        assert _compute_backoff_delay(2) == 20.0

    def test_cap_constant_relationship(self):
        """_RETRY_MAX_DELAY_SECONDS <= потолку task-retry schedule."""
        from src.tasks._runner import _RETRY_BASE_DELAY_SECONDS, _RETRY_MAX_DELAY_SECONDS
        assert _RETRY_MAX_DELAY_SECONDS >= _RETRY_BASE_DELAY_SECONDS, (
            "max delay должен быть не меньше base delay"
        )
        assert _RETRY_MAX_DELAY_SECONDS == 300.0, (
            "потолок retry-delay зафиксирован как 300s"
        )


# ══════════════════════════════════════════════════════════════════════════════
# GAP-3  audit_client DLQ: double-prefix guard и truncation в _send_to_dlq
# ══════════════════════════════════════════════════════════════════════════════


class TestSendToDlqDoublePrefix:
    """_send_to_dlq не добавляет второй [DLQ:...] если prefix уже есть."""

    def _make_dlq_row(self, last_error: str | None = None):
        from src.models import AuditOutbox
        row = AuditOutbox(task_id="task-test", payload={"action": "x"})
        row.attempts = 3
        row.last_error = last_error
        return row

    def test_fresh_row_gets_dlq_prefix(self):
        """Строка без prefix'а получает [DLQ:<reason>] в начале last_error."""
        from src.services.audit_outbox_publisher import (
            _reset_breaker_state,
            _send_to_dlq,
        )
        _reset_breaker_state()
        row = self._make_dlq_row(last_error="some prior error")
        _send_to_dlq(row, reason="attempts_cap")
        assert row.last_error.startswith("[DLQ:attempts_cap]"), (
            f"ожидали [DLQ:attempts_cap] в начале, got={row.last_error!r}"
        )
        assert "some prior error" in row.last_error

    def test_already_dlq_row_not_double_prefixed(self):
        """Строка уже с [DLQ:...] не получает второй prefix при повторном вызове.

        Сценарий: re_attempt_row оставил [DLQ:permanent_4xx] в last_error
        (partial reset не очищает prefix), затем строка снова попадает в
        _send_to_dlq (например, через attempts_cap на следующем прогоне).
        Guard `if not existing.startswith("[DLQ:")` должен пропустить prefix-вставку.
        """
        from src.services.audit_outbox_publisher import (
            _reset_breaker_state,
            _send_to_dlq,
        )
        _reset_breaker_state()
        existing_prefix = "[DLQ:permanent_4xx] loging_service 422"
        row = self._make_dlq_row(last_error=existing_prefix)
        _send_to_dlq(row, reason="attempts_cap")
        # Должен быть ровно один [DLQ: в начале.
        assert row.last_error.count("[DLQ:") == 1, (
            f"двойного prefix'а быть не должно: {row.last_error!r}"
        )
        assert row.last_error.startswith("[DLQ:permanent_4xx]"), (
            "оригинальный prefix должен остаться, не перезаписываться"
        )

    def test_dlq_counter_incremented_on_double_prefix_skip(self):
        """DLQ-счётчик растёт при повторном _send_to_dlq, даже если prefix skip'нут."""
        from src.services.audit_outbox_publisher import (
            _reset_breaker_state,
            _send_to_dlq,
            get_dlq_total,
        )
        _reset_breaker_state()
        row = self._make_dlq_row(last_error="[DLQ:missing_action] old")
        before = get_dlq_total()
        _send_to_dlq(row, reason="attempts_cap")
        assert get_dlq_total() == before + 1, (
            "счётчик должен расти даже при skip'е prefix'а"
        )

    def test_last_error_truncated_to_max_len(self):
        """last_error обрезается до LAST_ERROR_MAX_LEN с учётом prefix'а."""
        from src.core.constants import LAST_ERROR_MAX_LEN
        from src.services.audit_outbox_publisher import (
            _reset_breaker_state,
            _send_to_dlq,
        )
        _reset_breaker_state()
        long_error = "X" * (LAST_ERROR_MAX_LEN + 100)
        row = self._make_dlq_row(last_error=long_error)
        _send_to_dlq(row, reason="permanent_4xx")
        assert len(row.last_error) <= LAST_ERROR_MAX_LEN, (
            f"last_error длиннее {LAST_ERROR_MAX_LEN}: {len(row.last_error)}"
        )
        assert row.last_error.startswith("[DLQ:permanent_4xx]")

    def test_none_last_error_no_crash(self):
        """last_error=None — _send_to_dlq не падает, prefix добавляется корректно."""
        from src.services.audit_outbox_publisher import (
            _reset_breaker_state,
            _send_to_dlq,
        )
        _reset_breaker_state()
        row = self._make_dlq_row(last_error=None)
        _send_to_dlq(row, reason="missing_action")
        assert row.last_error.startswith("[DLQ:missing_action]")

    def test_published_at_set_on_dlq(self):
        """_send_to_dlq всегда выставляет published_at, независимо от prefix-skip."""
        from src.services.audit_outbox_publisher import (
            _reset_breaker_state,
            _send_to_dlq,
        )
        _reset_breaker_state()
        row = self._make_dlq_row(last_error="[DLQ:permanent_4xx] err")
        assert row.published_at is None
        _send_to_dlq(row, reason="attempts_cap")
        assert row.published_at is not None


# ══════════════════════════════════════════════════════════════════════════════
# GAP-4  login guard — _account_creds: apply_session_hints пробрасывает host
#         из payload на managed-сессии
# ══════════════════════════════════════════════════════════════════════════════


class TestAccountCredsHostPropagation:
    """_account_creds пробрасывает host/port из payload через apply_session_hints.

    Это тот аспект _account_creds, который не покрыт test_account_creds_guard.py:
    managed-ветка передаёт payload в apply_session_hints — host/ssh_port
    оттуда попадают в creds.
    """

    async def test_managed_creds_carry_host_from_payload(self, monkeypatch):
        """is_managed=True + host в payload → creds содержит host."""
        async def fail_fetch(*a, **kw):
            raise AssertionError("managed-ветка не должна тянуть пароль")

        monkeypatch.setattr(
            "src.tasks.users.server_service_client.fetch_account_password",
            fail_fetch,
        )

        from src.tasks.users import _account_creds

        creds = await _account_creds(
            {"is_managed": True, "login": "ops", "host": "10.5.0.1", "ssh_port": 22},
            "srv_w15", "acc_w15", target_dept=None,
        )
        assert creds["login"] == "ops"
        assert creds.get("host") == "10.5.0.1", (
            "host из payload должен попасть в creds через apply_session_hints"
        )

    async def test_self_session_carries_host_from_payload(self, monkeypatch):
        """is_managed=False + host в payload → host тоже попадает в creds."""
        async def ok_fetch(server_id, account_id, target_dept):
            return {"login": "ops", "password": "secret"}

        monkeypatch.setattr(
            "src.tasks.users.server_service_client.fetch_account_password",
            ok_fetch,
        )

        from src.tasks.users import _account_creds

        creds = await _account_creds(
            {"is_managed": False, "host": "10.5.0.2", "ssh_port": 2222},
            "srv_w15b", "acc_w15b", target_dept=None,
        )
        assert creds.get("host") == "10.5.0.2"
        assert creds.get("ssh_port") == 2222


# ══════════════════════════════════════════════════════════════════════════════
# GAP-5  ssh_private_key stash: структурная проверка, что оба ключа в scrub-листе
#        и что public_key намеренно не включён
# ══════════════════════════════════════════════════════════════════════════════


class TestProvisionScrubKeyList:
    """Scrub-лист account_provision — структурный анализ кода без запуска БД."""

    def _get_scrub_keys(self) -> list[str]:
        """Извлечь список ключей из scrub_payload_keys вызова в account_provision."""
        import ast
        import inspect
        from src.tasks import users

        source = inspect.getsource(users.account_provision)
        tree = ast.parse(source)
        result: list[str] = []
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "scrub_payload_keys"
            ):
                for arg in node.args:
                    if isinstance(arg, ast.List):
                        for elt in arg.elts:
                            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                result.append(elt.value)
        return result

    def test_private_key_in_scrub_list(self):
        """ssh_private_key_plaintext присутствует в scrub_payload_keys."""
        keys = self._get_scrub_keys()
        assert "ssh_private_key_plaintext" in keys, (
            f"ssh_private_key_plaintext не найден в scrub-листе: {keys}"
        )

    def test_password_in_scrub_list(self):
        """password_plaintext присутствует в scrub_payload_keys."""
        keys = self._get_scrub_keys()
        assert "password_plaintext" in keys, (
            f"password_plaintext не найден в scrub-листе: {keys}"
        )

    def test_public_key_not_in_scrub_list(self):
        """ssh_public_key НЕ в scrub-листе — он не секрет, нужен для форенсики."""
        keys = self._get_scrub_keys()
        assert "ssh_public_key" not in keys, (
            "ssh_public_key не должен быть в scrub-листе"
        )

    def test_scrub_list_length_as_expected(self):
        """Scrub-лист содержит ровно 2 ключа (не больше и не меньше)."""
        keys = self._get_scrub_keys()
        assert len(keys) == 2, (
            f"ожидали 2 ключа в scrub-листе, нашли {len(keys)}: {keys}"
        )

    def test_scrub_in_finally_block(self):
        """scrub_payload_keys вызывается в finally-блоке (работает на любом исходе)."""
        import ast
        import inspect
        from src.tasks import users

        source = inspect.getsource(users.account_provision)
        tree = ast.parse(source)

        in_finally = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.Try):
                continue
            # finalbody — список узлов в finally-ветке.
            for fin_stmt in node.finalbody:
                for sub in ast.walk(fin_stmt):
                    if (
                        isinstance(sub, ast.Call)
                        and isinstance(sub.func, ast.Attribute)
                        and sub.func.attr == "scrub_payload_keys"
                    ):
                        in_finally = True

        assert in_finally, (
            "scrub_payload_keys должен находиться в finally-блоке"
        )
