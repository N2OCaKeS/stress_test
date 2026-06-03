"""IPMI rotate dispatch на controller, чей server переехал в чужой dept.

`ipmi_repo.get_by_id(controller_id)` сам по себе dept-фильтра не делает —
controller разрешено лукапить по PK напрямую. Dept-изоляция возложена на
последующий `load_visible_server(controller.server_id)`. Если controller
живёт в нашем dept (исторически), а его server перевезли в чужой —
ветка ловит NotFoundError из `_ensure_visible` и переэмитит её в
`NO_IPMI_CONTROLLER` (чтобы leak информации о существовании server'а не
было).

Сценарий моделируем напрямую: controller в dep_a, server в dep_b (так
бывает после перевода server'а между департаментами без миграции его
controller'ов). Бот dep_a с rotate-permission → ожидаем 404 +
`audit: status=failure, reason=not_found_or_cross_dept`.
"""

from __future__ import annotations

import pytest

from src.models import IpmiController
from src.services import secrets_service
from src.utils.ids import _new_id

BASE = "/api/server/v1/ipmi-controllers"


def _hdr(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def captured_emits(monkeypatch):
    captured: list[dict] = []

    def fake_emit(action, actor_id=None, **kwargs):
        captured.append({"action": action, "actor_id": actor_id, **kwargs})

    import src.services.audit_service as audit_mod
    monkeypatch.setattr(audit_mod, "emit", fake_emit)
    for path in (
        "src.services.server.audit_service.emit",
        "src.services.permission_service.audit_service.emit",
        "src.api.v1.endpoints.worker_dispatch.audit_service.emit",
    ):
        try:
            monkeypatch.setattr(path, fake_emit)
        except (AttributeError, ImportError):
            pass
    return captured


@pytest.fixture
def stub_dispatch(monkeypatch):
    calls: list[dict] = []

    async def fake_dispatch(**kwargs):
        calls.append(kwargs)
        return_hit = kwargs.get("return_hit", False)
        new_id = "tsk_should_not_reach"
        return (new_id, False) if return_hit else new_id

    async def fake_dispatch_with_hit(**kwargs):
        kwargs["return_hit"] = True
        return await fake_dispatch(**kwargs)

    import src.services.worker_client as worker_mod
    monkeypatch.setattr(worker_mod, "dispatch_task", fake_dispatch)
    monkeypatch.setattr(worker_mod, "dispatch_task_with_hit", fake_dispatch_with_hit)
    monkeypatch.setattr(
        "src.api.v1.endpoints.worker_dispatch.worker_client.dispatch_task",
        fake_dispatch,
    )
    monkeypatch.setattr(
        "src.api.v1.endpoints.worker_dispatch.worker_client.dispatch_task_with_hit",
        fake_dispatch_with_hit,
    )
    return calls


@pytest.mark.asyncio
async def test_rotate_controller_in_my_dept_server_in_other_dept_404(
    client,
    db,
    worker_bot_token_a,
    make_server,
    captured_emits,
    stub_dispatch,
):
    """controller.dep=A, controller.server.dep=B → 404 NO_IPMI_CONTROLLER + audit failure."""
    # Server переехал в dep_b (или изначально там), controller-row остался
    # ссылкой на него. Department самого controller-row'а в схеме отсутствует
    # — visibility идёт через server. Так что достаточно иметь controller,
    # чей server_id указывает на dep_b-сервер.
    srv_b = await make_server(department_id="dep_b")

    ctrl_id = _new_id("ipm_")
    ctrl = IpmiController(
        id=ctrl_id,
        server_id=srv_b.id,
        kind="idrac",
        endpoint_url="https://idrac.cross-dept.test",
        username="ipmi_user",
        password_encrypted=secrets_service.encrypt(
            "ipmi-plaintext-secret",
            aad=secrets_service.aad_for_ipmi_credential(ctrl_id),
        ),
    )
    db.add(ctrl)
    await db.flush()

    resp = await client.post(
        f"{BASE}/{ctrl_id}/rotate",
        headers=_hdr(worker_bot_token_a),
    )
    assert resp.status_code == 404, resp.text
    body = resp.json()
    # Не должно протекать SERVER_NOT_FOUND — иначе раскрыли бы, что
    # controller_id привязан к чужому серверу.
    assert body["error_code"] == "NO_IPMI_CONTROLLER", body

    failures = [
        e for e in captured_emits
        if e["action"] == "ipmi_controller.rotate_dispatch"
        and e.get("status") == "failure"
        and (e.get("details") or {}).get("reason") == "not_found_or_cross_dept"
    ]
    assert len(failures) == 1, captured_emits
    ev = failures[0]
    assert ev["target_id"] == ctrl_id
    assert ev["target_type"] == "ipmi_controller"
    assert ev["allowed"] is True
    # dispatch_task НЕ должен был дёрнуться
    assert stub_dispatch == []


@pytest.mark.asyncio
async def test_rotate_no_permission_emits_denied_with_bot_subject_type(
    client,
    no_role_token_a,
    make_server,
    make_ipmi,
    captured_emits,
    stub_dispatch,
    monkeypatch,
):
    """Sanity: без rotate_credentials бьём 403 + denied audit. Используется как
    смежная проверка с test_w18_w2_subject_type (см. ниже)."""
    srv = await make_server(department_id="dep_a")
    ctrl = await make_ipmi(server_id=srv.id)

    resp = await client.post(
        f"{BASE}/{ctrl.id}/rotate",
        headers=_hdr(no_role_token_a),
    )
    assert resp.status_code == 403, resp.text
    denied = [
        e for e in captured_emits
        if e["action"] == "ipmi_controller.rotate_dispatch"
        and e.get("status") == "denied"
    ]
    assert len(denied) == 1, captured_emits
    assert stub_dispatch == []
