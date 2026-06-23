"""Интеграционные тесты `/api/server/v1/permissions`.

* GET / — список всех grants (требует VIEW на entity `permission`).
* GET /{entity_type} — фильтр по entity (422 при unknown entity_type).
* PUT /{entity}/{role}/{action} — grant + валидация по `ENTITY_ACTIONS` whitelist,
  идемпотентность (повтор возвращает existing), требует PERMISSION_GRANT.
* DELETE /{entity}/{role}/{action} — revoke + 404 при несуществующем grant,
  требует PERMISSION_REVOKE.

Авторизация: write-действия на entity `permission` по умолчанию выданы только
admin'у — другие роли получают 403 (см. `test_entity_actions.py::TestSensitiveActionsClosed`).
"""

from __future__ import annotations

BASE = "/api/server/v1/permissions"


from tests._helpers import assert_error, auth_hdr as _hdr  # noqa: E402


# ── GET / ────────────────────────────────────────────────────────────────────

class TestListAll:
    async def test_dept_admin_lists_default_grants(self, client, admin_token):
        """`admin_token` (department_admin) с service-role `admin` видит весь
        список grants — у admin есть VIEW на entity `permission`.

        Раньше тут был ``test_account_admin_lists_default_grants``.
        """
        resp = await client.get(BASE, headers=_hdr(admin_token))
        assert resp.status_code == 200
        body = resp.json()
        assert body["described"] is False
        rows = body["items"]
        assert body["total"] == len(rows)
        # Baseline (831ba55543e9) + позднее: drop boot_order/pxe/reinstall
        # (b8d4e3f9a712) убирает 5 admin server-action'ов и 1 worker_bot row;
        # reveal_* гранты сняты (c3f9b1a8d420), пароль теперь раскрывается
        # через view_password / view_credentials. Структурно: worker_bot —
        # 8 строк (4 secret-access + server.inventory_submit +
        # server_account.inventory_submit для user-инвентаризации +
        # server_account.provision_on_host для useradd/usermod/userdel callback +
        # server.prepare_callback для бутстрапа управления),
        # admin строго больше, общая сумма ≥ обоих.
        admin_grants = [r for r in rows if r["role"] == "admin"]
        worker_bot_grants = [r for r in rows if r["role"] == "worker_bot"]
        assert len(worker_bot_grants) == 8
        assert len(admin_grants) > len(worker_bot_grants)
        assert len(rows) >= len(admin_grants) + len(worker_bot_grants)

    async def test_admin_role_lists(self, client, admin_role_token_a):
        resp = await client.get(BASE, headers=_hdr(admin_role_token_a))
        assert resp.status_code == 200

    async def test_reader_can_list(self, client, reader_token_a):
        """`reader` имеет VIEW на каждый entity (включая `permission`) по default seed."""
        resp = await client.get(BASE, headers=_hdr(reader_token_a))
        assert resp.status_code == 200

    async def test_operator_can_list(self, client, operator_token_a):
        """`operator` default grants на entity `permission`: только `view` есть → 200."""
        resp = await client.get(BASE, headers=_hdr(operator_token_a))
        assert resp.status_code == 200

    async def test_guest_forbidden(self, client, guest_token_a):
        """`guest` не имеет ни одного grant → 403."""
        resp = await client.get(BASE, headers=_hdr(guest_token_a))
        assert_error(resp, 403, "PERMISSION_DENIED")

    async def test_no_token_returns_401(self, client):
        resp = await client.get(BASE)
        assert_error(resp, 401, "ACCESS_TOKEN_MISSING")


# ── GET /{entity_type} ───────────────────────────────────────────────────────

class TestListForEntity:
    async def test_filters_by_entity(self, client, admin_token):
        resp = await client.get(f"{BASE}/server", headers=_hdr(admin_token))
        assert resp.status_code == 200
        body = resp.json()
        assert body["described"] is False
        rows = body["items"]
        assert body["total"] == len(rows)
        assert all(r["entity_type"] == "server" for r in rows)
        # admin для server: baseline 17 actions, потом split добавил
        # reinstall_status_submit (18), затем b8d4e3f9a712 удалил 5 (boot_order_view,
        # boot_order_set, pxe_boot, reinstall_start, reinstall_status_submit) → 13,
        # e3f8c4b21a07 добавил view_drift → 14, d6c1f8a3b9e4 добавил console → 15,
        # d4f1a9c2e7b8 добавил manage_packages → 16.
        admin = [r for r in rows if r["role"] == "admin"]
        assert len(admin) == 16

    async def test_unknown_entity_type_422(self, client, admin_token):
        resp = await client.get(f"{BASE}/nonexistent_type", headers=_hdr(admin_token))
        assert_error(resp, 422, "UNKNOWN_ENTITY_TYPE")

    async def test_no_grants_returns_empty(self, client, admin_token, db):
        """После удаления всех grants по entity — 200 + []."""
        from src.models import EntityPermission
        from sqlalchemy import delete
        await db.execute(delete(EntityPermission).where(EntityPermission.entity_type == "os_version"))
        await db.commit()
        resp = await client.get(f"{BASE}/os_version", headers=_hdr(admin_token))
        assert resp.status_code == 200
        body = resp.json()
        assert body["items"] == []
        assert body["total"] == 0
        assert body["described"] is False


# ── PUT /{entity}/{role}/{action} ────────────────────────────────────────────

class TestGrant:
    async def test_admin_grants_new_combination(self, client, admin_token):
        """Default seed: operator НЕ имеет server.delete — grant'нем для dep_a.

        `admin_token` теперь department_admin с service-role `admin` в dep_a —
        grant пишется в его scope (`department_id="dep_a"`). Возвращается 200.
        """
        resp = await client.put(
            f"{BASE}/server/operator/delete", headers=_hdr(admin_token),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["entity_type"] == "server"
        assert body["role"] == "operator"
        assert body["action"] == "delete"

    async def test_unknown_action_for_entity_422(self, client, admin_token):
        resp = await client.put(
            f"{BASE}/server/operator/UNKNOWN_ACT", headers=_hdr(admin_token),
        )
        assert_error(resp, 422, "INVALID_ACTION_FOR_ENTITY")

    async def test_grant_for_unknown_entity_422(self, client, admin_token):
        resp = await client.put(
            f"{BASE}/nonexistent_entity/operator/view", headers=_hdr(admin_token),
        )
        assert_error(resp, 422, "INVALID_ACTION_FOR_ENTITY")

    async def test_idempotent_grant_returns_existing(self, client, admin_token):
        """Повторный PUT на уже выданный grant возвращает existing (200), не 409.

        `admin_token` department_admin — пишет в свой dep_a scope. Первый PUT
        создаёт dep_a-row; второй находит existing и возвращает 200 с тем же id.
        Built-in system-wide grant `(server, reader, view, NULL)` из seed'а
        НЕ участвует (separate row, separate UNIQUE).
        """
        first = await client.put(f"{BASE}/server/reader/update", headers=_hdr(admin_token))
        assert first.status_code == 200
        second = await client.put(f"{BASE}/server/reader/update", headers=_hdr(admin_token))
        assert second.status_code == 200
        # тот же id (existing)
        assert first.json()["id"] == second.json()["id"]

    async def test_reader_cannot_grant(self, client, reader_token_a):
        resp = await client.put(f"{BASE}/server/reader/view", headers=_hdr(reader_token_a))
        assert_error(resp, 403, "PERMISSION_DENIED")

    async def test_operator_cannot_grant(self, client, operator_token_a):
        """`operator` имеет только view на entity `permission` — не permission_grant."""
        resp = await client.put(f"{BASE}/server/operator/delete", headers=_hdr(operator_token_a))
        assert_error(resp, 403, "PERMISSION_DENIED")


# ── DELETE /{entity}/{role}/{action} ─────────────────────────────────────────

class TestRevoke:
    async def test_admin_revokes_existing_dept_grant(self, client, admin_token):
        """`admin_token` department_admin → revoke в свой scope.

        Сначала PUT (создаём dep_a-row), потом DELETE (удаляем его). Раньше
        тут был revoke сидированного system-wide grant'а — после §7-8 фикса
        non-account_admin'ы не пишут system-wide, поэтому тест проверяет
        per-dept lifecycle, что и есть нормальный use-case department_admin'а.
        """
        put = await client.put(f"{BASE}/server/reader/update", headers=_hdr(admin_token))
        assert put.status_code == 200
        resp = await client.delete(
            f"{BASE}/server/reader/update", headers=_hdr(admin_token),
        )
        assert resp.status_code == 200

    async def test_revoke_nonexistent_returns_404(self, client, admin_token):
        resp = await client.delete(
            f"{BASE}/server/reader/delete", headers=_hdr(admin_token),
        )
        assert_error(resp, 404, "PERMISSION_NOT_FOUND")

    async def test_revoke_twice_returns_404(self, client, admin_token):
        """Сначала grant в dep_a, потом revoke (200), потом revoke ещё раз (404)."""
        await client.put(f"{BASE}/server/reader/update", headers=_hdr(admin_token))
        first = await client.delete(f"{BASE}/server/reader/update", headers=_hdr(admin_token))
        assert first.status_code == 200
        second = await client.delete(f"{BASE}/server/reader/update", headers=_hdr(admin_token))
        assert second.status_code == 404

    async def test_reader_cannot_revoke(self, client, reader_token_a):
        resp = await client.delete(
            f"{BASE}/server/reader/view", headers=_hdr(reader_token_a),
        )
        assert_error(resp, 403, "PERMISSION_DENIED")

    async def test_operator_cannot_revoke(self, client, operator_token_a):
        resp = await client.delete(
            f"{BASE}/server/reader/view", headers=_hdr(operator_token_a),
        )
        assert_error(resp, 403, "PERMISSION_DENIED")


# ── department_admin full-cycle (replaces account_admin bypass) ─────────────

class TestDepartmentAdminFullCycle:
    """`admin_token` (department_admin + service-role `admin` в dep_a) может
    делать full grant/revoke цикл внутри своего департамента.

    Раньше тут был ``TestAccountAdminBypass`` — после §7-8 фикса account_admin
    блокируется guard middleware'ом ДО endpoint'а, см.
    ``tests/integration/test_platform_admin_block.py``.
    """

    async def test_dept_admin_full_grant_revoke_cycle(self, client, admin_token):
        await client.get(BASE, headers=_hdr(admin_token))
        await client.get(f"{BASE}/server", headers=_hdr(admin_token))
        grant = await client.put(f"{BASE}/server/guest/view", headers=_hdr(admin_token))
        assert grant.status_code == 200
        rev = await client.delete(f"{BASE}/server/guest/view", headers=_hdr(admin_token))
        assert rev.status_code == 200


# ── account_admin как мета-админ матрицы прав ────────────────────────────────

class TestAccountAdminMatrixMetaAdmin:
    """Платформенный ``account_admin`` управляет матрицей прав любого отдела.

    Он не привязан к департаменту и не имеет сервисных ролей, но на
    ``/permissions*`` его пропускает guard, а ``permission_service`` снимает
    ролевую проверку и dept-isolation. Сами серверные/аккаунтные операции
    ему по-прежнему недоступны (см. ``TestAccountAdminStillBlockedOnBusiness``
    и ``tests/integration/test_platform_admin_block.py``).
    """

    async def test_account_admin_lists_matrix(self, client, account_admin_token):
        resp = await client.get(BASE, headers=_hdr(account_admin_token))
        assert resp.status_code == 200, resp.text
        assert resp.json()["described"] is False

    async def test_account_admin_lists_for_entity(self, client, account_admin_token):
        resp = await client.get(f"{BASE}/server", headers=_hdr(account_admin_token))
        assert resp.status_code == 200, resp.text
        assert all(r["entity_type"] == "server" for r in resp.json()["items"])

    async def test_account_admin_reads_catalog(self, client, account_admin_token):
        resp = await client.get(f"{BASE}/catalog", headers=_hdr(account_admin_token))
        assert resp.status_code == 200, resp.text

    async def test_account_admin_sees_all_departments(
        self, client, account_admin_token, admin_token, admin_token_b, dept_a, dept_b,
    ):
        """account_admin видит per-dept строки и dep_a, и dep_b — всю матрицу."""
        a = await client.put(f"{BASE}/server/role_a_only/view", headers=_hdr(admin_token))
        b = await client.put(f"{BASE}/server/role_b_only/view", headers=_hdr(admin_token_b))
        assert a.status_code == 200 and b.status_code == 200
        resp = await client.get(BASE, headers=_hdr(account_admin_token))
        assert resp.status_code == 200, resp.text
        ids = {r["id"] for r in resp.json()["items"]}
        assert a.json()["id"] in ids
        assert b.json()["id"] in ids

    async def test_account_admin_grants_for_target_department(
        self, client, account_admin_token, dept_b,
    ):
        """account_admin может выдать grant в чужой (любой) отдел через
        ``target_department_id`` — dept-isolation к нему не применяется."""
        resp = await client.put(
            f"{BASE}/server/operator/delete",
            headers=_hdr(account_admin_token),
            json={"target_department_id": dept_b},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["entity_type"] == "server"
        assert body["role"] == "operator"
        assert body["action"] == "delete"
        assert body["department_id"] == dept_b

    async def test_account_admin_revokes_for_target_department(
        self, client, account_admin_token, dept_a,
    ):
        """Полный grant→revoke цикл account_admin'ом для конкретного отдела."""
        put = await client.put(
            f"{BASE}/server/reader/update",
            headers=_hdr(account_admin_token),
            json={"target_department_id": dept_a},
        )
        assert put.status_code == 200, put.text
        rev = await client.delete(
            f"{BASE}/server/reader/update?target_department_id={dept_a}",
            headers=_hdr(account_admin_token),
        )
        assert rev.status_code == 200, rev.text

    async def test_account_admin_grant_invalid_action_422(
        self, client, account_admin_token, dept_a,
    ):
        """Whitelist (entity, action) действует и для account_admin."""
        resp = await client.put(
            f"{BASE}/server/operator/UNKNOWN_ACT",
            headers=_hdr(account_admin_token),
            json={"target_department_id": dept_a},
        )
        assert_error(resp, 422, "INVALID_ACTION_FOR_ENTITY")

    async def test_account_admin_revoke_nonexistent_404(
        self, client, account_admin_token, dept_a,
    ):
        resp = await client.delete(
            f"{BASE}/server/reader/delete?target_department_id={dept_a}",
            headers=_hdr(account_admin_token),
        )
        assert_error(resp, 404, "PERMISSION_NOT_FOUND")

    async def test_grant_emits_success_audit(
        self, client, account_admin_token, dept_a, monkeypatch,
    ):
        """grant account_admin'ом эмитит ``permission.grant`` success с scope'ом.

        Actor попадает в event через ``audit_context`` (его заполняет
        ``get_permission_matrix_identity``), как и у обычных grant'ов — это
        тот же путь эмиссии, что и для department_admin'а.
        """
        from tests._helpers import make_emit_capture
        captured = make_emit_capture(
            monkeypatch, "src.services.permission_service.audit_service.emit",
        )
        resp = await client.put(
            f"{BASE}/server/guest/view",
            headers=_hdr(account_admin_token),
            json={"target_department_id": dept_a},
        )
        assert resp.status_code == 200, resp.text
        grants = [e for e in captured if e["action"] == "permission.grant"
                  and e.get("status") == "success"]
        assert grants, "ожидали успешный permission.grant audit-event"
        assert grants[-1]["details"]["department_id"] == dept_a


class TestAccountAdminStillBlockedOnBusiness:
    """Regression: bypass замкнут на матрицу прав — серверные/аккаунтные
    эндпоинты account_admin'у по-прежнему отдают 403 (режет guard).
    """

    async def test_account_admin_blocked_on_servers(self, client, account_admin_token):
        resp = await client.get("/api/server/v1/servers", headers=_hdr(account_admin_token))
        assert_error(resp, 403, "PLATFORM_ADMIN_BUSINESS_DATA_DENIED")

    async def test_account_admin_blocked_on_server_accounts(
        self, client, account_admin_token,
    ):
        resp = await client.get(
            "/api/server/v1/server-accounts", headers=_hdr(account_admin_token),
        )
        assert_error(resp, 403, "PLATFORM_ADMIN_BUSINESS_DATA_DENIED")


# ── GET /catalog ─────────────────────────────────────────────────────────────

class TestCatalog:
    async def test_returns_all_entities(self, client, admin_token):
        from src.core.constants import EntityType
        resp = await client.get(f"{BASE}/catalog", headers=_hdr(admin_token))
        assert resp.status_code == 200
        body = resp.json()
        got = {e["entity_type"] for e in body}
        assert got == {e.value for e in EntityType}

    async def test_actions_match_entity_actions(self, client, admin_token):
        from src.core.constants import ENTITY_ACTIONS
        resp = await client.get(f"{BASE}/catalog", headers=_hdr(admin_token))
        body = resp.json()
        for entity in body:
            actions = {a["action"] for a in entity["actions"]}
            assert actions == set(ENTITY_ACTIONS[entity["entity_type"]])

    async def test_descriptions_nonempty(self, client, admin_token):
        resp = await client.get(f"{BASE}/catalog", headers=_hdr(admin_token))
        for entity in resp.json():
            assert entity["description"].strip()
            for action in entity["actions"]:
                assert action["description"].strip()

    async def test_sensitive_and_worker_flags(self, client, admin_token):
        resp = await client.get(f"{BASE}/catalog", headers=_hdr(admin_token))
        flat = {
            (e["entity_type"], a["action"]): a
            for e in resp.json() for a in e["actions"]
        }
        assert flat[("server_account", "view_password")]["sensitive"] is True
        assert flat[("server_account", "view_password")]["worker_only"] is False
        assert flat[("server", "inventory_submit")]["worker_only"] is True
        assert flat[("server", "inventory_submit")]["sensitive"] is False
        assert flat[("server", "view")]["sensitive"] is False
        assert flat[("server", "view")]["worker_only"] is False

    async def test_operator_can_view_catalog(self, client, operator_token_a):
        """operator имеет view на permission → доступ к каталогу есть."""
        resp = await client.get(f"{BASE}/catalog", headers=_hdr(operator_token_a))
        assert resp.status_code == 200

    async def test_guest_forbidden(self, client, guest_token_a):
        resp = await client.get(f"{BASE}/catalog", headers=_hdr(guest_token_a))
        assert_error(resp, 403, "PERMISSION_DENIED")

    async def test_no_token_401(self, client):
        resp = await client.get(f"{BASE}/catalog")
        assert_error(resp, 401, "ACCESS_TOKEN_MISSING")

    async def test_catalog_not_treated_as_entity_type(self, client, admin_token):
        """`/catalog` не должен матчиться как `/{entity_type}` → не 422."""
        resp = await client.get(f"{BASE}/catalog", headers=_hdr(admin_token))
        assert resp.status_code == 200


# ── GET / with role filter + describe ────────────────────────────────────────

class TestMatrixByRole:
    async def test_role_filter_returns_only_that_role(self, client, admin_token):
        resp = await client.get(f"{BASE}?role=reader", headers=_hdr(admin_token))
        assert resp.status_code == 200
        body = resp.json()
        rows = body["items"]
        assert body["described"] is False
        assert body["total"] == len(rows)
        assert rows  # reader has seeded grants
        assert all(r["role"] == "reader" for r in rows)

    async def test_role_filter_unknown_role_empty(self, client, admin_token):
        resp = await client.get(f"{BASE}?role=no_such_role", headers=_hdr(admin_token))
        assert resp.status_code == 200
        body = resp.json()
        assert body["items"] == []
        assert body["total"] == 0
        assert body["described"] is False

    async def test_no_params_keeps_legacy_shape(self, client, admin_token):
        """Без describe строки не содержат полей-обогащений."""
        resp = await client.get(BASE, headers=_hdr(admin_token))
        assert resp.status_code == 200
        body = resp.json()
        assert body["described"] is False
        row = body["items"][0]
        assert "entity_description" not in row
        assert "action_description" not in row
        assert "sensitive" not in row

    async def test_describe_adds_descriptions(self, client, admin_token):
        resp = await client.get(f"{BASE}?describe=true", headers=_hdr(admin_token))
        assert resp.status_code == 200
        body = resp.json()
        assert body["described"] is True
        rows = body["items"]
        assert body["total"] == len(rows)
        for r in rows:
            assert "entity_description" in r
            assert "action_description" in r
            assert "sensitive" in r

    async def test_describe_sensitive_flag_correct(self, client, admin_token):
        """worker_bot имеет seeded view_password/view_credentials — sensitive=True."""
        resp = await client.get(
            f"{BASE}?role=worker_bot&describe=true", headers=_hdr(admin_token)
        )
        body = resp.json()
        assert body["described"] is True
        rows = body["items"]
        vp = [r for r in rows if r["action"] == "view_password"]
        assert vp and all(r["sensitive"] is True for r in vp)

    async def test_role_and_describe_combined(self, client, admin_token):
        resp = await client.get(
            f"{BASE}?role=reader&describe=true", headers=_hdr(admin_token)
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["described"] is True
        rows = body["items"]
        assert all(r["role"] == "reader" for r in rows)
        assert all("action_description" in r for r in rows)

    async def test_guest_forbidden_with_params(self, client, guest_token_a):
        resp = await client.get(
            f"{BASE}?role=admin&describe=true", headers=_hdr(guest_token_a)
        )
        assert_error(resp, 403, "PERMISSION_DENIED")


# ── Department scope on read (list_all / list_for_entity / list_for_role) ────

class TestListScopeByDepartment:
    """GET /permissions фильтруется по dept caller'а.

    department_admin / service-role `admin` своего отдела видят свои строки
    плюс system-wide; чужие dept-строки невидимы. Platform-уровневые роли
    блокируются `platform_admin_guard` middleware ещё до endpoint'а, поэтому
    «account_admin видит всё» проверяется на уровне unit-теста сервиса.
    """

    async def _seed_cross_dept_rows(self, client, admin_token, admin_token_b):
        """Создаёт по одной per-dept строке в dep_a и dep_b через PUT /permissions.

        Каждый department_admin пишет в свой scope (другое запрещено).
        Возвращает (row_a, row_b).
        """
        a = await client.put(f"{BASE}/server/role_a_only/view", headers=_hdr(admin_token))
        assert a.status_code == 200, a.text
        b = await client.put(f"{BASE}/server/role_b_only/view", headers=_hdr(admin_token_b))
        assert b.status_code == 200, b.text
        return a.json(), b.json()

    async def test_dept_admin_sees_own_dept_and_system_wide_only(
        self, client, admin_token, admin_token_b, dept_a,
    ):
        row_a, row_b = await self._seed_cross_dept_rows(client, admin_token, admin_token_b)
        resp = await client.get(BASE, headers=_hdr(admin_token))
        assert resp.status_code == 200
        rows = resp.json()["items"]
        dept_ids = {r["department_id"] for r in rows}
        # видимы только свой dept_a и system-wide (None)
        assert dept_ids <= {None, dept_a}
        # своя строка есть
        assert any(r["id"] == row_a["id"] for r in rows)
        # чужая невидима
        assert not any(r["id"] == row_b["id"] for r in rows)

    async def test_dept_admin_cannot_see_other_dept_via_role_filter(
        self, client, admin_token, admin_token_b,
    ):
        _, row_b = await self._seed_cross_dept_rows(client, admin_token, admin_token_b)
        # фильтр по чужой role_b_only из dep_a → пусто (есть только в dep_b)
        resp = await client.get(f"{BASE}?role=role_b_only", headers=_hdr(admin_token))
        assert resp.status_code == 200
        assert resp.json()["items"] == []

    async def test_dept_admin_cannot_see_other_dept_via_entity_filter(
        self, client, admin_token, admin_token_b, dept_a,
    ):
        _, row_b = await self._seed_cross_dept_rows(client, admin_token, admin_token_b)
        resp = await client.get(f"{BASE}/server", headers=_hdr(admin_token))
        assert resp.status_code == 200
        rows = resp.json()["items"]
        ids = {r["id"] for r in rows}
        assert row_b["id"] not in ids
        # все department_id у возвращённых строк — либо None, либо свой dep
        for r in rows:
            assert r["department_id"] in (None, dept_a)


class TestListScopeServiceLayer:
    """Юнит-проверка scope-логики service-слоя, минующая guard middleware.

    Через HTTP `account_admin` блокируется guard'ом — поэтому ветка
    «platform-уровневый видит всё» проверяется прямым вызовом
    `permission_service.list_all` с фейковым identity.
    """

    async def test_account_admin_identity_sees_all_depts(
        self, db, admin_token, admin_token_b, client,
    ):
        from src.schemas.identity import IdentityContext
        from src.services import permission_service

        # развести per-dept строки через обычный grant-API
        a = await client.put(f"{BASE}/server/role_a_only/view", headers=_hdr(admin_token))
        b = await client.put(f"{BASE}/server/role_b_only/view", headers=_hdr(admin_token_b))
        assert a.status_code == 200 and b.status_code == 200

        identity = IdentityContext(
            user_id="usr_account_admin",
            username="account_admin",
            department_id=None,
            allowed_services=["server_service"],
            # admin сервисная роль на permission view — чтобы пройти require_action
            service_roles={"server_service": ["admin"]},
            platform_role="account_admin",
        )
        rows = await permission_service.list_all(db, identity)
        ids = {r.id for r in rows}
        assert a.json()["id"] in ids
        assert b.json()["id"] in ids
