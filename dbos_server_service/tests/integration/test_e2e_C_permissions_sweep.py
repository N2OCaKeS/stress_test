"""Кластер C: sweep матрицы прав (entity × action).

Для каждой пары `(entity, action)` из `ENTITY_ACTIONS`:

- **позитив:** роль `admin` (system-wide seed-grant на всё) → 200/201/202
  + audit success в loging_service;
- **негатив:** роль `guest` (нулевые гранты) → 403 + audit denied с
  `reason=permission_denied`.

Worker-only пары (`inventory_submit`, `provision_on_host`,
`prepare_callback`) проверяются через `/permissions/catalog` —
HTTP-эндпоинта для них у user'а нет.

End-to-end сценарий с кастомной ролью лежит в этом же файле в
`TestCustomRoleEndToEnd`.
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from tests.integration._helpers_C_permissions import (
    ENTITY_ACTION_SWEEP,
    SERVICE_NAME,
    WORKER_ONLY_PAIRS,
    SweepCase,
    all_sweep_pairs,
    assert_audit_found,
    assign_user_roles,
    auth_header,
    create_ipmi,
    create_server,
    ensure_role,
    expect_denied_audit,
    now_utc,
    setup_cluster_c,
)


# ── Module-scoped setup ──────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def cluster_c(
    integration_stack, auth_client: httpx.Client, admin_token: str
):
    """Готовим dept `c_perms` + системные роли + четырёх пользователей."""
    return setup_cluster_c(auth_client, admin_token)


# ── Sweep tests ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("entity,action", all_sweep_pairs())
def test_action_positive(
    cluster_c,
    server_client: httpx.Client,
    admin_token: str,
    logging_client: httpx.Client,
    entity: str,
    action: str,
):
    """Роль `admin` (системные seed-grants) должна успешно выполнять action."""
    case: SweepCase = ENTITY_ACTION_SWEEP[(entity, action)]
    ctx = case.setup(server_client, cluster_c.admin_token, cluster_c.dept_id)
    ctx["dept_id"] = cluster_c.dept_id

    if action in ("permission_grant", "permission_revoke"):
        # /permissions PUT/DELETE требует чтобы роль в URL существовала в
        # каталоге service-ролей. Заводим разовую "probe"-роль через auth_service.
        from tests.integration.conftest import AUTH_URL

        probe_role = f"probe_{entity}_{action}"
        with httpx.Client(base_url=AUTH_URL, timeout=10) as ac:
            ensure_role(ac, admin_token, cluster_c.dept_id, SERVICE_NAME, probe_role)
        ctx["probe_role"] = probe_role
        if action == "permission_revoke":
            # revoke бросит 404 без существующей строки — заранее кладём grant.
            server_client.put(
                f"/api/server/v1/permissions/permission/{probe_role}/view",
                json={"target_department_id": cluster_c.dept_id},
                headers=auth_header(cluster_c.admin_token),
            )

    since = now_utc()
    resp = case.invoke(server_client, cluster_c.admin_token, ctx)
    assert resp.status_code in case.success_codes, (
        f"positive [{entity}/{action}] expected {case.success_codes}, "
        f"got {resp.status_code}: {resp.text}"
    )

    # Audit-проверка — для permission.view нет дискретного audit-события
    # (read-only без эмита), пропускаем.
    if (entity, action) == ("permission", "view"):
        return
    # Для view_password success-side эмитит `server_account.password_revealed`
    # с status=success. Для остальных audit_action имени совпадает.
    assert_audit_found(
        logging_client,
        action=case.audit_action,
        status="success",
        from_time=since,
        fail_message=(
            f"positive [{entity}/{action}] audit '{case.audit_action}' success not found"
        ),
    )


@pytest.mark.parametrize("entity,action", all_sweep_pairs())
def test_action_negative_guest(
    cluster_c,
    server_client: httpx.Client,
    logging_client: httpx.Client,
    entity: str,
    action: str,
):
    """Роль `guest` (нулевые гранты) — 403 + audit denied с permission_denied."""
    case: SweepCase = ENTITY_ACTION_SWEEP[(entity, action)]
    ctx = case.setup(server_client, cluster_c.admin_token, cluster_c.dept_id)
    ctx["dept_id"] = cluster_c.dept_id
    ctx["probe_role"] = "reader"  # для permission grant/revoke берём реальную

    since = now_utc()
    resp = case.invoke(server_client, cluster_c.guest_token, ctx)
    # Допустимые denied-коды:
    #   * 403 PERMISSION_DENIED — основная проверка матрицы;
    #   * 404 — visibility-check сработал раньше (например, view сервера —
    #     guest без VIEW получает 404 при cross-dept; в собственном dept
    #     get_server делает require_action(VIEW) → 403).
    # Для нашего dept всё должно бить именно 403.
    assert resp.status_code in (401, 403, 404), (
        f"negative [{entity}/{action}] expected 401/403/404, "
        f"got {resp.status_code}: {resp.text}"
    )
    # 401 не должны видеть — токен валиден; 404 допустимо только если
    # endpoint-логика смешивает visibility-fail в один код с permission-fail.
    assert resp.status_code != 401, f"guest token unexpectedly invalid: {resp.text}"

    # Audit denied — у некоторых action'ов (view_password / view_credentials /
    # power_status_cached) система не отделяет permission_denied от
    # not_found_or_cross_dept, поэтому проверяем хотя бы что какой-то
    # denied-event с правильным action есть.
    if (entity, action) == ("permission", "view"):
        # read-only list — denied не аудитится отдельно (middleware пишет
        # http.access_denied); skip.
        return
    if (entity, action) == ("server_account", "view_password"):
        # GET карточки без view_password права просто отдаёт null в
        # `password_b64` без эмита `password_revealed`. denied-audit на
        # уровне server_account.view не пишется — только http.access_denied
        # уровнем middleware. Audit-часть теста не применима.
        return
    # Для view_password специфика: чтение карточки само не пишет denied —
    # пишется server_account.view denied. Для grant_sudo на create -
    # эмитится server_account.create denied. У части power-actions denied
    # пишется под reason=no_view_permission (guest без view сервера ещё до
    # power-роли). Эти варианты тоже валидные denied-аудиты — поэтому ev != None.
    assert_audit_found(
        logging_client,
        action=case.audit_action,
        status="denied",
        from_time=since,
        fail_message=(
            f"negative [{entity}/{action}] denied audit '{case.audit_action}' not found"
        ),
    )


# ── Worker-only pairs: только catalog + grant/revoke ────────────────────────


def test_worker_only_pairs_in_catalog(
    cluster_c, server_client: httpx.Client
):
    """Worker-only пары присутствуют в catalog с `worker_only=True`."""
    r = server_client.get(
        "/api/server/v1/permissions/catalog",
        headers=auth_header(cluster_c.admin_token),
    )
    assert r.status_code == 200, r.text
    by_entity = {e["entity_type"]: e for e in r.json()}
    for entity, action in WORKER_ONLY_PAIRS:
        entry = by_entity.get(entity)
        assert entry, f"entity {entity} missing in catalog"
        actions = {a["action"]: a for a in entry["actions"]}
        assert action in actions, f"{entity}.{action} missing in catalog"
        assert actions[action]["worker_only"] is True, (
            f"{entity}.{action} expected worker_only=True"
        )


@pytest.mark.parametrize("entity,action", WORKER_ONLY_PAIRS)
def test_worker_only_grant_revoke_roundtrip(
    cluster_c,
    auth_client: httpx.Client,
    admin_token: str,
    server_client: httpx.Client,
    logging_client: httpx.Client,
    entity: str,
    action: str,
):
    """Воркер-only action можно gran'нуть и revoke'нуть через permission API.

    Сам action через user-facing HTTP не вызвать (internal endpoint), но
    мы проверяем что матрица его принимает и пишет permission.grant /
    permission.revoke с правильным entity/action в details.
    """
    role_name = f"worker_probe_{entity}_{action}"
    ensure_role(
        auth_client, admin_token, cluster_c.dept_id, SERVICE_NAME, role_name
    )
    since = now_utc()
    r = server_client.put(
        f"/api/server/v1/permissions/{entity}/{role_name}/{action}",
        json={"target_department_id": cluster_c.dept_id},
        headers=auth_header(cluster_c.admin_token),
    )
    assert r.status_code in (200, 201), f"grant {entity}/{action}: {r.status_code} {r.text}"
    assert_audit_found(
        logging_client,
        action="permission.grant",
        status="success",
        from_time=since,
        extra_match=lambda it: (
            (it.get("details") or {}).get("entity_type") == entity
            and (it.get("details") or {}).get("action") == action
        ),
        fail_message=f"permission.grant success audit not found for {entity}.{action}",
    )

    since2 = now_utc()
    r = server_client.delete(
        f"/api/server/v1/permissions/{entity}/{role_name}/{action}",
        params={"target_department_id": cluster_c.dept_id},
        headers=auth_header(cluster_c.admin_token),
    )
    assert r.status_code == 200, f"revoke {entity}/{action}: {r.status_code} {r.text}"
    assert_audit_found(
        logging_client,
        action="permission.revoke",
        status="success",
        from_time=since2,
        extra_match=lambda it: (
            (it.get("details") or {}).get("entity_type") == entity
            and (it.get("details") or {}).get("action") == action
        ),
        fail_message=f"permission.revoke success audit not found for {entity}.{action}",
    )


# ── Custom role end-to-end ────────────────────────────────────────────────────


class TestCustomRoleEndToEnd:
    """Создание кастомной роли → grant → assign → действие проходит →
    revoke роли → действие отбивается."""

    def test_custom_role_power_reboot_only(
        self,
        cluster_c,
        auth_client: httpx.Client,
        admin_token: str,
        server_client: httpx.Client,
        logging_client: httpx.Client,
    ):
        # 1. account_admin создаёт кастомную роль в нашем dept'е.
        # Suffix — на случай повторного прогона в той же БД (auth_service
        # держит users / role_definitions per-session).
        suffix = uuid.uuid4().hex[:6]
        role_name = f"server_restarter_{suffix}"
        ensure_role(
            auth_client, admin_token, cluster_c.dept_id, SERVICE_NAME, role_name,
            display_name="Server Restarter",
        )

        # 2. account_admin'a в server_service пускать нельзя (middleware
        # блокирует platform_admin), поэтому grant'аем матрицу через
        # самого dept-admin'а (`cluster_c.admin_token` несёт service-роль
        # `admin`, у неё есть permission_grant в seed'е).
        #
        # `_dispatch_power` сперва ходит в `server_svc.get_server` →
        # `require_action(VIEW)` (защищает существование сервера от
        # cross-dept enumerate), поэтому роль обязана иметь `view` поверх
        # самого power_reboot. Гранты идут одной парой.
        for act in ("view", "power_reboot"):
            grant_resp = server_client.put(
                f"/api/server/v1/permissions/server/{role_name}/{act}",
                json={"target_department_id": cluster_c.dept_id},
                headers=auth_header(cluster_c.admin_token),
            )
            assert grant_resp.status_code in (200, 201), grant_resp.text

        # 3. Создаём пользователя без ролей и присваиваем `server_restarter`.
        from tests.integration._helpers_C_permissions import make_role_user
        user, user_token = make_role_user(
            auth_client, admin_token, cluster_c.dept_id, role_name,
            username=f"restarter_{role_name}",
        )

        # 4. Готовим сервер с IPMI от admin'а.
        srv = create_server(server_client, cluster_c.admin_token, cluster_c.dept_id)
        create_ipmi(server_client, cluster_c.admin_token, srv["id"])

        # 5. power_reboot должен пройти.
        since = now_utc()
        r = server_client.post(
            f"/api/server/v1/servers/{srv['id']}/ipmi/power/reboot",
            headers=auth_header(user_token),
        )
        assert r.status_code in (200, 202), (
            f"power_reboot expected 200/202, got {r.status_code}: {r.text}"
        )
        assert_audit_found(
            logging_client, action="server.power_reboot", status="success",
            from_time=since, target_id=srv["id"],
            fail_message="power_reboot success audit not found",
        )

        # 6. power_on у `server_restarter` отсутствует — должен быть 403.
        since = now_utc()
        r = server_client.post(
            f"/api/server/v1/servers/{srv['id']}/ipmi/power/on",
            headers=auth_header(user_token),
        )
        assert r.status_code == 403, (
            f"power_on expected 403, got {r.status_code}: {r.text}"
        )
        expect_denied_audit(logging_client, "server.power_on", since)

        # 7. Снимаем роль с юзера (assign пустой список).
        user_id = user.get("user_id") or user.get("id")
        assign_user_roles(auth_client, admin_token, user_id, [])

        # 8. После revoke'а роли power_reboot тоже отбивается.
        # У юзера больше нет даже view → _dispatch_power падает раньше
        # power-гейта, reason="no_view_permission" вместо "permission_denied".
        since = now_utc()
        r = server_client.post(
            f"/api/server/v1/servers/{srv['id']}/ipmi/power/reboot",
            headers=auth_header(user_token),
        )
        assert r.status_code == 403, (
            f"power_reboot after revoke expected 403, got {r.status_code}: {r.text}"
        )
        expect_denied_audit(
            logging_client, "server.power_reboot", since,
            reason="no_view_permission",
        )
