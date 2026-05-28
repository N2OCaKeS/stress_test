"""E2E cluster D — /server-accounts CRUD + M2M link/unlink + view_password.

Endpoint paths:
  POST   /api/server/v1/server-accounts
  GET    /api/server/v1/server-accounts?server_id=...
  GET    /api/server/v1/server-accounts/{account_id}
  PATCH  /api/server/v1/server-accounts/{account_id}
  DELETE /api/server/v1/server-accounts/{account_id}
  POST   /api/server/v1/server-accounts/{account_id}/servers   (link)
  DELETE /api/server/v1/server-accounts/{account_id}/servers   (unlink)

Invariants exercised:
  * matrix: admin / operator create, reader / guest cannot
  * GET /{id} without view_password → password_b64 == null
  * GET /{id} with view_password (admin role has it via default seed) → b64 +
    CRITICAL audit `server_account.password_revealed`
  * link: duplicate login per server → 409; cross-dept server_id → 404
  * unlink: leaving zero servers → 409 ACCOUNT_NO_SERVERS
  * cross-dept GET → 404
"""

from __future__ import annotations

import base64

import httpx
import pytest

from tests.integration._helpers_D_server import (
    TenantBundle,
    build_tenants,
    create_server_as,
    db_count,
    db_row,
    expect_audit_event,
    make_account_body,
    poll_audit_event,
)


_SA = "/api/server/v1/server-accounts"


@pytest.fixture(scope="class")
def tenants(
    auth_client: httpx.Client, admin_token: str, make_user, login_token,
) -> TenantBundle:
    return build_tenants(
        auth_client=auth_client, admin_token=admin_token,
        make_user=make_user, login_token=login_token,
    )


@pytest.mark.usefixtures("reset_state")
class TestServerAccountCreate:
    def test_admin_create_201(
        self, server_client, server_db_engine, tenants: TenantBundle,
    ):
        srv = create_server_as(server_client, tenants.admin_a)
        body = make_account_body(server_ids=[srv["id"]], login="ops")
        r = server_client.post(
            _SA, json=body, headers=tenants.admin_a.headers(),
        )
        assert r.status_code == 201, r.text
        data = r.json()
        assert data["login"] == "ops"
        assert data["server_ids"] == [srv["id"]]
        # Plaintext / encrypted password never in response body
        assert "password" not in data
        assert "password_encrypted" not in data
        # By default (admin role HAS view_password, but POST response doesn't reveal)
        # On create the response should not echo password; b64 is for GET-card.
        # DB row
        assert db_row(server_db_engine, "server_accounts", id=data["id"]) is not None

    def test_create_on_multiple_servers(
        self, server_client, tenants: TenantBundle,
    ):
        s1 = create_server_as(server_client, tenants.admin_a)
        s2 = create_server_as(server_client, tenants.admin_a)
        body = make_account_body(server_ids=[s1["id"], s2["id"]], login="multi")
        r = server_client.post(
            _SA, json=body, headers=tenants.admin_a.headers(),
        )
        assert r.status_code == 201, r.text
        assert set(r.json()["server_ids"]) == {s1["id"], s2["id"]}

    def test_create_cross_dept_server_404(
        self, server_client, tenants: TenantBundle,
    ):
        s_b = create_server_as(server_client, tenants.admin_b)
        # admin_a tries to create account linked to a server in dept_b
        body = make_account_body(server_ids=[s_b["id"]], login="leaky")
        r = server_client.post(
            _SA, json=body, headers=tenants.admin_a.headers(),
        )
        assert r.status_code == 404, r.text

    def test_operator_can_create(self, server_client, tenants: TenantBundle):
        srv = create_server_as(server_client, tenants.admin_a)
        r = server_client.post(
            _SA,
            json=make_account_body(server_ids=[srv["id"]], login="opacct"),
            headers=tenants.operator_a.headers(),
        )
        assert r.status_code == 201, r.text

    def test_reader_cannot_create_403(self, server_client, tenants: TenantBundle):
        srv = create_server_as(server_client, tenants.admin_a)
        r = server_client.post(
            _SA,
            json=make_account_body(server_ids=[srv["id"]], login="readeracct"),
            headers=tenants.reader_a.headers(),
        )
        assert r.status_code == 403, r.text

    def test_grant_sudo_requires_action(self, server_client, tenants: TenantBundle):
        """has_sudo=True requires `grant_sudo`; operator (no grant_sudo) → 403."""
        srv = create_server_as(server_client, tenants.admin_a)
        # Operator without grant_sudo
        r_op = server_client.post(
            _SA,
            json=make_account_body(
                server_ids=[srv["id"]], login="sudoer1", has_sudo=True,
            ),
            headers=tenants.operator_a.headers(),
        )
        assert r_op.status_code == 403, r_op.text
        # Admin (has grant_sudo by default seed)
        r_adm = server_client.post(
            _SA,
            json=make_account_body(
                server_ids=[srv["id"]], login="sudoer2", has_sudo=True,
            ),
            headers=tenants.admin_a.headers(),
        )
        assert r_adm.status_code == 201, r_adm.text
        assert r_adm.json()["has_sudo"] is True


@pytest.mark.usefixtures("reset_state")
class TestServerAccountGet:
    def test_get_account_no_view_password_returns_null(
        self, server_client, loging_db_engine, tenants: TenantBundle,
    ):
        """operator has `view` but not `view_password` → password_b64 == None."""
        srv = create_server_as(server_client, tenants.admin_a)
        # Create as admin so the account exists
        created = server_client.post(
            _SA,
            json=make_account_body(server_ids=[srv["id"]], login="rdpass",
                                   password="ManagedPass1!"),
            headers=tenants.admin_a.headers(),
        )
        assert created.status_code == 201
        acct_id = created.json()["id"]
        # Operator fetches — has view, no view_password
        r = server_client.get(
            f"{_SA}/{acct_id}", headers=tenants.operator_a.headers(),
        )
        assert r.status_code == 200, r.text
        assert r.json()["password_b64"] is None
        # No password_revealed audit should be emitted for the no-view_password caller
        ev = poll_audit_event(
            loging_db_engine, action="server_account.password_revealed",
            actor_id=tenants.operator_a.user_id, target_id=acct_id,
            retries=4, delay=0.25,
        )
        assert ev is None, f"unexpected password_revealed audit: {ev}"

    def test_get_account_with_view_password_returns_b64_and_critical_audit(
        self, server_client, loging_db_engine, tenants: TenantBundle,
    ):
        """admin role has view_password (seed migration) → password_b64 + CRITICAL audit."""
        srv = create_server_as(server_client, tenants.admin_a)
        password = "S3cretManaged!"
        created = server_client.post(
            _SA,
            json=make_account_body(server_ids=[srv["id"]], login="revpass",
                                   password=password),
            headers=tenants.admin_a.headers(),
        )
        assert created.status_code == 201
        acct_id = created.json()["id"]
        r = server_client.get(
            f"{_SA}/{acct_id}", headers=tenants.admin_a.headers(),
        )
        assert r.status_code == 200, r.text
        b64 = r.json()["password_b64"]
        assert b64 is not None
        decoded = base64.b64decode(b64).decode("utf-8")
        assert decoded == password
        # CRITICAL audit
        ev = expect_audit_event(
            loging_db_engine, action="server_account.password_revealed",
            target_id=acct_id, actor_id=tenants.admin_a.user_id,
            status="success",
        )
        assert ev["severity"] == "CRITICAL"

    def test_guest_cannot_get_403(self, server_client, tenants: TenantBundle):
        srv = create_server_as(server_client, tenants.admin_a)
        created = server_client.post(
            _SA, json=make_account_body(server_ids=[srv["id"]], login="gst"),
            headers=tenants.admin_a.headers(),
        )
        acct_id = created.json()["id"]
        r = server_client.get(
            f"{_SA}/{acct_id}", headers=tenants.guest_a.headers(),
        )
        assert r.status_code == 403, r.text

    def test_cross_dept_get_404(self, server_client, tenants: TenantBundle):
        srv_a = create_server_as(server_client, tenants.admin_a)
        created = server_client.post(
            _SA, json=make_account_body(server_ids=[srv_a["id"]], login="xdept"),
            headers=tenants.admin_a.headers(),
        )
        acct_id = created.json()["id"]
        r = server_client.get(
            f"{_SA}/{acct_id}", headers=tenants.admin_b.headers(),
        )
        assert r.status_code == 404, r.text

    def test_list_by_server_id(self, server_client, tenants: TenantBundle):
        srv = create_server_as(server_client, tenants.admin_a)
        a1 = server_client.post(
            _SA, json=make_account_body(server_ids=[srv["id"]], login="x1"),
            headers=tenants.admin_a.headers(),
        ).json()
        a2 = server_client.post(
            _SA, json=make_account_body(server_ids=[srv["id"]], login="x2"),
            headers=tenants.admin_a.headers(),
        ).json()
        r = server_client.get(
            _SA, params={"server_id": srv["id"]},
            headers=tenants.admin_a.headers(),
        )
        assert r.status_code == 200, r.text
        ids = {item["id"] for item in r.json()["items"]}
        assert {a1["id"], a2["id"]} <= ids


@pytest.mark.usefixtures("reset_state")
class TestServerAccountPatchDelete:
    def test_admin_patch(self, server_client, tenants: TenantBundle):
        srv = create_server_as(server_client, tenants.admin_a)
        created = server_client.post(
            _SA, json=make_account_body(server_ids=[srv["id"]], login="patchme",
                                        unix_groups=["wheel"]),
            headers=tenants.admin_a.headers(),
        )
        acct_id = created.json()["id"]
        r = server_client.patch(
            f"{_SA}/{acct_id}",
            json={"unix_groups": ["wheel", "docker"], "shell": "/bin/zsh"},
            headers=tenants.admin_a.headers(),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert set(body["unix_groups"]) == {"wheel", "docker"}
        assert body["shell"] == "/bin/zsh"

    def test_reader_cannot_patch_403(self, server_client, tenants: TenantBundle):
        srv = create_server_as(server_client, tenants.admin_a)
        created = server_client.post(
            _SA, json=make_account_body(server_ids=[srv["id"]], login="rdpatch"),
            headers=tenants.admin_a.headers(),
        )
        acct_id = created.json()["id"]
        r = server_client.patch(
            f"{_SA}/{acct_id}", json={"shell": "/bin/sh"},
            headers=tenants.reader_a.headers(),
        )
        assert r.status_code == 403, r.text

    def test_delete_account(
        self, server_client, server_db_engine, tenants: TenantBundle,
    ):
        srv = create_server_as(server_client, tenants.admin_a)
        created = server_client.post(
            _SA, json=make_account_body(server_ids=[srv["id"]], login="delme"),
            headers=tenants.admin_a.headers(),
        )
        acct_id = created.json()["id"]
        r = server_client.delete(
            f"{_SA}/{acct_id}", headers=tenants.admin_a.headers(),
        )
        assert r.status_code == 200, r.text
        assert db_row(server_db_engine, "server_accounts", id=acct_id) is None

    def test_operator_cannot_delete_403(self, server_client, tenants: TenantBundle):
        srv = create_server_as(server_client, tenants.admin_a)
        created = server_client.post(
            _SA, json=make_account_body(server_ids=[srv["id"]], login="opdel"),
            headers=tenants.admin_a.headers(),
        )
        acct_id = created.json()["id"]
        r = server_client.delete(
            f"{_SA}/{acct_id}", headers=tenants.operator_a.headers(),
        )
        assert r.status_code == 403, r.text

    def test_cross_dept_patch_404(self, server_client, tenants: TenantBundle):
        srv = create_server_as(server_client, tenants.admin_a)
        created = server_client.post(
            _SA, json=make_account_body(server_ids=[srv["id"]], login="xpatch"),
            headers=tenants.admin_a.headers(),
        )
        acct_id = created.json()["id"]
        r = server_client.patch(
            f"{_SA}/{acct_id}", json={"shell": "/bin/sh"},
            headers=tenants.admin_b.headers(),
        )
        assert r.status_code == 404, r.text


@pytest.mark.usefixtures("reset_state")
class TestServerAccountLinkUnlink:
    """POST/DELETE /server-accounts/{id}/servers — M2M link/unlink."""

    def test_link_additional_servers(self, server_client, tenants: TenantBundle):
        s1 = create_server_as(server_client, tenants.admin_a)
        s2 = create_server_as(server_client, tenants.admin_a)
        s3 = create_server_as(server_client, tenants.admin_a)
        # Start linked to s1 only
        created = server_client.post(
            _SA, json=make_account_body(server_ids=[s1["id"]], login="linker"),
            headers=tenants.admin_a.headers(),
        )
        acct_id = created.json()["id"]
        # Link s2 + s3
        r = server_client.post(
            f"{_SA}/{acct_id}/servers",
            json={"server_ids": [s2["id"], s3["id"]]},
            headers=tenants.admin_a.headers(),
        )
        assert r.status_code == 200, r.text
        assert set(r.json()["server_ids"]) == {s1["id"], s2["id"], s3["id"]}

    def test_link_duplicate_login_per_server_409(
        self, server_client, tenants: TenantBundle,
    ):
        """Two accounts with same login can exist if on different servers,
        but linking second one to the first server's login collides."""
        s1 = create_server_as(server_client, tenants.admin_a)
        s2 = create_server_as(server_client, tenants.admin_a)
        # account A on s1 with login "shared"
        ra = server_client.post(
            _SA, json=make_account_body(server_ids=[s1["id"]], login="shared"),
            headers=tenants.admin_a.headers(),
        )
        assert ra.status_code == 201, ra.text
        # account B on s2 with login "shared"
        rb = server_client.post(
            _SA, json=make_account_body(server_ids=[s2["id"]], login="shared"),
            headers=tenants.admin_a.headers(),
        )
        assert rb.status_code == 201, rb.text
        acct_b_id = rb.json()["id"]
        # Now try to link account B onto s1 — login "shared" already used there
        r = server_client.post(
            f"{_SA}/{acct_b_id}/servers",
            json={"server_ids": [s1["id"]]},
            headers=tenants.admin_a.headers(),
        )
        assert r.status_code == 409, r.text

    def test_link_cross_dept_server_404(self, server_client, tenants: TenantBundle):
        s_a = create_server_as(server_client, tenants.admin_a)
        s_b = create_server_as(server_client, tenants.admin_b)
        created = server_client.post(
            _SA, json=make_account_body(server_ids=[s_a["id"]], login="xlink"),
            headers=tenants.admin_a.headers(),
        )
        acct_id = created.json()["id"]
        # try to link cross-dept server
        r = server_client.post(
            f"{_SA}/{acct_id}/servers",
            json={"server_ids": [s_b["id"]]},
            headers=tenants.admin_a.headers(),
        )
        assert r.status_code == 404, r.text

    def test_unlink_keeps_one_server(self, server_client, tenants: TenantBundle):
        s1 = create_server_as(server_client, tenants.admin_a)
        s2 = create_server_as(server_client, tenants.admin_a)
        created = server_client.post(
            _SA, json=make_account_body(
                server_ids=[s1["id"], s2["id"]], login="ulink",
            ),
            headers=tenants.admin_a.headers(),
        )
        acct_id = created.json()["id"]
        r = server_client.delete(
            f"{_SA}/{acct_id}/servers",
            json={"server_ids": [s2["id"]]},
            headers=tenants.admin_a.headers(),
        )
        assert r.status_code == 200, r.text
        assert r.json()["server_ids"] == [s1["id"]]

    def test_unlink_last_server_409(self, server_client, tenants: TenantBundle):
        s1 = create_server_as(server_client, tenants.admin_a)
        created = server_client.post(
            _SA, json=make_account_body(server_ids=[s1["id"]], login="lastone"),
            headers=tenants.admin_a.headers(),
        )
        acct_id = created.json()["id"]
        r = server_client.delete(
            f"{_SA}/{acct_id}/servers",
            json={"server_ids": [s1["id"]]},
            headers=tenants.admin_a.headers(),
        )
        assert r.status_code == 409, r.text
        assert r.json()["error_code"] == "ACCOUNT_NO_SERVERS"
