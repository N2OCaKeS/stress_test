"""update_bot — защитные проверки strict-guard'а и invalidate-кэша:

* После update_bot identity-кэш бота сбрасывается — иначе allowed_services /
  is_active / status / name из старой версии живут в introspect до истечения
  TTL (~5 секунд). По CLAUDE-инварианту «отзыв доступа вступает в силу
  немедленно» — это нарушение.
* Cross-tenant guard не должен пускать DEPARTMENT_ADMIN'а с
  `actor_department_id=None` (data integrity bug) — должен честно отдать
  403 BOT_UPDATE_FORBIDDEN, как все остальные bot-функции через
  `_check_can_manage_bot_or_audit`.
* Guard срабатывает до name-check'а: cross-tenant dept_admin'у не должно
  светиться имя из чужого отдела через 409 BOT_NAME_TAKEN.
* В failure-audit details кладётся явное `actor_department_id` (None при
  broken identity) — SIEM использует это поле для отделения cross-tenant от
  null-dept identity.
"""

import pytest

from src.core.constants import PlatformRole
from src.schemas.bots import BotCreate, BotUpdate
from src.services import bot_service

BOTS_URL = "/api/auth/v1/bots"


@pytest.fixture()
def captured_audit(monkeypatch):
    """Перехват emit'ов аудит-сервиса для inspect'а деталей события."""
    captured: list[dict] = []

    def fake_emit(action, actor_id=None, **kwargs):
        captured.append({"action": action, "actor_id": actor_id, **kwargs})

    import src.services.audit_service as audit_mod
    monkeypatch.setattr(audit_mod, "emit", fake_emit)
    return captured


# ── invalidate_identity_cache спай ────────────────────────────────────────────


@pytest.fixture()
def spy_invalidate(monkeypatch):
    seen: list[str] = []

    def spy(subject_id: str) -> None:
        seen.append(subject_id)

    monkeypatch.setattr(bot_service, "_invalidate_identity_cache", spy)
    return seen


# ── #1: update_bot invalidates identity cache ─────────────────────────────────


async def test_update_bot_invalidates_identity_cache(
    db, account_admin, dept_a_with_service, service_x, spy_invalidate,
):
    """Сужение allowed_services должно сбросить identity-кэш бота."""
    created = await bot_service.create_bot(
        db,
        actor_id=account_admin.id,
        actor_role=PlatformRole.ACCOUNT_ADMIN,
        data=BotCreate(
            name="cache_upd_bot",
            department_id=dept_a_with_service.id,
            allowed_services=[service_x.service_name],
        ),
    )
    spy_invalidate.clear()

    await bot_service.update_bot(
        db,
        actor_id=account_admin.id,
        actor_role=PlatformRole.ACCOUNT_ADMIN,
        bot_id=created.bot_id,
        data=BotUpdate(allowed_services=[]),
    )

    assert created.bot_id in spy_invalidate, (
        "update_bot должен инвалидировать identity-кэш — иначе introspect "
        "вернёт старые allowed_services до истечения TTL"
    )


async def test_update_bot_status_change_invalidates_cache(
    db, account_admin, dept_a, spy_invalidate,
):
    """Status active→disabled тоже должен сбрасывать кэш."""
    created = await bot_service.create_bot(
        db,
        actor_id=account_admin.id,
        actor_role=PlatformRole.ACCOUNT_ADMIN,
        data=BotCreate(
            name="cache_status_bot",
            department_id=dept_a.id,
            allowed_services=[],
        ),
    )
    spy_invalidate.clear()

    await bot_service.update_bot(
        db,
        actor_id=account_admin.id,
        actor_role=PlatformRole.ACCOUNT_ADMIN,
        bot_id=created.bot_id,
        data=BotUpdate(status="disabled"),
    )

    assert created.bot_id in spy_invalidate


# ── #2: DEPARTMENT_ADMIN с actor_department_id=None — 403, не bypass ──────────


async def test_update_bot_dept_admin_with_none_dept_is_forbidden(
    db, dept_b, account_admin,
):
    """Broken identity (DEPARTMENT_ADMIN, dept=None) → 403 BOT_UPDATE_FORBIDDEN.

    До фикса `if actor_dept_id is not None and ...` тихо пропускал такого
    актора через guard, и он мог патчить ботов чужих отделов.
    """
    # Бот в dept_b создаётся account_admin'ом.
    bot = await bot_service.create_bot(
        db,
        actor_id=account_admin.id,
        actor_role=PlatformRole.ACCOUNT_ADMIN,
        data=BotCreate(
            name="none_dept_target_bot",
            department_id=dept_b.id,
            allowed_services=[],
        ),
    )

    # actor_department_id=None имитирует broken IdentityContext.
    # Чтобы фолбэк-SELECT в _resolve_actor_dept тоже не дал валидный dept_a,
    # подменим dept_admin_a.id на актор без записи в users.
    from src.core.exceptions import AuthorizationError

    with pytest.raises(AuthorizationError) as exc:
        await bot_service.update_bot(
            db,
            actor_id="usr_does_not_exist_in_db",
            actor_role=PlatformRole.DEPARTMENT_ADMIN,
            bot_id=bot.bot_id,
            data=BotUpdate(description="hijack"),
            actor_department_id=None,
        )
    assert exc.value.error_code == "BOT_UPDATE_FORBIDDEN"


async def test_update_bot_account_admin_with_no_dept_still_allowed(
    db, account_admin, dept_a,
):
    """ACCOUNT_ADMIN без dept (штатно) — guard не запускается, update проходит.

    Регрессионный страх: после удаления `is not None` не сломалось ли
    случайно поведение для платформенных админов (у них dept_id всегда None).
    """
    bot = await bot_service.create_bot(
        db,
        actor_id=account_admin.id,
        actor_role=PlatformRole.ACCOUNT_ADMIN,
        data=BotCreate(
            name="acct_admin_upd_bot",
            department_id=dept_a.id,
            allowed_services=[],
        ),
    )

    updated = await bot_service.update_bot(
        db,
        actor_id=account_admin.id,
        actor_role=PlatformRole.ACCOUNT_ADMIN,
        bot_id=bot.bot_id,
        data=BotUpdate(description="ok"),
        actor_department_id=None,
    )
    assert updated.description == "ok"


# ── #3: DEPARTMENT_ADMIN со своим dept_id проходит guard на unit-уровне ───────


async def test_update_bot_dept_admin_same_dept_passes_guard_unit(
    db, account_admin, dept_admin_a, dept_a, captured_audit,
):
    """Прямой вызов сервиса (не HTTP) с валидным actor_department_id == bot.department_id.

    HTTP-paths уже покрыты (см. test_actor_dept_thread_through), но unit-вызов
    проверяет: guard не raise'ит, payload коммитится, success-audit содержит
    sorted `fields_changed`.
    """
    bot = await bot_service.create_bot(
        db,
        actor_id=account_admin.id,
        actor_role=PlatformRole.ACCOUNT_ADMIN,
        data=BotCreate(
            name="same_dept_bot",
            department_id=dept_a.id,
            allowed_services=[],
        ),
    )
    captured_audit.clear()

    updated = await bot_service.update_bot(
        db,
        actor_id=dept_admin_a.id,
        actor_role=PlatformRole.DEPARTMENT_ADMIN,
        bot_id=bot.bot_id,
        data=BotUpdate(description="patched_by_own_dept_admin"),
        actor_department_id=dept_a.id,
    )
    assert updated.description == "patched_by_own_dept_admin"

    success = [
        e for e in captured_audit
        if e["action"] == "bot.update" and e.get("status", "success") != "failure"
    ]
    assert success, f"no success bot.update audit: {captured_audit}"
    details = success[-1].get("details") or {}
    assert details.get("fields_changed") == ["description"]
    assert "description" in (details.get("changes") or {})


# ── #4: failure-audit при None-dept identity явно несёт actor_department_id ───


async def test_update_bot_dept_admin_none_dept_failure_audit_has_null_actor_dept(
    db, dept_b, account_admin, captured_audit,
):
    """В failure-audit details['actor_department_id'] == None (не отсутствует).

    SIEM-правило `actor_department_id IS NULL AND reason='cross_tenant_bot'`
    отличает broken identity от штатной cross-tenant попытки. Если поле
    пропадёт из details, правило молча перестанет матчить — регрессия.
    """
    bot = await bot_service.create_bot(
        db,
        actor_id=account_admin.id,
        actor_role=PlatformRole.ACCOUNT_ADMIN,
        data=BotCreate(
            name="none_dept_audit_target",
            department_id=dept_b.id,
            allowed_services=[],
        ),
    )
    captured_audit.clear()

    from src.core.exceptions import AuthorizationError

    with pytest.raises(AuthorizationError):
        await bot_service.update_bot(
            db,
            actor_id="usr_does_not_exist_for_audit",
            actor_role=PlatformRole.DEPARTMENT_ADMIN,
            bot_id=bot.bot_id,
            data=BotUpdate(description="hijack"),
            actor_department_id=None,
        )

    failures = [
        e for e in captured_audit
        if e["action"] == "bot.update" and e.get("status") == "failure"
    ]
    assert failures, f"no failure bot.update audit: {captured_audit}"
    details = failures[0].get("details") or {}
    assert details["reason"] == "cross_tenant_bot"
    assert details["bot_department_id"] == dept_b.id
    # Ключ присутствует и явно None — не отсутствует.
    assert "actor_department_id" in details
    assert details["actor_department_id"] is None


# ── #5: cross-tenant + name conflict — guard срабатывает раньше name-check'а ──


async def test_update_bot_cross_tenant_name_conflict_returns_403_not_409(
    db, account_admin, dept_admin_a, dept_a, dept_b, captured_audit,
):
    """Dept_admin отдела A патчит бота отдела B, целясь в имя, занятое в A.

    Guard на cross-tenant идёт ДО проверки уникальности имени — значит ответ
    обязан быть 403 BOT_UPDATE_FORBIDDEN, не 409 BOT_NAME_TAKEN. Иначе
    атакующий, не имея прав на чужой отдел, узнавал бы существование имён
    через 409-ответ (information disclosure).
    """
    # Бот в "родном" отделе dept_a с именем, которое попробуем "забрать".
    existing = await bot_service.create_bot(
        db,
        actor_id=account_admin.id,
        actor_role=PlatformRole.ACCOUNT_ADMIN,
        data=BotCreate(
            name="dept_a_taken_name",
            department_id=dept_a.id,
            allowed_services=[],
        ),
    )
    # Бот в чужом отделе dept_b, который dept_admin_a будет пробовать переименовать.
    foreign = await bot_service.create_bot(
        db,
        actor_id=account_admin.id,
        actor_role=PlatformRole.ACCOUNT_ADMIN,
        data=BotCreate(
            name="dept_b_target_bot",
            department_id=dept_b.id,
            allowed_services=[],
        ),
    )
    captured_audit.clear()

    from src.core.exceptions import AuthorizationError

    with pytest.raises(AuthorizationError) as exc:
        await bot_service.update_bot(
            db,
            actor_id=dept_admin_a.id,
            actor_role=PlatformRole.DEPARTMENT_ADMIN,
            bot_id=foreign.bot_id,
            data=BotUpdate(name="dept_a_taken_name"),
            actor_department_id=dept_a.id,
        )
    # Именно BOT_UPDATE_FORBIDDEN — не BOT_NAME_TAKEN. Различимые коды
    # критичны: 409 раскрыл бы факт занятости имени, 403 — нет.
    assert exc.value.error_code == "BOT_UPDATE_FORBIDDEN"

    # Failure-audit на cross-tenant эмитнут; success-audit на bot.update — нет.
    failures = [
        e for e in captured_audit
        if e["action"] == "bot.update" and e.get("status") == "failure"
    ]
    assert failures, f"guard не эмитнул failure-audit: {captured_audit}"
    assert failures[0]["details"]["reason"] == "cross_tenant_bot"

    # Имя дефолтного бота не изменилось — guard остановил до repo.update.
    successes = [
        e for e in captured_audit
        if e["action"] == "bot.update" and e.get("status", "success") != "failure"
    ]
    assert not successes, "success-audit не должен эмититься при отказе guard'а"

    # Существование "своего" бота тоже не пострадало — silence linter про unused.
    assert existing.name == "dept_a_taken_name"


# ── #6: success-audit поверх strict-guard'а не содержит actor_department_id ───


async def test_update_bot_success_audit_does_not_leak_actor_department_id(
    db, account_admin, dept_admin_a, dept_a, captured_audit,
):
    """Success-audit `bot.update` не несёт `actor_department_id` в details.

    SIEM ожидает `actor_department_id` только в failure-ветке как маркер
    cross-tenant. Если кто-то по ошибке добавит его в success-emit, правила
    `actor_department_id IS NOT NULL → cross_tenant suspect` начнут стрелять
    на штатных обновлениях. Регрессионная защита.
    """
    bot = await bot_service.create_bot(
        db,
        actor_id=account_admin.id,
        actor_role=PlatformRole.ACCOUNT_ADMIN,
        data=BotCreate(
            name="audit_shape_bot",
            department_id=dept_a.id,
            allowed_services=[],
        ),
    )
    captured_audit.clear()

    await bot_service.update_bot(
        db,
        actor_id=dept_admin_a.id,
        actor_role=PlatformRole.DEPARTMENT_ADMIN,
        bot_id=bot.bot_id,
        data=BotUpdate(description="audit-shape-check"),
        actor_department_id=dept_a.id,
    )

    success = [
        e for e in captured_audit
        if e["action"] == "bot.update" and e.get("status", "success") != "failure"
    ]
    assert success, f"no success audit: {captured_audit}"
    details = success[-1].get("details") or {}
    assert "actor_department_id" not in details, (
        f"actor_department_id просочился в success-audit: {details}"
    )
    # Минимальный sanity-check shape'а — без лишних предположений.
    assert details.get("department_id") == dept_a.id
    assert "changes" in details
    assert "fields_changed" in details
