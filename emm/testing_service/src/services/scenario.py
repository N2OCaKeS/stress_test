"""Многостендовые сценарии: CRUD, валидация, превью.

Сценарий — только описание: стенды пула и упорядоченные действия над ними.
Бронь, подготовку и исполнение делает.

* Права — матрица `test_definition` (отдельной сущности в каталоге прав
  сервиса нет): чтение — свой отдел, запись — `require_department_action`
  по отделу сценария.
* Валидация (`_validate`): стенды уникальны, существуют и принадлежат отделу
  сценария; `run_test` — стенд сценария и тест своего отдела или
  платформенный; `prepare_stand` — стенд сценария; `wait` — секунды; хотя бы
  одно действие `is_verdict` (вердикт берётся только из `run_test`).
* `stp_test_case_code` — тест-кейс СТП, который запускается сценарием;
  уникален в отделе, у такого сценария ровно одно действие `is_verdict`.
* Превью — превью запуска (`launch_preview.preview`) по каждому
  `run_test` со стендом действия: адреса других стендов в команде
  подставляет источник `stand_ref`.
"""

from __future__ import annotations

import logging

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import Action, EntityType, GlobalVariableSource, TestReadiness
from src.core.exceptions import (
    AppException,
    AuthorizationError,
    ConflictError,
    DomainValidationError,
    NotFoundError,
)
from src.dependencies.auth import Identity
from src.models import GlobalVariable, Scenario, ScenarioAction, ScenarioStand, TestDefinition, TestStand
from src.schemas.launch_preview import LaunchPreviewRequest
from src.schemas.scenario import ScenarioPreviewRequest, ScenarioWrite
from src.services import audit_service, permissions
from src.services import provisioning_profile as provisioning_svc
from src.utils.ids import scenario_action_id, scenario_id, scenario_stand_id

logger = logging.getLogger(__name__)

WAIT_MAX_SECONDS = 86400


def _invalid(error_code: str, message: str, **details) -> DomainValidationError:
    return DomainValidationError(error_code=error_code, message=message, details=details)


# ── ссылки на стенды ─────────────────────────────────────────────────────────

async def ensure_stand_not_referenced(db: AsyncSession, stand_id: str) -> None:
    """409 `TEST_STAND_IN_USE`, если на стенд ссылается переменная `stand_ref` или сценарий."""
    variables = list((await db.execute(
        select(GlobalVariable.code).where(
            GlobalVariable.source == GlobalVariableSource.STAND_REF,
            GlobalVariable.source_ref["stand_id"].astext == stand_id,
        ).order_by(GlobalVariable.code)
    )).scalars())
    scenarios = list((await db.execute(
        select(Scenario.code).join(ScenarioStand, ScenarioStand.scenario_id == Scenario.id)
        .where(ScenarioStand.stand_id == stand_id).order_by(Scenario.code)
    )).scalars())
    if variables or scenarios:
        raise ConflictError(
            error_code="TEST_STAND_IN_USE",
            message="Test stand is referenced by variables or scenarios and cannot be deleted",
            details={"stand_id": stand_id, "referenced_by_variables": variables, "referenced_by_scenarios": scenarios},
        )


# ── чтение ───────────────────────────────────────────────────────────────────

async def _get_or_404(db: AsyncSession, identity: Identity, scn_id: str) -> Scenario:
    obj = await db.get(Scenario, scn_id)
    if obj is None:
        raise NotFoundError(error_code="SCENARIO_NOT_FOUND", message="Scenario not found")
    permissions.require_own_department(identity, obj.department_id)
    return obj


async def _parts(db: AsyncSession, scn_id: str) -> tuple[list[ScenarioStand], list[ScenarioAction]]:
    stands = list((await db.execute(
        select(ScenarioStand).where(ScenarioStand.scenario_id == scn_id).order_by(ScenarioStand.position)
    )).scalars())
    actions = list((await db.execute(
        select(ScenarioAction).where(ScenarioAction.scenario_id == scn_id).order_by(ScenarioAction.position)
    )).scalars())
    return stands, actions


async def serialize(db: AsyncSession, obj: Scenario) -> dict:
    """Сценарий целиком: действия ссылаются на стенд сценария по `stand_id` пула."""
    stands, actions = await _parts(db, obj.id)
    pool = {
        s.id: s for s in (await db.execute(
            select(TestStand).where(TestStand.id.in_([row.stand_id for row in stands]))
        )).scalars()
    } if stands else {}
    test_ids = [a.test_id for a in actions if a.test_id]
    codes = dict((await db.execute(
        select(TestDefinition.id, TestDefinition.code).where(TestDefinition.id.in_(test_ids))
    )).all()) if test_ids else {}
    stand_by_row = {row.id: row.stand_id for row in stands}
    return {
        "id": obj.id, "code": obj.code, "name": obj.name, "department_id": obj.department_id,
        "readiness": obj.readiness, "stp_test_case_code": obj.stp_test_case_code, "created_by": obj.created_by,
        "created_at": obj.created_at, "updated_at": obj.updated_at,
        "stands": [
            {
                "id": row.id, "stand_id": row.stand_id, "label": row.label, "preparation": row.preparation,
                "skip_pam_fix": row.skip_pam_fix, "provisioning_profile_id": row.provisioning_profile_id, "stand_setup": row.stand_setup,
                "kernel_override": row.kernel_override, "mode_override": row.mode_override,
                "target_type": getattr(pool.get(row.stand_id), "target_type", None) or "server",
                "stand_name": pool[row.stand_id].legacy_token if row.stand_id in pool else None,
            }
            for row in stands
        ],
        "actions": [
            {
                "id": a.id, "position": a.position, "kind": a.kind,
                "stand_id": stand_by_row.get(a.scenario_stand_id), "test_id": a.test_id,
                "test_code": codes.get(a.test_id), "is_verdict": a.is_verdict, "params": a.params or {},
            }
            for a in actions
        ],
    }


async def get_scenario(db: AsyncSession, identity: Identity, scn_id: str) -> dict:
    return await serialize(db, await _get_or_404(db, identity, scn_id))


async def list_scenarios(db: AsyncSession, identity: Identity, department_id: str | None) -> list[dict]:
    scope = permissions.own_department_or_403(identity, department_id)
    stands_count = (
        select(func.count()).select_from(ScenarioStand)
        .where(ScenarioStand.scenario_id == Scenario.id).scalar_subquery()
    )
    actions_count = (
        select(func.count()).select_from(ScenarioAction)
        .where(ScenarioAction.scenario_id == Scenario.id).scalar_subquery()
    )
    rows = (await db.execute(
        select(Scenario, stands_count, actions_count)
        .where(Scenario.department_id == scope).order_by(Scenario.code)
    )).all()
    return [
        {
            "id": s.id, "code": s.code, "name": s.name, "department_id": s.department_id,
            "readiness": s.readiness, "stp_test_case_code": s.stp_test_case_code, "stands_count": sc, "actions_count": ac, "updated_at": s.updated_at,
        }
        for s, sc, ac in rows
    ]


# ── валидация ────────────────────────────────────────────────────────────────

async def _validate(db: AsyncSession, payload: ScenarioWrite, stp_test_case_code: str | None) -> None:
    department_id = payload.department_id
    seen: set[str] = set()
    for index, row in enumerate(payload.stands):
        if row.stand_id in seen:
            raise _invalid(
                "SCENARIO_STAND_DUPLICATE", f"Stand '{row.stand_id}' is listed in the scenario twice",
                stand_id=row.stand_id, index=index,
            )
        seen.add(row.stand_id)
        stand = await db.get(TestStand, row.stand_id)
        if stand is None or stand.department_id != department_id:
            raise _invalid(
                "SCENARIO_STAND_INVALID", f"Stand '{row.stand_id}' not found or belongs to another department",
                stand_id=row.stand_id, index=index,
            )
        await provisioning_svc.check_assignable(db, row.provisioning_profile_id, department_id)

    if not payload.actions:
        raise _invalid("SCENARIO_ACTIONS_EMPTY", "Scenario must have at least one action")
    for index, action in enumerate(payload.actions):
        where = {"index": index, "kind": action.kind}
        if action.kind == "wait":
            seconds = action.params.get("seconds")
            if (
                isinstance(seconds, bool) or not isinstance(seconds, int)
                or not 1 <= seconds <= WAIT_MAX_SECONDS or set(action.params) - {"seconds"}
            ):
                raise _invalid(
                    "SCENARIO_ACTION_INVALID", f"wait needs params.seconds between 1 and {WAIT_MAX_SECONDS}", **where,
                )
            if action.stand_id or action.test_id or action.is_verdict:
                raise _invalid("SCENARIO_ACTION_INVALID", "wait takes no stand, test or verdict flag", **where)
            continue
        if not action.stand_id:
            raise _invalid("SCENARIO_ACTION_INVALID", f"{action.kind} needs a scenario stand", **where)
        if action.stand_id not in seen:
            raise _invalid(
                "SCENARIO_ACTION_INVALID", f"Stand '{action.stand_id}' is not a stand of this scenario",
                stand_id=action.stand_id, **where,
            )
        if action.params:
            raise _invalid("SCENARIO_ACTION_INVALID", f"{action.kind} takes no params", **where)
        if action.kind == "prepare_stand":
            if action.test_id or action.is_verdict:
                raise _invalid("SCENARIO_ACTION_INVALID", "prepare_stand takes no test or verdict flag", **where)
            continue
        if not action.test_id:
            raise _invalid("SCENARIO_ACTION_INVALID", "run_test needs a test", **where)
        test = await db.get(TestDefinition, action.test_id)
        if test is None or test.department_id not in (None, department_id):
            raise _invalid(
                "SCENARIO_TEST_INVALID", f"Test '{action.test_id}' not found or belongs to another department",
                test_id=action.test_id, **where,
            )
    verdicts = sum(1 for a in payload.actions if a.is_verdict)
    if not verdicts:
        raise _invalid(
            "SCENARIO_VERDICT_MISSING", "At least one run_test action must be marked is_verdict",
            hint="отметьте действие, по которому считается вердикт сценария",
        )
    # Ячейка СТП одна — и вердикт, и стенд, по которому проверяется членство
    # кейса в столбце СТП, должны быть однозначны.
    if stp_test_case_code and verdicts != 1:
        raise _invalid(
            "SCENARIO_STP_VERDICT_AMBIGUOUS",
            "A scenario linked to an STP test case must have exactly one is_verdict action",
            stp_test_case_code=stp_test_case_code, verdict_actions=verdicts,
        )


# ── запись ───────────────────────────────────────────────────────────────────

async def _require(db: AsyncSession, identity: Identity, department_id: str, action: str, audit_action: str,
                   target_id: str | None = None) -> None:
    try:
        await permissions.require_department_action(db, identity, department_id, EntityType.TEST_DEFINITION, action)
    except AuthorizationError:
        audit_service.emit(
            audit_action, target_id=target_id, target_type="scenario",
            status="denied", allowed=False,
            details={"reason": "permission_denied", "department_id": department_id},
        )
        raise


async def _write_parts(db: AsyncSession, obj: Scenario, payload: ScenarioWrite) -> None:
    await db.execute(delete(ScenarioAction).where(ScenarioAction.scenario_id == obj.id))
    await db.execute(delete(ScenarioStand).where(ScenarioStand.scenario_id == obj.id))
    row_by_stand: dict[str, str] = {}
    for position, row in enumerate(payload.stands):
        row_id = scenario_stand_id()
        row_by_stand[row.stand_id] = row_id
        db.add(ScenarioStand(
            id=row_id, scenario_id=obj.id, stand_id=row.stand_id, position=position, label=row.label,
            preparation=row.preparation, skip_pam_fix=row.skip_pam_fix,
            provisioning_profile_id=row.provisioning_profile_id,
            stand_setup=row.stand_setup.model_dump(mode="json") if row.stand_setup else None,
            kernel_override=row.kernel_override,
            mode_override=str(row.mode_override) if row.mode_override else None,
        ))
    await db.flush()
    for position, action in enumerate(payload.actions):
        db.add(ScenarioAction(
            id=scenario_action_id(), scenario_id=obj.id, position=position, kind=action.kind,
            scenario_stand_id=row_by_stand.get(action.stand_id) if action.stand_id else None,
            test_id=action.test_id if action.kind == "run_test" else None,
            is_verdict=action.is_verdict, params=action.params,
        ))


async def _commit(db: AsyncSession, payload: ScenarioWrite, audit_action: str, target_id: str | None) -> None:
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        logger.warning("IntegrityError на %s: %s", audit_action, type(exc.orig).__name__)
        audit_service.emit(
            audit_action, target_id=target_id, target_type="scenario",
            status="failure", allowed=True, details={"reason": "duplicate", "code": payload.code},
        )
        raise ConflictError(
            error_code="SCENARIO_DUPLICATE", message="Scenario with this code already exists",
            details={"hint": "уникальное поле — code"},
        ) from exc


async def _validated(
    db: AsyncSession, payload: ScenarioWrite, audit_action: str, target_id: str | None,
    stp_test_case_code: str | None,
) -> None:
    taken = (await db.execute(
        select(Scenario.id).where(Scenario.code == payload.code, Scenario.id != (target_id or ""))
    )).scalar_one_or_none()
    if taken is not None:
        audit_service.emit(
            audit_action, target_id=target_id, target_type="scenario",
            status="failure", allowed=True, details={"reason": "duplicate", "code": payload.code},
        )
        raise ConflictError(
            error_code="SCENARIO_DUPLICATE", message="Scenario with this code already exists",
            details={"hint": "уникальное поле — code"},
        )
    if stp_test_case_code:
        linked = (await db.execute(
            select(Scenario.code).where(
                Scenario.department_id == payload.department_id,
                Scenario.stp_test_case_code == stp_test_case_code,
                Scenario.id != (target_id or ""),
            )
        )).scalar_one_or_none()
        if linked is not None:
            audit_service.emit(
                audit_action, target_id=target_id, target_type="scenario",
                status="failure", allowed=True,
                details={"reason": "stp_test_case_taken", "code": payload.code, "linked_scenario": linked},
            )
            raise ConflictError(
                error_code="SCENARIO_STP_CASE_TAKEN",
                message="Another scenario of this department already launches this STP test case",
                details={"stp_test_case_code": stp_test_case_code, "scenario_code": linked},
            )
    try:
        await _validate(db, payload, stp_test_case_code)
    except DomainValidationError as exc:
        audit_service.emit(
            audit_action, target_id=target_id, target_type="scenario",
            status="failure", allowed=True, details={"reason": exc.error_code, "code": payload.code},
        )
        raise


def _audit_details(payload: ScenarioWrite) -> dict:
    return {
        "code": payload.code, "department_id": payload.department_id,
        "stp_test_case_code": payload.stp_test_case_code,
        "stand_ids": [row.stand_id for row in payload.stands],
        "actions": [a.kind for a in payload.actions],
    }


async def create_scenario(db: AsyncSession, identity: Identity, payload: ScenarioWrite) -> dict:
    await _require(db, identity, payload.department_id, Action.CREATE, "scenario.create")
    await _validated(db, payload, "scenario.create", None, payload.stp_test_case_code)
    obj = Scenario(
        id=scenario_id(), code=payload.code, name=payload.name, department_id=payload.department_id,
        readiness=str(payload.readiness), stp_test_case_code=payload.stp_test_case_code,
        created_by=identity.user_id,
    )
    db.add(obj)
    await db.flush()
    await _write_parts(db, obj, payload)
    await _commit(db, payload, "scenario.create", None)
    await db.refresh(obj)
    audit_service.emit(
        "scenario.create", target_id=obj.id, target_type="scenario",
        status="success", allowed=True, details=_audit_details(payload),
    )
    return await serialize(db, obj)


async def update_scenario(db: AsyncSession, identity: Identity, scn_id: str, payload: ScenarioWrite) -> dict:
    obj = await _get_or_404(db, identity, scn_id)
    await _require(db, identity, obj.department_id, Action.UPDATE, "scenario.update", scn_id)
    if payload.department_id != obj.department_id:
        # Перенос в другой отдел — это право на запись в оба отдела; проще
        # запретить: сценарий привязан к стендам своего отдела.
        raise _invalid(
            "SCENARIO_DEPARTMENT_IMMUTABLE", "Scenario cannot be moved to another department",
            department_id=obj.department_id,
        )
    # Редактор, не знающий о поле, не должен молча снимать связь со СТП.
    stp_test_case_code = (
        payload.stp_test_case_code if "stp_test_case_code" in payload.model_fields_set else obj.stp_test_case_code
    )
    await _validated(db, payload, "scenario.update", scn_id, stp_test_case_code)
    obj.code, obj.name, obj.readiness = payload.code, payload.name, str(payload.readiness)
    obj.stp_test_case_code = stp_test_case_code
    await _write_parts(db, obj, payload)
    await _commit(db, payload, "scenario.update", scn_id)
    await db.refresh(obj)
    audit_service.emit(
        "scenario.update", target_id=obj.id, target_type="scenario",
        status="success", allowed=True, details=_audit_details(payload),
    )
    return await serialize(db, obj)


async def delete_scenario(db: AsyncSession, identity: Identity, scn_id: str) -> None:
    obj = await _get_or_404(db, identity, scn_id)
    await _require(db, identity, obj.department_id, Action.DELETE, "scenario.delete", scn_id)
    code = obj.code
    await db.delete(obj)
    await db.commit()
    audit_service.emit(
        "scenario.delete", target_id=scn_id, target_type="scenario",
        status="success", allowed=True, details={"code": code},
    )


# ── связь со СТП ─────────────────────────────────────────────────────────────

async def ready_for_stp_codes(
    db: AsyncSession, department_id: str, codes: list[str],
) -> dict[str, tuple[Scenario, str | None]]:
    """`stp_test_case_code → (сценарий, test_id действия-вердикта)` для `ready`-сценариев отдела."""
    if not codes:
        return {}
    scenarios = list((await db.execute(
        select(Scenario).where(
            Scenario.department_id == department_id,
            Scenario.readiness == TestReadiness.READY,
            Scenario.stp_test_case_code.in_(codes),
        )
    )).scalars())
    if not scenarios:
        return {}
    verdict_tests: dict[str, str | None] = {}
    for action in (await db.execute(
        select(ScenarioAction).where(
            ScenarioAction.scenario_id.in_([s.id for s in scenarios]), ScenarioAction.is_verdict.is_(True),
        ).order_by(ScenarioAction.position)
    )).scalars():
        verdict_tests.setdefault(action.scenario_id, action.test_id)
    return {s.stp_test_case_code: (s, verdict_tests.get(s.id)) for s in scenarios}


# ── превью ───────────────────────────────────────────────────────────────────

def _error(stage: str, exc: AppException) -> dict:
    return {
        "stage": stage, "error_code": exc.error_code, "message": exc.message,
        "details": dict(exc.details or {}),
    }


async def _prepare_preview(db: AsyncSession, row: ScenarioStand, stand: TestStand, launch_context: dict,
                           debug: bool) -> tuple[dict, list[dict]]:
    """Что сделает подготовка стенда: preparation, ядро, режим, шаг настройки с маской."""
    from src.services.launch_preview import _mask
    from src.services.variable_resolver import ResolveContext

    info: dict = {
        "preparation": row.preparation, "skip_pam_fix": row.skip_pam_fix,
        "kernel": launch_context["KERNEL"], "mode": launch_context["MODE"],
        "provisioning_profile_id": row.provisioning_profile_id, "stand_setup": None,
    }
    errors: list[dict] = []
    if not provisioning_svc.stand_setup_is_empty(row.stand_setup):
        ctx = ResolveContext(
            db=db, department_id=stand.department_id, test=None, stand=stand,
            launch_context=launch_context, debug=debug,
        )
        try:
            setup = await provisioning_svc.resolve_stand_setup(ctx, row.stand_setup)
        except AppException as exc:
            errors.append(_error("stand_setup", exc))
        else:
            secrets = sorted({v.value for v in ctx.resolved_values().values() if v.sensitive and v.value},
                             key=len, reverse=True)
            setup["script"] = _mask(setup["script"], secrets)
            info["stand_setup"] = setup
    return info, errors


async def preview_scenario(db: AsyncSession, identity: Identity, scn_id: str, payload: ScenarioPreviewRequest) -> dict:
    """Превью каждого действия: `run_test` — превью запуска со стендом действия.

    Ядро и режим стенда — `kernel_override`/`mode_override` стенда сценария,
    иначе ядро запроса и режим теста. Ошибка одного действия не обрывает
    превью остальных.
    """
    from src.services import launch_preview  # поздний импорт: launch_preview тянет queue

    obj = await _get_or_404(db, identity, scn_id)
    stands, actions = await _parts(db, obj.id)
    rows = {row.id: row for row in stands}
    result = []
    for action in actions:
        row = rows.get(action.scenario_stand_id) if action.scenario_stand_id else None
        item: dict = {
            "position": action.position, "kind": action.kind, "stand_id": row.stand_id if row else None,
            "test_id": action.test_id, "is_verdict": action.is_verdict, "params": action.params or {},
            "errors": [],
        }
        if action.kind == "run_test" and row is not None and action.test_id:
            request = LaunchPreviewRequest(
                stand_id=row.stand_id, os_version_id=payload.os_version_id,
                kernel=row.kernel_override or payload.kernel, mode=row.mode_override, debug=payload.debug,
            )
            try:
                item["launch"] = await launch_preview.preview(db, identity, action.test_id, request)
            except AppException as exc:
                item["errors"].append(_error("launch", exc))
        elif action.kind == "prepare_stand" and row is not None:
            stand = await db.get(TestStand, row.stand_id)
            if stand is None:
                item["errors"].append(_error("stand", NotFoundError(
                    error_code="TEST_STAND_NOT_FOUND", message="Test stand not found",
                )))
            else:
                launch_context = {
                    "RC": payload.os_version_id, "KERNEL": row.kernel_override or payload.kernel,
                    "MODE": row.mode_override or "",
                }
                item["stand"], item["errors"] = await _prepare_preview(db, row, stand, launch_context, payload.debug)
        result.append(item)
    audit_service.emit(
        "scenario.preview", target_id=obj.id, target_type="scenario",
        status="success", allowed=True,
        details={"os_version_id": payload.os_version_id, "kernel": payload.kernel, "actions": len(result)},
    )
    return {"scenario_id": obj.id, "actions": result}

