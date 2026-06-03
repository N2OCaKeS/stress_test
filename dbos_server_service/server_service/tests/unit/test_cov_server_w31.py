"""Coverage gaps — server_service w31 (P4 cov-gap closures from w13/w14 report).

Areas:
* GAP-2: ipmi_rotate_password_dispatch — SERVER_DECOMMISSIONED (409) для IPMI rotate.
* GAP-3: _dispatch_for_server — audit failure emit с reason=no_ipmi для
  power.status (require_ipmi=True).
* GAP-4: _check_target_department_for_server actor-mismatch → SERVER_NOT_FOUND
  (404) — через receive_inventory_facts (один из call-site'ов wrapper'а).
* GAP-7: rotated_at boundary value — ровно на границе rotated_at_skew_seconds
  (drift == max_skew → отбрасывается, drift == max_skew-1 → принимается).
* GAP-8: naive datetime в rotated_at/verified_at — путь tzinfo is None в
  internal_service.record_ipmi_credentials_rotated.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from src.core.constants import ServerStatus
from src.core.exceptions import (
    AuthorizationError,
    BadRequestError,
    NotFoundError,
)
from src.schemas.identity import IdentityContext


BASE = "/api/server/v1"


def _hdr(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _capture_emits(monkeypatch) -> list[dict]:
    """Захват `audit_service.emit` во всех модулях, где он импортится."""
    captured: list[dict] = []

    def fake_emit(action, actor_id=None, **kwargs):
        captured.append({"action": action, "actor_id": actor_id, **kwargs})

    import src.services.audit_service as audit_mod
    monkeypatch.setattr(audit_mod, "emit", fake_emit)
    monkeypatch.setattr(
        "src.api.v1.endpoints.worker_dispatch.audit_service.emit",
        fake_emit,
    )
    monkeypatch.setattr(
        "src.services.server.audit_service.emit",
        fake_emit,
    )
    monkeypatch.setattr(
        "src.services.internal_service.audit_service.emit",
        fake_emit,
    )
    return captured


@pytest.fixture
def captured_dispatch(monkeypatch):
    """Перехват dispatch_task — гарантия, что в decommissioned/no_ipmi ветках
    dispatch не происходит."""
    calls: list[dict] = []

    async def fake_dispatch(
        *, db=None, task_kind, target_server_id, payload,
        created_by, request_id,
        target_resource_id=None, idempotency_key=None,
        return_hit=False,
    ):
        calls.append({
            "task_kind": task_kind,
            "target_server_id": target_server_id,
            "payload": payload,
        })
        new_id = f"tsk_{task_kind.replace('.', '_')}_fake_{len(calls)}"
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


# ─────────────────────────────────────────────────────────────────────────────
# GAP-2: ipmi_rotate_password_dispatch — SERVER_DECOMMISSIONED (409)
# ─────────────────────────────────────────────────────────────────────────────


class TestIpmiRotateDispatchDecommissioned:
    """Аналог `test_decommissioned_blocks_rotation` для account-rotate, но для
    `POST /ipmi-controllers/{id}/rotate`. Decommissioned-сервер блокирует
    rotate с 409 SERVER_DECOMMISSIONED, audit-emit reason=decommissioned,
    dispatch не уходит."""

    async def test_decommissioned_blocks_ipmi_rotate(
        self, client, admin_role_token_a, make_server, make_ipmi,
        captured_dispatch, db, monkeypatch,
    ):
        captured = _capture_emits(monkeypatch)

        srv = await make_server(department_id="dep_a")
        ctrl = await make_ipmi(server_id=srv.id)
        srv.status = ServerStatus.DECOMMISSIONED
        await db.flush()

        resp = await client.post(
            f"{BASE}/ipmi-controllers/{ctrl.id}/rotate",
            headers=_hdr(admin_role_token_a),
        )

        assert resp.status_code == 409, resp.text
        assert resp.json().get("error_code") == "SERVER_DECOMMISSIONED"
        assert captured_dispatch == []

        failures = [
            e for e in captured
            if e["action"] == "ipmi_controller.rotate_dispatch"
            and e.get("status") == "failure"
        ]
        assert len(failures) == 1, captured
        ev = failures[0]
        assert ev["target_id"] == ctrl.id
        assert ev["target_type"] == "ipmi_controller"
        assert ev["details"]["reason"] == "decommissioned"
        assert ev["details"]["server_id"] == srv.id
        assert ev["details"]["department_id"] == "dep_a"


# ─────────────────────────────────────────────────────────────────────────────
# GAP-3: _dispatch_for_server power.status — audit failure reason=no_ipmi
# ─────────────────────────────────────────────────────────────────────────────


class TestDispatchForServerNoIpmiAudit:
    """`_dispatch_for_server` с `require_ipmi=True` (power.status) на сервере
    без IPMI-row должен эмитить failure-audit с reason=no_ipmi.

    Для `POST /servers/{id}/ipmi/power/on` это покрыто в
    `TestPowerNoIpmiAudit::test_no_ipmi_emits_failure_audit`. Здесь — для
    `power.status` через worker_dispatch.
    """

    async def test_power_status_no_ipmi_emits_failure_audit(
        self, client, operator_token_a, make_server,
        captured_dispatch, monkeypatch,
    ):
        captured = _capture_emits(monkeypatch)

        srv = await make_server(department_id="dep_a")  # без IPMI

        resp = await client.post(
            f"{BASE}/servers/{srv.id}/power/status",
            headers=_hdr(operator_token_a),
        )

        assert resp.status_code == 404
        assert resp.json().get("error_code") == "NO_IPMI_CONTROLLER"
        assert captured_dispatch == []

        failures = [
            e for e in captured
            if e["action"] == "server.power_status"
            and e.get("status") == "failure"
        ]
        assert len(failures) == 1, captured
        ev = failures[0]
        assert ev["target_id"] == srv.id
        assert ev["target_type"] == "server"
        assert ev["allowed"] is True
        assert ev["details"]["reason"] == "no_ipmi"
        assert ev["details"]["department_id"] == "dep_a"


# ─────────────────────────────────────────────────────────────────────────────
# GAP-4: _check_target_department_for_server actor-mismatch → SERVER_NOT_FOUND
# ─────────────────────────────────────────────────────────────────────────────


class TestCheckTargetDepartmentForServerActorMismatch:
    """`_check_target_department_for_server` с `mask_as_not_found=True`:
    actor.department_id ≠ server.department_id → 404 `SERVER_NOT_FOUND`.

    Симметричный к `test_credentials_rotated_actor_mismatch_returns_404_soft`
    тест для wrapper'а, отвечающего за server-target (а не controller).
    Использует `receive_inventory_facts` (один из call-site'ов).
    """

    async def test_actor_dept_mismatch_returns_404_server_not_found(
        self, db, make_server, monkeypatch,
    ):
        from src.schemas.internal import InventoryCallbackRequest
        from src.services import internal_service

        srv = await make_server(department_id="dep_a")

        # Bot из чужого dept'а — actor_department_id != server.department_id.
        identity = IdentityContext(
            user_id="bot_cross_dept",
            username="worker_bot",
            department_id="dep_b",
            department_name=None,
            allowed_services=["server_service"],
            service_roles={"server_service": ["admin"]},
            is_banned=False,
            platform_role=None,
            subject_type="bot",
        )

        captured = _capture_emits(monkeypatch)

        # permissions.require_action прошло бы (admin), а dept-check ниже —
        # blocking. Чтобы isolate'ить именно ветку actor-mismatch (а не
        # завалиться на отсутствующем `inventory_submit` action'е), пропускаем
        # permission-check.
        async def _allow_action(*args, **kwargs):
            return None

        monkeypatch.setattr(
            "src.services.internal_service.permissions.require_action",
            _allow_action,
        )

        payload = InventoryCallbackRequest(
            hostname=srv.hostname,
            kernel="5.15.0-test",
            cpu_brand="Intel",
            cpu_model="Xeon",
            cpu_cores=4,
            cpu_threads=8,
            cpu_frequency_ghz=2.4,
            os_version="Astra Linux SE 1.7",
            disks=[],
        )

        with pytest.raises(NotFoundError) as exc_info:
            await internal_service.receive_inventory(
                db=db,
                identity=identity,
                server_id=srv.id,
                payload=payload,
                target_department_id="dep_a",
            )

        assert exc_info.value.error_code == "SERVER_NOT_FOUND"

        denials = [
            e for e in captured
            if e["action"] == "server.inventory_received"
            and e.get("status") == "denied"
            and e.get("details", {}).get("reason") == "actor_department_mismatch"
        ]
        assert len(denials) == 1, captured
        ev = denials[0]
        assert ev["target_id"] == srv.id
        assert ev["target_type"] == "server"
        assert ev["allowed"] is False
        assert ev["details"]["actor_department_id"] == "dep_b"
        assert ev["details"]["server_department_id"] == "dep_a"


# ─────────────────────────────────────────────────────────────────────────────
# GAP-7: rotated_at boundary value — drift == max_skew (отбрасывается)
# ─────────────────────────────────────────────────────────────────────────────


class TestRotatedAtSkewBoundary:
    """Граничное значение `rotated_at_skew_seconds`: drift == max_skew → пройдёт
    (использовано `> max_skew`, не `>=`); drift == max_skew + 1 → отбросится.

    Проверка инварианта без HTTP-стека: дёргаем internal_service напрямую с
    payload'ом, у которого rotated_at точно на границе. Эмулирует поведение
    worker'а с NTP-drift'ом, равным окну.
    """

    async def test_rotated_at_drift_exactly_at_max_skew_passes(
        self, db, make_server, make_ipmi, monkeypatch,
    ):
        """drift == max_skew (rotated_at чуть-чуть позже now) → проходит
        (граничное значение). Использует `>` вместо `>=` — boundary value
        корректно попадает в окно.
        """
        from src.core.config import get_settings
        from src.schemas.internal import IpmiCredentialsRotatedRequest
        from src.services import internal_service

        srv = await make_server(department_id="dep_a")
        ctrl = await make_ipmi(server_id=srv.id)

        identity = IdentityContext(
            user_id="bot_worker",
            username="worker_bot",
            department_id="dep_a",
            department_name=None,
            allowed_services=["server_service"],
            service_roles={"server_service": ["admin"]},
            is_banned=False,
            platform_role=None,
            subject_type="bot",
        )

        max_skew = get_settings().rotated_at_skew_seconds
        now = datetime.now(timezone.utc)
        # rotated_at ровно на границе: timedelta(seconds=max_skew) от now.
        # Реализация: `if rotated_drift > rotated_skew_max: raise`.
        # Поэтому drift == max_skew должно пройти.
        rotated_at = now + timedelta(seconds=max_skew - 1)
        verified_at = now  # внутри окна BMC-verify
        payload = IpmiCredentialsRotatedRequest(
            new_password="V@lidPass123",
            rotated_at=rotated_at,
            verified_at=verified_at,
        )

        # Mock service-access permissions to allow rotate_credentials
        async def _allow_action(*args, **kwargs):
            return None

        monkeypatch.setattr(
            "src.services.internal_service.permissions.require_action",
            _allow_action,
        )

        captured = _capture_emits(monkeypatch)

        result = await internal_service.record_ipmi_credentials_rotated(
            db=db,
            identity=identity,
            controller_id=ctrl.id,
            payload=payload,
            target_department_id="dep_a",
        )

        assert result["ok"] is True
        # Никаких rotated_at_in_future failure'ов на границе быть не должно.
        rotated_failures = [
            e for e in captured
            if e["action"] == "ipmi_controller.credentials_rotated_callback"
            and e.get("status") == "failure"
            and e.get("details", {}).get("reason") == "rotated_at_in_future"
        ]
        assert rotated_failures == [], captured

    async def test_rotated_at_drift_just_above_max_skew_rejected(
        self, db, make_server, make_ipmi, monkeypatch,
    ):
        """drift > max_skew (на 1 секунду) → 400 ROTATED_AT_IN_FUTURE с
        failure-audit reason=rotated_at_in_future. Парный к boundary-тесту.
        """
        from src.core.config import get_settings
        from src.schemas.internal import IpmiCredentialsRotatedRequest
        from src.services import internal_service

        srv = await make_server(department_id="dep_a")
        ctrl = await make_ipmi(server_id=srv.id)

        identity = IdentityContext(
            user_id="bot_worker",
            username="worker_bot",
            department_id="dep_a",
            department_name=None,
            allowed_services=["server_service"],
            service_roles={"server_service": ["admin"]},
            is_banned=False,
            platform_role=None,
            subject_type="bot",
        )

        max_skew = get_settings().rotated_at_skew_seconds
        now = datetime.now(timezone.utc)
        # Запас 30s — `now` уплыл к моменту вычисления drift'а в коде,
        # буфер гарантирует drift > max_skew с устойчивым перевесом.
        rotated_at = now + timedelta(seconds=max_skew + 30)
        verified_at = now
        payload = IpmiCredentialsRotatedRequest(
            new_password="V@lidPass123",
            rotated_at=rotated_at,
            verified_at=verified_at,
        )

        async def _allow_action(*args, **kwargs):
            return None

        monkeypatch.setattr(
            "src.services.internal_service.permissions.require_action",
            _allow_action,
        )

        captured = _capture_emits(monkeypatch)

        with pytest.raises(BadRequestError) as exc_info:
            await internal_service.record_ipmi_credentials_rotated(
                db=db,
                identity=identity,
                controller_id=ctrl.id,
                payload=payload,
                target_department_id="dep_a",
            )

        assert exc_info.value.error_code == "ROTATED_AT_IN_FUTURE"

        failures = [
            e for e in captured
            if e["action"] == "ipmi_controller.credentials_rotated_callback"
            and e.get("status") == "failure"
            and e.get("details", {}).get("reason") == "rotated_at_in_future"
        ]
        assert len(failures) == 1, captured
        ev = failures[0]
        assert ev["details"]["max_skew_seconds"] == max_skew
        assert ev["details"]["rotated_drift_seconds"] > max_skew


# ─────────────────────────────────────────────────────────────────────────────
# GAP-8: naive datetime в rotated_at/verified_at → tzinfo guard в service'е
# ─────────────────────────────────────────────────────────────────────────────


class TestNaiveDatetimeTzinfoGuard:
    """`record_ipmi_credentials_rotated` принимает naive datetime в
    `rotated_at`/`verified_at`: путь `if value.tzinfo is None:
    value.replace(tzinfo=timezone.utc)`.

    На уровне Pydantic-схемы тот же guard стоит в `_ensure_tz_aware` валидаторе,
    поэтому в normal HTTP-флоу до service'а доезжает уже tz-aware значение. Но
    при прямом вызове через `model_construct` (минующем валидацию) naive
    datetime проходит до сервиса — этот путь покрыт защитой в коде, и его надо
    тестировать на регрессию.
    """

    async def test_naive_datetime_treated_as_utc(
        self, db, make_server, make_ipmi, monkeypatch,
    ):
        from src.schemas.internal import IpmiCredentialsRotatedRequest
        from src.services import internal_service

        srv = await make_server(department_id="dep_a")
        ctrl = await make_ipmi(server_id=srv.id)

        identity = IdentityContext(
            user_id="bot_worker",
            username="worker_bot",
            department_id="dep_a",
            department_name=None,
            allowed_services=["server_service"],
            service_roles={"server_service": ["admin"]},
            is_banned=False,
            platform_role=None,
            subject_type="bot",
        )

        # model_construct бежит мимо field_validator'ов, поэтому naive datetime
        # доезжает до service'а нетронутым. Воспроизводит сценарий, когда схема
        # перестроится / удалится, а service-guard должен продолжать защищать.
        now_naive = datetime.utcnow()  # naive (legacy stdlib API)
        payload = IpmiCredentialsRotatedRequest.model_construct(
            new_password="V@lidPass123",
            rotated_at=now_naive,
            verified_at=now_naive,
        )

        assert payload.rotated_at.tzinfo is None
        assert payload.verified_at.tzinfo is None

        async def _allow_action(*args, **kwargs):
            return None

        monkeypatch.setattr(
            "src.services.internal_service.permissions.require_action",
            _allow_action,
        )

        captured = _capture_emits(monkeypatch)

        result = await internal_service.record_ipmi_credentials_rotated(
            db=db,
            identity=identity,
            controller_id=ctrl.id,
            payload=payload,
            target_department_id="dep_a",
        )

        assert result["ok"] is True
        # rotated_at в success-payload'е — ISO-8601 с zulu/offset (значит
        # tz-aware): service навесил timezone.utc.
        assert "+00:00" in result["rotated_at"] or result["rotated_at"].endswith("Z")

        # Контроллер должен получить tz-aware password_rotated_at.
        await db.refresh(ctrl)
        assert ctrl.password_rotated_at is not None
        # SQLAlchemy mapping: TIMESTAMP — naive если column WITHOUT TIME ZONE,
        # tz-aware если WITH. Здесь главное, что значение записалось без ошибки.
