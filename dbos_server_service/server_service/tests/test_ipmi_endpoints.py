"""Интеграционные тесты `/api/server/v1/servers/{id}/ipmi/...`.

Реальный диспатч worker'а требует второй DB (dev_server_worker) и Redis — в
тесте этого нет, поэтому `worker_client.dispatch_task` мочится через
monkeypatch и фиксируется правильным образом.

Покрывается:
* GET /ipmi, PUT, DELETE, /credentials, /power — CRUD;
  (`/credentials/rotate` снят — 410 GONE, ротация только через worker dispatch)
* POST /power/{on,off,reboot} — 202 + task_id формата tsk_*,
  permission check, request_id propagation в dispatch, действие в matrix
  (POWER_ON / OFF / REBOOT) — reader → 403, operator → 200, без таска;
* без Bearer → 401.
"""

from __future__ import annotations

import pytest

from tests._helpers import assert_error, auth_hdr as _hdr

BASE = "/api/server/v1/servers"


@pytest.fixture
def captured_dispatch(monkeypatch):
    """Перехватывает worker_client.dispatch_task — возвращает фиктивный task_id.

    Эмулирует идемпотентный путь: при повторном вызове с тем же
    ``idempotency_key`` возвращает ранее сгенерированный task_id и НЕ
    добавляет новый element в ``calls`` (мы хотим зафиксировать, что
    endpoint попросил dispatch — но настоящая дедупликация делается внутри
    worker_client. Чтобы избежать дублирования логики, тут идемпотентность
    тоже встроена — иначе тест будет проверять симулятор, а не настоящий
    контракт endpoint→client.)
    """
    calls: list[dict] = []
    _by_key: dict[str, str] = {}

    async def fake_dispatch(*, db=None, task_kind, target_server_id, payload,
                            created_by, request_id,
                            target_resource_id=None, idempotency_key=None,
                            return_hit=False):
        if idempotency_key is not None and idempotency_key in _by_key:
            # Идемпотентный hit — НЕ регистрируем call (как делает
            # настоящий worker_client при попадании в существующий ключ).
            existing = _by_key[idempotency_key]
            return (existing, True) if return_hit else existing
        calls.append({
            "task_kind": task_kind,
            "target_server_id": target_server_id,
            "payload": payload,
            "created_by": created_by,
            "request_id": request_id,
            "idempotency_key": idempotency_key,
        })
        new_id = f"tsk_{task_kind.replace('.', '_')}_fake_{len(calls)}"
        if idempotency_key is not None:
            _by_key[idempotency_key] = new_id
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


# ── Power ON ─────────────────────────────────────────────────────────────────

class TestPowerOn:
    async def test_admin_role_dispatches(
        self, client, admin_role_token_a, make_server, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a", with_ipmi=True)
        resp = await client.post(
            f"{BASE}/{srv.id}/ipmi/power/on",
            headers=_hdr(admin_role_token_a),
        )
        assert resp.status_code == 202
        body = resp.json()
        assert body["task_id"].startswith("tsk_")
        assert body["status"] == "queued"
        assert len(captured_dispatch) == 1
        assert captured_dispatch[0]["task_kind"] == "power.on"
        assert captured_dispatch[0]["target_server_id"] == srv.id
        # `target_department_id` добавляется в payload, чтобы worker
        # эхо-фоллбэкнул его как `X-Target-Department-Id` при обращении к
        # internal credential endpoints — defense-in-depth против
        # cross-tenant использования worker-PAT (см. internal_service).
        assert captured_dispatch[0]["payload"] == {
            "server_id": srv.id,
            "target_department_id": "dep_a",
        }

    async def test_operator_can_power_on(
        self, client, operator_token_a, make_server, captured_dispatch,
    ):
        """operator имеет default grant `power_on`."""
        srv = await make_server(department_id="dep_a", with_ipmi=True)
        resp = await client.post(
            f"{BASE}/{srv.id}/ipmi/power/on",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 202

    async def test_reader_cannot_power_on(
        self, client, reader_token_a, make_server, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/{srv.id}/ipmi/power/on",
            headers=_hdr(reader_token_a),
        )
        assert_error(resp, 403, "PERMISSION_DENIED")
        assert captured_dispatch == []  # dispatch не вызывался

    async def test_guest_cannot_power_on(
        self, client, guest_token_a, make_server, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/{srv.id}/ipmi/power/on",
            headers=_hdr(guest_token_a),
        )
        assert_error(resp, 403, "PERMISSION_DENIED")

    async def test_no_token_returns_401(self, client, make_server, captured_dispatch):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(f"{BASE}/{srv.id}/ipmi/power/on")
        assert_error(resp, 401, "ACCESS_TOKEN_MISSING")
        assert captured_dispatch == []

    async def test_department_admin_can_power_on(
        self, client, admin_token, make_server, captured_dispatch,
    ):
        """`admin_token` теперь department_admin в dep_a — power_on в своём dept.

        Раньше тут был ``test_account_admin_can_power_on``. Account_admin
        теперь блокируется guard'ом ДО endpoint'а (403
        PLATFORM_ADMIN_BUSINESS_DATA_DENIED), см.
        ``tests/integration/test_platform_admin_block.py``.
        """
        srv = await make_server(department_id="dep_a", with_ipmi=True)
        resp = await client.post(
            f"{BASE}/{srv.id}/ipmi/power/on", headers=_hdr(admin_token),
        )
        assert resp.status_code == 202


# ── Power OFF ────────────────────────────────────────────────────────────────

class TestPowerOff:
    async def test_operator_can_power_off(
        self, client, operator_token_a, make_server, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a", with_ipmi=True)
        resp = await client.post(
            f"{BASE}/{srv.id}/ipmi/power/off",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 202
        assert captured_dispatch[0]["task_kind"] == "power.off"

    async def test_reader_cannot(self, client, reader_token_a, make_server, captured_dispatch):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/{srv.id}/ipmi/power/off",
            headers=_hdr(reader_token_a),
        )
        assert_error(resp, 403, "PERMISSION_DENIED")


# ── Power REBOOT ─────────────────────────────────────────────────────────────

class TestPowerReboot:
    async def test_operator_can_reboot(
        self, client, operator_token_a, make_server, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a", with_ipmi=True)
        resp = await client.post(
            f"{BASE}/{srv.id}/ipmi/power/reboot",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 202
        assert captured_dispatch[0]["task_kind"] == "power.reboot"


# ── dispatch_task payload / created_by ───────────────────────────────────────

class TestDispatchedPayload:
    async def test_created_by_carries_user_id(
        self, client, operator_token_a, make_server, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a", with_ipmi=True)
        await client.post(
            f"{BASE}/{srv.id}/ipmi/power/on",
            headers=_hdr(operator_token_a),
        )
        assert captured_dispatch[0]["created_by"]
        assert captured_dispatch[0]["created_by"].startswith("usr_")


# ── Cross-department / not-found / decommissioned visibility ─────────────────


class TestPowerVisibility:
    """`_dispatch_power` ДОЛЖЕН ходить через `server_svc.get_server` (VIEW +
    department isolation), чтобы:

    * operator из `dep_b` не мог power-cycle сервер из `dep_a` (видит 404);
    * запрос на несуществующий `server_id` → 404, без записи в worker-очередь;
    * сервер в статусе `decommissioned` → 409 SERVER_DECOMMISSIONED.

    Regression-guard для cross-dept изоляции IPMI.
    """

    async def test_cross_dept_operator_gets_404_power_off(
        self, client, operator_token_b, make_server, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/{srv.id}/ipmi/power/off",
            headers=_hdr(operator_token_b),
        )
        # error_code из server_svc.get_server / _ensure_visible
        assert_error(resp, 404, "SERVER_NOT_FOUND")
        assert captured_dispatch == []  # worker НЕ должен получить задачу

    async def test_cross_dept_operator_gets_404_power_on(
        self, client, operator_token_b, make_server, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/{srv.id}/ipmi/power/on",
            headers=_hdr(operator_token_b),
        )
        assert_error(resp, 404, "SERVER_NOT_FOUND")
        assert captured_dispatch == []

    async def test_cross_dept_operator_gets_404_power_reboot(
        self, client, operator_token_b, make_server, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/{srv.id}/ipmi/power/reboot",
            headers=_hdr(operator_token_b),
        )
        assert_error(resp, 404, "SERVER_NOT_FOUND")
        assert captured_dispatch == []

    async def test_nonexistent_server_returns_404(
        self, client, operator_token_a, captured_dispatch,
    ):
        resp = await client.post(
            f"{BASE}/srv_ghost_does_not_exist/ipmi/power/off",
            headers=_hdr(operator_token_a),
        )
        assert_error(resp, 404, "SERVER_NOT_FOUND")
        assert captured_dispatch == []

    async def test_nonexistent_server_returns_404_for_dept_admin(
        self, client, admin_token, captured_dispatch,
    ):
        """`admin_token` (department_admin) не bypass'ит реальное 404 — сервера-то нет.

        Раньше account_admin; теперь department_admin в dep_a — для
        несуществующего id (любого dept) логика одинаковая: 404.
        """
        resp = await client.post(
            f"{BASE}/srv_ghost/ipmi/power/on",
            headers=_hdr(admin_token),
        )
        assert_error(resp, 404, "SERVER_NOT_FOUND")
        assert captured_dispatch == []

    async def test_decommissioned_server_returns_409(
        self, client, operator_token_a, make_server, captured_dispatch, db,
    ):
        from src.core.constants import ServerStatus

        srv = await make_server(department_id="dep_a")
        srv.status = ServerStatus.DECOMMISSIONED
        await db.flush()
        resp = await client.post(
            f"{BASE}/{srv.id}/ipmi/power/off",
            headers=_hdr(operator_token_a),
        )
        assert_error(resp, 409, "SERVER_DECOMMISSIONED")
        assert captured_dispatch == []

    async def test_decommissioned_blocks_power_on(
        self, client, admin_role_token_a, make_server, captured_dispatch, db,
    ):
        from src.core.constants import ServerStatus

        srv = await make_server(department_id="dep_a")
        srv.status = ServerStatus.DECOMMISSIONED
        await db.flush()
        resp = await client.post(
            f"{BASE}/{srv.id}/ipmi/power/on",
            headers=_hdr(admin_role_token_a),
        )
        assert_error(resp, 409, "SERVER_DECOMMISSIONED")
        assert captured_dispatch == []

    async def test_decommissioned_blocks_reboot_even_for_dept_admin(
        self, client, admin_token, make_server, captured_dispatch, db,
    ):
        """`admin_token` (department_admin своего dept) не должен ребутить decommissioned.

        Бизнес-правило: списанные сервера не принимают power-операции
        независимо от роли. Раньше тут был ``test_..._for_account_admin``;
        семантика та же — даже admin_role в этом dept'е не bypass'ит
        SERVER_DECOMMISSIONED.
        """
        from src.core.constants import ServerStatus

        srv = await make_server(department_id="dep_a")
        srv.status = ServerStatus.DECOMMISSIONED
        await db.flush()
        resp = await client.post(
            f"{BASE}/{srv.id}/ipmi/power/reboot",
            headers=_hdr(admin_token),
        )
        assert_error(resp, 409, "SERVER_DECOMMISSIONED")
        assert captured_dispatch == []


# ── No IPMI controller — 404 NO_IPMI_CONTROLLER ────


class TestPowerNoIpmiController:
    """`_dispatch_power` ДОЛЖЕН валидировать наличие IPMI-controller row
    (BMC endpoint + credentials) ДО публикации задачи в worker.

    Без этой проверки server_service пускал бы задачу в Redis на сервер без
    BMC-конфигурации, worker'у пришлось бы fail'ить таску в runtime, клиент
    получил бы 202+task_id (ложный success), а реальная ошибка вылезала бы
    только асинхронно как failed task. Это ломает контракт «202 = задача
    физически выполнима».

    Фикс — `404 NO_IPMI_CONTROLLER` синхронно (унифицирован с rotate/get).
    Покрытие:
    * сервер без IPMI-controller → 404, dispatch не вызывался;
    * `_decommissioned` проверка идёт ПЕРЕД `_no_ipmi` — server без IPMI и в
      статусе decommissioned получает 409 SERVER_DECOMMISSIONED (приоритет
      бизнес-правила «не трогать списанные сервера»);
    * audit-failure event эмиттится с `reason="no_ipmi"`.
    """

    async def test_power_on_without_ipmi_returns_404(
        self, client, operator_token_a, make_server, captured_dispatch,
    ):
        # `make_server` без `with_ipmi=True` → IPMI controller row отсутствует
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/{srv.id}/ipmi/power/on",
            headers=_hdr(operator_token_a),
        )
        body = assert_error(resp, 404, "NO_IPMI_CONTROLLER")
        assert "IPMI" in body.get("message", "")
        # worker НЕ должен получить задачу
        assert captured_dispatch == []

    async def test_power_off_without_ipmi_returns_404(
        self, client, operator_token_a, make_server, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/{srv.id}/ipmi/power/off",
            headers=_hdr(operator_token_a),
        )
        assert_error(resp, 404, "NO_IPMI_CONTROLLER")
        assert captured_dispatch == []

    async def test_power_reboot_without_ipmi_returns_404(
        self, client, operator_token_a, make_server, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/{srv.id}/ipmi/power/reboot",
            headers=_hdr(operator_token_a),
        )
        assert_error(resp, 404, "NO_IPMI_CONTROLLER")
        assert captured_dispatch == []

    async def test_no_ipmi_blocks_dept_admin_too(
        self, client, admin_token, make_server, captured_dispatch,
    ):
        """`admin_token` (department_admin своего dept) не bypass'ит — даже
        admin не может power-cycle сервер без BMC-конфигурации (нет
        физического канала для команды).

        Раньше тут был ``test_no_ipmi_blocks_account_admin_too``.
        """
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/{srv.id}/ipmi/power/on",
            headers=_hdr(admin_token),
        )
        assert_error(resp, 404, "NO_IPMI_CONTROLLER")
        assert captured_dispatch == []

    async def test_decommissioned_takes_priority_over_no_ipmi(
        self, client, operator_token_a, make_server, captured_dispatch, db,
    ):
        """Сервер сразу и DECOMMISSIONED и без IPMI → 409 SERVER_DECOMMISSIONED
        (бизнес-правило «не трогаем списанные» приоритетнее «нет BMC»).
        """
        from src.core.constants import ServerStatus

        srv = await make_server(department_id="dep_a")  # без IPMI
        srv.status = ServerStatus.DECOMMISSIONED
        await db.flush()
        resp = await client.post(
            f"{BASE}/{srv.id}/ipmi/power/off",
            headers=_hdr(operator_token_a),
        )
        assert_error(resp, 409, "SERVER_DECOMMISSIONED")
        assert captured_dispatch == []

    async def test_no_ipmi_does_not_leak_to_unauthorized(
        self, client, reader_token_a, make_server, captured_dispatch,
    ):
        """403 от permission check идёт РАНЬШЕ IPMI-validate — reader без
        POWER_ON прав получит 403, а не 404 NO_IPMI_CONTROLLER (иначе мы бы
        утекали структурную информацию о сервере анонимам/guest'ам).
        """
        srv = await make_server(department_id="dep_a")  # без IPMI
        resp = await client.post(
            f"{BASE}/{srv.id}/ipmi/power/on",
            headers=_hdr(reader_token_a),
        )
        assert_error(resp, 403, "PERMISSION_DENIED")
        # error_code НЕ должен быть NO_IPMI_CONTROLLER — это утечка bookkeeping
        assert resp.json().get("error_code") != "NO_IPMI_CONTROLLER"
        assert captured_dispatch == []


class TestPowerNoIpmiAudit:
    """`reason="no_ipmi"` в audit-failure event'е."""

    async def test_no_ipmi_emits_failure_audit(
        self, client, operator_token_a, make_server, captured_emits, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")  # без IPMI
        resp = await client.post(
            f"{BASE}/{srv.id}/ipmi/power/on",
            headers=_hdr(operator_token_a),
        )
        assert_error(resp, 404, "NO_IPMI_CONTROLLER")
        failures = [
            e for e in _events(captured_emits, "server.power_on")
            if e.get("status") == "failure"
        ]
        assert len(failures) == 1
        ev = failures[0]
        assert ev["target_id"] == srv.id
        assert ev["target_type"] == "server"
        assert ev["allowed"] is True
        assert ev["details"]["reason"] == "no_ipmi"
        assert ev["details"]["department_id"] == "dep_a"

    async def test_no_ipmi_does_not_emit_success(
        self, client, operator_token_a, make_server, captured_emits, captured_dispatch,
    ):
        """Sanity: на 404 NO_IPMI_CONTROLLER success-emit не должен случиться."""
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/{srv.id}/ipmi/power/off",
            headers=_hdr(operator_token_a),
        )
        assert_error(resp, 404, "NO_IPMI_CONTROLLER")
        successes = [
            e for e in _events(captured_emits, "server.power_off")
            if e.get("status") == "success"
        ]
        assert successes == []


# ── Idempotency-Key (worker_client deduplication) ───────────────────


class TestPowerIdempotency:
    """Regression-guard для idempotency-key дедупликации.

    `worker_client._insert_task_row` раньше:
      * не передавал `idempotency_key` в INSERT,
      * не использовал helper `get_by_idempotency_key`,
      * на коллизию UNIQUE отдавал 500 IntegrityError клиенту.

    Теперь endpoint читает HTTP-header ``Idempotency-Key`` и пробрасывает
    в ``worker_client.dispatch_task``; повторный вызов с тем же ключом
    возвращает тот же ``task_id`` и НЕ публикует новую задачу.
    """

    async def test_same_key_returns_same_task_id_no_double_dispatch(
        self, client, operator_token_a, make_server, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a", with_ipmi=True)
        headers = {**_hdr(operator_token_a), "Idempotency-Key": "client-uuid-42"}

        r1 = await client.post(f"{BASE}/{srv.id}/ipmi/power/on", headers=headers)
        r2 = await client.post(f"{BASE}/{srv.id}/ipmi/power/on", headers=headers)

        assert r1.status_code == 202
        assert r2.status_code == 202
        assert r1.json()["task_id"] == r2.json()["task_id"]
        # dispatch_task получил вызов дважды, но только один РЕАЛЬНЫЙ
        # insert/публикация (второй раз внутри fake_dispatch вернул
        # закешированный id) — фиксируем по списку записанных calls.
        assert len(captured_dispatch) == 1
        assert captured_dispatch[0]["idempotency_key"] == "client-uuid-42"

    async def test_without_idempotency_key_each_call_creates_new_task(
        self, client, operator_token_a, make_server, captured_dispatch,
    ):
        """Без header — старое поведение: каждый POST → новый task."""
        srv = await make_server(department_id="dep_a", with_ipmi=True)
        r1 = await client.post(
            f"{BASE}/{srv.id}/ipmi/power/on", headers=_hdr(operator_token_a),
        )
        r2 = await client.post(
            f"{BASE}/{srv.id}/ipmi/power/on", headers=_hdr(operator_token_a),
        )
        assert r1.status_code == 202
        assert r2.status_code == 202
        assert r1.json()["task_id"] != r2.json()["task_id"]
        assert len(captured_dispatch) == 2
        assert captured_dispatch[0]["idempotency_key"] is None
        assert captured_dispatch[1]["idempotency_key"] is None

    async def test_different_keys_create_different_tasks(
        self, client, operator_token_a, make_server, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a", with_ipmi=True)
        r1 = await client.post(
            f"{BASE}/{srv.id}/ipmi/power/on",
            headers={**_hdr(operator_token_a), "Idempotency-Key": "k-1"},
        )
        r2 = await client.post(
            f"{BASE}/{srv.id}/ipmi/power/on",
            headers={**_hdr(operator_token_a), "Idempotency-Key": "k-2"},
        )
        assert r1.json()["task_id"] != r2.json()["task_id"]
        assert len(captured_dispatch) == 2
        assert {captured_dispatch[0]["idempotency_key"],
                captured_dispatch[1]["idempotency_key"]} == {"k-1", "k-2"}


# ── Audit-completeness: worker dispatch failures ─────────────────────────────


@pytest.fixture
def captured_emits(monkeypatch):
    """Захватывает `audit_service.emit` вызовы из endpoints/ipmi.py.

    Патчим прямой importer и источник — на случай если внутри модуля
    идёт обращение через `audit_service.emit(...)`.
    """
    from tests._helpers import make_emit_capture

    return make_emit_capture(
        monkeypatch,
        "src.api.v1.endpoints.ipmi.audit_service.emit",
    )


def _events(captured: list[dict], action: str) -> list[dict]:
    return [e for e in captured if e["action"] == action]


class TestPowerDispatchFailureAudit:
    """Regression-guard для audit-полноты при dispatch-сбоях.

    `worker_client.dispatch_task` может поднять:
      * `ConflictError(TASK_IDEMPOTENT_CONFLICT)` — гонка на UNIQUE
        `idempotency_key` после повторного SELECT;
      * `ServiceUnavailableError(WORKER_DB_NOT_CONFIGURED /
        WORKER_REDIS_NOT_CONFIGURED / UNKNOWN_TASK_KIND)`.

    До фикса эти ошибки минули `audit_service.emit` в `_dispatch_power`
    → попадали только в http-middleware fallback (`http.*`), что ломало
    инвариант «каждая попытка power-операции → `server.power_*` event».
    """

    async def test_conflict_error_emits_failure_and_returns_409(
        self, client, operator_token_a, make_server, captured_emits, monkeypatch,
    ):
        """`worker_client.dispatch_task` → ConflictError → 409 + audit failure."""
        from src.core.exceptions import ConflictError

        async def boom(**_kwargs):
            raise ConflictError(
                error_code="TASK_IDEMPOTENT_CONFLICT",
                message="Task insert failed and idempotent retry did not resolve",
            )

        monkeypatch.setattr(
            "src.api.v1.endpoints.ipmi.worker_client.dispatch_task", boom,
        )
        monkeypatch.setattr(
            "src.api.v1.endpoints.ipmi.worker_client.dispatch_task_with_hit", boom,
        )

        srv = await make_server(department_id="dep_a", with_ipmi=True)
        resp = await client.post(
            f"{BASE}/{srv.id}/ipmi/power/on",
            headers={**_hdr(operator_token_a), "Idempotency-Key": "race-key-1"},
        )
        assert_error(resp, 409, "TASK_IDEMPOTENT_CONFLICT")

        failures = [
            e for e in _events(captured_emits, "server.power_on")
            if e.get("status") == "failure"
        ]
        assert len(failures) == 1
        ev = failures[0]
        assert ev["target_id"] == srv.id
        assert ev["target_type"] == "server"
        assert ev["allowed"] is True
        assert ev["details"]["reason"] == "idempotent_conflict"
        assert ev["details"]["task_kind"] == "power.on"
        assert ev["details"]["department_id"] == "dep_a"

    async def test_service_unavailable_emits_failure_and_returns_503(
        self, client, operator_token_a, make_server, captured_emits, monkeypatch,
    ):
        """`worker_client.dispatch_task` → ServiceUnavailableError → 503 + audit failure."""
        from src.core.exceptions import ServiceUnavailableError

        async def boom(**_kwargs):
            raise ServiceUnavailableError(
                error_code="WORKER_REDIS_NOT_CONFIGURED",
                message="SERVER_WORKER_REDIS_URL is not set",
            )

        monkeypatch.setattr(
            "src.api.v1.endpoints.ipmi.worker_client.dispatch_task", boom,
        )
        monkeypatch.setattr(
            "src.api.v1.endpoints.ipmi.worker_client.dispatch_task_with_hit", boom,
        )

        srv = await make_server(department_id="dep_a", with_ipmi=True)
        resp = await client.post(
            f"{BASE}/{srv.id}/ipmi/power/off",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 503
        body = resp.json()
        assert body.get("error_code") == "WORKER_REDIS_NOT_CONFIGURED"

        failures = [
            e for e in _events(captured_emits, "server.power_off")
            if e.get("status") == "failure"
        ]
        assert len(failures) == 1
        ev = failures[0]
        assert ev["target_id"] == srv.id
        assert ev["target_type"] == "server"
        assert ev["allowed"] is True
        assert ev["details"]["reason"] == "worker_unreachable"
        assert ev["details"]["task_kind"] == "power.off"
        assert ev["details"]["department_id"] == "dep_a"

    async def test_service_unavailable_unknown_task_kind_emits_failure(
        self, client, operator_token_a, make_server, captured_emits, monkeypatch,
    ):
        """UNKNOWN_TASK_KIND (broker stub отсутствует) тоже идёт как worker_unreachable."""
        from src.core.exceptions import ServiceUnavailableError

        async def boom(**_kwargs):
            raise ServiceUnavailableError(
                error_code="UNKNOWN_TASK_KIND",
                message="No taskiq stub registered for task kind 'power.reboot'",
            )

        monkeypatch.setattr(
            "src.api.v1.endpoints.ipmi.worker_client.dispatch_task", boom,
        )
        monkeypatch.setattr(
            "src.api.v1.endpoints.ipmi.worker_client.dispatch_task_with_hit", boom,
        )

        srv = await make_server(department_id="dep_a", with_ipmi=True)
        resp = await client.post(
            f"{BASE}/{srv.id}/ipmi/power/reboot",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 503
        failures = [
            e for e in _events(captured_emits, "server.power_reboot")
            if e.get("status") == "failure"
        ]
        assert len(failures) == 1
        assert failures[0]["details"]["reason"] == "worker_unreachable"

    async def test_conflict_error_does_not_emit_success(
        self, client, operator_token_a, make_server, captured_emits, monkeypatch,
    ):
        """Sanity: при ConflictError success-emit не должен случиться."""
        from src.core.exceptions import ConflictError

        async def boom(**_kwargs):
            raise ConflictError(
                error_code="TASK_IDEMPOTENT_CONFLICT", message="race",
            )

        monkeypatch.setattr(
            "src.api.v1.endpoints.ipmi.worker_client.dispatch_task", boom,
        )
        monkeypatch.setattr(
            "src.api.v1.endpoints.ipmi.worker_client.dispatch_task_with_hit", boom,
        )

        srv = await make_server(department_id="dep_a", with_ipmi=True)
        resp = await client.post(
            f"{BASE}/{srv.id}/ipmi/power/on",
            headers={**_hdr(operator_token_a), "Idempotency-Key": "race-key-2"},
        )
        assert_error(resp, 409, "TASK_IDEMPOTENT_CONFLICT")
        successes = [
            e for e in _events(captured_emits, "server.power_on")
            if e.get("status") == "success"
        ]
        assert successes == []


# Read-only IPMI view-эндпоинты (view_credentials метаданные + cached power) —
# в tests/test_view_credentials_get.py. Reveal-credentials — в
# tests/test_ipmi_reveal_credentials.py.
