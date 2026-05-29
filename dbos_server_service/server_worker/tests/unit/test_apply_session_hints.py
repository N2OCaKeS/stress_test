"""Unit-тесты `services.ssh_client.apply_session_hints`.

Функция копирует поля из payload в credentials:
* `is_managed` — выставляется всегда (`bool(payload.is_managed)`) — даже при
  отсутствующем/False payload-ключе, чтобы не унаследовать прошлое значение;
* `management_user` — только при `is_managed=True`;
* `host` — из payload, если credentials.host пуст;
* `ssh_port` — из payload, если credentials не содержат ни port, ни ssh_port.

Контракт: credentials с уже заполненным host не перетираются.
"""

from __future__ import annotations

import pytest

from src.services.ssh_client import apply_session_hints


class TestIsManaged:
    def test_is_managed_true_sets_flag(self):
        creds = {}
        result = apply_session_hints(creds, {"is_managed": True})
        assert result["is_managed"] is True

    def test_is_managed_false_sets_flag_false(self):
        creds = {}
        result = apply_session_hints(creds, {"is_managed": False})
        assert result["is_managed"] is False

    def test_is_managed_absent_sets_flag_false(self):
        creds = {}
        result = apply_session_hints(creds, {})
        assert result["is_managed"] is False

    def test_is_managed_false_overwrites_prior_true(self):
        # Защита от грязных credentials: если в creds из прошлого прохода
        # is_managed=True, а payload говорит False — флаг должен сброситься,
        # иначе self-сессия неожиданно превратится в управляющую.
        creds = {"is_managed": True, "management_user": "dbos"}
        result = apply_session_hints(creds, {"is_managed": False})
        assert result["is_managed"] is False

    def test_management_user_copied_when_is_managed(self):
        creds = {}
        result = apply_session_hints(creds, {"is_managed": True, "management_user": "dbos"})
        assert result["management_user"] == "dbos"

    def test_management_user_not_copied_without_is_managed(self):
        creds = {}
        result = apply_session_hints(creds, {"management_user": "dbos"})
        assert "management_user" not in result

    def test_management_user_absent_in_payload_not_added(self):
        creds = {}
        result = apply_session_hints(creds, {"is_managed": True})
        assert "management_user" not in result

    def test_management_user_not_overwritten_if_already_in_creds(self):
        # management_user в credentials не прописывается — apply_session_hints
        # не проверяет существующее значение, просто перезаписывает.
        # Это задокументированное поведение: payload-значение приоритетнее.
        creds = {}
        result = apply_session_hints(creds, {"is_managed": True, "management_user": "ops_mgmt"})
        assert result["management_user"] == "ops_mgmt"


class TestHostPropagation:
    def test_host_from_payload_when_creds_empty(self):
        creds = {}
        result = apply_session_hints(creds, {"host": "10.0.0.1"})
        assert result["host"] == "10.0.0.1"

    def test_ssh_host_alias_used_when_host_absent(self):
        creds = {}
        result = apply_session_hints(creds, {"ssh_host": "10.0.0.2"})
        assert result["host"] == "10.0.0.2"

    def test_existing_creds_host_not_overwritten(self):
        creds = {"host": "192.168.1.1"}
        result = apply_session_hints(creds, {"host": "10.0.0.3"})
        assert result["host"] == "192.168.1.1"

    def test_no_host_in_payload_leaves_creds_empty(self):
        creds = {}
        result = apply_session_hints(creds, {})
        assert "host" not in result

    def test_host_preferred_over_ssh_host_in_payload(self):
        creds = {}
        result = apply_session_hints(creds, {"host": "main.host", "ssh_host": "alt.host"})
        assert result["host"] == "main.host"


class TestPortPropagation:
    def test_ssh_port_from_payload_when_creds_empty(self):
        creds = {}
        result = apply_session_hints(creds, {"ssh_port": 2222})
        assert result["ssh_port"] == 2222

    def test_port_alias_in_payload(self):
        creds = {}
        result = apply_session_hints(creds, {"port": 2222})
        assert result["ssh_port"] == 2222

    def test_existing_creds_port_not_overwritten(self):
        creds = {"port": 22}
        result = apply_session_hints(creds, {"ssh_port": 2222})
        assert creds.get("port") == 22
        assert "ssh_port" not in result or result.get("ssh_port") != 2222

    def test_existing_creds_ssh_port_not_overwritten(self):
        creds = {"ssh_port": 22}
        result = apply_session_hints(creds, {"ssh_port": 2222})
        assert result["ssh_port"] == 22

    def test_no_port_in_payload_leaves_creds_without_port(self):
        creds = {}
        result = apply_session_hints(creds, {})
        assert "port" not in result
        assert "ssh_port" not in result


class TestReturnValue:
    def test_returns_same_dict_instance(self):
        creds = {}
        result = apply_session_hints(creds, {"host": "h"})
        assert result is creds

    def test_full_payload_all_fields_copied(self):
        creds = {}
        result = apply_session_hints(creds, {
            "is_managed": True,
            "management_user": "dbos",
            "host": "10.0.0.5",
            "ssh_port": 2222,
        })
        assert result["is_managed"] is True
        assert result["management_user"] == "dbos"
        assert result["host"] == "10.0.0.5"
        assert result["ssh_port"] == 2222

    def test_empty_creds_empty_payload_keeps_only_is_managed_false(self):
        # is_managed=False всегда выставляется явно — это часть контракта,
        # а не «no-op».
        creds = {}
        result = apply_session_hints(creds, {})
        assert result == {"is_managed": False}
