"""Интеграционные тесты: значимые операции server_service шлют audit-события в loging_service.

Перехват: monkeypatch `audit_service.emit` (а не httpx) — это и проще, и стабильнее
относительно того, синхронный или асинхронный путь используется внутри emit.

Покрытие:
* `server.create / update / delete` → success;
* `server.view` → failure на cross-dept / nonexistent (GET-asymmetry tail);
* `server.power_on / off / reboot` → success + permission_denied;
* `ipmi_controller.view_credentials` → success + permission_denied;
* `server_account.view_password` → success + permission_denied;
* `server_account.rotate_password` → success + permission_denied;
* `permission.grant / revoke` → success + permission_denied;
* health-чек (`/api/server/v1/health`, `/ready`) — НЕ аудитится HTTP-middleware'ом.
"""

from __future__ import annotations

import pytest

BASE = "/api/server/v1"


def _hdr(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ── Фикстура: перехват emit ─────────────────────────────────────────────────

@pytest.fixture
def captured_emits(monkeypatch):
    """Захватывает все вызовы audit_service.emit (call kwargs).

    Патч идёт через monkeypatch модулей-импортёров, чтобы покрыть и
    `from src.services import audit_service; audit_service.emit(...)`,
    и потенциально другие пути.
    """
    captured: list[dict] = []

    def fake_emit(action, actor_id=None, **kwargs):
        captured.append({"action": action, "actor_id": actor_id, **kwargs})

    # Главный модуль — патчим тут, остальные importer'ы тащат из него.
    import src.services.audit_service as audit_mod
    monkeypatch.setattr(audit_mod, "emit", fake_emit)

    # Прямые importer'ы держат локальную ссылку — патчим их тоже.
    for path in (
        "src.services.server.audit_service.emit",
        "src.services.internal_service.audit_service.emit",
        "src.services.permission_service.audit_service.emit",
        "src.api.v1.endpoints.ipmi.audit_service.emit",
    ):
        try:
            monkeypatch.setattr(path, fake_emit)
        except (AttributeError, ImportError):
            pass

    return captured


def _events(captured: list[dict], action: str) -> list[dict]:
    return [e for e in captured if e["action"] == action]


# ── server.create / update / delete ─────────────────────────────────────────

class TestServerCrudAudit:
    async def test_create_server_emits_success(
        self, client, admin_role_token_a, captured_emits, dept_a,
    ):
        resp = await client.post(
            f"{BASE}/servers",
            json={
                "hostname": "srv-audit-1",
                "ip_address": "10.99.0.1",
                "department_id": dept_a,
                "ssh_port": 22,
            },
            headers=_hdr(admin_role_token_a),
        )
        assert resp.status_code in (200, 201), resp.text
        events = _events(captured_emits, "server.create")
        success = [e for e in events if e.get("status") == "success"]
        assert len(success) == 1
        ev = success[0]
        assert ev.get("target_type") == "server"
        assert ev.get("allowed") is True
        assert ev["target_id"] == resp.json()["id"]

    async def test_create_server_cross_dept_emits_failure(
        self, client, admin_role_token_a, captured_emits, dept_b,
    ):
        # admin роль в dep_a, но пытается создать в dep_b
        resp = await client.post(
            f"{BASE}/servers",
            json={
                "hostname": "srv-leak",
                "ip_address": "10.99.0.2",
                "department_id": dept_b,
                "ssh_port": 22,
            },
            headers=_hdr(admin_role_token_a),
        )
        assert resp.status_code == 403
        failures = [
            e for e in _events(captured_emits, "server.create")
            if e.get("status") == "failure"
        ]
        assert len(failures) == 1
        assert failures[0].get("allowed") is True
        assert failures[0]["details"]["reason"] == "department_isolation"

    async def test_update_server_emits_success(
        self, client, admin_role_token_a, make_server, captured_emits,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.patch(
            f"{BASE}/servers/{srv.id}",
            json={"display_name": "Renamed Server"},
            headers=_hdr(admin_role_token_a),
        )
        assert resp.status_code == 200, resp.text
        success = [
            e for e in _events(captured_emits, "server.update")
            if e.get("status") == "success"
        ]
        assert len(success) == 1
        assert success[0]["target_id"] == srv.id

    async def test_delete_server_emits_success(
        self, client, admin_role_token_a, make_server, captured_emits,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.delete(
            f"{BASE}/servers/{srv.id}", headers=_hdr(admin_role_token_a),
        )
        assert resp.status_code in (200, 204), resp.text
        success = [
            e for e in _events(captured_emits, "server.delete")
            if e.get("status") == "success"
        ]
        assert len(success) == 1
        assert success[0]["target_id"] == srv.id
        assert success[0]["target_type"] == "server"

    # ── update/delete cross-dept и non-existent → explicit `failure` ─────────
    # `_ensure_visible` прячет cross-dept за 404 (anti-enumeration); раньше
    # update/delete на этом пути не эмитили action-specific audit — попытка
    # терялась в middleware'е как `http.client_error` без action key. Permission
    # уже прошёл выше — здесь visibility-404, `failure`/`allowed=True`.

    async def test_update_cross_dept_emits_failure(
        self, client, admin_role_token_a, make_server, captured_emits,
    ):
        srv = await make_server(department_id="dep_b")
        resp = await client.patch(
            f"{BASE}/servers/{srv.id}",
            json={"display_name": "x"},
            headers=_hdr(admin_role_token_a),
        )
        assert resp.status_code == 404
        failures = [
            e for e in _events(captured_emits, "server.update")
            if e.get("status") == "failure"
        ]
        assert len(failures) == 1
        ev = failures[0]
        assert ev["target_id"] == srv.id
        assert ev["target_type"] == "server"
        assert ev["allowed"] is True
        assert ev["details"]["reason"] == "not_found_or_cross_dept"

    async def test_update_nonexistent_emits_failure(
        self, client, admin_role_token_a, captured_emits,
    ):
        resp = await client.patch(
            f"{BASE}/servers/srv_ghost",
            json={"display_name": "x"},
            headers=_hdr(admin_role_token_a),
        )
        assert resp.status_code == 404
        failures = [
            e for e in _events(captured_emits, "server.update")
            if e.get("status") == "failure"
        ]
        assert len(failures) == 1
        ev = failures[0]
        assert ev["target_id"] == "srv_ghost"
        assert ev["allowed"] is True
        assert ev["details"]["reason"] == "not_found_or_cross_dept"

    async def test_delete_cross_dept_emits_failure(
        self, client, admin_role_token_a, make_server, captured_emits,
    ):
        srv = await make_server(department_id="dep_b")
        resp = await client.delete(
            f"{BASE}/servers/{srv.id}", headers=_hdr(admin_role_token_a),
        )
        assert resp.status_code == 404
        failures = [
            e for e in _events(captured_emits, "server.delete")
            if e.get("status") == "failure"
        ]
        assert len(failures) == 1
        ev = failures[0]
        assert ev["target_id"] == srv.id
        assert ev["target_type"] == "server"
        assert ev["allowed"] is True
        assert ev["details"]["reason"] == "not_found_or_cross_dept"

    async def test_delete_nonexistent_emits_failure(
        self, client, admin_role_token_a, captured_emits,
    ):
        resp = await client.delete(
            f"{BASE}/servers/srv_ghost", headers=_hdr(admin_role_token_a),
        )
        assert resp.status_code == 404
        failures = [
            e for e in _events(captured_emits, "server.delete")
            if e.get("status") == "failure"
        ]
        assert len(failures) == 1
        ev = failures[0]
        assert ev["target_id"] == "srv_ghost"
        assert ev["allowed"] is True
        assert ev["details"]["reason"] == "not_found_or_cross_dept"

    # ── GET cross-dept и non-existent → explicit `failure` ───────────────────
    # `get_server` исторически молчал при cross-dept / nonexistent — попытка
    # чтения чужого сервера терялась как `http.client_error` без action-key.
    # Permission уже прошёл — здесь visibility-404, `failure`/`allowed=True`.

    async def test_get_cross_dept_emits_failure(
        self, client, reader_token_a, make_server, captured_emits,
    ):
        srv = await make_server(department_id="dep_b")
        resp = await client.get(
            f"{BASE}/servers/{srv.id}", headers=_hdr(reader_token_a),
        )
        assert resp.status_code == 404
        failures = [
            e for e in _events(captured_emits, "server.view")
            if e.get("status") == "failure"
        ]
        assert len(failures) == 1
        ev = failures[0]
        assert ev["target_id"] == srv.id
        assert ev["target_type"] == "server"
        assert ev["allowed"] is True
        assert ev["details"]["reason"] == "not_found_or_cross_dept"

    async def test_get_nonexistent_emits_failure(
        self, client, reader_token_a, captured_emits,
    ):
        resp = await client.get(
            f"{BASE}/servers/srv_ghost", headers=_hdr(reader_token_a),
        )
        assert resp.status_code == 404
        failures = [
            e for e in _events(captured_emits, "server.view")
            if e.get("status") == "failure"
        ]
        assert len(failures) == 1
        ev = failures[0]
        assert ev["target_id"] == "srv_ghost"
        assert ev["target_type"] == "server"
        assert ev["allowed"] is True
        assert ev["details"]["reason"] == "not_found_or_cross_dept"

    async def test_get_permission_denied_emits_denied(
        self, client, guest_token_a, make_server, captured_emits,
    ):
        """guest без `server.view` → 403 + explicit denied audit.

        Симметрия с cross-dept/nonexistent: отказ по матрице прав тоже
        должен попадать в audit с action-key `server.view`, иначе SIEM
        не увидит попытку обращения к ресурсу.
        """
        srv = await make_server(department_id="dep_a")
        resp = await client.get(
            f"{BASE}/servers/{srv.id}", headers=_hdr(guest_token_a),
        )
        assert resp.status_code == 403
        denied = [
            e for e in _events(captured_emits, "server.view")
            if e.get("status") == "denied"
        ]
        assert len(denied) == 1
        ev = denied[0]
        assert ev["target_id"] == srv.id
        assert ev["target_type"] == "server"
        assert ev["allowed"] is False
        assert ev["details"]["reason"] == "permission_denied"


# ── power_on / off / reboot ──────────────────────────────────────────────────

@pytest.fixture
def captured_dispatch(monkeypatch):
    """Стаб worker_client.dispatch_task — возвращает фиктивный task_id."""

    async def fake_dispatch(*, task_kind, target_server_id, payload,
                            created_by, request_id,
                            target_resource_id=None, idempotency_key=None,
                            return_hit=False):
        new_id = f"tsk_{task_kind.replace('.', '_')}_fake"
        return (new_id, False) if return_hit else new_id

    async def fake_dispatch_with_hit(**kwargs):
        kwargs["return_hit"] = True
        return await fake_dispatch(**kwargs)

    monkeypatch.setattr(
        "src.api.v1.endpoints.ipmi.worker_client.dispatch_task", fake_dispatch
    )
    monkeypatch.setattr(
        "src.api.v1.endpoints.ipmi.worker_client.dispatch_task_with_hit",
        fake_dispatch_with_hit,
    )


class TestPowerAudit:
    async def test_power_on_success_emits_audit(
        self, client, admin_role_token_a, make_server,
        captured_emits, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a", with_ipmi=True)
        resp = await client.post(
            f"{BASE}/servers/{srv.id}/ipmi/power/on",
            headers=_hdr(admin_role_token_a),
        )
        assert resp.status_code == 202
        events = [
            e for e in _events(captured_emits, "server.power_on")
            if e.get("status") == "success"
        ]
        assert len(events) == 1
        assert events[0]["target_id"] == srv.id
        assert events[0]["allowed"] is True
        assert events[0]["details"]["task_id"].startswith("tsk_")

    async def test_power_off_success_emits_audit(
        self, client, admin_role_token_a, make_server,
        captured_emits, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a", with_ipmi=True)
        resp = await client.post(
            f"{BASE}/servers/{srv.id}/ipmi/power/off",
            headers=_hdr(admin_role_token_a),
        )
        assert resp.status_code == 202
        events = [
            e for e in _events(captured_emits, "server.power_off")
            if e.get("status") == "success"
        ]
        assert len(events) == 1

    async def test_power_reboot_success_emits_audit(
        self, client, admin_role_token_a, make_server,
        captured_emits, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a", with_ipmi=True)
        resp = await client.post(
            f"{BASE}/servers/{srv.id}/ipmi/power/reboot",
            headers=_hdr(admin_role_token_a),
        )
        assert resp.status_code == 202
        events = [
            e for e in _events(captured_emits, "server.power_reboot")
            if e.get("status") == "success"
        ]
        assert len(events) == 1

    async def test_power_on_denied_for_reader_role_emits_denied(
        self, client, reader_token_a, make_server,
        captured_emits, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/servers/{srv.id}/ipmi/power/on",
            headers=_hdr(reader_token_a),
        )
        assert resp.status_code == 403
        denied = [
            e for e in _events(captured_emits, "server.power_on")
            if e.get("status") == "denied"
        ]
        assert len(denied) == 1
        assert denied[0]["allowed"] is False

    async def test_power_off_denied_for_guest_role_emits_denied(
        self, client, guest_token_a, make_server,
        captured_emits, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/servers/{srv.id}/ipmi/power/off",
            headers=_hdr(guest_token_a),
        )
        assert resp.status_code == 403
        denied = [
            e for e in _events(captured_emits, "server.power_off")
            if e.get("status") == "denied"
        ]
        assert len(denied) == 1

    async def test_power_reboot_denied_for_no_role_emits_denied(
        self, client, no_role_token_a, make_server,
        captured_emits, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/servers/{srv.id}/ipmi/power/reboot",
            headers=_hdr(no_role_token_a),
        )
        assert resp.status_code == 403
        denied = [
            e for e in _events(captured_emits, "server.power_reboot")
            if e.get("status") == "denied"
        ]
        assert len(denied) == 1

    async def test_power_on_cross_dept_emits_failure(
        self, client, operator_token_b, make_server,
        captured_emits, captured_dispatch,
    ):
        """Operator из dep_b пытается выключить сервер dep_a — 404 (hidden cross-dept).

        Permission уже прошёл — это visibility-404, `failure`/`allowed=True`.
        """
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/servers/{srv.id}/ipmi/power/on",
            headers=_hdr(operator_token_b),
        )
        assert resp.status_code == 404
        failures = [
            e for e in _events(captured_emits, "server.power_on")
            if e.get("status") == "failure"
        ]
        assert len(failures) == 1
        assert failures[0]["details"]["reason"] == "not_found_or_cross_dept"


# ── Internal: view_credentials / view_password / rotate_password ────────────

INT = f"{BASE}/internal"


class TestInternalSensitiveAudit:
    @pytest.mark.usefixtures("soft_dept_mode")
    async def test_view_credentials_success_emits_audit(
        self, client, worker_pat_token, make_server, make_ipmi, captured_emits,
    ):
        srv = await make_server(department_id="dep_a")
        ctrl = await make_ipmi(server_id=srv.id, password="ipmi-secret")
        resp = await client.get(
            f"{INT}/servers/{srv.id}/ipmi/credentials",
            headers=_hdr(worker_pat_token),
        )
        assert resp.status_code == 200
        success = [
            e for e in _events(captured_emits, "ipmi_controller.view_credentials")
            if e.get("status") == "success"
        ]
        assert len(success) == 1
        assert success[0]["target_id"] == ctrl.id

    async def test_view_credentials_denied_for_reader_emits_denied(
        self, client, reader_token_a, make_server, make_ipmi, captured_emits,
    ):
        srv = await make_server(department_id="dep_a")
        await make_ipmi(server_id=srv.id)
        resp = await client.get(
            f"{INT}/servers/{srv.id}/ipmi/credentials",
            headers=_hdr(reader_token_a),
        )
        assert resp.status_code == 403
        denied = [
            e for e in _events(captured_emits, "ipmi_controller.view_credentials")
            if e.get("status") == "denied"
        ]
        assert len(denied) == 1
        assert denied[0]["allowed"] is False

    @pytest.mark.usefixtures("soft_dept_mode")
    async def test_view_password_success_emits_audit(
        self, client, worker_pat_token, make_server, make_account, captured_emits,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, password="root-secret")
        resp = await client.get(
            f"{INT}/servers/{srv.id}/accounts/{acc.id}/password",
            headers=_hdr(worker_pat_token),
        )
        assert resp.status_code == 200
        success = [
            e for e in _events(captured_emits, "server_account.view_password")
            if e.get("status") == "success"
        ]
        assert len(success) == 1
        assert success[0]["target_id"] == acc.id

    async def test_view_password_denied_for_operator_emits_denied(
        self, client, operator_token_a, make_server, make_account, captured_emits,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id)
        resp = await client.get(
            f"{INT}/servers/{srv.id}/accounts/{acc.id}/password",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 403
        denied = [
            e for e in _events(captured_emits, "server_account.view_password")
            if e.get("status") == "denied"
        ]
        assert len(denied) == 1

    @pytest.mark.usefixtures("soft_dept_mode")
    async def test_rotate_password_success_emits_audit(
        self, client, worker_pat_token, make_server, make_account, captured_emits,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, password="old-pwd")
        resp = await client.post(
            f"{INT}/servers/{srv.id}/accounts/{acc.id}/password/rotate",
            json={"password": "FreshRotated1234"},
            headers=_hdr(worker_pat_token),
        )
        assert resp.status_code == 200, resp.text
        success = [
            e for e in _events(captured_emits, "server_account.rotate_password")
            if e.get("status") == "success"
        ]
        assert len(success) == 1
        assert success[0]["target_id"] == acc.id

    async def test_rotate_password_denied_for_reader_emits_denied(
        self, client, reader_token_a, make_server, make_account, captured_emits,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id)
        resp = await client.post(
            f"{INT}/servers/{srv.id}/accounts/{acc.id}/password/rotate",
            json={"password": "ReaderTry1234"},
            headers=_hdr(reader_token_a),
        )
        assert resp.status_code == 403
        denied = [
            e for e in _events(captured_emits, "server_account.rotate_password")
            if e.get("status") == "denied"
        ]
        assert len(denied) == 1


# ── permission.grant / revoke ───────────────────────────────────────────────

class TestPermissionMatrixAudit:
    async def test_grant_success_emits_audit(
        self, client, admin_token, captured_emits,
    ):
        # `admin_token` теперь department_admin в dep_a с service-role `admin` —
        # имеет permission_grant на entity `permission` (см. seed 831ba55543e9).
        # Grant пишется в dep_a-scope.
        resp = await client.put(
            f"{BASE}/permissions/server/reader/create",
            headers=_hdr(admin_token),
        )
        assert resp.status_code in (200, 201), resp.text
        success = [
            e for e in _events(captured_emits, "permission.grant")
            if e.get("status") == "success"
        ]
        assert len(success) == 1
        assert success[0]["details"]["entity_type"] == "server"
        assert success[0]["details"]["role"] == "reader"
        assert success[0]["details"]["action"] == "create"

    async def test_grant_denied_for_reader_emits_denied(
        self, client, reader_token_a, captured_emits,
    ):
        resp = await client.put(
            f"{BASE}/permissions/server/guest/view",
            headers=_hdr(reader_token_a),
        )
        assert resp.status_code == 403
        denied = [
            e for e in _events(captured_emits, "permission.grant")
            if e.get("status") == "denied"
        ]
        assert len(denied) == 1
        assert denied[0]["allowed"] is False

    async def test_revoke_denied_for_operator_emits_denied(
        self, client, operator_token_a, captured_emits,
    ):
        resp = await client.delete(
            f"{BASE}/permissions/server/guest/view",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 403
        denied = [
            e for e in _events(captured_emits, "permission.revoke")
            if e.get("status") == "denied"
        ]
        assert len(denied) == 1


# ── Health/ready НЕ аудитятся middleware'ом ─────────────────────────────────

class TestHealthNotAudited:
    async def test_health_endpoint_not_audited(self, client, captured_emits):
        # Перед запросом — пусто
        assert _events(captured_emits, "http.access_denied") == []
        resp = await client.get(f"{BASE}/health")
        assert resp.status_code == 200
        # health не должен ничего эмитить (ни http.client_error, ни http.server_error,
        # ни access_denied — это пропуск middleware'а).
        for action in ("http.access_denied", "http.client_error", "http.server_error"):
            assert _events(captured_emits, action) == [], (
                f"health endpoint must not emit {action}"
            )

    async def test_health_unauthenticated_not_audited(self, client, captured_emits):
        """Health доступен без Bearer — но даже если бы 401 был, middleware пропустит health."""
        resp = await client.get(f"{BASE}/health")
        assert resp.status_code == 200
        assert _events(captured_emits, "http.access_denied") == []


# ── 501 stub-эндпоинты НЕ аудитятся (фикс stub-501 audit-amplification) ─────


# Список оставшихся stub-эндпоинтов. Все 41 закрыты — массив пустой.
# Класс ниже сохраняется как regression-guard на `audit_access` фильтр 501,
# чтобы при будущем добавлении нового stub'а защита от audit-amplification
# не сломалась (тест `test_500_real_server_error_still_emits_audit` ниже
# проверяет другое направление — настоящие 5xx по-прежнему аудятся).
_STUB_ENDPOINTS_41: list[tuple[str, str]] = []


class TestStub501NotAudited:
    """501 NOT_IMPLEMENTED эндпоинты НЕ должны эмитить audit-event.

    Регрессия: authenticated insider циклом по 41 stub-эндпоинту порождал
    41 × `http.server_error` audit-emit за круг — класс audit-amplification
    (нужен валидный bearer, но rate-limit 500/min позволяет ~12
    циклов/минуту = 500 audit-event'ов в loging_service).

    Фикс в `src/main.py:audit_access`: ранний return при `status_code == 501`.
    501 — это «endpoint не реализован», не действие пользователя, нет смысла
    в audit-trail. Реальные 5xx (баги/БД-сбои) по-прежнему дают
    `http.server_error`.

    Сейчас 0 stub'ов — массив пустой. Класс сохранён как regression-guard:
    `test_500_real_server_error_still_emits_audit` ниже проверяет, что
    фильтр 501 не глушит настоящие 5xx.
    """

    def test_stub_count_matches_todo(self) -> None:
        """Sanity-check: все 41 historical stubs закрыты."""
        assert len(_STUB_ENDPOINTS_41) == 0

    async def test_500_real_server_error_still_emits_audit(
        self, client, admin_token, captured_emits,
    ):
        """Реальный 500 (НЕ 501 stub) по-прежнему эмитит `http.server_error`.

        Регрессия-страховка: фильтр на 501 не должен заглушить настоящие 5xx.
        Регистрируем тестовый endpoint, который возвращает 500 как
        `JSONResponse` (а не raise'ит) — это эмулирует ситуацию, где
        downstream-handler сформировал ответ 500 (например, через
        `AppException(http_status=500, ...)`), который `audit_access`
        должен увидеть и аудитнуть.

        Note: настоящий `raise RuntimeError` всплыл бы ДО `audit_access`
        через `await call_next`; его перехватывает `ServerErrorMiddleware`
        (outermost у Starlette) — но `audit_access` exception всё равно не
        увидит. Поэтому здесь имитируем response-based 5xx.
        """
        from fastapi.responses import JSONResponse

        from src.main import app

        async def _return_500() -> JSONResponse:
            return JSONResponse(
                status_code=500,
                content={
                    "error": "internal_error",
                    "error_code": "SYNTHETIC_500",
                    "message": "synthetic 500 for audit test",
                    "details": {},
                    "request_id": None,
                    "timestamp": "2026-01-01T00:00:00+00:00",
                },
            )

        # Регистрируем уникальный route только для этого теста.
        unique_path = "/api/server/v1/__test_real_500"
        app.add_api_route(unique_path, _return_500, methods=["GET"])

        try:
            resp = await client.get(unique_path, headers=_hdr(admin_token))
            assert resp.status_code == 500, (
                f"ожидался 500, получено {resp.status_code}: {resp.text}"
            )

            # Реальный 500 ДОЛЖЕН породить http.server_error.
            server_errors = _events(captured_emits, "http.server_error")
            assert len(server_errors) == 1, (
                f"real 500 не породил http.server_error audit-emit: "
                f"{captured_emits}"
            )
            ev = server_errors[0]
            assert ev["status"] == "failure"
            assert ev["details"]["status_code"] == 500
            assert ev["details"]["path"] == unique_path
        finally:
            # Cleanup: убираем тестовый route, чтобы он не утекал в соседние тесты.
            app.router.routes = [
                r for r in app.router.routes
                if getattr(r, "path", None) != unique_path
            ]

    async def test_anonymous_stub_returns_401_and_emits_access_denied(
        self, client, captured_emits,
    ):
        """Анонимный запрос на закрытый endpoint → 401 (auth-dep), а НЕ 501.
        Это http.access_denied.

        Контрольный тест: фильтр на 501 не должен влиять на 401-ответы. До
        endpoint'а не доходит, auth-dep отбивает раньше — поведение должно
        остаться прежним.
        """
        resp = await client.get(f"{BASE}/servers")
        assert resp.status_code == 401

        denied = _events(captured_emits, "http.access_denied")
        assert len(denied) == 1, (
            f"анонимный запрос на закрытый endpoint должен эмитить "
            f"http.access_denied: {captured_emits}"
        )
        assert denied[0]["status"] == "denied"
        assert denied[0]["details"]["status_code"] == 401
