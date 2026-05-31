"""Unit-тесты `redact_error_message` + интеграция в `_runner.run_task`.

Покрытие:
  * URL credentials, shell-style флаги `-U/-P`, `password=...`, `Bearer ...`,
    JWT, опаковые токены `dbos_pat_*` / `dbos_bot_*`.
  * Безсекретные сообщения остаются как есть.
  * После прохождения через `_runner` (impl бросает Exception) секреты не
    утекают ни в `task.last_error`, ни в audit `details.error`.
"""

from __future__ import annotations

import pytest

from src.tasks._runner import run_task
from src.utils.redaction import redact_error_message


# ── Чистые unit-тесты функции ────────────────────────────────────────────────


class TestRedactErrorMessageURL:
    def test_https_with_creds_masked(self):
        msg = "ConnectionError: GET https://root:Calvin@idrac.example/redfish/v1/Systems failed"
        out = redact_error_message(msg)
        assert "root" not in out
        assert "Calvin" not in out
        assert "<USER>:<PASSWORD>@idrac.example" in out

    def test_redfish_scheme_creds_masked(self):
        msg = "redfish://admin:secret@10.0.0.1/path"
        out = redact_error_message(msg)
        assert "admin" not in out
        assert "secret" not in out
        assert "redfish://<USER>:<PASSWORD>@10.0.0.1/path" == out

    def test_postgres_url_masked(self):
        msg = "OperationalError: could not connect to postgres://app:hunter2@db:5432/x"
        out = redact_error_message(msg)
        assert "hunter2" not in out
        assert "<USER>:<PASSWORD>@db:5432" in out

    def test_url_without_creds_untouched(self):
        msg = "GET https://idrac.example/redfish/v1/Systems failed"
        assert redact_error_message(msg) == msg


class TestRedactErrorMessageShellFlags:
    def test_ipmitool_U_P_masked(self):
        msg = "ipmitool -U admin -P plaintext lan print 1 returned non-zero"
        out = redact_error_message(msg)
        assert "admin" not in out
        assert "plaintext" not in out
        assert "-U <USER>" in out
        assert "-P <PASSWORD>" in out

    def test_dash_p_equals_form(self):
        msg = "command -P=hunter2 failed"
        out = redact_error_message(msg)
        assert "hunter2" not in out
        assert "<PASSWORD>" in out

    def test_word_inside_other_token_untouched(self):
        # `--port` не должен матчить `-P`
        msg = "connection error on --port=8080"
        assert redact_error_message(msg) == msg

    def test_ipmitool_lowercase_p_is_port_not_password(self):
        # У ipmitool `-p` — номер порта (623 по умолчанию для IPMI-over-LAN),
        # `-P` — пароль. Маскировать `-p 623` нельзя, иначе оператор не видит,
        # к какому BMC шёл коннект. `_DASH_P_RE` ловит только uppercase `-P`.
        msg = "ipmitool -H 10.0.0.1 -p 623 -U admin -P plaintext lan print 1"
        out = redact_error_message(msg)
        assert "plaintext" not in out
        assert "-P <PASSWORD>" in out
        # Порт остаётся в выводе как есть.
        assert "-p 623" in out

    def test_ipmitool_argv_joined_lowercase_p_preserved(self):
        # Эмулирует случай, когда argv ipmitool логируется как пробел-
        # разделённая строка (stderr / `argv_safe` join). Пароль маскируем,
        # порт оставляем.
        argv = ["ipmitool", "-p", "623", "-P", "secret"]
        out = redact_error_message(" ".join(argv))
        assert "secret" not in out
        assert "<PASSWORD>" in out
        assert "-p 623" in out


class TestRedactErrorMessageKVForms:
    def test_password_equals_masked(self):
        msg = "AuthError: password=hunter2 rejected"
        out = redact_error_message(msg)
        assert "hunter2" not in out
        assert "password=<PASSWORD>" in out

    def test_passwd_equals_masked(self):
        msg = "passwd=plaintext"
        assert "<PASSWORD>" in redact_error_message(msg)

    def test_secret_equals_masked(self):
        msg = "config error: secret=topsecret123"
        out = redact_error_message(msg)
        assert "topsecret123" not in out
        assert "secret=<SECRET>" in out or "secret=<PASSWORD>" in out

    def test_token_equals_masked(self):
        msg = "auth: token=abcdef123456 expired"
        out = redact_error_message(msg)
        assert "abcdef123456" not in out
        assert "token=<TOKEN>" in out

    def test_quoted_password_masked(self):
        msg = "Auth failed for user 'root' with password 'hunter2'"
        out = redact_error_message(msg)
        assert "hunter2" not in out
        assert "<PASSWORD>" in out

    def test_secret_with_underscore_prefix_masked(self):
        # `\b` не работал на границе `_api_key`: `_` — word-char. После фикса
        # `service_api_key=...` редактится наравне с голым `api_key=...`.
        msg = "config: service_api_key=topsecretvalue123"
        out = redact_error_message(msg)
        assert "topsecretvalue123" not in out
        assert "<SECRET>" in out

    def test_secret_inside_word_not_masked(self):
        # Граница не должна теряться полностью: внутри буквенного слова
        # секрет-ключ — это, скорее всего, артефакт другого имени, не утечка.
        msg = "myapi_key=should_stay"
        out = redact_error_message(msg)
        assert "should_stay" in out


class TestRedactErrorMessageBearer:
    def test_bearer_token_masked(self):
        msg = "HTTP 401 on Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig"
        out = redact_error_message(msg)
        assert "eyJhbGciOiJIUzI1NiJ9" not in out
        assert "Bearer <TOKEN>" in out

    def test_bearer_short_opaque_masked(self):
        msg = "Bearer abc123def456"
        out = redact_error_message(msg)
        assert "abc123def456" not in out
        assert "Bearer <TOKEN>" in out


class TestRedactErrorMessageOpaqueTokens:
    def test_dbos_pat_masked(self):
        msg = "Invalid token: dbos_pat_aBcDeF1234567890XyZ presented"
        out = redact_error_message(msg)
        assert "dbos_pat_aBcDeF1234567890XyZ" not in out
        assert "<TOKEN>" in out

    def test_dbos_bot_masked(self):
        msg = "bot auth failed for dbos_bot_xx1234567890ZZ"
        out = redact_error_message(msg)
        assert "dbos_bot_xx1234567890ZZ" not in out
        assert "<TOKEN>" in out

    def test_legacy_pat_prefix_masked(self):
        msg = "token pat_abcdef1234567890 not found"
        out = redact_error_message(msg)
        assert "pat_abcdef1234567890" not in out
        assert "<TOKEN>" in out


class TestRedactErrorMessageJWT:
    def test_jwt_three_segments_masked(self):
        msg = "JWT verify failed: eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c3IifQ.signaturedata1234"
        out = redact_error_message(msg)
        assert "eyJhbGciOiJIUzI1NiJ9" not in out
        assert "<TOKEN>" in out


class TestRedactErrorMessageNoSecrets:
    def test_plain_message_untouched(self):
        msg = "ConnectionError: timeout after 30s connecting to idrac.example"
        assert redact_error_message(msg) == msg

    def test_empty_string_untouched(self):
        assert redact_error_message("") == ""

    def test_keyerror_style_untouched(self):
        msg = "KeyError: 'server_id'"
        assert redact_error_message(msg) == msg

    def test_runtime_error_untouched(self):
        msg = "RuntimeError: Boom!"
        assert redact_error_message(msg) == msg


class TestRedactErrorMessageTruncation:
    def test_very_long_string_truncated(self):
        # 8 КБ строки → должно усечься до ~4 КБ.
        msg = "x" * 10_000
        out = redact_error_message(msg)
        assert len(out) <= 4096 + len("…<TRUNCATED>") + 1
        assert out.endswith("<TRUNCATED>")


class TestRedactErrorMessageNonString:
    def test_none_passthrough(self):
        # Сигнатура `str`, но runtime-защита от None есть.
        assert redact_error_message(None) is None  # type: ignore[arg-type]


# ── Интеграция с _runner.run_task ────────────────────────────────────────────


class TestRunnerRedactsSecrets:
    """Имитируем реальный сценарий: impl бросает Exception с creds внутри."""

    @pytest.mark.parametrize(
        "exc_message,forbidden_substring",
        [
            ("GET https://root:Calvin@idrac.example/redfish failed", "Calvin"),
            ("ipmitool -U admin -P plaintext failed", "plaintext"),
            ("Auth failed: password=hunter2", "hunter2"),
            (
                "401 Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.signature",
                "eyJhbGciOiJIUzI1NiJ9",
            ),
        ],
    )
    async def test_secret_not_in_last_error_or_audit(
        self,
        make_task,
        fetch_task,
        captured_audit,
        exc_message,
        forbidden_substring,
    ):
        tid = await make_task()

        async def boom(_):
            raise RuntimeError(exc_message)

        await run_task(
            tid,
            audit_action="server.power_on",
            audit_target_type="server",
            impl=boom,
        )

        t = await fetch_task(tid)
        # task.last_error: секрет замаскирован, но тип ошибки сохранён
        assert "RuntimeError" in t.last_error
        assert forbidden_substring not in t.last_error

        # audit details.error: то же самое
        assert len(captured_audit) == 1
        ev_error = captured_audit[0]["details"]["error"]
        assert "RuntimeError" in ev_error
        assert forbidden_substring not in ev_error

    async def test_non_secret_exception_passes_through_unchanged(
        self, make_task, fetch_task, captured_audit,
    ):
        """Без секретов — текст ошибки сохраняется вербатим."""
        tid = await make_task()

        async def boom(_):
            raise ValueError("invalid server_id")

        await run_task(tid, audit_action="x", impl=boom)

        t = await fetch_task(tid)
        assert t.last_error == "ValueError: invalid server_id"
        assert captured_audit[0]["details"]["error"] == "ValueError: invalid server_id"
