"""Запуск многостендового сценария: бронь, подготовка, действия.

Одностендовая очередь (`services/queue.py`) устроена вокруг «один item —
один стенд». Сценарию нужно несколько стендов сразу, поэтому он живёт рядом
и касается очереди в трёх местах:

* `holds_stand` — пока запуск сценария держит стенд, `queue.enqueue` ставит
  одиночные тесты на него в очередь (`queued`), не запуская цикл: они идут
  после сценария;
* `on_stand_advance` — `queue._advance_stand_queue` отдаёт стенд сюда, если
  его держит сценарий: item действия завершился (успех, провал, вердикт,
  skip), и сценарий решает, что дальше, вместо «следующий item или
  release»;
* `handle_prepare_completed` — callback prepare-for-test по стенду сценария
  (у него нет своего queue_item).

Жизненный цикл запуска:

1. **Бронь «всё или ничего»** (`_try_acquire`). Стенды сценария должны быть
   без активной очереди и не держаться другим сценарием. Стенды помечаются
   `acquiring` (с этого момента одиночные постановки на них ждут), затем
   `acquire-for-service` по порядку `stand_id` — у двух сценариев с общими
   стендами порядок захвата один, взаимной блокировки нет. Не взялся хоть
   один — уже взятые брони отпускаются (`release-for-service`), запуск
   `waiting_for_stands` с перечнем занятых; повтор — фоновый тик (`tick`).
2. **Подготовка** — параллельно: `full` — prepare-for-test с ядром/режимом
   стенда сценария, шагом настройки и профилем подготовки; `revert_only` —
   prepare-for-test с `preparation=revert_only`: откат снимка ВМ / ACS
   restore, учётка и ядро, без смены режима и без шага настройки (легаси
   так готовил клиент FreeIPA — `modes = False`); `none` — стенд готов сразу.
   PAM-правку снимает отдельный флаг стенда `skip_pam_fix` (легаси
   `modes = False` снимал и её), от `preparation` он не зависит.
   Провал любого — провал сценария, брони отпускаются.
3. **Действия** строго по `position`. `run_test` — `queue_item` сразу в
   `ready` (стенд уже подготовлен) с `scenario_run_id`; воркер берёт его
   обычным claim. `prepare_stand` — повторная подготовка стенда. `wait` —
   пауза, её снимает тик. Следующее действие — после терминального исхода
   предыдущего. Провал действия без `is_verdict` — провал сценария; итог
   действий с `is_verdict` — вердикт сценария.
4. **Конец** (`_finalize`): стенды перестают считаться занятыми сценарием и
   уходят обычным `_advance_stand_queue` — следующий одиночный item
   (бронь переходит к нему, не отпускаясь) или `testing_done`. Запуск из
   кампании пересчитывает её статус (`test_run_status.recompute`).

Запуск по СТП (кампания или ячейка СТП) несёт `stp_test_run_id`; item
действия-вердикта получает его же, и `stp_status` пишет исход в ячейку кейса
`scenarios.stp_test_case_code`.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import (
    ACTIVE_QUEUE_STATES,
    ACTIVE_SCENARIO_RUN_STATES,
    HOLDING_SCENARIO_STAND_STATES,
    QueueItemState,
    QueueVerdict,
    ScenarioRunStandState,
    ScenarioRunState,
    TestReadiness,
)
from src.core.exceptions import AppException, ConflictError, DomainValidationError, NotFoundError
from src.dependencies.auth import Identity
from src.models import QueueItem, Scenario, ScenarioAction, ScenarioRun, ScenarioRunStand, ScenarioStand, TestStand
from src.repositories import queue_item as queue_repo
from src.repositories import test_definition as test_definition_repo
from src.services import (
    audit_service,
    creds_stash,
    department_test_settings as dts_svc,
    permissions,
    provisioning_profile as provisioning_svc,
    server_client,
    stand_target,
    test_account as test_account_svc,
)
from src.services.variable_resolver import ResolveContext
from src.utils.ids import queue_item_id as new_queue_item_id
from src.utils.ids import scenario_run_id, scenario_run_stand_id

logger = logging.getLogger(__name__)

_TERMINAL_ITEM_STATES = frozenset({
    QueueItemState.SUCCEEDED, QueueItemState.FAILED, QueueItemState.TIMED_OUT,
    QueueItemState.SKIPPED, QueueItemState.PREPARED,
})
_TERMINAL_RUN_STATES = frozenset({ScenarioRunState.SUCCEEDED, ScenarioRunState.FAILED, ScenarioRunState.STOPPED})


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── бронь стенда сценарием ───────────────────────────────────────────────────

async def _holding_row(db: AsyncSession, stand_id: str, *, exclude_run_id: str | None = None):
    stmt = (
        select(ScenarioRunStand, ScenarioRun)
        .join(ScenarioRun, ScenarioRun.id == ScenarioRunStand.scenario_run_id)
        .where(
            ScenarioRunStand.stand_id == stand_id,
            ScenarioRunStand.state.in_(HOLDING_SCENARIO_STAND_STATES),
            ScenarioRun.state.in_(ACTIVE_SCENARIO_RUN_STATES),
        )
    )
    if exclude_run_id:
        stmt = stmt.where(ScenarioRun.id != exclude_run_id)
    return (await db.execute(stmt.limit(1))).first()


async def holds_stand(db: AsyncSession, stand_id: str) -> bool:
    """Стенд сейчас за запуском сценария — одиночные постановки ждут в очереди."""
    return await _holding_row(db, stand_id) is not None


async def _kick_single_queue(db: AsyncSession, stand: TestStand) -> None:
    """Одиночные item'ы, вставшие в очередь, пока стенд помечался за сценарием,
    запускаются, когда сценарий стенд так и не взял (или отдал до подготовки)."""
    from src.services import queue as queue_svc  # поздний импорт: queue импортирует этот модуль

    if await queue_repo.has_in_flight_for_stand(db, stand.id):
        return
    next_item = await queue_repo.get_next_queued_for_stand(db, stand.id)
    if next_item is not None:
        await queue_svc.start_stand_cycle(db, stand, next_item, is_first_ever=True)


# ── запуск ───────────────────────────────────────────────────────────────────

async def start_run(
    db: AsyncSession, identity: Identity, scenario_id: str, *,
    os_version_id: str, kernel: str, mode: str, debug: bool = False,
    stp_test_run_id: str | None = None, test_run_id: str | None = None, test_run_entry_id: str | None = None,
) -> ScenarioRun:
    """Запуск сценария.

    Запуск по СТП (`stp_test_run_id` — клик по ячейке, `test_run_id` —
    кампания) допускается так же, как обычный запуск теста по СТП: права на
    стенды (`public_queue.authorize`) и членство кейса сценария в столбце СТП
    по стенду действия-вердикта (`launch_stp.require_membership`). Вердикт
    потом пишется в эту ячейку. Ручной запуск без СТП — право на запись
    сценариев отдела.
    """
    scenario = await db.get(Scenario, scenario_id)
    if scenario is None:
        raise NotFoundError(error_code="SCENARIO_NOT_FOUND", message="Scenario not found")
    permissions.require_own_department(identity, scenario.department_id)
    from src.core.constants import Action, EntityType

    via_stp = stp_test_run_id is not None or test_run_id is not None
    if via_stp and debug:
        raise DomainValidationError(
            error_code="SCENARIO_STP_DEBUG_CONFLICT",
            message="A debug run is not an STP run; launch without stp_test_run_id",
        )
    if not via_stp:
        await permissions.require_department_action(
            db, identity, scenario.department_id, EntityType.TEST_DEFINITION, Action.UPDATE,
        )
    if not debug and scenario.readiness != TestReadiness.READY:
        raise DomainValidationError(
            error_code="SCENARIO_REQUIRES_DEBUG",
            message="Scenario is not ready; run it in debug mode",
            details={"readiness": scenario.readiness},
        )
    stands = list((await db.execute(
        select(ScenarioStand).where(ScenarioStand.scenario_id == scenario.id).order_by(ScenarioStand.position)
    )).scalars())
    actions = list((await db.execute(
        select(ScenarioAction).where(ScenarioAction.scenario_id == scenario.id)
    )).scalars())
    if not stands or not actions:
        raise DomainValidationError(
            error_code="SCENARIO_NOT_RUNNABLE", message="Scenario has no stands or no actions",
        )
    for row in stands:
        stand = await db.get(TestStand, row.stand_id)
        if stand is None or not stand.is_active:
            raise DomainValidationError(
                error_code="SCENARIO_STAND_INVALID", message=f"Stand '{row.stand_id}' is missing or inactive",
                details={"stand_id": row.stand_id},
            )
    if not debug:
        for action in actions:
            test = await test_definition_repo.get_by_id(db, action.test_id) if action.test_id else None
            if action.kind == "run_test" and (test is None or test.readiness != TestReadiness.READY):
                raise DomainValidationError(
                    error_code="SCENARIO_REQUIRES_DEBUG",
                    message="A test of the scenario is not ready; run it in debug mode",
                    details={"test_id": action.test_id},
                )
    if via_stp:
        stp_test_run_id = await _require_stp_membership(
            db, identity, scenario, stands, actions,
            context={"RC": os_version_id, "KERNEL": kernel, "MODE": mode}, stp_test_run_id=stp_test_run_id,
        )

    run = ScenarioRun(
        id=scenario_run_id(), scenario_id=scenario.id, department_id=scenario.department_id,
        state=ScenarioRunState.WAITING_FOR_STANDS,
        launch_context={"RC": os_version_id, "KERNEL": kernel, "MODE": mode},
        debug_mode=debug, created_by=identity.user_id, blocked_by=[],
        stp_test_run_id=stp_test_run_id, test_run_id=test_run_id, test_run_entry_id=test_run_entry_id,
    )
    db.add(run)
    await db.flush()
    for row in stands:
        db.add(ScenarioRunStand(
            id=scenario_run_stand_id(), scenario_run_id=run.id, scenario_stand_id=row.id,
            stand_id=row.stand_id, label=row.label, preparation=row.preparation,
            skip_pam_fix=row.skip_pam_fix, kernel=row.kernel_override or kernel, mode=row.mode_override or mode,
            provisioning_profile_id=row.provisioning_profile_id,
            stand_setup=row.stand_setup if row.preparation == "full" else None,
            state=ScenarioRunStandState.PENDING,
        ))
    await db.commit()
    audit_service.emit(
        "scenario_run.started", target_id=run.id, target_type="scenario_run",
        status="success", allowed=True,
        details={"scenario_id": scenario.id, "stand_ids": [r.stand_id for r in stands], "debug": debug,
                 "stp_test_run_id": stp_test_run_id, "test_run_id": test_run_id},
    )
    await _try_acquire(db, run)
    await db.refresh(run)
    return run


async def _require_stp_membership(
    db: AsyncSession, identity: Identity, scenario: Scenario, stands: list[ScenarioStand],
    actions: list[ScenarioAction], *, context: dict, stp_test_run_id: str | None,
) -> str:
    """Допуск запуска по СТП; возвращает столбец СТП, куда пишется вердикт."""
    from src.services import launch_stp, public_queue  # поздний импорт: public_queue тянет queue

    if not scenario.stp_test_case_code:
        raise DomainValidationError(
            error_code="SCENARIO_NOT_LINKED_TO_STP",
            message="Scenario is not linked to an STP test case (stp_test_case_code)",
        )
    verdicts = [a for a in actions if a.is_verdict]
    if len(verdicts) != 1:
        raise DomainValidationError(
            error_code="SCENARIO_STP_VERDICT_AMBIGUOUS",
            message="A scenario launched from STP must have exactly one is_verdict action",
            details={"verdict_actions": len(verdicts)},
        )
    for row in stands:
        await public_queue.authorize(db, identity, row.stand_id)
    verdict_row = next(row for row in stands if row.id == verdicts[0].scenario_stand_id)
    context = {
        **context,
        "KERNEL": verdict_row.kernel_override or context["KERNEL"],
        "MODE": verdict_row.mode_override or context["MODE"],
    }
    stp = await launch_stp.require_membership(
        db, scenario.stp_test_case_code, verdict_row.stand_id, context, run_id=stp_test_run_id,
    )
    return stp.id


async def _run_stands(db: AsyncSession, run: ScenarioRun) -> list[ScenarioRunStand]:
    return list((await db.execute(
        select(ScenarioRunStand).where(ScenarioRunStand.scenario_run_id == run.id)
        .order_by(ScenarioRunStand.stand_id)
    )).scalars())


async def _try_acquire(db: AsyncSession, run: ScenarioRun) -> bool:
    """Бронь всех стендов «всё или ничего». `True` — взяты все, подготовка начата."""
    rows = await _run_stands(db, run)
    missing = [row.stand_id for row in rows if await db.get(TestStand, row.stand_id) is None]
    if missing:
        await _finalize(db, run, ScenarioRunState.FAILED, error=f"stands no longer exist: {', '.join(missing)}")
        return False
    blocked = []
    for row in rows:
        if await queue_repo.count_active_for_stand(db, row.stand_id) > 0:
            blocked.append({"stand_id": row.stand_id, "reason": "stand_queue_active"})
        elif await _holding_row(db, row.stand_id, exclude_run_id=run.id) is not None:
            blocked.append({"stand_id": row.stand_id, "reason": "held_by_scenario"})
    if blocked:
        await _wait(db, run, blocked)
        return False

    # С этого момента одиночные постановки на эти стенды встают в очередь.
    for row in rows:
        row.state = ScenarioRunStandState.ACQUIRING
    await db.commit()

    acquired: list[tuple[ScenarioRunStand, TestStand]] = []
    failure: dict | None = None
    for row in rows:  # детерминированный порядок — по stand_id
        stand = await db.get(TestStand, row.stand_id)
        try:
            await server_client.acquire_stand(
                stand_target.target_of(stand), busy_state="acs",
                busy_note=f"SCENARIO|{run.id}|{row.label or ''}",
                requested_by_department_id=stand.department_id,
            )
        except AppException as exc:
            failure = {"stand_id": row.stand_id, "reason": exc.error_code, "message": exc.message}
            break
        acquired.append((row, stand))

    if failure is not None:
        for row, stand in acquired:
            try:
                await server_client.release_stand(stand_target.target_of(stand))
            except AppException as exc:
                logger.warning("scenario %s: release of %s failed: %s", run.id, row.stand_id, exc)
        for row in rows:
            row.state = ScenarioRunStandState.PENDING
        await _wait(db, run, [failure])
        for row in rows:
            await _kick_single_queue(db, await db.get(TestStand, row.stand_id))
        return False

    run.state = ScenarioRunState.PREPARING
    run.blocked_by = []
    run.started_at = run.started_at or _now()
    await db.commit()
    audit_service.emit(
        "scenario_run.stands_acquired", target_id=run.id, target_type="scenario_run",
        status="success", allowed=True, details={"stand_ids": [r.stand_id for r in rows]},
    )
    for row, stand in acquired:
        if row.preparation == "none":
            row.state = ScenarioRunStandState.READY
            await db.commit()
            continue
        if not await _start_prepare(db, run, row, stand):
            return False
    await _maybe_start_actions(db, run)
    return True


async def _wait(db: AsyncSession, run: ScenarioRun, blocked: list[dict]) -> None:
    changed = run.blocked_by != blocked or run.state != ScenarioRunState.WAITING_FOR_STANDS
    run.state = ScenarioRunState.WAITING_FOR_STANDS
    run.blocked_by = blocked
    await db.commit()
    if changed:
        audit_service.emit(
            "scenario_run.waiting_for_stands", target_id=run.id, target_type="scenario_run",
            status="success", allowed=True, details={"blocked_by": blocked},
        )


async def _start_prepare(db: AsyncSession, run: ScenarioRun, row: ScenarioRunStand, stand: TestStand) -> bool:
    """prepare-for-test стенда сценария; провал — провал сценария (`False`)."""
    try:
        credential_id = await test_account_svc.require_credential_id(db, stand.department_id)
        settings = await dts_svc.get_effective(db, stand.department_id)
        provisioning = await provisioning_svc.effective_values_for(
            db, row.provisioning_profile_id, stand.department_id,
        )
        stand_setup = None
        if not provisioning_svc.stand_setup_is_empty(row.stand_setup):
            ctx = ResolveContext(
                db=db, department_id=stand.department_id, test=None, stand=stand,
                launch_context={**run.launch_context, "KERNEL": row.kernel, "MODE": row.mode,
                                "SCENARIO_RUN_ID": run.id},
                debug=run.debug_mode,
            )
            stand_setup = await provisioning_svc.resolve_stand_setup(ctx, row.stand_setup)
        resp = await server_client.start_stand_prepare_for_test(
            stand_target.target_of(stand),
            os_version_id=run.launch_context.get("RC"), kernel=row.kernel, mode=row.mode,
            test_username=settings["test_username"], requested_by_department_id=stand.department_id,
            correlation_id=f"{run.id}:{row.id}:{int(_now().timestamp())}",
            test_account_credential_id=credential_id, stand_setup=stand_setup, provisioning=provisioning,
            # `none` сюда доходит только действием `prepare_stand` — это явная полная подготовка.
            preparation="revert_only" if row.preparation == "revert_only" else "full",
            skip_pam_fix=row.skip_pam_fix,
        )
    except AppException as exc:
        row.state = ScenarioRunStandState.FAILED
        row.error = f"{exc.error_code}: {exc.message}"[:2048]
        await db.commit()
        await _finalize(db, run, ScenarioRunState.FAILED, error=f"{row.stand_id}: {row.error}")
        return False
    row.state = ScenarioRunStandState.PREPARING
    row.prepare_request_id = resp.get("prepare_request_id")
    row.error = None
    await db.commit()
    return True


async def handle_prepare_completed(db: AsyncSession, prepare_request_id: str, body) -> bool:
    """Callback prepare-for-test по стенду сценария. `False` — это не стенд сценария."""
    row = (await db.execute(
        select(ScenarioRunStand).where(ScenarioRunStand.prepare_request_id == prepare_request_id)
    )).scalar_one_or_none()
    if row is None:
        return False
    if row.state != ScenarioRunStandState.PREPARING:
        return True  # повторная доставка
    run = await db.get(ScenarioRun, row.scenario_run_id)
    stand = await db.get(TestStand, row.stand_id)
    if run.state in _TERMINAL_RUN_STATES:
        # Сценарий закончился, пока стенд готовился: отдать стенд очереди.
        row.state = ScenarioRunStandState.RELEASED
        await db.commit()
        await _release_to_queue(db, stand)
        return True
    if not body.succeeded:
        row.state = ScenarioRunStandState.FAILED
        row.error = f"{body.failed_step or ''}: {body.error or 'prepare-for-test failed'}"[:2048]
        await db.commit()
        await _finalize(db, run, ScenarioRunState.FAILED, error=f"{row.stand_id}: {row.error}")
        return True
    row.state = ScenarioRunStandState.READY
    row.error = None
    await db.commit()
    if run.state == ScenarioRunState.STOPPING:
        await _finalize(db, run, ScenarioRunState.STOPPED)
    elif run.state == ScenarioRunState.PREPARING:
        await _maybe_start_actions(db, run)
    else:
        # Действие `prepare_stand` посреди сценария.
        await _run_next_action(db, run)
    return True


async def _maybe_start_actions(db: AsyncSession, run: ScenarioRun) -> None:
    rows = await _run_stands(db, run)
    if run.state != ScenarioRunState.PREPARING or any(r.state != ScenarioRunStandState.READY for r in rows):
        return
    run.state = ScenarioRunState.RUNNING
    await db.commit()
    await _run_next_action(db, run)


# ── действия ─────────────────────────────────────────────────────────────────

async def _actions(db: AsyncSession, run: ScenarioRun) -> list[ScenarioAction]:
    return list((await db.execute(
        select(ScenarioAction).where(ScenarioAction.scenario_id == run.scenario_id)
        .order_by(ScenarioAction.position)
    )).scalars())


async def _run_next_action(db: AsyncSession, run: ScenarioRun) -> None:
    actions = await _actions(db, run)
    position = -1 if run.current_position is None else run.current_position
    upcoming = [a for a in actions if a.position > position]
    if not upcoming:
        await _finalize(db, run, None)
        return
    action = upcoming[0]
    run.current_position = action.position
    run.current_queue_item_id = None
    run.wait_until = None
    if action.kind == "wait":
        run.wait_until = _now() + timedelta(seconds=int((action.params or {}).get("seconds") or 0))
        await db.commit()
        return
    row = (await db.execute(
        select(ScenarioRunStand).where(
            ScenarioRunStand.scenario_run_id == run.id,
            ScenarioRunStand.scenario_stand_id == action.scenario_stand_id,
        )
    )).scalar_one()
    stand = await db.get(TestStand, row.stand_id)
    if action.kind == "prepare_stand":
        await db.commit()
        await _start_prepare(db, run, row, stand)
        return
    await _start_test_item(db, run, action, row, stand)


async def _start_test_item(
    db: AsyncSession, run: ScenarioRun, action: ScenarioAction, row: ScenarioRunStand, stand: TestStand,
) -> None:
    """`run_test`: item сразу `ready` — стенд уже подготовлен сценарием."""
    item_id = new_queue_item_id()
    stash_key = creds_stash.new_stash_key(item_id)
    await creds_stash.store_creds(stash_key, {
        "test_account_credential_id": await test_account_svc.get_credential_id(db, stand.department_id),
    })
    item = QueueItem(
        id=item_id, stand_id=stand.id, test_id=action.test_id,
        launch_context={**run.launch_context, "KERNEL": row.kernel, "MODE": row.mode, "SCENARIO_RUN_ID": run.id},
        state=QueueItemState.READY, position=await queue_repo.next_position_for_stand(db, stand.id),
        is_retry=False, debug_mode=run.debug_mode, prepare_only=False,
        creds_stash_key=stash_key, created_by=run.created_by,
        scenario_run_id=run.id, scenario_action_id=action.id,
        stp_test_run_id=run.stp_test_run_id if action.is_verdict else None,
    )
    db.add(item)
    run.current_queue_item_id = item.id
    await db.commit()
    audit_service.emit(
        "queue_item.enqueued", target_id=item.id, target_type="queue_item",
        status="success", allowed=True,
        details={"test_id": action.test_id, "stand_id": stand.id, "scenario_run_id": run.id,
                 "debug_mode": run.debug_mode, "prepare_only": False},
    )


async def on_stand_advance(db: AsyncSession, stand: TestStand) -> bool:
    """Перехват `queue._advance_stand_queue`: стенд держит сценарий — решает он.

    `True` — стенд за сценарием, очередь стенда не трогается (ни следующего
    одиночного item'а, ни освобождения).
    """
    found = await _holding_row(db, stand.id)
    if found is None:
        return False
    _row, run = found
    if run.current_queue_item_id is None:
        return True
    item = await db.get(QueueItem, run.current_queue_item_id)
    if item is None or item.stand_id != stand.id or item.state not in _TERMINAL_ITEM_STATES:
        return True
    await _after_item(db, run, item)
    return True


async def _after_item(db: AsyncSession, run: ScenarioRun, item: QueueItem) -> None:
    action = await db.get(ScenarioAction, item.scenario_action_id) if item.scenario_action_id else None
    run.current_queue_item_id = None
    await db.commit()
    if run.state == ScenarioRunState.STOPPING:
        await _finalize(db, run, ScenarioRunState.STOPPED)
        return
    if item.state != QueueItemState.SUCCEEDED and not (action is not None and action.is_verdict):
        await _finalize(
            db, run, ScenarioRunState.FAILED,
            error=f"action {run.current_position + 1 if run.current_position is not None else '?'}: "
                  f"{item.state}{f' — {item.error}' if item.error else ''}",
        )
        return
    await _run_next_action(db, run)


async def tick(db: AsyncSession) -> int:
    """Фоновый тик: ждущие стенды сценарии и истёкшие `wait`. Возвращает число продвинутых."""
    moved = 0
    waiting = list((await db.execute(
        select(ScenarioRun).where(ScenarioRun.state == ScenarioRunState.WAITING_FOR_STANDS)
        .order_by(ScenarioRun.created_at)
    )).scalars())
    for run in waiting:
        run_id = run.id
        try:
            if await _try_acquire(db, run):
                moved += 1
        except Exception as exc:  # noqa: BLE001 — один запуск не должен ронять тик
            await db.rollback()
            logger.warning("scenario tick (acquire) failed for %s: %s", run_id, exc)
    due = list((await db.execute(
        select(ScenarioRun).where(
            ScenarioRun.state.in_([ScenarioRunState.RUNNING, ScenarioRunState.STOPPING]),
            and_(ScenarioRun.wait_until.is_not(None), ScenarioRun.wait_until <= _now()),
        )
    )).scalars())
    for run in due:
        run_id = run.id
        try:
            run.wait_until = None
            await db.commit()
            if run.state == ScenarioRunState.STOPPING:
                await _finalize(db, run, ScenarioRunState.STOPPED)
            else:
                await _run_next_action(db, run)
            moved += 1
        except Exception as exc:  # noqa: BLE001
            await db.rollback()
            logger.warning("scenario tick (wait) failed for %s: %s", run_id, exc)
    return moved


# ── остановка и конец ────────────────────────────────────────────────────────

async def stop_run(db: AsyncSession, identity: Identity, run_id: str) -> ScenarioRun:
    """skip текущего действия, отмена оставшихся, освобождение всех стендов."""
    from src.core.constants import Action, EntityType, QueueInterruptAction
    from src.services import queue as queue_svc

    run = await get_run_or_404(db, identity, run_id)
    await permissions.require_department_action(
        db, identity, run.department_id, EntityType.TEST_DEFINITION, Action.UPDATE,
    )
    if run.state in _TERMINAL_RUN_STATES:
        raise ConflictError(
            error_code="SCENARIO_RUN_FINISHED", message="Scenario run is already finished",
            details={"state": run.state},
        )
    audit_service.emit(
        "scenario_run.stop_requested", target_id=run.id, target_type="scenario_run",
        status="success", allowed=True, details={"state": run.state},
    )
    if run.state == ScenarioRunState.WAITING_FOR_STANDS:
        await _finalize(db, run, ScenarioRunState.STOPPED)
        return run
    item = await db.get(QueueItem, run.current_queue_item_id) if run.current_queue_item_id else None
    run.state = ScenarioRunState.STOPPING
    await db.commit()
    if item is not None and item.state in ACTIVE_QUEUE_STATES:
        stand = await db.get(TestStand, item.stand_id)
        # `running` — заявка воркеру, исход придёт через complete_item и
        # `on_stand_advance`; иначе skip мгновенный и сам зовёт продвижение.
        await queue_svc.request_interrupt(db, item, stand, QueueInterruptAction.SKIP)
        await db.refresh(run)
        return run
    rows = await _run_stands(db, run)
    if not any(r.state == ScenarioRunStandState.PREPARING for r in rows):
        await _finalize(db, run, ScenarioRunState.STOPPED)
    # Иначе конец — на callback подготовки (`handle_prepare_completed`).
    await db.refresh(run)
    return run


async def _verdict(db: AsyncSession, run: ScenarioRun) -> tuple[str | None, bool]:
    """Вердикт сценария по действиям `is_verdict` и признак «все такие действия прошли»."""
    verdict_actions = {a.id for a in await _actions(db, run) if a.is_verdict}
    items = list((await db.execute(
        select(QueueItem).where(QueueItem.scenario_run_id == run.id)
    )).scalars())
    verdicts = [i for i in items if i.scenario_action_id in verdict_actions]
    if not verdicts:
        return None, False
    if any(i.verdict == QueueVerdict.FAILED or i.state in (QueueItemState.FAILED, QueueItemState.TIMED_OUT)
           for i in verdicts):
        return QueueVerdict.FAILED, False
    if len(verdicts) < len(verdict_actions) or any(i.state != QueueItemState.SUCCEEDED for i in verdicts):
        return QueueVerdict.UNKNOWN, False
    if any(i.verdict == QueueVerdict.UNKNOWN for i in verdicts):
        return QueueVerdict.UNKNOWN, True
    return QueueVerdict.PASSED, True


async def _release_to_queue(db: AsyncSession, stand: TestStand) -> None:
    """Стенд больше не за сценарием: следующий одиночный item или `testing_done`."""
    from src.services import queue as queue_svc

    await queue_svc.advance_stand_queue(db, stand)


async def _finalize(db: AsyncSession, run: ScenarioRun, state: str | None, *, error: str | None = None) -> None:
    """Терминальный переход; `state=None` — действия кончились, итог по вердикту."""
    verdict, all_passed = await _verdict(db, run)
    if state is None:
        state = ScenarioRunState.SUCCEEDED if all_passed else ScenarioRunState.FAILED
        if not all_passed and error is None:
            error = "verdict action did not pass"
    run.state = state
    run.verdict = verdict
    run.error = error[:2048] if error else None
    run.finished_at = _now()
    run.wait_until = None
    rows = await _run_stands(db, run)
    to_release = []
    for row in rows:
        if row.state in (ScenarioRunStandState.READY, ScenarioRunStandState.FAILED, ScenarioRunStandState.ACQUIRING):
            row.state = ScenarioRunStandState.RELEASED
            to_release.append(row.stand_id)
        elif row.state == ScenarioRunStandState.PENDING:
            row.state = ScenarioRunStandState.RELEASED
    await db.commit()
    audit_service.emit(
        f"scenario_run.{state}", target_id=run.id, target_type="scenario_run",
        status="success" if state == ScenarioRunState.SUCCEEDED else "failure", allowed=True,
        details={"verdict": verdict, "error": run.error, "released": to_release},
    )
    if run.test_run_id:
        from src.services import queue as queue_svc
        from src.services import test_run_status

        test_run_id = run.test_run_id
        new_status = await test_run_status.recompute(db, test_run_id)
        await db.commit()
        await queue_svc.on_test_run_status(db, new_status, test_run_id)
    for stand_id in to_release:
        stand = await db.get(TestStand, stand_id)
        if stand is not None:
            await _release_to_queue(db, stand)


# ── чтение ───────────────────────────────────────────────────────────────────

async def get_run_or_404(db: AsyncSession, identity: Identity, run_id: str) -> ScenarioRun:
    run = await db.get(ScenarioRun, run_id)
    if run is None:
        raise NotFoundError(error_code="SCENARIO_RUN_NOT_FOUND", message="Scenario run not found")
    permissions.require_own_department(identity, run.department_id)
    return run


async def serialize_run(db: AsyncSession, run: ScenarioRun) -> dict:
    rows = await _run_stands(db, run)
    actions = await _actions(db, run)
    items = {
        i.scenario_action_id: i for i in (await db.execute(
            select(QueueItem).where(QueueItem.scenario_run_id == run.id).order_by(QueueItem.created_at)
        )).scalars()
    }
    stand_by_row = {r.scenario_stand_id: r.stand_id for r in rows}
    return {
        "id": run.id, "scenario_id": run.scenario_id, "department_id": run.department_id,
        "state": run.state, "launch_context": run.launch_context, "debug_mode": run.debug_mode,
        "stp_test_run_id": run.stp_test_run_id, "test_run_id": run.test_run_id,
        "current_position": run.current_position, "wait_until": run.wait_until,
        "blocked_by": run.blocked_by or [], "verdict": run.verdict, "error": run.error,
        "created_by": run.created_by, "created_at": run.created_at, "started_at": run.started_at,
        "finished_at": run.finished_at,
        "stands": [
            {"stand_id": r.stand_id, "label": r.label, "preparation": r.preparation,
             "skip_pam_fix": r.skip_pam_fix, "kernel": r.kernel, "mode": r.mode, "state": r.state, "error": r.error}
            for r in await _run_stands(db, run)
        ],
        "actions": [
            {
                "position": a.position, "kind": a.kind, "stand_id": stand_by_row.get(a.scenario_stand_id),
                "test_id": a.test_id, "is_verdict": a.is_verdict, "params": a.params or {},
                "queue_item_id": items[a.id].id if a.id in items else None,
                "state": items[a.id].state if a.id in items else None,
                "verdict": items[a.id].verdict if a.id in items else None,
            }
            for a in actions
        ],
    }


async def list_runs(db: AsyncSession, identity: Identity, scenario_id: str) -> list[ScenarioRun]:
    scenario = await db.get(Scenario, scenario_id)
    if scenario is None:
        raise NotFoundError(error_code="SCENARIO_NOT_FOUND", message="Scenario not found")
    permissions.require_own_department(identity, scenario.department_id)
    return list((await db.execute(
        select(ScenarioRun).where(ScenarioRun.scenario_id == scenario_id)
        .order_by(ScenarioRun.created_at.desc()).limit(50)
    )).scalars())
