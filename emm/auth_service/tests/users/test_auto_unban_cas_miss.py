"""`auto_unban_if_expired` ветка CAS-miss: `deactivate` вернул False.

Это случай, когда оба worker'а одновременно прочитали активный ban-row, прошли
guard'ы (`expires_at < now`), но успешный CAS-UPDATE сделал только один из них.
Проигравший должен `return True` БЕЗ commit'а и БЕЗ audit-эмита — caller
перечитает свежий state и продолжит обычный flow.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import update

from src.models import Ban
from src.repositories.bans import BanRepository
from src.services import audit_service as audit_mod
from src.services import user_service as us_mod


USERS_URL = "/api/auth/v1/users"


async def test_cas_miss_returns_true_without_audit_or_commit(
    client, admin_token, user_a, db, monkeypatch,
):
    # Ставим истёкший temporary ban.
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    await client.post(
        f"{USERS_URL}/{user_a.id}/ban",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"ban_type": "temporary", "reason": "cas-miss", "expires_at": future},
    )
    past = datetime.now(timezone.utc) - timedelta(seconds=5)
    await db.execute(update(Ban).where(Ban.user_id == user_a.id).values(expires_at=past))
    await db.commit()

    # Перехватываем `user.unban`-audit'ы.
    captured: list[dict] = []
    original_emit = audit_mod.emit

    def _capture(action, actor_id=None, **kw):
        if action == "user.unban":
            captured.append({"actor_id": actor_id, "details": dict(kw.get("details") or {})})
        return original_emit(action, actor_id, **kw)

    monkeypatch.setattr(audit_mod, "emit", _capture)
    monkeypatch.setattr(us_mod, "audit_service", audit_mod)

    # Forcing CAS-miss: подменяем `BanRepository.deactivate` чтобы он возвращал
    # False, имитируя проигравшего worker'а в CAS-гонке.
    async def _deactivate_false(self, ban, unbanned_by=None):
        return False

    monkeypatch.setattr(BanRepository, "deactivate", _deactivate_false)

    await db.refresh(user_a)
    result = await us_mod.auto_unban_if_expired(db, user_a)

    # По контракту silently bail: True без commit'а и без audit'а.
    assert result is True, (
        "CAS-miss должен вернуть True — caller перечитывает свежий state"
    )
    assert captured == [], (
        f"CAS-miss НЕ должен эмитить user.unban, got {captured}"
    )
