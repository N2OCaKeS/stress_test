"""Интеграционные тесты создания сервера с вложенным IPMI-блоком.

Покрытие:

* POST /servers с `ipmi` — контроллер создан атомарно, доступен через
  GET /servers/{id}/ipmi, server_id привязан верно, пароль зашифрован,
  plaintext в ответе сервера не светится.
* POST /servers без `ipmi` — сервер есть, контроллера нет (GET → 404).
* Атомарность: невалидный IPMI-блок (422) и конфликт hostname (409) не
  оставляют ни сервера, ни осиротевшего контроллера в БД.
* Аудит `ipmi_controller.create` эмитится наравне с `server.create`.
"""

from __future__ import annotations

import pytest

from src.services import secrets_service

BASE = "/api/server/v1/servers"


def _hdr(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _payload(**overrides):
    data = {
        "hostname": "ipmi-host",
        "ip_address": "10.20.20.20",
        "department_id": "dep_a",
        "ssh_port": 22,
    }
    data.update(overrides)
    return data


def _ipmi_block(**overrides):
    data = {
        "kind": "idrac",
        "endpoint_url": "https://idrac.example.com",
        "username": "ipmi_admin",
        "password": "bmc-secret-pw1",
    }
    data.update(overrides)
    return data


class TestCreateServerWithIpmi:
    async def test_controller_created_and_reachable(self, client, admin_token):
        resp = await client.post(
            BASE, headers=_hdr(admin_token),
            json=_payload(ipmi=_ipmi_block()),
        )
        assert resp.status_code == 201
        srv_id = resp.json()["id"]
        # ответ сервера не должен светить пароль BMC
        assert "ipmi" not in resp.json() or "password" not in str(resp.json().get("ipmi", ""))

        get_ipmi = await client.get(f"{BASE}/{srv_id}/ipmi", headers=_hdr(admin_token))
        assert get_ipmi.status_code == 200
        body = get_ipmi.json()
        assert body["server_id"] == srv_id
        assert body["kind"] == "idrac"
        assert body["endpoint_url"] == "https://idrac.example.com"
        assert body["username"] == "ipmi_admin"

    async def test_bmc_vendor_defaults_when_omitted(self, client, admin_token):
        resp = await client.post(
            BASE, headers=_hdr(admin_token),
            json=_payload(hostname="ipmi-default-vendor", ip_address="10.20.20.21",
                          ipmi=_ipmi_block()),
        )
        assert resp.status_code == 201
        srv_id = resp.json()["id"]
        get_ipmi = await client.get(f"{BASE}/{srv_id}/ipmi", headers=_hdr(admin_token))
        assert get_ipmi.json()["bmc_vendor"] == "ipmi_generic"

    async def test_explicit_bmc_vendor_persisted(self, client, admin_token):
        resp = await client.post(
            BASE, headers=_hdr(admin_token),
            json=_payload(hostname="ipmi-dell", ip_address="10.20.20.22",
                          ipmi=_ipmi_block(bmc_vendor="idrac")),
        )
        assert resp.status_code == 201
        srv_id = resp.json()["id"]
        get_ipmi = await client.get(f"{BASE}/{srv_id}/ipmi", headers=_hdr(admin_token))
        assert get_ipmi.json()["bmc_vendor"] == "idrac"

    async def test_password_is_encrypted_in_db(self, client, admin_token, db):
        from sqlalchemy import select

        from src.models import IpmiController

        resp = await client.post(
            BASE, headers=_hdr(admin_token),
            json=_payload(hostname="ipmi-enc", ip_address="10.20.20.23",
                          ipmi=_ipmi_block(password="super-secret-bmc-987")),
        )
        assert resp.status_code == 201
        srv_id = resp.json()["id"]
        row = (
            await db.execute(
                select(IpmiController).where(IpmiController.server_id == srv_id)
            )
        ).scalar_one()
        assert "super-secret-bmc-987" not in row.password_encrypted
        assert row.password_encrypted.startswith("v")
        assert secrets_service.decrypt(
            row.password_encrypted,
            aad=secrets_service.aad_for_ipmi_credential(row.id),
        ) == "super-secret-bmc-987"

    async def test_operator_can_create_with_ipmi(self, client, operator_token_a):
        resp = await client.post(
            BASE, headers=_hdr(operator_token_a),
            json=_payload(hostname="op-ipmi", ip_address="10.20.20.24",
                          ipmi=_ipmi_block()),
        )
        assert resp.status_code == 201


class TestCreateServerWithoutIpmi:
    async def test_no_ipmi_means_no_controller(self, client, admin_token):
        resp = await client.post(
            BASE, headers=_hdr(admin_token),
            json=_payload(hostname="no-ipmi", ip_address="10.20.20.30"),
        )
        assert resp.status_code == 201
        srv_id = resp.json()["id"]
        get_ipmi = await client.get(f"{BASE}/{srv_id}/ipmi", headers=_hdr(admin_token))
        assert get_ipmi.status_code == 404
        assert get_ipmi.json().get("error_code") == "IPMI_NOT_FOUND"

    async def test_explicit_null_ipmi_means_no_controller(self, client, admin_token):
        resp = await client.post(
            BASE, headers=_hdr(admin_token),
            json=_payload(hostname="null-ipmi", ip_address="10.20.20.31", ipmi=None),
        )
        assert resp.status_code == 201
        srv_id = resp.json()["id"]
        get_ipmi = await client.get(f"{BASE}/{srv_id}/ipmi", headers=_hdr(admin_token))
        assert get_ipmi.status_code == 404


class TestAtomicity:
    @pytest.mark.parametrize(
        "bad_ipmi",
        [
            {"endpoint_url": "https://x", "username": "u", "password": "p"},  # нет kind
            {"kind": "idrac", "username": "u", "password": "p"},  # нет endpoint_url
            {"kind": "idrac", "endpoint_url": "https://x", "password": "p"},  # нет username
            {"kind": "idrac", "endpoint_url": "https://x", "username": "u"},  # нет password
            {"kind": "bogus", "endpoint_url": "https://x", "username": "u", "password": "p"},  # битый kind
        ],
    )
    async def test_invalid_ipmi_block_rejects_server_and_controller(
        self, client, admin_token, db, bad_ipmi,
    ):
        from sqlalchemy import select

        from src.models import IpmiController, Server

        resp = await client.post(
            BASE, headers=_hdr(admin_token),
            json=_payload(hostname="atomic-bad", ip_address="10.20.20.40", ipmi=bad_ipmi),
        )
        # Невалидный вложенный блок отбивается схемой ДО любой записи в БД.
        assert resp.status_code == 422
        srv = (
            await db.execute(select(Server).where(Server.hostname == "atomic-bad"))
        ).scalar_one_or_none()
        assert srv is None
        orphan = (
            await db.execute(
                select(IpmiController).where(IpmiController.endpoint_url == "https://x")
            )
        ).scalar_one_or_none()
        assert orphan is None

    async def test_duplicate_hostname_rolls_back_ipmi(
        self, client, admin_token, make_server, db,
    ):
        from sqlalchemy import select

        from src.models import IpmiController

        await make_server(department_id="dep_a", hostname="dup-ipmi-host")
        marker_url = "https://orphan-check.example.com"
        resp = await client.post(
            BASE, headers=_hdr(admin_token),
            json=_payload(hostname="dup-ipmi-host", ip_address="10.20.20.41",
                          ipmi=_ipmi_block(endpoint_url=marker_url)),
        )
        assert resp.status_code == 409
        assert resp.json().get("error_code") == "SERVER_DUPLICATE"
        # Контроллер с нашим уникальным endpoint_url не должен остаться: сервер
        # упал на UNIQUE(hostname), вся транзакция (включая IPMI) откатилась.
        orphan = (
            await db.execute(
                select(IpmiController).where(IpmiController.endpoint_url == marker_url)
            )
        ).scalar_one_or_none()
        assert orphan is None


@pytest.fixture
def captured_emits(monkeypatch):
    """Захватывает `audit_service.emit` server-сервиса для проверки action-key'ев."""
    captured: list[dict] = []

    def fake_emit(action, actor_id=None, **kwargs):
        captured.append({"action": action, "actor_id": actor_id, **kwargs})

    import src.services.audit_service as audit_mod
    monkeypatch.setattr(audit_mod, "emit", fake_emit)
    monkeypatch.setattr("src.services.server.audit_service.emit", fake_emit)
    return captured


class TestAuditEmission:
    async def test_create_with_ipmi_emits_both_events(
        self, client, admin_token, captured_emits,
    ):
        resp = await client.post(
            BASE, headers=_hdr(admin_token),
            json=_payload(hostname="audit-ipmi", ip_address="10.20.20.50",
                          ipmi=_ipmi_block(bmc_vendor="idrac")),
        )
        assert resp.status_code == 201
        srv_id = resp.json()["id"]

        server_events = [
            e for e in captured_emits
            if e["action"] == "server.create" and e.get("status") == "success"
        ]
        assert len(server_events) == 1

        ipmi_events = [
            e for e in captured_emits
            if e["action"] == "ipmi_controller.create" and e.get("status") == "success"
        ]
        assert len(ipmi_events) == 1
        ev = ipmi_events[0]
        assert ev["target_type"] == "ipmi_controller"
        assert ev["allowed"] is True
        assert ev["details"]["server_id"] == srv_id
        assert ev["details"]["kind"] == "idrac"
        assert ev["details"]["bmc_vendor"] == "idrac"
        assert ev["details"]["department_id"] == "dep_a"

    async def test_create_without_ipmi_emits_no_ipmi_event(
        self, client, admin_token, captured_emits,
    ):
        resp = await client.post(
            BASE, headers=_hdr(admin_token),
            json=_payload(hostname="audit-no-ipmi", ip_address="10.20.20.51"),
        )
        assert resp.status_code == 201
        ipmi_events = [
            e for e in captured_emits if e["action"] == "ipmi_controller.create"
        ]
        assert ipmi_events == []
