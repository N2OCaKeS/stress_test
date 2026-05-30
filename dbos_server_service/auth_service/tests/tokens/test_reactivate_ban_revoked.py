"""Unit-тест на `TokenRepository.reactivate_ban_revoked` — граничное условие.

До фикса фильтр был `revoked_at > since`. `unban_user` передавал
`since=ban.banned_at`, а `ban_user` пишет `revoked_at = utcnow()` уже после
создания `Ban`-row. Если БД-таймлайн возвращал ту же микросекунду для обоих
(PostgreSQL `now()` стабилен внутри транзакции) — PAT, отозванный ровно в
момент бана, не реактивировался.

Фикс — заменить `>` на `>=`.
"""

from datetime import timedelta

from src.models.personal_access_token import PersonalAccessToken
from src.repositories.tokens import TokenRepository
from src.utils.ids import pat_id


async def _make_pat(db, user_id, *, revoked_at, revoked_reason, name):
    pat = PersonalAccessToken(
        id=pat_id(),
        user_id=user_id,
        name=name,
        token_hash=f"hash_{name}",
        token_prefix="dbos_pat_",
        allowed_services=["service_x"],
        revoked_at=revoked_at,
        revoked_reason=revoked_reason,
    )
    db.add(pat)
    await db.flush()
    return pat


async def test_reactivate_ban_revoked_includes_pat_revoked_at_exact_boundary(
    db, user_a,
):
    """`revoked_at == since` должен попадать (включающая граница).

    Воспроизводим ситуацию: `ban_user` revoke'нул PAT в ту же микросекунду,
    что и `Ban.banned_at`. До фикса `>` отбрасывал такой PAT — `unban_user`
    оставлял его revoked'ым. С `>=` он реактивируется.
    """
    repo = TokenRepository(db)

    from src.utils.time import utcnow
    ban_moment = utcnow()

    # PAT, отозванный ровно в момент бана (та же микросекунда).
    boundary_pat = await _make_pat(
        db, user_a.id,
        revoked_at=ban_moment,
        revoked_reason="ban",
        name="boundary_pat",
    )
    # PAT прошлого бана — должен НЕ реактивироваться.
    old_pat = await _make_pat(
        db, user_a.id,
        revoked_at=ban_moment - timedelta(hours=1),
        revoked_reason="ban",
        name="old_pat",
    )
    # PAT, отозванный юзером, — не трогаем независимо от времени.
    manual_pat = await _make_pat(
        db, user_a.id,
        revoked_at=ban_moment + timedelta(seconds=1),
        revoked_reason="user",
        name="manual_pat",
    )

    reactivated = await repo.reactivate_ban_revoked(user_a.id, since=ban_moment)
    assert reactivated == 1

    await db.refresh(boundary_pat)
    await db.refresh(old_pat)
    await db.refresh(manual_pat)
    assert boundary_pat.revoked_at is None
    assert boundary_pat.revoked_reason is None
    assert old_pat.revoked_at is not None  # старый ban остался revoked
    assert manual_pat.revoked_at is not None  # user-revoke не воскрешаем
    assert manual_pat.revoked_reason == "user"
