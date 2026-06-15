"""Unit-тесты cleanup'а для server_worker.

Покрытие:

* `redact_error_message` маскирует многострочный PEM-блок (defense-in-depth
  на случай, если asyncssh когда-нибудь положит plaintext-ключа в repr).
* `_DASH_P_RE` не ловит `-Path /foo` как `-P` + value `ath`; `-P pass`,
  `-P=pass`, `-P!secret123` слитно — продолжают маскироваться.
* `validate_task_id` / `validate_outbox_id` отбивают мусорные значения
  (двоеточия, слэши, `..` сегменты, пустые строки, не-строки).
* `STASH_TTL_SECONDS` и `SCRUBBED_SENTINEL` импортируются из единого
  источника `core/constants.py` (модули users/passwords/repositories
  больше не дублируют literal'ы).
* Legacy plain-string в `_ipmi_stash_parse` → `(None, None)` graceful:
  caller сгенерирует новый пароль и пойдёт обычным retry-путём.
"""

from __future__ import annotations

import pytest

from src.core.constants import SCRUBBED_SENTINEL, STASH_TTL_SECONDS
from src.core.identifiers import validate_outbox_id, validate_task_id
from src.utils.redaction import redact_error_message


# ── PEM-blob ─────────────────────────────────────────────────────────


class TestRedactPemBlock:
    """Многострочный PEM-блок маскируется одним токеном `<PRIVATE_KEY>`."""

    def test_openssh_private_key_block_masked(self):
        msg = (
            "ssh handshake failed: -----BEGIN OPENSSH PRIVATE KEY-----\n"
            "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAA\n"
            "AAABAAAAMwAAAAtzc2gtZWQyNTUxOQAAACBdoq8\n"
            "-----END OPENSSH PRIVATE KEY-----\n"
            "while connecting to host"
        )
        out = redact_error_message(msg)
        assert "OPENSSH PRIVATE KEY" not in out
        assert "b3BlbnNzaC1rZXk" not in out
        assert "<PRIVATE_KEY>" in out
        assert "while connecting to host" in out

    def test_rsa_private_key_block_masked(self):
        msg = (
            "-----BEGIN RSA PRIVATE KEY-----\n"
            "MIIEpAIBAAKCAQEA1234567890abcdef\n"
            "-----END RSA PRIVATE KEY-----"
        )
        assert "<PRIVATE_KEY>" in redact_error_message(msg)
        assert "MIIEpAIBAAKCAQEA" not in redact_error_message(msg)

    def test_ec_private_key_block_masked(self):
        msg = (
            "preface "
            "-----BEGIN EC PRIVATE KEY-----\nABC\n-----END EC PRIVATE KEY-----"
            " trailer"
        )
        out = redact_error_message(msg)
        assert "preface" in out
        assert "trailer" in out
        assert "<PRIVATE_KEY>" in out
        assert "EC PRIVATE KEY" not in out

    def test_no_match_when_only_begin(self):
        """Без `-----END ... -----` ничего не маскируется."""
        msg = "-----BEGIN PRIVATE KEY-----\nAAAA\nno end marker"
        assert "<PRIVATE_KEY>" not in redact_error_message(msg)


# ── -DASH_P false-positive ───────────────────────────────────────────


class TestDashPRegex:
    """`-P` маскируется только когда сепаратор однозначно отделяет значение."""

    def test_dash_p_with_space_masked(self):
        out = redact_error_message("ipmitool -P plaintext lan print")
        assert "plaintext" not in out
        assert "-P <PASSWORD>" in out

    def test_dash_p_with_equals_masked(self):
        out = redact_error_message("ipmitool -P=plaintext lan print")
        assert "plaintext" not in out
        assert "<PASSWORD>" in out

    def test_dash_p_joined_with_special_masked(self):
        """Слитная форма `-P!secret`: первый символ значения — не буква."""
        out = redact_error_message("ipmitool -P!secret123 lan print")
        assert "!secret123" not in out
        assert "<PASSWORD>" in out

    def test_dash_path_not_masked(self):
        """`-Path /foo` — PowerShell-флаг, после `-P` идёт буква 'a'."""
        msg = "Get-Item -Path /foo/bar.cfg failed: not found"
        out = redact_error_message(msg)
        assert "ath" in out
        assert "<PASSWORD>" not in out
        assert "-Path" in out

    def test_dash_pattern_not_masked(self):
        """Никаких ложных срабатываний на `-Pattern`."""
        msg = "Select-String -Pattern foo input.txt"
        out = redact_error_message(msg)
        assert "<PASSWORD>" not in out
        assert "-Pattern" in out


# ── Items 2, 3: task_id / outbox_id validation ───────────────────────────────


class TestValidateTaskId:
    def test_canonical_task_id_accepted(self):
        canonical = "tsk_" + "a" * 32
        assert validate_task_id(canonical) == canonical

    def test_short_test_id_accepted(self):
        """Test-id'ы вроде `tsk_stash_roundtrip_test` — alnum+underscore."""
        tid = "tsk_stash_roundtrip_test"
        assert validate_task_id(tid) == tid

    @pytest.mark.parametrize(
        "bad",
        [
            "tsk_aaa:other",
            "tsk_../etc/passwd",
            "tsk_with space",
            "",
            "tsk_" + "a" * 200,
            "tsk_aaa\nrogue",
            "tsk_aaa;rm -rf",
        ],
    )
    def test_invalid_task_id_rejected(self, bad):
        with pytest.raises(ValueError, match="invalid task_id"):
            validate_task_id(bad)

    def test_non_string_rejected(self):
        with pytest.raises(ValueError, match="invalid task_id"):
            validate_task_id(None)  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="invalid task_id"):
            validate_task_id(123)  # type: ignore[arg-type]


class TestValidateOutboxId:
    def test_canonical_rox_id_accepted(self):
        rid = "rox_" + "a" * 32
        assert validate_outbox_id(rid) == rid

    def test_short_test_id_accepted(self):
        assert validate_outbox_id("rox_x") == "rox_x"

    @pytest.mark.parametrize(
        "bad",
        [
            "../admin",
            "rox_aaa/../etc",
            "rox_aaa other",
            "",
            "rox_" + "a" * 200,
        ],
    )
    def test_invalid_outbox_id_rejected(self, bad):
        with pytest.raises(ValueError, match="invalid outbox_id"):
            validate_outbox_id(bad)


# ── Items 4, 5: shared constants ─────────────────────────────────────────────


class TestSharedConstants:
    """STASH_TTL_SECONDS и SCRUBBED_SENTINEL — единый источник."""

    def test_stash_ttl_value(self):
        assert STASH_TTL_SECONDS == 1800

    def test_scrubbed_sentinel_value(self):
        assert SCRUBBED_SENTINEL == "<scrubbed>"

    def test_users_module_uses_shared_constants(self):
        from src.tasks import users
        # Раньше дублировался literal'ом — теперь из constants.
        assert users.SCRUBBED_SENTINEL is SCRUBBED_SENTINEL
        assert users.STASH_TTL_SECONDS is STASH_TTL_SECONDS

    def test_passwords_module_uses_shared_ttl(self):
        from src.tasks import passwords
        assert passwords.STASH_TTL_SECONDS is STASH_TTL_SECONDS

    def test_repositories_task_default_uses_shared_sentinel(self):
        """`scrub_payload_keys` дефолтным replacement берёт shared sentinel."""
        from inspect import signature

        from src.repositories import task as task_repo

        sig = signature(task_repo.scrub_payload_keys)
        assert sig.parameters["replacement"].default == SCRUBBED_SENTINEL


# ── legacy stash → graceful ──────────────────────────────────────────


class TestIpmiStashParseLegacyGraceful:
    """Legacy plain-string в Redis-stash больше не парсится как пароль.

    После миграции writer всегда пишет JSON dict. Если в Redis вдруг
    лежит «чужой» формат (тестовый прогон, ручное вмешательство,
    устаревший воркер) — caller должен получить «как будто пусто»
    и сгенерировать новый пароль штатно, а не использовать произвольную
    строку как ключ ротации.
    """

    def _parse(self, raw):
        from src.tasks.passwords import _ipmi_stash_parse
        return _ipmi_stash_parse(raw)

    def test_legacy_plain_bytes_returns_none_pair(self):
        pw, ts = self._parse(b"legacy_plaintext_password")
        assert pw is None
        assert ts is None

    def test_legacy_plain_str_returns_none_pair(self):
        pw, ts = self._parse("legacy_plaintext_password")
        assert pw is None
        assert ts is None

    def test_json_dict_still_parses(self):
        import json
        raw = json.dumps({"password": "p", "rotated_at": "2026-05-31T00:00:00Z"})
        pw, ts = self._parse(raw)
        assert pw == "p"
        assert ts == "2026-05-31T00:00:00Z"

    def test_json_list_returns_none_pair(self):
        import json
        pw, ts = self._parse(json.dumps([1, 2, 3]))
        assert pw is None
        assert ts is None
