"""Coverage W16 — точечные пробелы после F-W15.

GAP-1  _ipmi_stash_parse: backward-compat plain-string path (non-JSON → (text, None)),
        bytes input, str input, JSON с non-dict top-level, пустой JSON.
GAP-2  _KV_SECRET_RE именованные варианты в redact_error_message:
        client_secret=, private_key=, signing_key=, apikey= → <SECRET>.
GAP-3  _account_creds: is_managed=True + полностью отсутствующий ключ login
        (не None, а отсутствие в dict) → SSH_INVALID_ARG.
GAP-4  _filter_result_for_audit unit-level: все ветки (None, no_whitelist,
        not_dict, filtered).
GAP-5  _cancel_timestamp unit: task=None, cancelled_at=None, cancelled_at set.
GAP-6  _recover_due_scheduled_retries_once: exception ловится, worker не падает.
"""

from __future__ import annotations

import pytest

from src.utils.redaction import redact_error_message


# ══════════════════════════════════════════════════════════════════════════════
# GAP-1  _ipmi_stash_parse — backward-compat plain-string path
# ══════════════════════════════════════════════════════════════════════════════


class TestIpmiStashParseFormats:
    """Парсер stash: новый JSON-формат → значения, всё прочее → (None, None).

    Старый plain-string путь удалён — writer всегда пишет JSON dict.
    Если в Redis вдруг лежит чужой/устаревший формат — caller получит
    `(None, None)`, сгенерит новый пароль и пойдёт штатным retry-путём.
    """

    def _parse(self, raw):
        from src.tasks.passwords import _ipmi_stash_parse
        return _ipmi_stash_parse(raw)

    def test_json_dict_with_both_fields(self):
        """Новый формат: JSON dict с password и rotated_at."""
        import json
        raw = json.dumps({"password": "newpass", "rotated_at": "2026-01-01T00:00:00"}).encode()
        pw, ts = self._parse(raw)
        assert pw == "newpass"
        assert ts == "2026-01-01T00:00:00"

    def test_json_dict_missing_rotated_at(self):
        """JSON dict без rotated_at → ts == None."""
        import json
        raw = json.dumps({"password": "onlypass"}).encode()
        pw, ts = self._parse(raw)
        assert pw == "onlypass"
        assert ts is None

    def test_plain_string_bytes_returns_none_pair(self):
        """Не-JSON байты → (None, None) — graceful, caller regenerates."""
        raw = b"plaintext_password_123"
        pw, ts = self._parse(raw)
        assert pw is None
        assert ts is None

    def test_plain_string_str_returns_none_pair(self):
        """Не-JSON строка → (None, None)."""
        raw = "hunter2-secret"
        pw, ts = self._parse(raw)
        assert pw is None
        assert ts is None

    def test_json_non_dict_list_returns_none_pair(self):
        """JSON-list (не dict) → (None, None)."""
        import json
        raw = json.dumps(["not", "a", "dict"]).encode()
        pw, ts = self._parse(raw)
        assert pw is None
        assert ts is None

    def test_json_non_dict_scalar_returns_none_pair(self):
        """JSON scalar (число) → (None, None)."""
        raw = b"42"
        pw, ts = self._parse(raw)
        assert pw is None
        assert ts is None

    def test_empty_bytes_returns_none_pair(self):
        """Пустые байты → (None, None)."""
        raw = b""
        pw, ts = self._parse(raw)
        assert pw is None
        assert ts is None


# ══════════════════════════════════════════════════════════════════════════════
# GAP-2  _KV_SECRET_RE — именованные варианты в redact_error_message
# ══════════════════════════════════════════════════════════════════════════════


class TestRedactKvSecretNamedVariants:
    """_KV_SECRET_RE покрывает client_secret, private_key, signing_key, apikey."""

    @pytest.mark.parametrize(
        "key,value",
        [
            ("client_secret", "mysupersecret999"),
            ("private_key", "-----BEGIN-PRIVATE-KEY"),
            ("signing_key", "hmac_abc123"),
            ("apikey", "ak_live_xyzABC00"),
            ("api_key", "api_key_value"),
            ("secret_key", "secret_key_value"),
        ],
    )
    def test_key_value_is_redacted(self, key: str, value: str):
        """Каждый вариант имени секрета в key=value форме маскируется."""
        msg = f"config error: {key}={value}"
        out = redact_error_message(msg)
        assert value not in out, (
            f"ожидали что {value!r} будет замаскировано для ключа {key!r}"
        )
        assert "<SECRET>" in out, (
            f"ожидали <SECRET> в выводе для ключа {key!r}"
        )

    def test_service_api_key_masked(self):
        """service_api_key=val маскируется через api_key sub-match."""
        msg = "auth: service_api_key=topsecretvalue123"
        out = redact_error_message(msg)
        assert "topsecretvalue123" not in out
        assert "<SECRET>" in out

    def test_value_within_word_not_masked(self):
        """Имя ключа внутри другого слова (myapi_key) не маскируется — lookbehind."""
        msg = "myapi_key=should_stay_visible"
        out = redact_error_message(msg)
        assert "should_stay_visible" in out

    def test_key_colon_separator_masked(self):
        """Поддерживает разделитель `:` наряду с `=`."""
        msg = "client_secret: secretvalue42"
        out = redact_error_message(msg)
        assert "secretvalue42" not in out
        assert "<SECRET>" in out


# ══════════════════════════════════════════════════════════════════════════════
# GAP-3  _account_creds — отсутствующий ключ login при is_managed=True
# ══════════════════════════════════════════════════════════════════════════════


class TestAccountCredsMissingLoginKey:
    """is_managed=True без ключа login в payload (не None, просто нет) → SSH_INVALID_ARG."""

    async def test_missing_key_login_raises_ssh_invalid_arg(self, monkeypatch):
        """Ключ login полностью отсутствует в payload → guard срабатывает.

        payload.get("login") вернёт None при отсутствии ключа — идентично None
        явному. guard `if is_managed and not login:` ловит оба случая.
        """
        from src.clients.ssh import SshError
        from src.tasks.users import _account_creds

        async def fail_fetch(*a, **kw):
            raise AssertionError("fetch не должен вызываться при managed=True")

        monkeypatch.setattr(
            "src.tasks.users.server_service_client.fetch_account_password",
            fail_fetch,
        )

        payload = {"is_managed": True, "server_id": "srv_x"}
        with pytest.raises(SshError) as ei:
            await _account_creds(payload, "srv_x", "acc_x", target_dept=None)
        assert ei.value.error_code == "SSH_INVALID_ARG", (
            f"ожидали SSH_INVALID_ARG, получили {ei.value.error_code!r}"
        )

    async def test_managed_true_zero_string_login_raises(self, monkeypatch):
        """Пробельная строка ('   ') также не проходит guard (falsy)."""
        from src.clients.ssh import SshError
        from src.tasks.users import _account_creds

        async def fail_fetch(*a, **kw):
            raise AssertionError("should not be called")

        monkeypatch.setattr(
            "src.tasks.users.server_service_client.fetch_account_password",
            fail_fetch,
        )

        with pytest.raises(SshError) as ei:
            await _account_creds(
                {"is_managed": True, "login": "   "},
                "srv_x", "acc_x", target_dept=None,
            )
        assert ei.value.error_code == "SSH_INVALID_ARG"

    async def test_not_managed_missing_login_uses_fetch(self, monkeypatch):
        """is_managed=False без login → fetch_account_password вызывается."""
        from src.tasks.users import _account_creds

        fetched: list = []

        async def ok_fetch(server_id, account_id, target_dept):
            fetched.append(server_id)
            return {"login": "ops", "password": "pw"}

        monkeypatch.setattr(
            "src.tasks.users.server_service_client.fetch_account_password",
            ok_fetch,
        )

        creds = await _account_creds(
            {"is_managed": False},
            "srv_y", "acc_y", target_dept=None,
        )
        assert creds["login"] == "ops"
        assert fetched == ["srv_y"]


# ══════════════════════════════════════════════════════════════════════════════
# GAP-4  _filter_result_for_audit — unit-level все ветки
# ══════════════════════════════════════════════════════════════════════════════


class TestFilterResultForAudit:
    """Все ветки `_filter_result_for_audit` без запуска БД."""

    def _f(self, result, safe_fields):
        from src.tasks._runner import _filter_result_for_audit
        return _filter_result_for_audit(result, safe_fields)

    def test_result_none_returns_none(self):
        """result=None → None независимо от safe_fields."""
        assert self._f(None, None) is None
        assert self._f(None, {"field"}) is None

    def test_safe_fields_none_returns_sentinel(self):
        """safe_fields=None → sentinel dict с emitted=False, reason=no_whitelist."""
        result = self._f({"password": "secret", "server_id": "srv_1"}, None)
        assert isinstance(result, dict)
        assert result["emitted"] is False
        assert result["reason"] == "no_whitelist"
        assert "result_type" in result
        # Секрет не утёк.
        assert "password" not in result
        assert "secret" not in str(result)

    def test_result_not_dict_returns_sentinel(self):
        """result не dict (строка, список) → sentinel с reason=result_not_dict."""
        result_str = self._f("just a string", {"field"})
        assert result_str["emitted"] is False
        assert result_str["reason"] == "result_not_dict"

        result_list = self._f([1, 2, 3], {"field"})
        assert result_list["emitted"] is False
        assert result_list["reason"] == "result_not_dict"

    def test_dict_filtered_by_whitelist(self):
        """Dict с safe_fields → только whitelisted поля остаются."""
        result = self._f(
            {"server_id": "srv_1", "password": "secret", "status": "ok"},
            {"server_id", "status"},
        )
        assert result == {"server_id": "srv_1", "status": "ok"}
        assert "password" not in result

    def test_empty_safe_fields_returns_empty_dict(self):
        """safe_fields=set() → все поля отрезаны, пустой dict."""
        result = self._f({"a": 1, "b": 2}, set())
        assert result == {}

    def test_whitelist_superset_keeps_only_existing_keys(self):
        """safe_fields содержит больше ключей, чем result — остаются только общие."""
        result = self._f(
            {"server_id": "srv_1"},
            {"server_id", "management_user", "prepared"},
        )
        assert result == {"server_id": "srv_1"}

    def test_none_safe_fields_reports_result_type(self):
        """Sentinel несёт result_type для диагностики."""
        result = self._f({"x": 1}, None)
        assert result["result_type"] == "dict"

        result_str = self._f("text", None)
        assert result_str["result_type"] == "str"


# ══════════════════════════════════════════════════════════════════════════════
# GAP-5  _cancel_timestamp — unit-level все ветки
# ══════════════════════════════════════════════════════════════════════════════


class TestCancelTimestamp:
    """_cancel_timestamp возвращает ISO-строку или None."""

    def _ts(self, task):
        from src.tasks._runner import _cancel_timestamp
        return _cancel_timestamp(task)

    def test_task_none_returns_none(self):
        """task=None → None."""
        assert self._ts(None) is None

    def test_cancelled_at_none_returns_none(self):
        """task.cancelled_at=None → None."""
        class FakeTask:
            cancelled_at = None

        assert self._ts(FakeTask()) is None

    def test_cancelled_at_set_returns_isoformat(self):
        """task.cancelled_at задан → ISO-строка с timezone."""
        from datetime import datetime, timezone

        ts = datetime(2026, 1, 15, 12, 30, 0, tzinfo=timezone.utc)

        class FakeTask:
            cancelled_at = ts

        result = self._ts(FakeTask())
        assert result == ts.isoformat()
        assert "T" in result

    def test_task_without_attribute_returns_none(self):
        """task без атрибута cancelled_at → None (getattr fallback)."""
        class FakeTask:
            pass

        assert self._ts(FakeTask()) is None


# ══════════════════════════════════════════════════════════════════════════════
# GAP-6  _recover_due_scheduled_retries_once — exception не крашит
# ══════════════════════════════════════════════════════════════════════════════


class TestRecoverScheduledRetriesException:
    """Exception внутри recovery не должен пробрасываться наружу."""

    async def test_db_exception_is_caught_and_logged(self, monkeypatch, caplog):
        """Если claim_one_due_scheduled_retry бросает — функция возвращает None, не raises."""
        import logging

        import src.repositories.task as task_repo_mod

        async def boom_list(*a, **kw):
            raise RuntimeError("postgresql://user:pass@db:5432/worker")

        monkeypatch.setattr(task_repo_mod, "claim_one_due_scheduled_retry", boom_list)

        from src.main import _recover_due_scheduled_retries_once

        with caplog.at_level(logging.WARNING, logger="src.main"):
            result = await _recover_due_scheduled_retries_once()

        assert result is None, "функция не должна пробрасывать exception"
        # Лог написан.
        assert any(
            "scheduled_retries" in rec.getMessage().lower()
            or "recovery" in rec.getMessage().lower()
            for rec in caplog.records
        ), "ожидали warning-лог о failed recovery"

    async def test_db_exception_redacts_credentials(self, monkeypatch, caplog):
        """DSN с паролем в repr(exception) → не должен утечь в лог."""
        import logging

        import src.repositories.task as task_repo_mod

        async def boom_list(*a, **kw):
            raise RuntimeError(
                "could not connect to "
                "postgresql://admin:hunter2_secret@db.local:5432/worker"
            )

        monkeypatch.setattr(task_repo_mod, "claim_one_due_scheduled_retry", boom_list)

        from src.main import _recover_due_scheduled_retries_once

        with caplog.at_level(logging.WARNING, logger="src.main"):
            await _recover_due_scheduled_retries_once()

        log_blob = " ".join(rec.getMessage() for rec in caplog.records)
        assert "hunter2_secret" not in log_blob, (
            "DSN-пароль не должен утекать в лог recovery"
        )
