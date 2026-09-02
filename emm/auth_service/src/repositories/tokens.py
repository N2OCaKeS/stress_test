"""DAO для `PersonalAccessToken` — CRUD + touch + revoke (включая bulk при ban'е)."""

from datetime import datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.personal_access_token import PersonalAccessToken
from src.utils.ids import pat_id
from src.utils.time import utcnow

# Допустимые значения для `PersonalAccessToken.revoked_reason`.
#   "user"        — юзер сам через DELETE /tokens/{id}.
#   "ban"         — bulk при ban'е; `unban_user` именно по нему ищет PAT для
#                   реактивации.
#   "admin_reset" — каскад при password-reset.
#   "hard_delete" — bulk при `DELETE /users/{id}` перед ORM-cascade'ом
#                   (нужен для audit `pat_revoked_count`).
# Любое другое значение → пакеты выше по стеку молча писали бы строку в БД;
# Literal даёт mypy/pyright отсечь опечатки. Значения зеркалят DB-CHECK
# `ck_personal_access_tokens_revoked_reason` в `g4h5i6j7k8l9_auth_db_hardening`.
RevokeReason = Literal["user", "ban", "admin_reset", "hard_delete"]


class TokenRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get_by_id(self, tid: str) -> PersonalAccessToken | None:
        return await self._db.get(PersonalAccessToken, tid)

    async def get_active_by_hash(self, token_hash: str) -> PersonalAccessToken | None:
        return await self._db.scalar(
            select(PersonalAccessToken).where(
                PersonalAccessToken.token_hash == token_hash,
                PersonalAccessToken.revoked_at.is_(None),
            )
        )

    async def list_for_user(self, user_id: str) -> list[PersonalAccessToken]:
        result = await self._db.scalars(
            select(PersonalAccessToken)
            .where(PersonalAccessToken.user_id == user_id)
            .order_by(PersonalAccessToken.created_at.desc())
        )
        return list(result)

    async def exists_name(self, user_id: str, name: str) -> bool:
        """True если у юзера есть **активный** (не revoked) PAT с этим именем.

        После revoke имя освобождается — это нужно, чтобы юзер мог пересоздать
        PAT с прежним именем (типичный flow при ротации). Уникальность по
        revoked-строкам не держим: история сохраняется в `revoked_reason` /
        `revoked_at`, но не блокирует новое имя.
        """
        return await self._db.scalar(
            select(PersonalAccessToken.id).where(
                PersonalAccessToken.user_id == user_id,
                PersonalAccessToken.name == name,
                PersonalAccessToken.revoked_at.is_(None),
            )
        ) is not None

    async def create(
        self,
        user_id: str,
        name: str,
        token_hash: str,
        token_prefix: str,
        allowed_services: list[str],
        expires_at=None,
    ) -> PersonalAccessToken:
        pat = PersonalAccessToken(
            id=pat_id(),
            user_id=user_id,
            name=name,
            token_hash=token_hash,
            token_prefix=token_prefix,
            allowed_services=allowed_services,
            expires_at=expires_at,
        )
        self._db.add(pat)
        await self._db.flush()
        return pat

    async def revoke(
        self, pat: PersonalAccessToken, reason: RevokeReason | None = "user"
    ) -> None:
        """Отозвать PAT. См. `RevokeReason` про допустимые значения."""
        pat.revoked_at = utcnow()
        pat.revoked_reason = reason
        await self._db.flush()

    async def revoke_all_for_user(
        self, user_id: str, reason: RevokeReason | None = "user"
    ) -> int:
        """Bulk-revoke всех активных PAT юзера.

        Возвращает count затронутых строк (использует ``unban_user`` для
        cap'а количества реактиваций).
        """
        now = utcnow()
        result = await self._db.scalars(
            select(PersonalAccessToken).where(
                PersonalAccessToken.user_id == user_id,
                PersonalAccessToken.revoked_at.is_(None),
            )
        )
        count = 0
        for pat in result:
            pat.revoked_at = now
            pat.revoked_reason = reason
            count += 1
        await self._db.flush()
        return count

    async def reactivate_ban_revoked(
        self, user_id: str, since: datetime | None = None
    ) -> int:
        """Реактивировать PAT, revoke'нутые последним ban'ом.

        Критерии:
          * `user_id` совпадает;
          * `revoked_reason == "ban"` — отделяет от "user"/"admin_reset"
            и от legacy без reason;
          * `revoked_at >= since` — если задан (timestamp последнего ban'а),
            отбрасывает PAT'ы предыдущих ban'ов. Окно «ban → юзер создал
            ещё PAT → второй ban → unban» возвращает только последние.
            Граница включающая — PAT, отозванный в ту же микросекунду, что
            и сам ban (массовый revoke внутри `ban_user`), должен попадать.

        Одним UPDATE ставим `revoked_at=NULL`, `revoked_reason=NULL`. CAS не
        нужен — мы уже под exclusive lock'ом unban'а, параллельных ban-worker'ов
        на этом юзере не будет.
        """
        from sqlalchemy import update as _update

        conds = [
            PersonalAccessToken.user_id == user_id,
            PersonalAccessToken.revoked_reason == "ban",
        ]
        if since is not None:
            conds.append(PersonalAccessToken.revoked_at >= since)

        stmt = (
            _update(PersonalAccessToken)
            .where(*conds)
            .values(revoked_at=None, revoked_reason=None)
            .returning(PersonalAccessToken.id)
        )
        rows = await self._db.scalars(stmt)
        ids = list(rows)
        await self._db.flush()
        return len(ids)

    async def touch(self, pat: PersonalAccessToken) -> None:
        pat.last_used_at = utcnow()
        await self._db.flush()
