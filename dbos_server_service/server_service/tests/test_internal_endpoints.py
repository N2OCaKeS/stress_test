"""Интеграционные тесты `/api/server/v1/internal/*`.

Скрытые от публичного OpenAPI endpoints, обслуживают server_worker:
* `GET /internal/.../ipmi/credentials` — расшифровка пароля IPMI;
* `GET /internal/.../accounts/{aid}/password` — расшифровка пароля аккаунта;
* `POST /internal/.../password/rotate` — приём нового plaintext + сохранение.

Проверяется: include_in_schema=False, permission gates (view_credentials /
view_password / rotate_password — НЕ выданы по умолчанию никому кроме admin),
404 vs ACCOUNT_HAS_NO_PASSWORD, plaintext НЕ светится за пределы response,
`X-Target-Department-Id` cross-check (безусловный: missing → 403, mismatch → 404).
"""

from __future__ import annotations

import pytest

from src.core.config import get_settings

BASE_INT = "/api/server/v1/internal"


from tests._helpers import assert_error, auth_hdr as _hdr  # noqa: E402


# ── OpenAPI exposure ─────────────────────────────────────────────────────────

class TestOpenApiHidden:
    async def test_internal_paths_not_in_openapi_json(self, client):
        resp = await client.get("/openapi.json")
        assert resp.status_code == 200
        paths = resp.json().get("paths", {})
        assert not any(p.startswith("/api/server/v1/internal/") for p in paths), (
            "internal endpoints must be hidden from OpenAPI"
        )


# ── IPMI credentials ─────────────────────────────────────────────────────────

@pytest.mark.usefixtures("soft_dept_mode")
class TestIpmiCredentials:
    async def test_admin_role_fetches_plaintext(
        self, client, worker_pat_token, make_server, make_ipmi,
    ):
        srv = await make_server(department_id="dep_a")
        await make_ipmi(server_id=srv.id, password="dr@$ts3cret")
        resp = await client.get(
            f"{BASE_INT}/servers/{srv.id}/ipmi/credentials",
            headers=_hdr(worker_pat_token),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["password"] == "dr@$ts3cret"
        assert body["username"] == "ipmi_user"
        assert body["kind"] == "idrac"

    async def test_reader_forbidden(self, client, reader_token_a, make_server, make_ipmi):
        srv = await make_server(department_id="dep_a")
        await make_ipmi(server_id=srv.id)
        resp = await client.get(
            f"{BASE_INT}/servers/{srv.id}/ipmi/credentials",
            headers=_hdr(reader_token_a),
        )
        assert_error(resp, 403, "PERMISSION_DENIED")

    async def test_operator_forbidden(self, client, operator_token_a, make_server, make_ipmi):
        """operator не получает view_credentials по default."""
        srv = await make_server(department_id="dep_a")
        await make_ipmi(server_id=srv.id)
        resp = await client.get(
            f"{BASE_INT}/servers/{srv.id}/ipmi/credentials",
            headers=_hdr(operator_token_a),
        )
        assert_error(resp, 403, "PERMISSION_DENIED")

    async def test_no_token_returns_401(self, client, make_server):
        srv = await make_server(department_id="dep_a")
        resp = await client.get(f"{BASE_INT}/servers/{srv.id}/ipmi/credentials")
        assert_error(resp, 401, "ACCESS_TOKEN_MISSING")

    async def test_server_not_found_returns_404(self, client, worker_pat_token):
        resp = await client.get(
            f"{BASE_INT}/servers/srv_ghost/ipmi/credentials",
            headers=_hdr(worker_pat_token),
        )
        assert_error(resp, 404, "SERVER_NOT_FOUND")

    async def test_no_ipmi_controller_returns_404(
        self, client, worker_pat_token, make_server,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.get(
            f"{BASE_INT}/servers/{srv.id}/ipmi/credentials",
            headers=_hdr(worker_pat_token),
        )
        assert_error(resp, 404, "NO_IPMI_CONTROLLER")


# ── Account password ─────────────────────────────────────────────────────────

@pytest.mark.usefixtures("soft_dept_mode")
class TestAccountPassword:
    async def test_admin_fetches_plaintext(
        self, client, worker_pat_token, make_server, make_account,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="ops", password="super-secret-pwd")
        resp = await client.get(
            f"{BASE_INT}/servers/{srv.id}/accounts/{acc.id}/password",
            headers=_hdr(worker_pat_token),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["login"] == "ops"
        assert body["password"] == "super-secret-pwd"

    async def test_account_has_no_password_returns_404(
        self, client, worker_pat_token, make_server, make_account,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, password=None)
        resp = await client.get(
            f"{BASE_INT}/servers/{srv.id}/accounts/{acc.id}/password",
            headers=_hdr(worker_pat_token),
        )
        assert_error(resp, 404, "ACCOUNT_HAS_NO_PASSWORD")

    async def test_discovered_account_without_password_returns_empty(
        self, client, worker_pat_token, make_server, make_account, db,
    ):
        # Discovered-аккаунт (`source=discovered`) приходит из инвентаризации
        # без пароля — provision на managed-сервер не должен валить task'у:
        # endpoint отдаёт пустой password, worker трактует как «chpasswd skip».
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, password=None)
        acc.source = "discovered"
        await db.flush()
        resp = await client.get(
            f"{BASE_INT}/servers/{srv.id}/accounts/{acc.id}/password",
            headers=_hdr(worker_pat_token),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["login"] == acc.login
        assert body["password"] == ""

    async def test_account_on_different_server_returns_404(
        self, client, worker_pat_token, make_server, make_account,
    ):
        srv_a = await make_server(department_id="dep_a")
        srv_b = await make_server(department_id="dep_b")
        acc = await make_account(server_id=srv_b.id)
        resp = await client.get(
            f"{BASE_INT}/servers/{srv_a.id}/accounts/{acc.id}/password",
            headers=_hdr(worker_pat_token),
        )
        assert_error(resp, 404, "ACCOUNT_NOT_FOUND")

    async def test_reader_forbidden(self, client, reader_token_a, make_server, make_account):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id)
        resp = await client.get(
            f"{BASE_INT}/servers/{srv.id}/accounts/{acc.id}/password",
            headers=_hdr(reader_token_a),
        )
        assert_error(resp, 403, "PERMISSION_DENIED")


# ── Rotate password ──────────────────────────────────────────────────────────

@pytest.mark.usefixtures("soft_dept_mode")
class TestRotatePassword:
    async def test_admin_rotates(
        self, client, worker_pat_token, make_server, make_account, db,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, password="old-pwd")
        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/accounts/{acc.id}/password/rotate",
            headers=_hdr(worker_pat_token),
            json={"password": "NewPwdRotated1234"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body.get("rotated_at")

        # После ротации новый GET вернёт обновлённый plaintext
        fetch = await client.get(
            f"{BASE_INT}/servers/{srv.id}/accounts/{acc.id}/password",
            headers=_hdr(worker_pat_token),
        )
        assert fetch.json()["password"] == "NewPwdRotated1234"

    async def test_rotate_for_account_without_initial_password_works(
        self, client, worker_pat_token, make_server, make_account,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, password=None)
        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/accounts/{acc.id}/password/rotate",
            headers=_hdr(worker_pat_token),
            json={"password": "FirstTimePwd1234"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body.get("rotated_at")

    async def test_operator_can_rotate(self, client, operator_token_a, make_server, make_account):
        """operator имеет default grant `server_account.rotate_password`."""
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, password="old")
        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/accounts/{acc.id}/password/rotate",
            headers=_hdr(operator_token_a),
            json={"password": "OpsNewPwd1234"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body.get("rotated_at")

    async def test_reader_cannot_rotate(self, client, reader_token_a, make_server, make_account):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, password="old")
        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/accounts/{acc.id}/password/rotate",
            headers=_hdr(reader_token_a),
            json={"password": "ReaderTry1234"},
        )
        assert_error(resp, 403, "PERMISSION_DENIED")

    async def test_rotate_account_not_found_returns_404(
        self, client, worker_pat_token, make_server,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/accounts/acc_ghost/password/rotate",
            headers=_hdr(worker_pat_token),
            json={"password": "GhostPwd1234"},
        )
        assert_error(resp, 404, "ACCOUNT_NOT_FOUND")


# ── X-Target-Department-Id cross-check (soft + strict) ───────────────────────

@pytest.fixture
def strict_dept_mode(monkeypatch):
    """Enable `internal_require_dept_header=True` for the duration of the test.

    Clears the lru_cache on `get_settings` before and after so the override
    propagates and is reset cleanly.
    """
    monkeypatch.setenv("INTERNAL_REQUIRE_DEPT_HEADER", "true")
    get_settings.cache_clear()  # type: ignore[attr-defined]
    yield
    get_settings.cache_clear()  # type: ignore[attr-defined]


def _hdr_with_dept(token: str, dept: str | None) -> dict[str, str]:
    return _hdr(token, dept=dept)


class TestTargetDeptHeaderScoping:
    """`X-Target-Department-Id` — единственный cross-dept гард internal-эндпоинтов,
    enforce'ится безусловно. Отдел самого воркер-бота в авторизации не участвует:
    один глобальный бот обслуживает серверы всех отделов.

      * заголовок отсутствует → 403 TARGET_DEPARTMENT_HEADER_REQUIRED;
      * заголовок ≠ server.department_id → 404 (маска not-found);
      * заголовок = server.department_id → операция проходит, даже если бот
        числится в другом отделе.
    """

    async def test_matched_header_returns_200(
        self, client, worker_pat_token, make_server, make_ipmi,
    ):
        srv = await make_server(department_id="dep_a")
        await make_ipmi(server_id=srv.id, password="ipmi-match-pwd")
        resp = await client.get(
            f"{BASE_INT}/servers/{srv.id}/ipmi/credentials",
            headers=_hdr_with_dept(worker_pat_token, "dep_a"),
        )
        assert resp.status_code == 200
        assert resp.json()["password"] == "ipmi-match-pwd"

    async def test_no_header_returns_403(
        self, client, worker_pat_token, make_server, make_ipmi,
    ):
        srv = await make_server(department_id="dep_a")
        await make_ipmi(server_id=srv.id, password="ipmi-no-hdr")
        resp = await client.get(
            f"{BASE_INT}/servers/{srv.id}/ipmi/credentials",
            headers=_hdr(worker_pat_token),
        )
        assert_error(resp, 403, "TARGET_DEPARTMENT_HEADER_REQUIRED")
        assert "ipmi-no-hdr" not in resp.text

    async def test_mismatched_header_returns_404(
        self, client, worker_pat_token, make_server, make_ipmi,
    ):
        srv = await make_server(department_id="dep_a")
        await make_ipmi(server_id=srv.id, password="ipmi-mismatch-pwd")
        resp = await client.get(
            f"{BASE_INT}/servers/{srv.id}/ipmi/credentials",
            headers=_hdr_with_dept(worker_pat_token, "dep_b"),  # wrong dept
        )
        assert_error(resp, 404, "SERVER_NOT_FOUND")
        assert "ipmi-mismatch-pwd" not in resp.text

    async def test_worker_from_other_dept_succeeds_with_matching_header(
        self, client, worker_pat_token, make_server, make_ipmi, dept_b,
    ):
        """Ключевой мультидепт-кейс: бот числится в dep_a, сервер — в dep_b,
        заголовок = dep_b → УСПЕХ. До фикса actor-check отбивал это 404."""
        srv = await make_server(department_id="dep_b")
        await make_ipmi(server_id=srv.id, password="cross-dept-ok")
        resp = await client.get(
            f"{BASE_INT}/servers/{srv.id}/ipmi/credentials",
            headers=_hdr_with_dept(worker_pat_token, "dep_b"),
        )
        assert resp.status_code == 200
        assert resp.json()["password"] == "cross-dept-ok"

    async def test_worker_from_other_dept_no_header_returns_403(
        self, client, worker_pat_token, make_server, make_ipmi, dept_b,
    ):
        srv = await make_server(department_id="dep_b")
        await make_ipmi(server_id=srv.id, password="cross-dept-secret")
        resp = await client.get(
            f"{BASE_INT}/servers/{srv.id}/ipmi/credentials",
            headers=_hdr(worker_pat_token),
        )
        assert_error(resp, 403, "TARGET_DEPARTMENT_HEADER_REQUIRED")
        assert "cross-dept-secret" not in resp.text

    async def test_worker_from_other_dept_wrong_header_returns_404(
        self, client, worker_pat_token, make_server, make_ipmi, dept_b,
    ):
        """Сервер dep_b, заголовок dep_c (≠ server dept) → 404-маска."""
        srv = await make_server(department_id="dep_b")
        await make_ipmi(server_id=srv.id, password="cross-dept-secret")
        resp = await client.get(
            f"{BASE_INT}/servers/{srv.id}/ipmi/credentials",
            headers=_hdr_with_dept(worker_pat_token, "dep_c"),
        )
        assert_error(resp, 404, "SERVER_NOT_FOUND")
        assert "cross-dept-secret" not in resp.text


class TestTargetDeptHeaderStrictMode:
    """Header-scoping на остальных read/rotate endpoint'ах: missing → 403
    TARGET_DEPARTMENT_HEADER_REQUIRED, mismatch → 404 (маска not-found),
    matched → 200. Enforce безусловно.
    """

    async def test_missing_header_returns_403_for_ipmi(
        self, client, worker_pat_token, make_server, make_ipmi, strict_dept_mode,
    ):
        srv = await make_server(department_id="dep_a")
        await make_ipmi(server_id=srv.id, password="should-not-leak")
        resp = await client.get(
            f"{BASE_INT}/servers/{srv.id}/ipmi/credentials",
            headers=_hdr(worker_pat_token),
        )
        assert_error(resp, 403, "TARGET_DEPARTMENT_HEADER_REQUIRED")

    async def test_mismatched_header_returns_404_for_ipmi(
        self, client, worker_pat_token, make_server, make_ipmi, strict_dept_mode,
    ):
        srv = await make_server(department_id="dep_a")
        await make_ipmi(server_id=srv.id, password="should-not-leak")
        resp = await client.get(
            f"{BASE_INT}/servers/{srv.id}/ipmi/credentials",
            headers=_hdr_with_dept(worker_pat_token, "dep_b"),
        )
        assert_error(resp, 404, "SERVER_NOT_FOUND")
        # Plaintext password MUST NOT appear in the 404 response.
        assert "should-not-leak" not in resp.text

    async def test_matched_header_returns_200_for_ipmi(
        self, client, worker_pat_token, make_server, make_ipmi, strict_dept_mode,
    ):
        srv = await make_server(department_id="dep_a")
        await make_ipmi(server_id=srv.id, password="strict-ok-pwd")
        resp = await client.get(
            f"{BASE_INT}/servers/{srv.id}/ipmi/credentials",
            headers=_hdr_with_dept(worker_pat_token, "dep_a"),
        )
        assert resp.status_code == 200
        assert resp.json()["password"] == "strict-ok-pwd"

    async def test_missing_header_returns_403_for_account_password(
        self, client, worker_pat_token, make_server, make_account, strict_dept_mode,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, password="acc-should-not-leak")
        resp = await client.get(
            f"{BASE_INT}/servers/{srv.id}/accounts/{acc.id}/password",
            headers=_hdr(worker_pat_token),
        )
        assert_error(resp, 403, "TARGET_DEPARTMENT_HEADER_REQUIRED")

    async def test_mismatched_header_returns_404_for_account_password(
        self, client, worker_pat_token, make_server, make_account, strict_dept_mode,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, password="acc-mismatch")
        resp = await client.get(
            f"{BASE_INT}/servers/{srv.id}/accounts/{acc.id}/password",
            headers=_hdr_with_dept(worker_pat_token, "dep_b"),
        )
        assert_error(resp, 404, "ACCOUNT_NOT_FOUND")
        assert "acc-mismatch" not in resp.text

    async def test_matched_header_returns_200_for_account_password(
        self, client, worker_pat_token, make_server, make_account, strict_dept_mode,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, password="acc-ok-pwd")
        resp = await client.get(
            f"{BASE_INT}/servers/{srv.id}/accounts/{acc.id}/password",
            headers=_hdr_with_dept(worker_pat_token, "dep_a"),
        )
        assert resp.status_code == 200
        assert resp.json()["password"] == "acc-ok-pwd"

    async def test_mismatched_header_returns_404_for_rotate(
        self, client, worker_pat_token, make_server, make_account, strict_dept_mode,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, password="old")
        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/accounts/{acc.id}/password/rotate",
            headers=_hdr_with_dept(worker_pat_token, "dep_b"),
            json={"password": "NewBlocked1234"},
        )
        assert_error(resp, 404, "ACCOUNT_NOT_FOUND")

    async def test_matched_header_allows_rotate(
        self, client, worker_pat_token, make_server, make_account, strict_dept_mode,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, password="old")
        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/accounts/{acc.id}/password/rotate",
            headers=_hdr_with_dept(worker_pat_token, "dep_a"),
            json={"password": "NewAllowed1234"},
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True


# ── Actor-agnostic: бот из чужого отдела с валидным header'ом проходит ───────

class TestWorkerBotDeptAgnostic:
    """Отдел самого бота в авторизации не участвует — важен только заголовок.
    Бот dep_b (foreign) с корректным `X-Target-Department-Id` работает с
    серверами dep_a, как и «родной» бот; неверный/отсутствующий заголовок
    отбивается одинаково для любого бота.
    """

    async def test_foreign_bot_matching_header_succeeds(
        self, client, make_token, make_server, make_account,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, password="dep-a-pwd")
        foreign_token = make_token(
            department_id="dep_b",
            service_roles={"server_service": ["admin"]},
        )
        resp = await client.get(
            f"{BASE_INT}/servers/{srv.id}/accounts/{acc.id}/password",
            headers=_hdr_with_dept(foreign_token, "dep_a"),
        )
        assert resp.status_code == 200
        assert resp.json()["password"] == "dep-a-pwd"

    async def test_foreign_bot_no_header_returns_403(
        self, client, make_token, make_server, make_account,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, password="leak-target")
        foreign_token = make_token(
            department_id="dep_b",
            service_roles={"server_service": ["admin"]},
        )
        resp = await client.get(
            f"{BASE_INT}/servers/{srv.id}/accounts/{acc.id}/password",
            headers=_hdr(foreign_token),
        )
        assert_error(resp, 403, "TARGET_DEPARTMENT_HEADER_REQUIRED")
        assert "leak-target" not in resp.text

    async def test_foreign_bot_wrong_header_returns_404(
        self, client, make_token, make_server, make_account,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, password="leak-target")
        foreign_token = make_token(
            department_id="dep_b",
            service_roles={"server_service": ["admin"]},
        )
        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/accounts/{acc.id}/password/rotate",
            headers=_hdr_with_dept(foreign_token, "dep_b"),
            json={"password": "CrossDeptInj5678"},
        )
        assert_error(resp, 404, "ACCOUNT_NOT_FOUND")
        assert "leak-target" not in resp.text
