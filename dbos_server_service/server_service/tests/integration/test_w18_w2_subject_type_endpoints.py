"""Интеграционный тест: `subject_type: "bot"` в denied-audit на endpoint'ах
с `identity=` в `emit_denied_on_authz_error`.

Helper получает `identity=identity`, чтобы denied-trail
SIEM мог отличать bot'ов от user'ов. Unit-тесты на helper уже есть
(`tests/unit/test_audit_helpers_subject_type.py`,
`tests/unit/test_subject_type_propagation.py`). Этот файл закрывает
интеграционный gap: реально дергаем endpoint'ы bot-token'ом без прав
и assert'им, что в captured `audit_service.emit` для denied'а попало
`details.subject_type == "bot"`.

Покрытие фиксированное — список собран из всех call-site'ов
`emit_denied_on_authz_error(..., identity=identity)` в
`src/api/v1/endpoints/`. При появлении новых identity= sites — добавлять
сюда новую строку (или поменять список из автоматического discovery'я,
если решат вынести в общий fixture).
"""

from __future__ import annotations

import pytest
import pytest_asyncio

BASE = "/api/server/v1"


from tests._helpers import auth_hdr as _hdr, make_emit_capture  # noqa: E402


@pytest.fixture
def captured_emits(monkeypatch):
    return make_emit_capture(
        monkeypatch,
        "src.services.server.audit_service.emit",
        "src.services.server_account.audit_service.emit",
        "src.services.permission_service.audit_service.emit",
        "src.api.v1.endpoints.worker_dispatch.audit_service.emit",
        "src.api.v1.endpoints.ipmi.audit_service.emit",
        "src.api.v1.endpoints.tasks.audit_service.emit",
        "src.api.v1.endpoints.inventory.audit_service.emit",
        "src.api.v1.endpoints.installed_packages.audit_service.emit",
        "src.services.audit_helpers.audit_service.emit",
    )


@pytest.fixture
def stub_dispatch(monkeypatch):
    """Заглушка `worker_client.dispatch_task` на всех call-site'ах."""

    async def fake_dispatch(**kwargs):
        return "tsk_unused"

    async def fake_dispatch_with_hit(**kwargs):
        return ("tsk_unused", False)

    for path in (
        "src.api.v1.endpoints.worker_dispatch.worker_client.dispatch_task",
        "src.api.v1.endpoints.ipmi.worker_client.dispatch_task",
        # installed_packages / users.inventory диспатчат через общую обвязку
        # `endpoints/_dispatch.py`.
        "src.api.v1.endpoints._dispatch.worker_client.dispatch_task",
    ):
        try:
            monkeypatch.setattr(path, fake_dispatch)
        except (AttributeError, ImportError):
            pass
    for path in (
        "src.api.v1.endpoints.worker_dispatch.worker_client.dispatch_task_with_hit",
        "src.api.v1.endpoints.ipmi.worker_client.dispatch_task_with_hit",
        "src.api.v1.endpoints._dispatch.worker_client.dispatch_task_with_hit",
    ):
        try:
            monkeypatch.setattr(path, fake_dispatch_with_hit)
        except (AttributeError, ImportError):
            pass


@pytest.fixture
def bot_no_role_token(make_token):
    """Bot-token (subject_type=bot) без service-роли в server_service.

    На любом endpoint'е с require_action(...) → AuthorizationError →
    denied-audit. В details ожидаем `subject_type: "bot"`.
    """
    return make_token(
        department_id="dep_a",
        service_roles={"server_service": []},
        allowed_services=["server_service"],
        subject_type="bot",
    )


@pytest_asyncio.fixture
async def setup_resources(make_server, make_account, make_ipmi):
    """Создаёт server в dep_a (с IPMI controller'ом и account'ом), чтобы
    URL'ы со {server_id} / {account_id} / {controller_id} были валидными.

    Permission-чек идёт ДО visibility/existence-чеков, так что для denied'а
    эти ресурсы даже не обязаны существовать — но иметь их полезно, чтобы
    тест не получил 404 раньше, чем сработает permission-emit (в
    edge-case'ах с handler'ом, где visibility-проверка идёт раньше).
    """
    srv = await make_server(department_id="dep_a", with_ipmi=False)
    ctrl = await make_ipmi(server_id=srv.id)
    acc = await make_account(server_id=srv.id, login="root")
    return {"server_id": srv.id, "controller_id": ctrl.id, "account_id": acc.id}


# Список endpoint'ов с `emit_denied_on_authz_error(..., identity=identity)`.
# (method, url_template, audit_action, target_type, target_key)
#   - url_template: подставляются {server_id}/{account_id}/{controller_id}/{task_id}
#   - audit_action: что искать в captured_emits
#   - target_type: ожидаемый target_type для denied
#   - target_key: ключ ресурса в setup_resources, который попадёт в target_id
#                 ("server_id", "account_id", "controller_id"); None если literal
# Тела для эндпоинтов, которым строго нужен валидный body (иначе 422 от схемы
# уходит ДО permission-эмита и тест поймает не то). `server.prepare` —
# единственный пример: ServerPrepareRequest валидирует base64 + strong password.
# `dXNlcg==` = "user"; пароль "Bootstrap-Pwd-Long-1!" base64.
_PREPARE_BODY = {
    "username_b64": "dXNlcg==",
    "password_b64": "Qm9vdHN0cmFwLVB3ZC1Mb25nLTEh",
}

# Дефолт для всех POST — пустой `{}`. Эндпоинты с обязательным body
# переопределяют его через `body_overrides`.
_BODY_OVERRIDES = {
    "/servers/{server_id}/prepare": _PREPARE_BODY,
}


ENDPOINTS = [
    # ipmi.py: power family (action emitted = server.power_on/off/reboot)
    ("POST", "/servers/{server_id}/ipmi/power/on", "server.power_on", "server", "server_id"),
    ("POST", "/servers/{server_id}/ipmi/power/off", "server.power_off", "server", "server_id"),
    ("POST", "/servers/{server_id}/ipmi/power/reboot", "server.power_reboot", "server", "server_id"),
    # ipmi.py: view_credentials_meta (GET /servers/{id}/ipmi/credentials)
    ("GET", "/servers/{server_id}/ipmi/credentials", "ipmi_controller.view_credentials_meta", "ipmi_controller", "server_id"),
    # installed_packages.py
    ("POST", "/servers/{server_id}/installed-packages", "installed_packages.list", "server", "server_id"),
    # inventory.py (users-inventory via SSH)
    ("POST", "/servers/{server_id}/users/inventory", "server.users_inventory_triggered", "server", "server_id"),
    # tasks.py (cancel) — target=task, target_id literal
    ("POST", "/tasks/tsk_fake_for_audit/cancel", "task.cancelled", "task", None),
    # worker_dispatch.py: power.status / inventory.sync (via _dispatch_for_server)
    ("POST", "/servers/{server_id}/power/status", "server.power_status", "server", "server_id"),
    ("POST", "/servers/{server_id}/inventory/sync", "server.inventory_sync", "server", "server_id"),
    # worker_dispatch.py: server.prepare
    ("POST", "/servers/{server_id}/prepare", "server.prepare", "server", "server_id"),
    # worker_dispatch.py: account.* (_dispatch_account_on_host) — provision/update/deprovision
    ("POST", "/server-accounts/{account_id}/provision?server_id={server_id}", "server_account.provision", "server_account", "account_id"),
    ("POST", "/server-accounts/{account_id}/update_on_host?server_id={server_id}", "server_account.update_on_host", "server_account", "account_id"),
    ("POST", "/server-accounts/{account_id}/deprovision?server_id={server_id}", "server_account.deprovision", "server_account", "account_id"),
    # worker_dispatch.py: account.rotate_password_dispatch
    ("POST", "/server-accounts/{account_id}/rotate", "server_account.rotate_password_dispatch", "server_account", "account_id"),
    # worker_dispatch.py: ipmi.rotate_dispatch
    ("POST", "/ipmi-controllers/{controller_id}/rotate", "ipmi_controller.rotate_dispatch", "ipmi_controller", "controller_id"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("method,url_tpl,audit_action,target_type,target_key", ENDPOINTS)
async def test_denied_audit_carries_bot_subject_type(
    client, bot_no_role_token, setup_resources, captured_emits, stub_dispatch,
    method, url_tpl, audit_action, target_type, target_key,
):
    """Bot без service-роли → 403 + denied audit с details.subject_type='bot'."""
    url = BASE + url_tpl.format(**setup_resources)
    # Найдём body-override по url_tpl без query-string'а
    url_tpl_path = url_tpl.split("?", 1)[0]
    body = _BODY_OVERRIDES.get(url_tpl_path, {})
    if method == "POST":
        resp = await client.post(url, headers=_hdr(bot_no_role_token), json=body)
    else:
        resp = await client.get(url, headers=_hdr(bot_no_role_token))
    # Permission-check эмит идёт первым в каждом из этих handler'ов — даже если
    # дальнейшая логика отдаст 404 или 409, нужное emit-событие уже captured.
    assert resp.status_code in (403, 404, 409), (url, resp.status_code, resp.text)

    denied = [
        e for e in captured_emits
        if e["action"] == audit_action
        and e.get("status") == "denied"
        and (e.get("details") or {}).get("reason") == "permission_denied"
    ]
    assert len(denied) == 1, (
        f"{audit_action} denied-emit not found for {url}; captured={captured_emits}"
    )
    ev = denied[0]
    assert ev["allowed"] is False
    assert ev["target_type"] == target_type
    if target_key is not None:
        assert ev["target_id"] == setup_resources[target_key]
    # Главный assert этого файла:
    assert (ev.get("details") or {}).get("subject_type") == "bot", ev
