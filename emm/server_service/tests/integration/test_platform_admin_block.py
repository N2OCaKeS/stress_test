"""Интеграционные тесты ``platform_admin_guard`` middleware.

Платформенные роли ``account_admin`` / ``loging_admin`` — **не имеют доступа**
к бизнес-данным ``server_service`` (см. ``services/permissions.py``
docstring). Middleware ``src/middleware/platform_admin_guard.py`` отбивает их
**до** endpoint-логики с 403 ``PLATFORM_ADMIN_BUSINESS_DATA_DENIED`` + audit
``http.platform_admin_blocked``.

``loging_reader`` middleware'ом НЕ блокируется — у него есть department_id
и он может быть обычным сотрудником с service-role'ями. Если service-role
нет, матрица прав отдаст 403 сама.

Покрытие:

* **Блокировка:** ``account_admin`` / ``loging_admin``
  → 403 на любом business endpoint (GET /servers, POST /power/cycle, и т.д.).
* **Allow-list путей:** ``/health`` / ``/ready`` / ``/openapi.json`` / ``/docs``
  / ``/redoc`` — пропускаются (нужно для k8s probes + публичной Swagger).
* **Regression:** ``department_admin``, обычный пользователь с service-role,
  worker_bot через PAT — **не** блокируются.
* **Audit:** на блок эмитится explicit ``http.platform_admin_blocked``.

Все тесты идут через реальный PostgreSQL (через ``client`` fixture с
SAVEPOINT-rollback) — никаких mock'ов БД. Введён общий
``captured_emits`` чтобы детектить audit-emission.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

BASE = "/api/server/v1"


from tests._helpers import assert_error, auth_hdr as _hdr, make_emit_capture, next_stand_number  # noqa: E402


# ── Audit capture (общий patcher как в test_audit_emission) ──────────────────

@pytest.fixture
def captured_emits(monkeypatch):
    """Захватывает все вызовы ``audit_service.emit`` (call kwargs)."""
    return make_emit_capture(
        monkeypatch,
        "src.middleware.platform_admin_guard.audit_service.emit",
        "src.services.server.audit_service.emit",
        "src.services.internal_service.audit_service.emit",
        "src.services.permission_service.audit_service.emit",
        "src.api.v1.endpoints.ipmi.audit_service.emit",
    )


def _events(captured: list[dict], action: str) -> list[dict]:
    return [e for e in captured if e["action"] == action]


# ── Captured dispatch для power-операций ─────────────────────────────────────

@pytest.fixture
def captured_dispatch(monkeypatch):
    """Перехватывает ``worker_client.dispatch_task`` — повторяем контракт из
    ``test_ipmi_endpoints.captured_dispatch``, но локально в этом файле.
    """
    calls: list[dict] = []

    async def fake_dispatch(*, db=None, task_kind, target_server_id, payload,
                            created_by, request_id,
                            target_resource_id=None, idempotency_key=None,
                            priority=0,
                            return_hit=False):
        calls.append({
            "task_kind": task_kind,
            "target_server_id": target_server_id,
            "payload": payload,
            "created_by": created_by,
            "request_id": request_id,
            "idempotency_key": idempotency_key,
        })
        new_id = f"tsk_{task_kind.replace('.', '_')}_fake_{len(calls)}"
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
    return calls


# ── 1. account_admin блокируется на servers list ────────────────────────────


class TestAccountAdminBlocked:
    async def test_account_admin_blocked_on_servers_list(
        self, client, account_admin_token,
    ):
        """``account_admin`` → 403 PLATFORM_ADMIN_BUSINESS_DATA_DENIED на GET /servers."""
        resp = await client.get(f"{BASE}/servers", headers=_hdr(account_admin_token))
        body = assert_error(resp, 403, "PLATFORM_ADMIN_BUSINESS_DATA_DENIED")
        assert body["error"] == "forbidden"
        assert body["details"]["platform_role"] == "account_admin"
        # details содержит весь список заблокированных ролей — SIEM-rule может
        # сверять что middleware действительно applied.
        assert set(body["details"]["blocked_platform_roles"]) == {
            "account_admin", "loging_admin",
        }
        # request_id обязан быть в envelope (выставлен outer middleware'ом).
        assert body["request_id"] is not None

    async def test_account_admin_blocked_on_power_cycle(
        self, client, account_admin_token, make_server, captured_dispatch,
    ):
        """``account_admin`` → 403 PLATFORM_ADMIN_BUSINESS_DATA_DENIED на POST /power/reboot.

        Заодно verify, что worker НЕ получает задачу — middleware режет ДО
        endpoint-логики, ``dispatch_task`` не вызывается.
        """
        srv = await make_server(department_id="dep_a", with_ipmi=True)
        resp = await client.post(
            f"{BASE}/servers/{srv.id}/ipmi/power/reboot",
            headers=_hdr(account_admin_token),
        )
        assert_error(resp, 403, "PLATFORM_ADMIN_BUSINESS_DATA_DENIED")
        assert captured_dispatch == [], (
            "worker НЕ должен получить задачу — guard режет ДО endpoint'а"
        )


# ── 2. loging_admin блокируется на любом business endpoint ──────────────────


class TestLogingAdminBlocked:
    async def test_loging_admin_blocked_on_any_business(
        self, client, loging_admin_token,
    ):
        """``loging_admin`` → 403 на business endpoint (servers list)."""
        resp = await client.get(f"{BASE}/servers", headers=_hdr(loging_admin_token))
        body = assert_error(resp, 403, "PLATFORM_ADMIN_BUSINESS_DATA_DENIED")
        assert body["details"]["platform_role"] == "loging_admin"

    async def test_loging_admin_blocked_on_permissions(
        self, client, loging_admin_token,
    ):
        """``loging_admin`` → 403 на GET /permissions."""
        resp = await client.get(f"{BASE}/permissions", headers=_hdr(loging_admin_token))
        assert_error(resp, 403, "PLATFORM_ADMIN_BUSINESS_DATA_DENIED")


# ── 3. loging_reader НЕ блокируется guard'ом (имеет dept, может быть юзером) ─


class TestLogingReaderNotBlocked:
    """``loging_reader`` имеет ``department_id`` (читает логи своего отдела) и
    одновременно может быть обычным сотрудником с сервисными ролями в
    server_service. Middleware его не блокирует — доступ регулирует
    обычная матрица прав. Если service-role нет — 403 отдаст матрица, не
    middleware."""

    async def test_loging_reader_without_service_role_blocked_by_matrix(
        self, client, loging_reader_token,
    ):
        """``loging_reader`` без service-role → 403 от матрицы прав,
        НЕ от platform-admin guard (другой error_code)."""
        # Должен получить 403, но через матрицу прав, не guard.
        resp = await client.get(f"{BASE}/servers", headers=_hdr(loging_reader_token))
        body = assert_error(resp, 403)
        # Если бы guard блокировал — был бы PLATFORM_ADMIN_BUSINESS_DATA_DENIED.
        # Здесь — матрица: PERMISSION_DENIED или подобный.
        assert body["error_code"] != "PLATFORM_ADMIN_BUSINESS_DATA_DENIED"


# ── 4. Allow-list путей: health / ready / openapi / docs / redoc ─────────────


class TestAllowListPaths:
    async def test_account_admin_allowed_on_health(self, client, account_admin_token):
        """GET /health → 200 (k8s probe), даже с account_admin токеном.

        Сам endpoint не требует auth, но middleware **не должен** блокировать
        путь даже если кто-то прислал bearer.
        """
        resp = await client.get(
            f"{BASE}/health",
            headers=_hdr(account_admin_token),
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    async def test_health_works_without_jwt(self, client):
        """Sanity: /health работает и без bearer'а (k8s liveness probe)."""
        resp = await client.get(f"{BASE}/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    async def test_account_admin_allowed_on_ready(self, client, account_admin_token):
        """GET /ready → 200 (readiness probe), даже с account_admin токеном."""
        resp = await client.get(
            f"{BASE}/ready",
            headers=_hdr(account_admin_token),
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ready"

    async def test_openapi_accessible(self, client, account_admin_token):
        """GET /openapi.json → 200 даже от account_admin (Swagger публичный в dev/test).

        В production он отключён через ``settings.app_env`` — это другой
        механизм, не platform-admin guard.
        """
        resp = await client.get(
            "/openapi.json",
            headers=_hdr(account_admin_token),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("openapi", "").startswith("3.")
        # И санитарная проверка: что-нибудь из path'ов реально есть.
        assert "/api/server/v1/servers" in body.get("paths", {})

    async def test_loging_reader_can_read_openapi(self, client, loging_reader_token):
        """Tester: loging_reader тоже видит публичный OpenAPI (regression)."""
        resp = await client.get(
            "/openapi.json",
            headers=_hdr(loging_reader_token),
        )
        assert resp.status_code == 200


# ── 5. Regression: department_admin НЕ блокируется ──────────────────────────


class TestDepartmentAdminNotBlocked:
    async def test_department_admin_NOT_blocked(self, client, admin_token, make_server):
        """``department_admin`` своего отдела → 200 на GET /servers (своего dept).

        ``admin_token`` фикстура отдаёт ``platform_role=department_admin`` +
        ``service_roles={server_service: [admin]}`` в dep_a — это
        легитимный admin своего департамента, у него есть полный доступ к
        бизнес-данным своего отдела (§7-8).
        """
        await make_server(department_id="dep_a")
        await make_server(department_id="dep_a")
        resp = await client.get(f"{BASE}/servers", headers=_hdr(admin_token))
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2

    async def test_department_admin_can_create_in_own_dept(self, client, admin_token):
        """``department_admin`` создаёт сервер в своём dep_a (regression)."""
        resp = await client.post(
            f"{BASE}/servers",
            headers=_hdr(admin_token),
            json={
                "hostname": "dept-admin-srv",
                "ip_address": "10.99.0.50",
                "department_id": "dep_a",
                "ssh_port": 22,
                "number": next_stand_number(),
            },
        )
        assert resp.status_code == 201
        assert resp.json()["department_id"] == "dep_a"


# ── 6. Regression: обычный пользователь с service-role НЕ блокируется ───────


class TestServiceRoleUserNotBlocked:
    async def test_service_role_user_NOT_blocked(
        self, client, reader_token_a, make_server,
    ):
        """Reader с ``service_roles={server_service: [reader]}`` → 200 на GET /servers.

        Sanity: middleware блокирует только трёх platform-ролей, не задевая
        обычные сервисные роли.
        """
        await make_server(department_id="dep_a")
        resp = await client.get(f"{BASE}/servers", headers=_hdr(reader_token_a))
        assert resp.status_code == 200


# ── 7. Regression: worker_bot (PAT) НЕ блокируется ──────────────────────────


class TestWorkerBotNotBlocked:
    async def test_worker_bot_NOT_blocked(
        self, client, worker_bot_token_a, make_server, make_ipmi,
    ):
        """worker_bot — service-role (не platform), идёт через PAT → 200 на /internal.

        worker_bot имеет 4 grants (миграция ``43cf9cfef9e1``):
        ``server_account.{view_password,rotate_password}`` +
        ``ipmi_controller.{view_credentials,rotate_credentials}``. Тест
        проверяет, что middleware его НЕ блокирует — это сервисный аккаунт,
        не platform-admin.
        """
        srv = await make_server(department_id="dep_a")
        await make_ipmi(server_id=srv.id)
        resp = await client.get(
            f"{BASE}/internal/servers/{srv.id}/ipmi/credentials",
            headers={
                **_hdr(worker_bot_token_a),
                "X-Target-Department-Id": "dep_a",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        # plaintext-credential попал в ответ (тест ровно про допуск, не про
        # шифрование — это покрыто отдельно).
        assert body["password"] == "ipmi-plaintext-secret"


# ── 8. Audit: на блок эмитится http.platform_admin_blocked ──────────────────


class TestAuditEmittedOnBlock:
    async def test_audit_emitted_on_account_admin_block(
        self, client, account_admin_token, captured_emits,
    ):
        """После блока в audit_service пишется event ``http.platform_admin_blocked``.

        Один блок → ровно один event с ``status=denied``, ``allowed=False``,
        ``details.platform_role=account_admin``, ``details.path`` — конкретный
        URL.
        """
        resp = await client.get(f"{BASE}/servers", headers=_hdr(account_admin_token))
        assert_error(resp, 403, "PLATFORM_ADMIN_BUSINESS_DATA_DENIED")

        events = _events(captured_emits, "http.platform_admin_blocked")
        assert len(events) == 1, (
            f"ожидался ровно один http.platform_admin_blocked, got {events}"
        )
        ev = events[0]
        assert ev["status"] == "denied"
        assert ev["allowed"] is False
        assert ev["details"]["platform_role"] == "account_admin"
        assert ev["details"]["reason"] == "platform_admin_business_data_blocked"
        assert ev["details"]["method"] == "GET"
        assert ev["details"]["path"] == f"{BASE}/servers"
        # target_type/target_id — нужны SIEM для группировки.
        assert ev["target_type"] == "http_endpoint"
        assert ev["target_id"] == f"{BASE}/servers"

    async def test_audit_emitted_on_loging_admin_block(
        self, client, loging_admin_token, captured_emits,
    ):
        """``loging_admin`` блок тоже эмитит event с правильным platform_role."""
        resp = await client.post(
            f"{BASE}/permissions/server/reader/view",
            headers=_hdr(loging_admin_token),
        )
        assert_error(resp, 403, "PLATFORM_ADMIN_BUSINESS_DATA_DENIED")

        events = _events(captured_emits, "http.platform_admin_blocked")
        assert len(events) == 1
        assert events[0]["details"]["platform_role"] == "loging_admin"

    async def test_no_audit_emitted_on_loging_reader_block(
        self, client, loging_reader_token, captured_emits,
    ):
        """``loging_reader`` middleware'ом НЕ блокируется — НЕТ
        ``http.platform_admin_blocked`` события. Если матрица отдаст 403,
        это её собственный audit (другое событие)."""
        resp = await client.get(
            f"{BASE}/servers",
            headers=_hdr(loging_reader_token),
        )
        # 403 от матрицы, не от guard
        assert_error(resp, 403)
        events = _events(captured_emits, "http.platform_admin_blocked")
        assert len(events) == 0, (
            "guard не должен эмитить http.platform_admin_blocked для loging_reader"
        )

    async def test_no_audit_emitted_on_legitimate_request(
        self, client, admin_token, make_server, captured_emits,
    ):
        """Sanity: легитимный department_admin не порождает ``http.platform_admin_blocked``."""
        await make_server(department_id="dep_a")
        resp = await client.get(f"{BASE}/servers", headers=_hdr(admin_token))
        assert resp.status_code == 200

        events = _events(captured_emits, "http.platform_admin_blocked")
        assert events == []

    async def test_no_audit_emitted_on_health(self, client, account_admin_token, captured_emits):
        """Health-path не триггерит ``http.platform_admin_blocked``, даже с account_admin token."""
        resp = await client.get(
            f"{BASE}/health",
            headers=_hdr(account_admin_token),
        )
        assert resp.status_code == 200

        events = _events(captured_emits, "http.platform_admin_blocked")
        assert events == []


# ── 9. Без bearer-токена middleware пропускает ──────────────────────────────


class TestAnonymousNotIntrospected:
    """Запросы без Authorization-header'а пропускаются middleware'ом — пусть
    endpoint решает, нужен ли auth. ``CurrentIdentity`` отдаст 401, либо
    health-endpoint пройдёт сам.
    """

    async def test_anonymous_business_endpoint_returns_401_not_403(self, client):
        """GET /servers без bearer → 401 (от ``CurrentIdentity``), не 403 platform-admin."""
        resp = await client.get(f"{BASE}/servers")
        # Конкретно ``ACCESS_TOKEN_MISSING``, не платформенный block
        assert_error(resp, 401, "ACCESS_TOKEN_MISSING")

    async def test_invalid_token_returns_401_not_403(self, client):
        """Bearer с мусором → 401 INVALID_TOKEN_FORMAT (от shape-check), не 403."""
        resp = await client.get(
            f"{BASE}/servers",
            headers={"Authorization": "Bearer garbage"},
        )
        # 401 — middleware не маскирует невалидный токен под platform-admin блок
        assert_error(resp, 401, "ACCESS_TOKEN_INVALID")


# ── 10. Sanity: account_admin без service_roles, но с заголовком — блок ─────


class TestAccountAdminPureBlocking:
    """Сanity: account_admin блокируется НЕЗАВИСИМО от наличия service_roles в
    его токене. По модели §7 платформенные роли не должны иметь сервисных
    ролей вообще, но даже если introspect соврёт и вернёт что-то — middleware
    блочит по ``platform_role``, а не по service_roles."""

    async def test_account_admin_with_fake_service_roles_still_blocked(
        self, client, make_token,
    ):
        """account_admin + service_roles = admin → всё ещё 403 от guard'а."""
        token = make_token(
            platform_role="account_admin",
            department_id="dep_a",
            service_roles={"server_service": ["admin"]},
            allowed_services=["server_service"],
        )
        resp = await client.get(f"{BASE}/servers", headers=_hdr(token))
        assert_error(resp, 403, "PLATFORM_ADMIN_BUSINESS_DATA_DENIED")


# ── 11b. os-versions read открыт платформенным ролям, запись — нет ──────────


class TestOsVersionsReadExemptForPlatformAdmins:
    """Каталог OS-версий — глобальный справочник, не бизнес-данные отдела.

    GET-чтение каталога открыто всем, включая платформенные роли
    ``account_admin`` / ``loging_admin``: guard их на этих путях не отбивает.
    Но исключение строго для GET — POST/PATCH/DELETE остаются под матрицей
    прав, у платформенных ролей действия ``os_version.*`` нет. И прочие
    business-эндпоинты по-прежнему 403.
    """

    OSV = f"{BASE}/os-versions"

    async def test_account_admin_can_list_os_versions(
        self, client, account_admin_token, admin_role_token_a,
    ):
        """``account_admin`` → 200 на GET /os-versions (раньше резал guard)."""
        await client.post(
            self.OSV, headers=_hdr(admin_role_token_a),
            json={"name": "astra-guard-list", "description": "x"},
        )
        resp = await client.get(self.OSV, headers=_hdr(account_admin_token))
        assert resp.status_code == 200
        assert resp.json()["total"] >= 1

    async def test_loging_admin_can_list_os_versions(
        self, client, loging_admin_token,
    ):
        """``loging_admin`` → 200 на GET /os-versions."""
        resp = await client.get(self.OSV, headers=_hdr(loging_admin_token))
        assert resp.status_code == 200

    async def test_account_admin_can_get_os_version_by_id(
        self, client, account_admin_token, admin_role_token_a,
    ):
        """``account_admin`` → 200 на GET /os-versions/{id}."""
        created = await client.post(
            self.OSV, headers=_hdr(admin_role_token_a),
            json={"name": "astra-guard-get", "description": "x"},
        )
        ov_id = created.json()["id"]
        resp = await client.get(f"{self.OSV}/{ov_id}", headers=_hdr(account_admin_token))
        assert resp.status_code == 200
        assert resp.json()["id"] == ov_id

    async def test_account_admin_can_get_os_version_by_name(
        self, client, account_admin_token, admin_role_token_a,
    ):
        """``account_admin`` → 200 на GET /os-versions/by-name/{name}."""
        await client.post(
            self.OSV, headers=_hdr(admin_role_token_a),
            json={"name": "astra-guard-byname", "description": "x"},
        )
        resp = await client.get(
            f"{self.OSV}/by-name/astra-guard-byname",
            headers=_hdr(account_admin_token),
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "astra-guard-byname"

    async def test_os_versions_read_emits_no_block_audit(
        self, client, account_admin_token, captured_emits,
    ):
        """Чтение каталога платформенной ролью не эмитит ``http.platform_admin_blocked``."""
        resp = await client.get(self.OSV, headers=_hdr(account_admin_token))
        assert resp.status_code == 200
        assert _events(captured_emits, "http.platform_admin_blocked") == []

    async def test_account_admin_still_blocked_on_business(
        self, client, account_admin_token,
    ):
        """Regression: os-versions exempt не открывает прочие business-эндпоинты."""
        resp = await client.get(f"{BASE}/servers", headers=_hdr(account_admin_token))
        assert_error(resp, 403, "PLATFORM_ADMIN_BUSINESS_DATA_DENIED")

    async def test_loging_admin_still_blocked_on_business(
        self, client, loging_admin_token,
    ):
        resp = await client.get(f"{BASE}/permissions", headers=_hdr(loging_admin_token))
        assert_error(resp, 403, "PLATFORM_ADMIN_BUSINESS_DATA_DENIED")

    async def test_account_admin_cannot_create_os_version(
        self, client, account_admin_token,
    ):
        """Запись каталога платформенной ролью отбивается guard'ом (POST не exempt)."""
        resp = await client.post(
            self.OSV, headers=_hdr(account_admin_token),
            json={"name": "astra-guard-write", "description": "x"},
        )
        assert_error(resp, 403, "PLATFORM_ADMIN_BUSINESS_DATA_DENIED")

    async def test_account_admin_cannot_update_os_version(
        self, client, account_admin_token, admin_role_token_a,
    ):
        """PATCH каталога платформенной ролью отбивается guard'ом."""
        created = await client.post(
            self.OSV, headers=_hdr(admin_role_token_a),
            json={"name": "astra-guard-patch", "description": "x"},
        )
        ov_id = created.json()["id"]
        resp = await client.patch(
            f"{self.OSV}/{ov_id}", headers=_hdr(account_admin_token),
            json={"description": "y"},
        )
        assert_error(resp, 403, "PLATFORM_ADMIN_BUSINESS_DATA_DENIED")

    async def test_account_admin_cannot_delete_os_version(
        self, client, account_admin_token, admin_role_token_a,
    ):
        """DELETE каталога платформенной ролью отбивается guard'ом."""
        created = await client.post(
            self.OSV, headers=_hdr(admin_role_token_a),
            json={"name": "astra-guard-delete", "description": "x"},
        )
        ov_id = created.json()["id"]
        resp = await client.delete(
            f"{self.OSV}/{ov_id}", headers=_hdr(account_admin_token),
        )
        assert_error(resp, 403, "PLATFORM_ADMIN_BUSINESS_DATA_DENIED")

    async def test_normal_user_still_reads_catalog(
        self, client, reader_token_a, admin_role_token_a,
    ):
        """Regression: обычный user/reader по-прежнему читает каталог."""
        await client.post(
            self.OSV, headers=_hdr(admin_role_token_a),
            json={"name": "astra-guard-user-read", "description": "x"},
        )
        resp = await client.get(self.OSV, headers=_hdr(reader_token_a))
        assert resp.status_code == 200


# ── 11. Неизвестный platform_role (rolling deploy) → 503, не 500 ────────────


class TestUnknownPlatformRoleFailsClosed:
    """Если auth_service выкатили впереди server_service и introspect вернул
    ``active=true`` с неизвестным ``platform_role`` (например, новая роль
    ``super_admin``), ``_to_identity`` не ложится в ``IdentityContext``-enum.

    Раньше ValidationError пробивала наверх как 500 на каждом запросе таких
    пользователей. Теперь guard ловит её и отвечает 503 fail-closed — доступ
    к бизнес-данным НЕ выдаётся.
    """

    async def test_unknown_platform_role_returns_503(self, client, make_token):
        token = make_token(
            platform_role="super_admin",  # роль вне PlatformRole-enum
            department_id="dep_a",
            allowed_services=["server_service"],
        )
        resp = await client.get(f"{BASE}/servers", headers=_hdr(token))
        assert resp.status_code == 503
        body = resp.json()
        assert body["error_code"] == "AUTH_SERVICE_UNAVAILABLE"
        # request_id обязан быть в envelope (выставлен outer middleware'ом).
        assert body["request_id"] is not None

    async def test_unknown_platform_role_does_not_emit_block_audit(
        self, client, make_token, captured_emits,
    ):
        """503-путь не эмитит ``http.platform_admin_blocked`` — это не блок
        platform-admin'а, а отказ из-за нераспознанной identity."""
        token = make_token(
            platform_role="super_admin",
            department_id="dep_a",
            allowed_services=["server_service"],
        )
        resp = await client.get(f"{BASE}/servers", headers=_hdr(token))
        assert resp.status_code == 503
        assert _events(captured_emits, "http.platform_admin_blocked") == []
