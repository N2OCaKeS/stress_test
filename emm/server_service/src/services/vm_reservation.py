"""Бронь ВМ «от имени сервиса» — зеркало `services/server.py` с другими полями.

Стадия — `vms.service_busy_state` + `busy_service_name` + `busy_note`.
`vms.busy_state` — отдельный lifecycle-lock (creating/reverting), не путать
со стадией брони. Человеческая бронь — `vms.status`; сервисная ставит
`run test`, чтобы старые экраны видели ВМ занятой без правок.

`reservation_view()` сводит всё к серверному словарю `busy_state`, чтобы
testing_service разбирал ВМ-стенд тем же кодом, что и физический.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import (
    VM_STATUS_FREE,
    VM_STATUS_RUN_TEST,
    BusyActorType,
    BusyState,
)
from src.core.exceptions import ConflictError, NotFoundError
from src.models import Vm
from src.repositories import vm as repo
from src.services import audit_service
from src.services.server import _TAKEOVER_STATES

# Стадии, пока держится которые ВМ не трогает никто из людей, включая
# админа — как `testing`/`acs` у серверов (`services/reservation.py`).
SERVICE_LOCKED_STATES = (BusyState.ACS, BusyState.TESTING, BusyState.BUSY)


def reservation_view(vm: Vm) -> dict:
    """Бронь ВМ в терминах серверного `busy_state` (для ответов и обзора пула).

    Приоритет: сервисная бронь → lifecycle-операция (`updating`: ВМ сейчас
    нельзя брать, как сервер под astra-update) → человеческая бронь (`busy`,
    логин — в `busy_note`) → `free`.
    """
    if vm.service_busy_state:
        return {
            "busy_state": vm.service_busy_state,
            "busy_actor_type": BusyActorType.SERVICE.value,
            "busy_service_name": vm.busy_service_name,
            "busy_user_id": None,
            "busy_note": vm.busy_note,
            "busy_since": vm.service_busy_since,
        }
    if vm.busy_state:
        return {
            "busy_state": BusyState.UPDATING.value,
            "busy_actor_type": BusyActorType.USER.value,
            "busy_service_name": None,
            "busy_user_id": None,
            "busy_note": f"vm {vm.busy_state}",
            "busy_since": vm.busy_since,
        }
    if vm.status != VM_STATUS_FREE:
        return {
            "busy_state": BusyState.BUSY.value,
            "busy_actor_type": BusyActorType.USER.value,
            "busy_service_name": None,
            "busy_user_id": None,
            "busy_note": vm.status,
            "busy_since": None,
        }
    return {
        "busy_state": BusyState.FREE.value,
        "busy_actor_type": BusyActorType.USER.value,
        "busy_service_name": None,
        "busy_user_id": None,
        "busy_note": None,
        "busy_since": None,
    }


def service_holds(vm: Vm, service_name: str) -> bool:
    """True, если сервисную бронь ВМ держит именно этот сервис."""
    return bool(vm.service_busy_state) and vm.busy_service_name == service_name


def _not_found(vm_id: str, action: str, service_name: str, reason: str) -> NotFoundError:
    audit_service.emit(
        action, target_id=vm_id, target_type="vm",
        status="failure", allowed=True,
        details={"reason": reason, "service_name": service_name},
    )
    return NotFoundError(error_code="VM_NOT_FOUND", message="VM not found")


def _ensure_service_holds(vm: Vm, service_name: str, *, action: str) -> None:
    """409 `VM_RESERVED_BY_OTHER`, если бронь держит не этот сервис (или человек)."""
    if service_holds(vm, service_name):
        return
    view = reservation_view(vm)
    audit_service.emit(
        action, target_id=vm.id, target_type="vm",
        status="denied", allowed=False,
        details={
            "reason": "reservation_held_by_other",
            "service_name": service_name,
            "department_id": vm.department_id,
            "busy_state": view["busy_state"],
            "busy_service_name": vm.busy_service_name,
        },
    )
    raise ConflictError(
        error_code="VM_RESERVED_BY_OTHER",
        message=(
            "VM reservation is held by another actor; a service can only "
            "release or update the reservation it acquired itself"
        ),
        details={"busy_actor_type": view["busy_actor_type"], "busy_service_name": vm.busy_service_name},
    )


def _service_values(service_name: str, busy_state: str, busy_note: str | None) -> dict:
    return {
        "service_busy_state": busy_state,
        "busy_service_name": service_name,
        "busy_note": busy_note,
        "service_busy_since": datetime.now(timezone.utc),
        "status": VM_STATUS_RUN_TEST,
    }


_CLEARED = {
    "service_busy_state": None,
    "busy_service_name": None,
    "busy_note": None,
    "service_busy_since": None,
    "status": VM_STATUS_FREE,
}


async def acquire_vm_for_service(
    db: AsyncSession,
    *,
    vm_id: str,
    service_name: str,
    busy_state: str,
    busy_note: str | None,
    requested_by_department_id: str | None = None,
    takeover: bool = False,
) -> tuple[Vm, dict | None]:
    """Захват ВМ от имени сервиса — аналог `acquire_server_for_service`.

    Атомарный CAS: ВМ свободна по всем трём полям (`status='free'`, нет
    lifecycle-операции, нет сервисной брони). Несовпадение отдела —
    404 `VM_NOT_FOUND` (не 403: enumeration-oracle). `takeover=True`
    отнимает человеческую бронь (`status=<login>`) и `testing_done`.
    """
    action = "vm.acquired_for_service"
    obj = await repo.get_by_id(db, vm_id)
    if obj is None:
        raise _not_found(vm_id, action, service_name, "not_found")
    if requested_by_department_id is not None and requested_by_department_id != obj.department_id:
        audit_service.emit(
            action, target_id=vm_id, target_type="vm",
            status="denied", allowed=False,
            details={
                "reason": "target_department_mismatch",
                "service_name": service_name,
                "vm_department_id": obj.department_id,
                "requested_by_department_id": requested_by_department_id,
            },
        )
        raise NotFoundError(error_code="VM_NOT_FOUND", message="VM not found")

    result = await db.execute(
        sa_update(Vm)
        .where(
            Vm.id == vm_id,
            Vm.status == VM_STATUS_FREE,
            Vm.busy_state.is_(None),
            Vm.service_busy_state.is_(None),
        )
        .values(**_service_values(service_name, busy_state, busy_note))
    )
    if result.rowcount == 1:
        await db.commit()
        await db.refresh(obj)
        audit_service.emit(
            action, target_id=vm_id, target_type="vm",
            status="success", allowed=True,
            details={
                "service_name": service_name, "department_id": obj.department_id,
                "busy_state": busy_state, "busy_note": busy_note,
            },
        )
        return obj, None

    db.expire(obj)
    if takeover:
        taken = await _takeover(db, vm_id, service_name=service_name, busy_state=busy_state, busy_note=busy_note)
        if taken is not None:
            return taken
    current = await repo.get_by_id(db, vm_id)
    if current is None:
        raise _not_found(vm_id, action, service_name, "vanished_during_acquire")
    view = reservation_view(current)
    if view["busy_state"] == BusyState.FREE:
        # Бронь сняли между CAS и перечиткой — обычный захват заново.
        return await acquire_vm_for_service(
            db, vm_id=vm_id, service_name=service_name, busy_state=busy_state,
            busy_note=busy_note, requested_by_department_id=requested_by_department_id,
            takeover=takeover,
        )
    audit_service.emit(
        action, target_id=vm_id, target_type="vm",
        status="failure", allowed=True,
        details={
            "reason": "already_busy", "service_name": service_name,
            "department_id": current.department_id, "current_state": view["busy_state"],
            "busy_service_name": current.busy_service_name, "takeover": takeover,
        },
    )
    raise ConflictError(
        error_code="VM_ALREADY_BUSY",
        message="VM is already busy, reserved or has an operation in progress",
        details={"current_state": view["busy_state"]},
    )


async def _takeover(
    db: AsyncSession, vm_id: str, *, service_name: str, busy_state: str, busy_note: str | None,
) -> tuple[Vm, dict] | None:
    """Переписать бронь `busy` (человек) / `testing_done` на сервис под FOR UPDATE.

    Правило то же, что у серверов (`services/server.py::_TAKEOVER_STATES`).
    None — бронь не отнимаемая (или ВМ освободилась), решает вызывающий.
    """
    locked = await repo.get_for_update(db, vm_id)
    if locked is None:
        return None
    view = reservation_view(locked)
    if view["busy_state"] not in _TAKEOVER_STATES:
        return None
    previous = {
        "busy_state": view["busy_state"],
        "busy_user_id": None,
        "busy_service_name": locked.busy_service_name,
        "busy_note": view["busy_note"],
    }
    for key, value in _service_values(service_name, busy_state, busy_note).items():
        setattr(locked, key, value)
    await db.commit()
    await db.refresh(locked)
    audit_service.emit(
        "vm.reservation_taken_over", target_id=vm_id, target_type="vm",
        status="success", allowed=True,
        details={
            "service_name": service_name, "department_id": locked.department_id,
            "busy_state": busy_state, "busy_note": busy_note,
            "previous_busy_state": previous["busy_state"],
            "previous_busy_service_name": previous["busy_service_name"],
            "previous_busy_note": previous["busy_note"],
        },
    )
    return locked, previous


async def _load_held(db: AsyncSession, vm_id: str, service_name: str, action: str) -> Vm:
    obj = await repo.get_for_update(db, vm_id)
    if obj is None:
        raise _not_found(vm_id, action, service_name, "not_found")
    if not obj.service_busy_state:
        audit_service.emit(
            action, target_id=vm_id, target_type="vm",
            status="failure", allowed=True,
            details={"reason": "not_busy", "service_name": service_name, "department_id": obj.department_id},
        )
        raise ConflictError(error_code="VM_NOT_BUSY", message="VM has no service reservation")
    _ensure_service_holds(obj, service_name, action=action)
    return obj


async def release_vm_for_service(db: AsyncSession, *, vm_id: str, service_name: str) -> Vm:
    """Снять собственную бронь — ВМ в `free` (`status='free'`)."""
    action = "vm.released_for_service"
    obj = await _load_held(db, vm_id, service_name, action)
    previous_state = obj.service_busy_state
    for key, value in _CLEARED.items():
        setattr(obj, key, value)
    await db.commit()
    await db.refresh(obj)
    audit_service.emit(
        action, target_id=vm_id, target_type="vm",
        status="success", allowed=True,
        details={"service_name": service_name, "department_id": obj.department_id, "previous_state": previous_state},
    )
    return obj


async def release_vm_for_service_as_done(db: AsyncSession, *, vm_id: str, service_name: str) -> Vm:
    """Очередь стенда опустела: бронь → `testing_done`, ВМ ждёт человека.

    Держатель и метка остаются справочным контекстом «кто тестировал»;
    снимает статус человеческий `POST /vms/{id}/release` (любой с правом
    release — как `acknowledge-testing-done` у серверов).
    """
    action = "vm.released_for_service"
    obj = await _load_held(db, vm_id, service_name, action)
    previous_state = obj.service_busy_state
    obj.service_busy_state = BusyState.TESTING_DONE.value
    await db.commit()
    await db.refresh(obj)
    audit_service.emit(
        action, target_id=vm_id, target_type="vm",
        status="success", allowed=True,
        details={
            "service_name": service_name, "department_id": obj.department_id,
            "previous_state": previous_state, "mark_as": BusyState.TESTING_DONE.value,
        },
    )
    return obj


async def set_vm_service_status(
    db: AsyncSession, *, vm_id: str, service_name: str, busy_state: str, busy_note: str | None,
) -> Vm:
    """Сменить стадию своей брони (`acs` → `testing`); `service_busy_since` не двигается."""
    action = "vm.service_status_changed"
    obj = await _load_held(db, vm_id, service_name, action)
    previous_state = obj.service_busy_state
    obj.service_busy_state = busy_state
    obj.status = VM_STATUS_RUN_TEST
    if busy_note is not None:
        obj.busy_note = busy_note
    await db.commit()
    await db.refresh(obj)
    audit_service.emit(
        action, target_id=vm_id, target_type="vm",
        status="success", allowed=True,
        details={
            "service_name": service_name, "department_id": obj.department_id,
            "previous_state": previous_state, "busy_state": busy_state, "busy_note": obj.busy_note,
        },
    )
    return obj


async def get_vm_for_service(db: AsyncSession, *, vm_id: str) -> Vm:
    """ВМ по id для s2s-каллера (connection-info, снимки) — без видимости отдела."""
    obj = await repo.get_by_id(db, vm_id)
    if obj is None:
        raise NotFoundError(error_code="VM_NOT_FOUND", message="VM not found")
    return obj


async def get_batch_status_for_service(db: AsyncSession, *, vm_ids: list[str]) -> dict[str, Vm]:
    """ВМ пачкой для batch-status обзора пула; отсутствующие id просто пропущены."""
    return await repo.get_many_by_ids(db, vm_ids)


# ── Гейт человеческих операций (`services/vm.py`) ────────────────────────────


def ensure_not_service_locked(vm: Vm, *, action: str) -> None:
    """409 `VM_RESERVED_BY_SERVICE`, пока сервис держит ВМ в `acs`/`testing`/`busy`.

    Как `testing`/`acs` у серверов: обхода для админа нет — посреди отката
    снимка или теста ВМ не трогает никто из людей. `testing_done` сюда не
    входит: его снимает человеческий release, остальное гейтит `status`.
    """
    if vm.service_busy_state not in SERVICE_LOCKED_STATES:
        return
    audit_service.emit(
        "vm.reservation_denied", target_id=vm.id, target_type="vm",
        status="denied", allowed=False,
        details={
            "blocked_action": action, "service_busy_state": vm.service_busy_state,
            "busy_service_name": vm.busy_service_name, "department_id": vm.department_id,
        },
    )
    raise ConflictError(
        error_code="VM_RESERVED_BY_SERVICE",
        message=f"VM is reserved by {vm.busy_service_name} ({vm.service_busy_state}); try again later",
        details={"service_busy_state": vm.service_busy_state, "busy_service_name": vm.busy_service_name},
    )


def clear_service_reservation(vm: Vm) -> None:
    """Снять сервисную бронь с ВМ (человеческий release `testing_done`)."""
    vm.service_busy_state = None
    vm.busy_service_name = None
    vm.busy_note = None
    vm.service_busy_since = None


# ── Для пайплайна prepare-for-test ВМ ────────────────────────────────────────


async def hold_for_prepare(db: AsyncSession, vm: Vm, *, service_name: str, busy_note: str) -> bool:
    """Занять ВМ под подготовку; True — бронь взял сам запрос (снимать на провале).

    Зеркало `prepare_for_test._hold_reservation`: своя бронь вызывающего —
    только стадия `acs` и заметка; свободная ВМ — берём сами; иначе 409.
    Коммит — на вызывающем.
    """
    if service_holds(vm, service_name):
        vm.service_busy_state = BusyState.ACS.value
        vm.busy_note = busy_note
        vm.status = VM_STATUS_RUN_TEST
        return False
    if reservation_view(vm)["busy_state"] != BusyState.FREE:
        raise ConflictError(error_code="VM_ALREADY_BUSY", message="VM is reserved by someone else")
    for key, value in _service_values(service_name, BusyState.ACS.value, busy_note).items():
        setattr(vm, key, value)
    return True


async def release_own(db: AsyncSession, vm_id: str, service_name: str) -> None:
    """Вернуть бронь, взятую самим запросом подготовки. Чужую не трогаем."""
    vm = await repo.get_by_id(db, vm_id)
    if vm is None or not service_holds(vm, service_name):
        return
    for key, value in _CLEARED.items():
        setattr(vm, key, value)
    await db.commit()
